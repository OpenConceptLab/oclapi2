from core.collections.models import Collection, Expansion, CollectionReference
from core.common.constants import HEAD
from core.reports.models import AbstractReport


class CollectionReport(AbstractReport):
    queryset = Collection.objects.filter(version=HEAD)
    name = 'Collections'
    id = 'collections'
    select_related = ['created_by', 'organization', 'user']
    verbose_fields = [
        'mnemonic',
        'name',
        'collection_type',
        'public_access',
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
        "Collection Type",
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


class CollectionVersionReport(AbstractReport):
    queryset = Collection.objects.exclude(version=HEAD)
    name = 'Collection Versions'
    id = 'collection_versions'
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
        "Collection URL",
        "Created By",
        "Created At",
        "Released"
    ]

    @property
    def retired(self):
        return self.NA


class ExpansionReport(AbstractReport):
    queryset = Expansion.objects.filter()
    name = 'Expansions'
    id = 'expansions'
    verbose = False

    @property
    def retired(self):
        return self.NA


class ReferenceReport(AbstractReport):
    queryset = CollectionReference.objects.filter()
    name = 'References'
    id = 'references'
    grouped_label = "New References"
    summary_label = "Summary of References"
    verbose = False
    grouped = True
    GROUPED_HEADERS = [
        "Reference Type",
        "Static during Period",
        "Dynamic during Period",
        "Subtotal during Period",
        "Total as of Report Date"
    ]

    SUMMARY_HEADERS = [
        "Created during Period -- with canonical URL",
        "Created during Period -- no canonical URL",
        "Subtotal Created during Period",
        "Active as of Report Date -- with canonical URL",
        "Active as of Report Date -- no canonical URL",
        "Total as of Report Date"
    ]

    def to_summary_row(self):
        base_queryset = CollectionReference.objects.filter()
        queryset = self.queryset
        return [
            queryset.filter(system__contains=':').count(),
            queryset.exclude(system__contains=':').count(),
            queryset.count(),
            base_queryset.filter(system__contains=':').count(),
            base_queryset.exclude(system__contains=':').count(),
            base_queryset.count()
        ]

    @property
    def retired(self):
        return self.NA

    def get_cascaded_queryset(self):
        return self.queryset.exclude(cascade__isnull=True).exclude(cascade__in=['', 'none', False])

    def get_concepts_queryset(self):
        return self.queryset.filter(reference_type='concepts').exclude(cascade__isnull=False)

    def get_mappings_queryset(self):
        return self.queryset.filter(reference_type='mappings').exclude(cascade__isnull=False)

    @property
    def grouped_queryset(self):
        if not self.queryset.exists():
            return []
        concepts_queryset = self.get_concepts_queryset()
        mappings_queryset = self.get_mappings_queryset()
        total_concepts = concepts_queryset.count()
        total_mappings = mappings_queryset.count()
        static_criteria = CollectionReference.get_static_references_criteria()
        total_static_concepts = concepts_queryset.filter(static_criteria).count()
        total_static_mappings = mappings_queryset.filter(static_criteria).count()
        overall_report = self.get_overall_report_instance()
        overall_concepts = overall_report.get_concepts_queryset()
        overall_mappings = overall_report.get_mappings_queryset()
        cascade_count = self.get_cascaded_queryset().count()
        overall_cascade_count = overall_report.get_cascaded_queryset().count()
        return [
            [
                'Concepts only',
                total_static_concepts,
                total_concepts - total_static_concepts,
                total_concepts,
                overall_concepts.count()
            ],
            [
                'Mappings only',
                total_static_mappings,
                total_mappings - total_static_mappings,
                total_mappings,
                overall_mappings.count()
            ],
            [
                'Cascade',
                self.NA,
                cascade_count,
                cascade_count,
                overall_cascade_count
            ],
        ]

    @staticmethod
    def to_grouped_stat_csv_row(obj):
        return [*obj]
