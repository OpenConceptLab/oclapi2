import logging

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.response import Response

from core.collections.views import CollectionListView, CollectionRetrieveUpdateDestroyView, \
    CollectionVersionExpansionsView
from core.common.constants import HEAD
from core.fhir.adapters import r4
from core.fhir.base import FhirViewMixin, FhirOperationView
from core.fhir.ops.validate_code import validate_value_set
from core.fhir.search import FhirSearchMixin, FhirReadMixin
from core.value_sets.serializers import ValueSetDetailSerializer, \
    ValueSetExpansionParametersSerializer, ValueSetExpansionSerializer

logger = logging.getLogger('oclapi')


class ValueSetListView(FhirViewMixin, FhirSearchMixin, CollectionListView):
    resource_type = 'ValueSet'
    serializer_class = ValueSetDetailSerializer

    def get_serializer_class(self):
        return self.serializer_class

    def get_detail_serializer(self, obj):
        return self.serializer_class(obj)


class ValueSetValidateCodeView(FhirOperationView):
    """ValueSet/$validate-code and ValueSet/{id}/$validate-code: core.fhir.ops.validate_code."""
    instance = 'collection'
    operation = staticmethod(validate_value_set)
    adapter = staticmethod(r4.validate_code_parameters)


class ValueSetRetrieveUpdateView(FhirViewMixin, FhirReadMixin, CollectionRetrieveUpdateDestroyView):
    resource_type = 'ValueSet'
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


class ValueSetExpandView(FhirViewMixin, CollectionVersionExpansionsView):
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
