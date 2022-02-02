from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from pydash import compact
from rest_framework import status
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.renderers import TemplateHTMLRenderer, JSONRenderer

from core.common.swagger_parameters import verbose_param, start_date_param, end_date_param
from core.common.views import BaseAPIView
from core.reports.models import MonthlyUsageReport, UserReport
from core.reports.serializers import MonthlyUsageReportSerializer


class MonthlyUsageView(BaseAPIView, RetrieveAPIView):  # pragma: no cover
    permission_classes = (IsAdminUser, )
    renderer_classes = [TemplateHTMLRenderer, JSONRenderer]
    template_name = "monthly_usage_report.html"
    serializer_class = MonthlyUsageReportSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context.update({'is_verbose': self.is_verbose()})
        return context

    def is_verbose(self):
        return super().is_verbose() or self.request.query_params.dict().get('format') != 'json'

    def get_object(self, _=None):
        is_verbose = self.is_verbose()
        report = MonthlyUsageReport(
            verbose=is_verbose,
            start=self.request.query_params.get('start', None),
            end=self.request.query_params.get('end', None)
        )
        report.prepare()
        result = report.result
        result["verbose"] = is_verbose

        return result

    @swagger_auto_schema(manual_parameters=[verbose_param, start_date_param, end_date_param])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


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

        report = UserReport(instance_ids=usernames)
        result = report.get_authoring_report()

        return Response(result, status.HTTP_200_OK)
