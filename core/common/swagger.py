"""Swagger helpers for OCL-specific OpenAPI generation."""

from drf_yasg.generators import EndpointEnumerator, OpenAPISchemaGenerator


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
