from core.collections.models import Collection, Expansion, CollectionReference
from core.common.constants import HEAD
from core.reports.models import AbstractReport


class CollectionReport(AbstractReport):
    queryset = Collection.objects.filter(version=HEAD)
    name = 'Collections'
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


class CollectionVersionReport(AbstractReport):
    queryset = Collection.objects.exclude(version=HEAD)
    name = 'Collection Versions'
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


class ExpansionReport(AbstractReport):
    queryset = Expansion.objects.filter()
    name = 'Expansions'
    verbose = False


class ReferenceReport(AbstractReport):
    queryset = CollectionReference.objects.filter()
    name = 'References'
    grouped_label = "New References"
    verbose = False
    grouped = True
    GROUPED_HEADERS = ["Resource Type", "Static", "Dynamic", "Total"]

    @property
    def grouped_queryset(self):
        if not self.queryset.exists():
            return []
        concepts_queryset = self.queryset.filter(reference_type='concepts')
        mappings_queryset = self.queryset.filter(reference_type='mappings')
        total_concepts = concepts_queryset.count()
        total_mappings = mappings_queryset.count()
        static_criteria = CollectionReference.get_static_references_criteria()
        total_static_concepts = concepts_queryset.filter(static_criteria).count()
        total_static_mappings = mappings_queryset.filter(static_criteria).count()
        return [
            [
                'Concepts',
                total_static_concepts,
                total_concepts - total_static_concepts,
                total_concepts
            ],
            [
                'Mappings',
                total_static_mappings,
                total_mappings - total_static_mappings,
                total_mappings
            ]
        ]

    @staticmethod
    def to_grouped_stat_csv_row(obj):
        return [*obj]

    @property
    def retired(self):
        return 0

    @property
    def active(self):
        return self.count
