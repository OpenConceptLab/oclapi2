import uuid

from celery.result import AsyncResult
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.tasks import bulk_import, bulk_priority_import

task_param = openapi.Parameter(
    'task', openapi.IN_QUERY, description="task uuid (mandatory)", type=openapi.TYPE_STRING
)
result_param = openapi.Parameter(
    'result', openapi.IN_QUERY, description="result format (json | report) (optional)", type=openapi.TYPE_STRING
)
update_if_exists_param = openapi.Parameter(
    'update_if_exists', openapi.IN_QUERY, description="true | false (mandatory)", type=openapi.TYPE_STRING,
    default='true'
)


class BulkImportView(APIView):
    permission_classes = (IsAuthenticated, )

    @swagger_auto_schema(manual_parameters=[update_if_exists_param])
    def post(self, request):
        username = self.request.user.username
        update_if_exists = request.GET.get('update_if_exists', 'true')
        if update_if_exists not in ['true', 'false']:
            return Response(
                dict(exception="update_if_exists must be either 'true' or 'false'"),
                status=status.HTTP_400_BAD_REQUEST
            )
        update_if_exists = update_if_exists == 'true'

        task_id = str(uuid.uuid4()) + '-' + username
        if username in ['root', 'ocladmin']:
            task = bulk_priority_import.apply_async((request.body, username, update_if_exists), task_id=task_id)
        else:
            task = bulk_import.apply_async((request.body, username, update_if_exists), task_id=task_id)

        return Response(dict(task=task.id, state=task.state))

    @swagger_auto_schema(manual_parameters=[task_param, result_param])
    def get(self, request):  # pylint: disable=too-many-return-statements
        task_id = request.GET.get('task')
        result_format = request.GET.get('result')
        if not task_id:
            return Response(dict(exception='Required task id'), status=status.HTTP_400_BAD_REQUEST)
        username = task_id[37:]
        user = self.request.user

        if not user.is_staff and user.username != username:
            return Response(status=status.HTTP_403_FORBIDDEN)

        task = AsyncResult(task_id)

        if task.successful():
            result = task.get()
            if result_format == 'json':
                response = Response(result.json, content_type="application/json")
                response['Content-Encoding'] = 'gzip'
                return response
            if result_format == 'report':
                return Response(result.report)
            return Response(result.detailed_summary)

        if task.failed():
            return Response(dict(exception=str(task.result)), status=status.HTTP_400_BAD_REQUEST)

        return Response(dict(task=task.id, state=task.state))
