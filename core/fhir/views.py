"""
The FHIR metadata views, at the global base and at owner bases: what core.fhir.capabilities builds from the code,
in R4 (core.fhir.adapters.r4).
"""
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.fhir import capabilities
from core.fhir.adapters import r4
from core.fhir.mixins import FhirOperationView, FhirViewMixin


class CapabilityStatementView(FhirViewMixin, APIView):
    """metadata: the base's CapabilityStatement, or with mode=terminology its TerminologyCapabilities for the caller."""
    permission_classes = (AllowAny,)

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        if request.query_params.get('mode') == 'terminology':
            return Response(r4.terminology_capabilities(capabilities.terminology_capabilities(request, self.kwargs)))
        return Response(r4.capability_statement(capabilities.capability_statement(request, self.kwargs)))


class VersionsView(FhirOperationView):
    """$versions, GET only: the FHIR versions served, and the default."""
    http_method_names = ['get', 'head', 'options']
    operation = staticmethod(capabilities.versions)
    adapter = staticmethod(r4.versions_parameters)
