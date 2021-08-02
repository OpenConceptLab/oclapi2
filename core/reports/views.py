from drf_yasg.utils import swagger_auto_schema
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAdminUser

from core.common.swagger_parameters import verbose_param, start_date_param, end_date_param
from core.common.views import BaseAPIView
from core.reports.models import MonthlyUsageReport
from core.reports.serializers import MonthlyUsageReportSerializer


class MonthlyUsageView(BaseAPIView, RetrieveAPIView):  # pragma: no cover
    permission_classes = (IsAdminUser, )
    serializer_class = MonthlyUsageReportSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context.update({'is_verbose': self.is_verbose()})
        return context

    def get_object(self, _=None):
        is_verbose = self.is_verbose()
        report = MonthlyUsageReport(
            verbose=is_verbose,
            start=self.request.query_params.get('start', None),
            end=self.request.query_params.get('end', None)
        )
        report.prepare()
        return report.result

    @swagger_auto_schema(manual_parameters=[verbose_param, start_date_param, end_date_param])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
