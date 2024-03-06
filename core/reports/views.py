from dateutil.relativedelta import relativedelta
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from pydash import compact
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.tasks import resources_report
from core.common.views import BaseAPIView
from core.tasks.models import Task
from core.tasks.serializers import TaskBriefSerializer
from core.users.reports import UserReport


class ResourcesReportJobView(APIView):  # pragma: no cover
    permission_classes = (IsAdminUser, )

    @staticmethod
    @swagger_auto_schema(
        operation_description='Mails CSV of OCL resources usage for given period of time',
        operation_summary='Reports Resources Usage on the env for given period of time',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'start_date': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='YYYY-MM-DD (default: 1st of last month)',
                    default=(timezone.now().replace(day=1) - relativedelta(months=1)).strftime('%Y-%m-%d')
                ),
                'end_date': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='YYYY-MM-DD (default: 1st of current month)',
                    default=timezone.now().replace(day=1).strftime('%Y-%m-%d')
                ),
            }
        )
    )
    def post(request):
        task = Task.make_new(queue='default', user=request.user, name=resources_report.__name__)
        resources_report.apply_async(
            (request.data.get('start_date'), request.data.get('end_date')), queue=task.queue, task_id=task.id)

        task.refresh_from_db()

        return Response(TaskBriefSerializer(task).data, status=status.HTTP_202_ACCEPTED)


class AuthoredView(BaseAPIView):  # pragma: no cover
    permission_classes = (IsAdminUser, )

    @staticmethod
    @swagger_auto_schema(
        operation_description='Returns count of content created/updated by the user(s)',
        operation_summary='Resources Authored Summary for user(s)',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'usernames': openapi.Schema(type=openapi.TYPE_STRING, description='usernames comma separated')
            }
        )
    )
    def post(request):
        if not request.data:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        usernames = request.data.get('usernames', None)
        if not usernames:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if isinstance(usernames, str):
            usernames = compact([username.strip() for username in usernames.split(',')])

        return Response(UserReport.get_authoring_report(usernames), status.HTTP_200_OK)
