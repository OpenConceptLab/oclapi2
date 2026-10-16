import logging

from core.code_systems.serializers import CodeSystemDetailSerializer
from core.common.constants import HEAD
from core.fhir.adapters import r4
from core.fhir.base import FhirViewMixin, FhirOperationView
from core.fhir.ops.lookup import lookup
from core.fhir.ops.validate_code import validate_code_system
from core.fhir.search import FhirSearchMixin, FhirReadMixin
from core.sources.views import SourceListView, SourceRetrieveUpdateDestroyView

logger = logging.getLogger('oclapi')


class CodeSystemListView(FhirViewMixin, FhirSearchMixin, SourceListView):
    resource_type = 'CodeSystem'
    serializer_class = CodeSystemDetailSerializer

    def get_serializer_class(self):
        return self.serializer_class

    def get_detail_serializer(self, obj):
        return self.serializer_class(obj)


class CodeSystemLookupView(FhirOperationView):
    """CodeSystem/$lookup and CodeSystem/{id}/$lookup: core.fhir.ops.lookup."""
    instance = 'source'
    operation = staticmethod(lookup)
    adapter = staticmethod(r4.lookup_parameters)


class CodeSystemValidateCodeView(FhirOperationView):
    """CodeSystem/$validate-code and CodeSystem/{id}/$validate-code: core.fhir.ops.validate_code."""
    instance = 'source'
    operation = staticmethod(validate_code_system)
    adapter = staticmethod(r4.validate_code_parameters)


class CodeSystemRetrieveUpdateView(FhirViewMixin, FhirReadMixin, SourceRetrieveUpdateDestroyView):
    resource_type = 'CodeSystem'
    serializer_class = CodeSystemDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method == 'DELETE':
            return queryset  # Delete HEAD with all versions
        return queryset.exclude(version=HEAD)

    def get_detail_serializer(self, obj):
        return CodeSystemDetailSerializer(obj)
