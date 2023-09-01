from django.db.models import F, Count

from core.reports.models import AbstractReport
from core.mappings.models import Mapping


class MappingReport(AbstractReport):
    queryset = Mapping.objects.filter(id=F('versioned_object_id'))
    name = 'Mappings'
    grouped_label = "New Mappings Grouped by Target Source"
    verbose = False
    grouped = True
    GROUPED_HEADERS = ["Target Source ID", "Count"]
    retired_criteria = {'retired': True}

    @property
    def grouped_queryset(self):
        from core.sources.models import Source
        return Source.objects.values(
            'id', 'mnemonic'
        ).filter(
            mappings_to__id=F('mappings_to__versioned_object_id'),
            mappings_to__created_at__gte=self.start_date,
            mappings_to__created_at__lte=self.end_date
        ).annotate(
            count=Count('mappings_to__id')
        ).order_by('-count').values_list(
            'mnemonic', 'count'
        )

    @staticmethod
    def to_grouped_stat_csv_row(obj):
        return [*obj]


class MappingVersionReport(AbstractReport):
    queryset = Mapping.objects.exclude(id=F('versioned_object_id')).exclude(is_latest_version=True)
    name = 'Mapping Versions'
    verbose = False
    retired_criteria = {'retired': True}
