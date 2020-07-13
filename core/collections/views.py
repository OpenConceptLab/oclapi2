from django.http import Http404
from rest_framework import status
from rest_framework.generics import RetrieveAPIView, DestroyAPIView
from rest_framework.response import Response

from core.collections.constants import INCLUDE_REFERENCES_PARAM
from core.collections.models import Collection, CollectionReference
from core.collections.serializers import CollectionDetailSerializer, CollectionListSerializer, \
    CollectionCreateSerializer, CollectionReferenceSerializer
from core.collections.utils import is_concept
from core.common.constants import HEAD
from core.common.mixins import ConceptDictionaryCreateMixin, ListWithHeadersMixin, ConceptDictionaryUpdateMixin
from core.common.permissions import CanViewConceptDictionary, CanEditConceptDictionary
from core.common.utils import compact_dict_by_values
from core.common.views import BaseAPIView


class CollectionBaseView(BaseAPIView):
    lookup_field = 'collection'
    pk_field = 'mnemonic'
    model = Collection
    permission_classes = (CanViewConceptDictionary,)
    queryset = Collection.objects.filter(is_active=True)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context.update({'request': self.request, INCLUDE_REFERENCES_PARAM: self.should_include_references()})
        return context

    @staticmethod
    def get_detail_serializer(obj):
        return CollectionDetailSerializer(obj)

    def get_queryset(self):
        query_params = self.request.query_params
        params = dict()
        params['user'] = query_params.get('user', None) or self.kwargs.get('user', None)
        params['org'] = query_params.get('org', None) or self.kwargs.get('org', None)
        params['collection'] = query_params.get('collection', None) or self.kwargs.get('collection', None)
        params['version'] = query_params.get('version', None) or self.kwargs.get('version', None) or HEAD
        params['is_latest'] = self.kwargs.get('is_latest', None)
        params['contains'] = query_params.get('contains', None) or self.kwargs.get('contains', None)
        params['include_references'] = self.should_include_references()
        return Collection.get_base_queryset(compact_dict_by_values(params))

    def should_include_references(self):
        return self.request.query_params.get(INCLUDE_REFERENCES_PARAM, 'false').lower() == 'true'


class CollectionListView(CollectionBaseView, ConceptDictionaryCreateMixin, ListWithHeadersMixin):
    serializer_class = CollectionListSerializer

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return CollectionDetailSerializer if self.is_verbose(self.request) else CollectionListSerializer
        if self.request.method == 'POST':
            return CollectionCreateSerializer

        return CollectionListSerializer

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def get_csv_rows(self, queryset=None):
        if not queryset:
            queryset = self.get_queryset()

        values = queryset.values('mnemonic', 'name', 'full_name', 'collection_type', 'description', 'default_locale',
                                 'supported_locales', 'website', 'external_id', 'updated_at', 'updated_by', 'uri')

        for value in values:
            value['Owner'] = Collection.objects.get(uri=value['uri']).parent.mnemonic
            value['Collection ID'] = value.pop('mnemonic')
            value['Collection Name'] = value.pop('name')
            value['Collection Full Name'] = value.pop('full_name')
            value['Collection Type'] = value.pop('collection_type')
            value['Description'] = value.pop('description')
            value['Default Locale'] = value.pop('default_locale')
            value['Supported Locales'] = ",".join(value.pop('supported_locales'))
            value['Website'] = value.pop('website')
            value['External ID'] = value.pop('external_id')
            value['Last Updated'] = value.pop('updated_at')
            value['Updated By'] = value.pop('updated_by')
            value['URI'] = value.pop('uri')

        values.field_names.extend([
            'Owner', 'Collection ID', 'Collection Name', 'Collection Full Name', 'Collection Type', 'Description',
            'Default Locale', 'Supported Locales', 'Website', 'External ID', 'Last Updated', 'Updated By', 'URI'
        ])
        del values.field_names[0:12]
        return values


class CollectionRetrieveUpdateDestroyView(
        CollectionBaseView, ConceptDictionaryUpdateMixin, RetrieveAPIView, DestroyAPIView
):
    serializer_class = CollectionDetailSerializer

    def get_object(self, queryset=None):
        return self.get_queryset().filter(is_active=True).order_by('-created_at').first()

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [CanEditConceptDictionary()]

    def destroy(self, request, *args, **kwargs):
        collection = self.get_object()
        try:
            collection.delete()
        except Exception as ex:
            return Response({'detail': ex.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Successfully deleted collection.'}, status=status.HTTP_204_NO_CONTENT)


class CollectionReferencesView(
        CollectionBaseView, ConceptDictionaryUpdateMixin, RetrieveAPIView, DestroyAPIView, ListWithHeadersMixin
):
    serializer_class = CollectionDetailSerializer

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [CanEditConceptDictionary()]

    def get_object(self, queryset=None):
        instance = super().get_queryset().filter(is_active=True).order_by('-created_at').first()

        if not instance:
            raise Http404('No Collection matches the given query.')

        return instance

    def get_queryset(self):
        search_query = self.request.query_params.get('q', '')
        sort = self.request.query_params.get('search_sort', 'ASC')
        if sort == 'ASC':
            sort = ''
        else:
            sort = '-'

        instance = self.get_object()
        queryset = instance.references

        if search_query:
            queryset = queryset.filter(expression__iexact=search_query).order_by(sort + 'expression')

        return queryset

    def retrieve(self, request, *args, **kwargs):
        self.serializer_class = CollectionReferenceSerializer
        return self.list(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        expressions = request.data.get("references") or request.data.get("expressions")
        cascade_mappings_flag = request.data.get('cascade', 'none')

        if not expressions:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if expressions == '*':
            expressions = list(instance.references.values_list('expression', flat=True))
        if self.cascade_mapping_resolver(cascade_mappings_flag):
            expressions += self.get_related_mappings_with_version_information(instance, expressions)

        instance.delete_references(expressions)
        return Response({'message': 'ok!'}, status=status.HTTP_200_OK)

    @staticmethod
    def cascade_mapping_resolver(cascade_mappings_flag):
        cascade_mappings_flag_resolver = {
            'none': False,
            'sourcemappings': True
        }

        return cascade_mappings_flag_resolver.get(cascade_mappings_flag.lower(), False)

    def get_related_mappings_with_version_information(self, instance, expressions):
        related_mappings = []

        for expression in expressions:
            if is_concept(expression):
                concept = CollectionReference.get_concept_head_from_expression(expression)
                related_mappings += concept.get_unidirectional_mappings()

        return self.get_version_information_of_related_mappings(instance, related_mappings)

    @staticmethod
    def get_version_information_of_related_mappings(instance, related_mappings):
        return list(
            instance.references.filter(
                expression__in=[mapping.url for mapping in related_mappings]
            ).values_list('expression', flat=True)
        )


class CollectionVersionRetrieveUpdateDestroyView(CollectionBaseView):
    pass
