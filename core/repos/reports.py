from core.common.constants import HEAD
from core.reports.models import AbstractReport


class RepoReport(AbstractReport):
    summary_label = "Summary of Repository Types"
    name = 'Repositories'
    id = 'repos'
    verbose = False
    grouped = True
    retired_criteria = {'is_active': False}
    SUMMARY_HEADERS = [
        "Source / Collection",
        "Repository Type",
        "source_type / collection_type ",
        "Created during Period -- with canonical URL",
        "Created during Period -- no canonical URL",
        "Subtotal Created during Period",
        "Active as of Report Date -- with canonical URL",
        "Active as of Report Date -- no canonical URL",
        "Total as of Report Date"
    ]

    def build_queryset(self):
        pass

    def to_source_summary_rows(self):
        from core.sources.models import Source
        base_queryset = Source.objects.filter(version=HEAD)
        queryset = self.make_queryset(Source.objects.filter(version=HEAD))
        rows = []
        for source_type in set(base_queryset.values_list('source_type', flat=True)):
            source_type_queryset = queryset.filter(source_type=source_type)
            base_source_type_queryset = base_queryset.filter(source_type=source_type)
            rows.append([
                Source.OBJECT_TYPE,
                None,  # repo_type
                'None' if source_type is None else source_type,
                source_type_queryset.filter(canonical_url__isnull=False).count(),
                source_type_queryset.filter(canonical_url__isnull=True).count(),
                source_type_queryset.count(),
                base_source_type_queryset.filter(canonical_url__isnull=False).count(),
                base_source_type_queryset.filter(canonical_url__isnull=True).count(),
                base_source_type_queryset.count()
            ])
        return sorted(rows, key=lambda x: x[-1], reverse=True)

    def to_collection_summary_rows(self):
        from core.collections.models import Collection
        base_queryset = Collection.objects.filter(version=HEAD)
        queryset = self.make_queryset(Collection.objects.filter(version=HEAD))
        rows = []
        for collection_type in set(base_queryset.values_list('collection_type', flat=True)):
            collection_type_queryset = queryset.filter(collection_type=collection_type)
            base_collection_type_queryset = base_queryset.filter(collection_type=collection_type)
            rows.append([
                Collection.OBJECT_TYPE,
                None,  # repo_type
                'None' if collection_type is None else collection_type,
                collection_type_queryset.filter(canonical_url__isnull=False).count(),
                collection_type_queryset.filter(canonical_url__isnull=True).count(),
                collection_type_queryset.count(),
                base_collection_type_queryset.filter(canonical_url__isnull=False).count(),
                base_collection_type_queryset.filter(canonical_url__isnull=True).count(),
                base_collection_type_queryset.count()
            ])
        return sorted(rows, key=lambda x: x[-1], reverse=True)

    def to_summary_rows(self):
        return self.to_source_summary_rows() + self.to_collection_summary_rows()
