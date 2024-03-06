from core.common.constants import HEAD
from core.reports.models import AbstractReport
from core.sources.models import Source


class SourceReport(AbstractReport):
    queryset = Source.objects.filter(version=HEAD)
    name = 'Sources'
    id = 'sources'
    select_related = ['created_by', 'organization', 'user']
    verbose_fields = [
        'mnemonic',
        'name',
        "source_type",
        "public_access",
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
        "Source Type",
        "Public Access",
        "Created By",
        "Created At",
        "Owner Type",
        "Owner",
        "Canonical URL",
        "Validation Schema"
    ]

    @property
    def retired(self):
        return self.NA


class SourceVersionReport(AbstractReport):
    queryset = Source.objects.exclude(version=HEAD)
    name = 'Source Versions'
    id = 'source_versions'
    note = 'Excludes HEAD versions'
    select_related = ['created_by']
    verbose_fields = [
        'version',
        'versioned_object_url',
        'created_by.username',
        'created_at',
        'released'
    ]
    VERBOSE_HEADERS = [
        "Version",
        "Source URL",
        "Created By",
        "Created At",
        "Released"
    ]

    @property
    def retired(self):
        return self.NA
