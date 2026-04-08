import strawberry
from strawberry_django.optimizer import DjangoOptimizerExtension

from .constants import EXPECTED_GRAPHQL_ERROR_CODES, get_graphql_error_code
from .queries import Query


class OCLGraphQLSchema(strawberry.Schema):
    def process_errors(self, errors, execution_context=None):
        # Expected business-rule failures should reach clients, but they should not be recorded as server errors.
        unexpected_errors = [
            error for error in errors if get_graphql_error_code(error) not in EXPECTED_GRAPHQL_ERROR_CODES
        ]
        if unexpected_errors:
            super().process_errors(unexpected_errors, execution_context)


schema = OCLGraphQLSchema(
    query=Query,
    extensions=[DjangoOptimizerExtension],
)
