import logging

from core.bundles.serializers import FHIRBundleSerializer
from core.common.constants import HEAD
from core.common.fhir_helpers import translate_fhir_query
from core.concept_maps.serializers import ConceptMapDetailSerializer
from core.sources.views import SourceListView, SourceRetrieveUpdateDestroyView

logger = logging.getLogger('oclapi')


class ConceptMapListView(SourceListView):
    serializer_class = ConceptMapDetailSerializer

    @staticmethod
    def bundle_response(data, paginator):
        bundle = FHIRBundleSerializer(
            {'meta': {}, 'type': 'searchset', 'entry': FHIRBundleSerializer.convert_to_entry(data)},
            context=dict(paginator=paginator)
        )
        return bundle.data

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def apply_query_filters(self, queryset):
        query_fields = list(self.serializer_class.Meta.fields)
        queryset = translate_fhir_query(query_fields, self.request.query_params, queryset)
        return queryset

    def apply_filters(self, queryset):
        queryset = queryset.exclude(version=HEAD).filter(is_latest_version=True)
        return self.apply_query_filters(queryset)

    def get_serializer_class(self):
        return self.serializer_class

    def get_detail_serializer(self, obj):
        return self.serializer_class(obj)


class ConceptMapRetrieveUpdateView(SourceRetrieveUpdateDestroyView):
    serializer_class = ConceptMapDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.exclude(version=HEAD)

    def get_detail_serializer(self, obj):
        return ConceptMapDetailSerializer(obj)
