from core.reports.models import AbstractReport
from core.orgs.models import Organization


class OrganizationReport(AbstractReport):
    queryset = Organization.objects.filter()
    name = 'Organizations'
    select_related = ['created_by']
    verbose_fields = ['mnemonic', 'name', 'created_by.username', 'created_at']
    VERBOSE_HEADERS = ["ID", "Name", "Created By", "Created At"]
