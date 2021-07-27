from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.common.views import BaseAPIView
from core.reports.models import MonthlyUsageReport


class MonthlyUsageView(BaseAPIView):  # pragma: no cover
    permission_classes = (IsAdminUser, )

    def get(self, request):
        report = MonthlyUsageReport(
            verbose=self.is_verbose(),
            start=request.query_params.get('start', None),
            end=request.query_params.get('end', None)
        )
        report.prepare()

        return Response(report.result)
