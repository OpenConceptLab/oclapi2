"""Shared FHIR view behavior for operations, repository searches and reads."""
from django.conf import settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.throttling import FhirMinuteThrottle
from core.fhir import outcomes
from core.fhir.negotiation import FhirContentNegotiation, FhirVersioning, PARSER_CLASSES, RENDERER_CLASSES
from core.fhir.parameters import FhirParameters
from core.fhir.search import read, search_bundle, within_search_timeout
from core.fhir.utils import fhir_base_url, namespace_of


class FhirViewMixin:
    """
    The first base of every FHIR view: FHIR JSON and XML in and out, `_format`, the fhirVersion parameter, and every
    error as an OperationOutcome with a tx-issue-type, never a 5xx.
    """
    parser_classes = PARSER_CLASSES
    renderer_classes = RENDERER_CLASSES
    content_negotiation_class = FhirContentNegotiation
    versioning_class = FhirVersioning

    def get_exception_handler(self):
        return outcomes.exception_handler

    def get_throttles(self):
        """One rate for every caller, whoever they are: it protects the server."""
        return [FhirMinuteThrottle()] if settings.ENABLE_THROTTLING else []

    def finalize_response(self, request, response, *args, **kwargs):
        """Normalize returned outcomes at every status and depth, and wrap other error responses as outcomes."""
        if isinstance(response, Response):
            if response.status_code >= 400:
                if not outcomes.is_outcome(response.data):
                    response.data = outcomes.outcome_for_status(response.status_code, response.data)
                if response.status_code >= 500:
                    outcomes.report_server_error(response.status_code)
                response.status_code = outcomes.response_status(response.status_code)
            response.data = outcomes.normalize_outcomes(response.data)
        return super().finalize_response(request, response, *args, **kwargs)

    def get_fhir_parameters(self):
        return FhirParameters.from_request(self.request)

    def get_fhir_base_url(self):
        return fhir_base_url(self.request, self.kwargs)


class FhirOperationView(FhirViewMixin, APIView):
    """
    A FHIR operation that only reads, GET and POST alike, at the global base and at owner bases: `operation`'s answer
    for the request's FhirParameters, base and user (and the resource the route kwarg `instance` names, at the instance
    level), written by `adapter`.
    """
    permission_classes = (AllowAny,)  # what an operation shows the caller is core.fhir.resolve's rule
    operation = adapter = None
    instance = None

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        answer = self.operation(  # pylint: disable=not-callable
            self.get_fhir_parameters(), self.kwargs, request.user, kwargs.get(self.instance))
        return Response(self.adapter(answer))  # pylint: disable=not-callable

    post = get


class FhirRepositoryViewMixin:
    """The search and read views of CodeSystem, ValueSet and ConceptMap (after FhirViewMixin)."""
    resource_type = None

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'opaque_ids': namespace_of(self.kwargs) is None}


class FhirSearchMixin(FhirRepositoryViewMixin):
    """GET on a resource type is its search."""

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        """The searchset Bundle, in at most FHIR_SEARCH_TIMEOUT_MS of database time."""
        return Response(within_search_timeout(lambda: search_bundle(self)))

    head = get


class FhirReadMixin(FhirRepositoryViewMixin):
    """
    GET on an id is its read, at the global base without the owner check the view's writes keep (verify_scope), and
    without the source and collection views' recounts.
    """

    def verify_scope(self):
        if self.request.method not in ('GET', 'HEAD'):
            super().verify_scope()

    def get_object(self, queryset=None):
        if self.request.method not in ('GET', 'HEAD'):
            return super().get_object(queryset)
        return read(self)
