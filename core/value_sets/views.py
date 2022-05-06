import logging

from core.bundles.serializers import FhirBundleSerializer
from core.collections.views import CollectionListView, CollectionRetrieveUpdateDestroyView
from core.value_sets.serializers import ValueSetDetailSerializer

logger = logging.getLogger('oclapi')


class ValueSetListView(CollectionListView):
    serializer_class = ValueSetDetailSerializer

    @staticmethod
    def bundle_response(data):
        bundle = FhirBundleSerializer(
            {'meta': {}, 'type': 'searchset', 'entry': FhirBundleSerializer.convert_to_entry(data)})
        return bundle.data

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def apply_query_filters(self, queryset):
        query_fields = list(self.serializer_class.Meta.fields)

        url = self.request.query_params.get('url')
        if url:
            queryset = queryset.filter(canonical_url=url)
            query_fields.remove('url')

        language = self.request.query_params.get('language')
        if language:
            queryset = queryset.filter(locale=language)
            query_fields.remove('language')
        status = self.request.query_params.get('status')
        if status:
            query_fields.remove('status')
            if status == 'retired':
                queryset = queryset.filter(retired=True)
            elif status == 'active':
                queryset = queryset.filter(released=True)
            elif status == 'draft':
                queryset = queryset.filter(released=False)

        for query_field in query_fields:
            query_value = self.request.query_params.get(query_field)
            if query_value:
                kwargs = {query_field: query_value}
                queryset = queryset.filter(**kwargs)

        return queryset

    def apply_filters(self, queryset):
        queryset = queryset.exclude(version='HEAD').filter(is_latest_version=True)
        return self.apply_query_filters(queryset)

    def get_serializer_class(self):
        return self.serializer_class

    def get_detail_serializer(self, obj):
        return self.serializer_class(obj)


class ValueSetRetrieveUpdateView(CollectionRetrieveUpdateDestroyView):
    serializer_class = ValueSetDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.exclude(version='HEAD')

    def get_detail_serializer(self, obj):
        return ValueSetDetailSerializer(obj)
