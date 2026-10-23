"""Swagger helpers for OCL-specific OpenAPI generation."""

from copy import deepcopy

from django.conf import settings
from drf_yasg.generators import EndpointEnumerator, OpenAPISchemaGenerator

from core.fhir.base import FhirViewMixin, is_fhir_path
from core.fhir.swagger import FhirSwaggerSchemaGenerator, fhir_paths, fhir_swagger_url, on_fhir_host


class OCLSwaggerEndpointEnumerator(EndpointEnumerator):
    """Preserve literal dollar signs when deriving OpenAPI paths from Django URL regexes."""

    dollar_sentinel = "__ocl_escaped_dollar__"

    def get_path_from_regex(self, path_regex):
        """Keep escaped dollar signs intact instead of letting schema simplification drop them."""
        sanitized_regex = path_regex.replace(r"\$", self.dollar_sentinel).replace("$", self.dollar_sentinel)
        return super().get_path_from_regex(sanitized_regex).replace(self.dollar_sentinel, "$")


class OCLSwaggerSchemaGenerator(OpenAPISchemaGenerator):
    """Use the OCL endpoint enumerator so Swagger paths match the published URLs."""

    endpoint_enumerator_class = OCLSwaggerEndpointEnumerator

    def get_endpoints(self, request):
        """Replace every FHIR view entry, including malformed paths from no-slash includes."""
        return {path: endpoint for path, endpoint in super().get_endpoints(request).items()
                if not is_fhir_path(path) and not issubclass(endpoint[0], FhirViewMixin)}

    def get_paths(self, endpoints, components, request, public):
        """Keep the other API operations intact, adding FHIR only when both share a host."""
        paths, prefix = super().get_paths(endpoints, components, request, public)
        if not settings.FHIR_SUBDOMAIN:
            paths.update(fhir_paths(components))
        return paths, prefix

    def get_schema(self, request=None, public=False):
        """Select the host's schema and link the API documentation to the separate FHIR service."""
        if on_fhir_host(request):
            return FhirSwaggerSchemaGenerator(self.info).get_schema(request, public)
        schema = super().get_schema(request, public)
        if settings.FHIR_SUBDOMAIN:
            schema.info = deepcopy(schema.info)
            link = fhir_swagger_url(request, self.url or settings.API_BASE_URL)
            schema.info.description = schema.info.get('description', '') + f'\n\n[FHIR terminology Swagger]({link}).'
        return schema
