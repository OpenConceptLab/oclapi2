from celery_once import AlreadyQueued
from django.conf import settings
from pydash import get
from rest_framework import status
from rest_framework.response import Response

from core.tasks.constants import TASK_NOT_COMPLETED
from core.tasks.models import Task
from core.tasks.serializers import TaskBriefSerializer
from core.tasks.utils import wait_until_task_complete


class TaskMixin:
    """
    - Runs task in following way:
        1.?inline=true or TEST_MODE , run the task inline
        2. ?async=true, return task id/state/queue
        3. else, run the task and wait for few seconds to get the result, either returns result or task id/state/queue
    - Assigns username to task_id so that it can be tracked by username
    """
    @staticmethod
    def task_response(task):
        return Response(TaskBriefSerializer(task).data, status=status.HTTP_202_ACCEPTED)

    def perform_task(self, task_func, task_args, queue='default', is_default_async=False):
        is_async = is_default_async or self.is_async_requested()
        if self.is_inline_requested() or (get(settings, 'TEST_MODE', False) and not is_async):
            result = task_func(*task_args)
        else:
            celery_task = None
            try:
                celery_task = Task.new(queue, self.request.user, name=task_func.__name__)
                task_func.apply_async(task_args, task_id=celery_task.id)
            except AlreadyQueued:
                if celery_task:
                    celery_task.delete()
                return Response({'detail': 'Already Queued'}, status=status.HTTP_409_CONFLICT)
            if is_async:
                return self.task_response(celery_task)

            result = wait_until_task_complete(celery_task.id, 25)
            if result == TASK_NOT_COMPLETED:
                return self.task_response(celery_task)

        return result
