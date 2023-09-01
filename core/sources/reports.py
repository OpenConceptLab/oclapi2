from core.common.constants import HEAD
from core.reports.models import AbstractReport
from core.sources.models import Source


class SourceReport(AbstractReport):
    queryset = Source.objects.filter(version=HEAD)
    name = 'Sources'
    select_related = ['created_by', 'organization', 'user']
    verbose_fields = [
        'mnemonic',
        'name',
        'created_by.username',
        'created_at',
        'parent_resource_type',
        'parent_resource',
        'canonical_url',
        'custom_validation_schema'
    ]
    VERBOSE_HEADERS = [
        "ID",
        "Name",
        "Created By",
        "Created At",
        "Owner Type",
        "Owner",
        "Canonical URL",
        "Validation Schema"
    ]


class SourceVersionReport(AbstractReport):
    queryset = Source.objects.exclude(version=HEAD)
    name = 'Source Versions'
    select_related = ['created_by', 'organization', 'user']
    verbose_fields = [
        'version',
        'mnemonic',
        'name',
        'created_by.username',
        'created_at',
        'parent_resource_type',
        'parent_resource',
        'custom_validation_schema'
    ]
    VERBOSE_HEADERS = [
        "Version",
        "ID",
        "Name",
        "Created By",
        "Created At",
        "Owner Type",
        "Owner",
        "Validation Schema"
    ]
