from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from pydash import compact
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.tasks import resources_report
from core.common.views import BaseAPIView
from core.users.reports import UserReport


class ResourcesReportJobView(APIView):  # pragma: no cover
    permission_classes = (IsAdminUser, )

    @staticmethod
    def post(_):
        task = resources_report.delay()
        return Response(
            {
                'task': task.id,
                'state': task.state,
                'queue': task.queue or 'default'
            },
            status=status.HTTP_202_ACCEPTED
        )


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
