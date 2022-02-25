import logging

from core.bundles.serializers import FhirBundleSerializer
from core.code_systems.serializers import CodeSystemDetailSerializer
from core.sources.views import SourceListView, SourceRetrieveUpdateDestroyView

logger = logging.getLogger('oclapi')


class CodeSystemListView(SourceListView):
    serializer_class = CodeSystemDetailSerializer

    @staticmethod
    def bundle_response(data):
        bundle = FhirBundleSerializer(
            {'meta': {}, 'type': 'searchset', 'entry': FhirBundleSerializer.convert_to_entry(data)})
        return bundle.data

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def apply_filters(self, queryset):
        queryset = queryset.exclude(version='HEAD').filter(is_latest_version=True)
        url = self.request.query_params.get('url')
        if url:
            queryset = queryset.filter(canonical_url=url)
        return queryset

    def get_serializer_class(self):
        return self.serializer_class

    def get_detail_serializer(self, obj):
        return CodeSystemDetailSerializer(obj)


class CodeSystemRetrieveUpdateView(SourceRetrieveUpdateDestroyView):
    serializer_class = CodeSystemDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.exclude(version='HEAD')

    def get_detail_serializer(self, obj):
        return CodeSystemDetailSerializer(obj)
