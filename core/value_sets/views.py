import logging

from core.collections.views import CollectionListView, CollectionRetrieveUpdateDestroyView
from core.common.constants import HEAD
from core.fhir.adapters import r4
from core.fhir.mixins import FhirOperationView, FhirReadMixin, FhirSearchMixin, FhirViewMixin
from core.fhir.ops.expand import expand
from core.fhir.ops.validate_code import validate_value_set
from core.fhir.utils import namespace_of
from core.value_sets.serializers import ValueSetDetailSerializer, ValueSetExpansionSerializer

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


class ValueSetExpandView(FhirOperationView):
    """ValueSet/$expand and ValueSet/{id}/$expand: core.fhir.ops.expand. A POST reads as a GET does."""
    instance = 'collection'
    operation = staticmethod(expand)

    def adapter(self, answer):
        """The answer as an R4 ValueSet: the value set version as a read shows it, with the expansion."""
        resource = ValueSetExpansionSerializer(
            answer['value_set'], context={'opaque_ids': namespace_of(self.kwargs) is None}).data
        return r4.expansion_value_set(resource, answer)
