from django.db.models import F

from core.mappings.models import Mapping
from core.reports.models import AbstractReport


class MappingReport(AbstractReport):
    queryset = Mapping.objects.filter(id=F('versioned_object_id'))
    name = 'Mappings'
    id = 'mappings'
    limit = 20
    grouped_label = f"Top {limit} New Mappings Grouped by Target Source"
    verbose = False
    grouped = True
    GROUPED_HEADERS = [
        "Target Source URL",
        "Subtotal during Period",
        "Count of Internal Source Relationships",
        "Count of Mappings between Sources",
    ]
    retired_criteria = {'retired': True}
    note = 'Equivalent of latest mapping version'

    @property
    def grouped_queryset(self):
        from core.sources.models import Source
        queryset = self.queryset
        to_source_ids = set(queryset.values_list('to_source_id', flat=True))
        result = []
        for to_source_id in to_source_ids:
            source = Source.objects.filter(id=to_source_id).first()
            if source:
                count = queryset.filter(to_source_id=to_source_id).count()
                internal_count = queryset.filter(
                    to_source_id=to_source_id,
                    from_source_id=to_source_id,
                    parent_id=to_source_id
                ).count()
                between_sources_count = count - internal_count
                result.append([source.uri, count, internal_count, between_sources_count])

        return sorted(result, lambda x: x[1], reverse=True)

    @staticmethod
    def to_grouped_stat_csv_row(obj):
        return [*obj]


class MappingVersionReport(AbstractReport):
    queryset = Mapping.objects.exclude(id=F('versioned_object_id')).exclude(is_latest_version=True)
    name = 'Mapping Versions'
    id = 'mapping_versions'
    verbose = False
    retired_criteria = {'retired': True}
    note = 'Excludes latest mapping version'
