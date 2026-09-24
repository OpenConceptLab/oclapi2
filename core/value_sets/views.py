import logging

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from core.bundles.serializers import FHIRBundleSerializer
from core.code_systems.views import CodeSystemValidateCodeView
from core.collections.models import Collection
from core.collections.views import CollectionListView, CollectionRetrieveUpdateDestroyView, \
    CollectionVersionExpansionsView
from core.common.constants import HEAD
from core.common.fhir_helpers import translate_fhir_query
from core.concepts.views import ConceptRetrieveUpdateDestroyView
from core.sources.models import Source
from core.value_sets.serializers import ValueSetDetailSerializer, \
    ValueSetExpansionParametersSerializer, ValueSetExpansionSerializer

logger = logging.getLogger('oclapi')


class ValueSetListView(CollectionListView):
    serializer_class = ValueSetDetailSerializer

    @staticmethod
    def bundle_response(data, paginator):
        bundle = FHIRBundleSerializer(
            {'meta': {}, 'total': len(data), 'type': 'searchset', 'entry': FHIRBundleSerializer.convert_to_entry(data)},
            context={'paginator': paginator}
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


class ValueSetValidateCodeView(CodeSystemValidateCodeView):
    INVALID_RESULT = {'parameter': [{'name': 'result', 'valueBoolean': False}]}

    def get_value_sets(self):
        value_sets = []
        url = self.get_parameters().get('url')
        if url:
            value_sets.append(Collection.objects.filter(
                canonical_url=url, is_latest_version=True).exclude(version=HEAD).first())
        collection = self.kwargs.get('collection')
        if collection:
            owner_filters = {'organization__mnemonic': self.kwargs['org']} if 'org' in self.kwargs else {
                'user__username': self.kwargs.get('user')}
            value_sets.append(Collection.get_version(collection, self.kwargs.get('version', HEAD), owner_filters))
        return [value_set for value_set in value_sets if value_set]

    def get_object(self, queryset=None):
        """A value set the requester can't view gives the same answer as a code that isn't in it."""
        if any(not value_set.has_view_access(self.request.user) for value_set in self.get_value_sets()):
            return self.INVALID_RESULT
        try:
            return super().get_object(queryset)
        except PermissionDenied:
            return self.INVALID_RESULT

    def get_queryset(self):
        queryset = super(ConceptRetrieveUpdateDestroyView, self).get_queryset()

        parameters = self.get_parameters()

        url = parameters.get('url')
        code = parameters.get('code')
        system = parameters.get('system')
        display = parameters.get('display')
        system_version = parameters.get('systemVersion')

        if url:
            collection = Collection.objects.filter(canonical_url=url).exclude(version=HEAD)\
                .filter(is_latest_version=True)
            if not collection:
                return queryset.none()
            queryset = queryset.filter(references__collection=collection.first())

        if code and system:
            concept_source = Source.objects.filter(canonical_url=system)
            if not concept_source:
                return queryset.none()
            if system_version:
                concept_source = concept_source.filter(version=system_version)
            else:
                concept_source = concept_source.filter(is_latest_version=True).exclude(version=HEAD)
            if concept_source:
                queryset = queryset.filter(sources=concept_source.first(), mnemonic=code)
            else:
                return queryset.none()

        if display:
            instance = queryset.first()
            if display not in (instance.name, instance.display_name):
                return queryset.none()

        return queryset


class ValueSetRetrieveUpdateView(CollectionRetrieveUpdateDestroyView):
    serializer_class = ValueSetDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method == 'DELETE':
            return queryset  # Delete HEAD with all versions

        return queryset.exclude(version=HEAD)

    def get_detail_serializer(self, obj):
        return ValueSetDetailSerializer(obj)


class ValueSetExpandView(CollectionVersionExpansionsView):
    sync = True

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ValueSetExpansionParametersSerializer

        return ValueSetExpansionSerializer

    def get_response_serializer_class(self):
        return ValueSetExpansionSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_base_queryset(self):
        queryset = super().get_base_queryset()
        queryset = queryset.exclude(version=HEAD).filter(is_latest_version=True)[:1]
        return queryset

    def get_queryset(self):
        qs = super().get_queryset()

        if self.request.method == 'GET':
            parameters = ValueSetExpansionParametersSerializer.parse_query_params(self.request.query_params)
            if not parameters.is_valid():
                raise ValidationError(message=parameters.errors)

            params = parameters.validated_data
            params = params.get('parameters', {})
            qs = qs.filter(parameters=params if params else {}).order_by('-id')

        return qs

    def get(self, request, *args, **kwargs):
        instance = self.get_queryset().first()
        if instance:
            serializer = self.get_serializer(instance)
            return Response(serializer.data)

        return Response(status=status.HTTP_404_NOT_FOUND)
