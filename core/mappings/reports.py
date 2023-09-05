from django.db import models
from django.db.models import F, Count

from core.reports.models import AbstractReport
from core.mappings.models import Mapping


class MappingReport(AbstractReport):
    queryset = Mapping.objects.filter(id=F('versioned_object_id'))
    name = 'Mappings'
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

    @property
    def grouped_queryset(self):
        from core.sources.models import Source
        queryset = Source.objects.values(
            'id', 'uri', 'id'
        ).filter(
            mappings_to__id=F('mappings_to__versioned_object_id'),
            mappings_to__created_at__gte=self.start_date,
            mappings_to__created_at__lte=self.end_date
        )
        count_queryset = queryset.annotate(  # count of mappings by target source
            count=Count('mappings_to__id')
        ).order_by('-count')[:self.limit].values_list('uri', 'count', 'id')
        result = []
        for result_set in count_queryset:
            source_id = result_set[2]
            internal_count = Mapping.objects.filter(  # count of mappings where target, from and parent is source_id
                id=F('versioned_object_id'),
                created_at__gte=self.start_date,
                created_at__lte=self.end_date,
                from_source_id=source_id,
                to_source_id=source_id,
                parent_id=source_id
            ).count()
            between_sources_count = Mapping.objects.filter(  # count of mappings where target or from is source_id
                id=F('versioned_object_id'),
                created_at__gte=self.start_date,
                created_at__lte=self.end_date,
            ).filter(
                models.Q(to_source_id=source_id) | models.Q(from_source_id=source_id)
            ).exclude(to_source_id=F('from_source_id')).count()
            result.append([result_set[0], result_set[1], internal_count, between_sources_count])

        return result

    @staticmethod
    def to_grouped_stat_csv_row(obj):
        return [*obj]


class MappingVersionReport(AbstractReport):
    queryset = Mapping.objects.exclude(id=F('versioned_object_id')).exclude(is_latest_version=True)
    name = 'Mapping Versions'
    verbose = False
    retired_criteria = {'retired': True}
