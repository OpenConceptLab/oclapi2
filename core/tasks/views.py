from celery.states import FAILURE, PENDING, REVOKED
from django.db import transaction
from django.http import Http404
from drf_yasg.utils import swagger_auto_schema, no_body
from pydash import compact
from rest_framework import status
from rest_framework.generics import RetrieveAPIView, DestroyAPIView, get_object_or_404
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from core.common.exceptions import Http400
from core.common.mixins import ListWithHeadersMixin
from core.common.tasks import unfinished_readd_tasks, readd_references_to_expansion_on_references_removal
from core.common.swagger_parameters import page_param, verbose_param, task_state_param, limit_param, \
    task_start_date_param, q_param
from core.common.utils import from_string_to_date, get_truthy_values
from core.common.views import BaseAPIView
from core.tasks.models import Task
from core.tasks.serializers import TaskDetailSerializer, TaskListSerializer, TaskResultSerializer


class AbstractTaskView(BaseAPIView, RetrieveAPIView):
    queryset = Task.objects.filter()
    is_searchable = False
    permission_classes = (IsAuthenticated,)
    default_qs_sort_attr = '-created_at'

    def get_serializer_class(self):
        if self.request.query_params.get('result', None) in get_truthy_values():
            return TaskResultSerializer
        return TaskDetailSerializer if self.is_verbose() else TaskListSerializer

    def get_queryset(self):
        states = compact((self.request.query_params.get('state', None) or '').split(','))
        start_date = from_string_to_date(self.request.query_params.get('start_date', None))
        search_str = self.request.query_params.get('q', None)
        queryset = self.queryset.filter(started_at__gte=start_date) if start_date else self.queryset

        if states:
            queryset = queryset.filter(state__in=states)
        if search_str:
            queryset = queryset.filter(name__icontains=search_str)

        return queryset.order_by('-created_at')


class AbstractTaskListView(AbstractTaskView, ListWithHeadersMixin):
    def get_serializer_class(self):
        return TaskDetailSerializer if self.is_verbose() else TaskListSerializer

    @swagger_auto_schema(
        manual_parameters=[q_param, task_state_param, task_start_date_param, page_param, limit_param, verbose_param]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class TaskView(AbstractTaskView, DestroyAPIView):
    lookup_field = 'id'
    lookup_url_kwarg = 'task_id'
    pk_field = 'id'

    def get_object(self, queryset=None):
        queryset = self.get_queryset()
        obj = get_object_or_404(queryset, **{self.lookup_field: self.kwargs[self.lookup_url_kwarg]})
        if not obj.has_access(self.request.user):
            raise Http404()
        self.check_object_permissions(self.request, obj)
        return obj

    def destroy(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        self.perform_destroy(self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    def perform_destroy(self, instance):
        if not instance.has_access(self.request.user):
            self.permission_denied(self.request)
        if instance.is_finished:
            raise Http400('Task is already finished.')
        try:
            instance.revoke()
        except Exception as ex:
            raise Http400({'errors': ex.args}) from ex


class TaskRerunView(BaseAPIView):
    permission_classes = (IsAdminUser,)
    restored_fields = (
        'state', 'result', 'summary', 'error_message', 'traceback', 'retry', 'started_at', 'finished_at', 'children')

    @swagger_auto_schema(request_body=no_body, responses={202: TaskListSerializer})
    def post(self, request, task_id):  # pylint: disable=unused-argument
        get_object_or_404(Task.objects.all(), id=task_id)
        task, snapshot = self.claim(task_id)
        if not task:
            raise Http400('Only a failed or revoked re-add can be rerun.')
        # a revoked id stays revoked on the workers, so it reruns as a new record
        rerun = Task.new(
            queue=task.queue, user=task.created_by, name=readd_references_to_expansion_on_references_removal.name,
            args=task.args
        ) if snapshot['state'] == REVOKED else task
        try:
            rerun.rerun(force=True)
        except Exception as ex:  # pylint: disable=broad-except
            if rerun is not task:
                rerun.delete()
            Task.objects.filter(id=task.id, state=PENDING).update(**snapshot)
            if isinstance(ex, ValueError):
                raise Http400(str(ex)) from ex
            raise
        if rerun is not task:
            task.delete()
        rerun.refresh_from_db()
        return Response(TaskListSerializer(rerun).data, status=status.HTTP_202_ACCEPTED)

    @classmethod
    def claim(cls, task_id):
        with transaction.atomic():
            task = unfinished_readd_tasks().select_for_update().filter(
                id=task_id, state__in=(FAILURE, REVOKED)).first()
            if not task:
                return None, None
            snapshot = {field: getattr(task, field) for field in cls.restored_fields}
            Task.objects.filter(id=task.id).update(state=PENDING)
        return task, snapshot


class UserTaskListView(AbstractTaskListView):
    def get_queryset(self):
        return super().get_queryset().filter(created_by=self.request.user)


class TaskListView(AbstractTaskListView):
    permission_classes = (IsAdminUser,)
