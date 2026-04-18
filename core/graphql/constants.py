"""Shared GraphQL error metadata used by views, resolvers, and tests."""

from strawberry.exceptions import GraphQLError

AUTHENTICATION_FAILED = 'AUTHENTICATION_FAILED'
FORBIDDEN = 'FORBIDDEN'
SEARCH_UNAVAILABLE = 'SEARCH_UNAVAILABLE'

GRAPHQL_ERROR_DEFINITIONS = {
    AUTHENTICATION_FAILED: {
        'message': 'Authentication failure',
        'description': 'The provided credentials are invalid for the GraphQL API.',
    },
    FORBIDDEN: {
        'message': 'Forbidden',
        'description': 'The current user cannot access the requested repository.',
    },
    SEARCH_UNAVAILABLE: {
        'message': 'Search unavailable',
        'description': 'Global concept search requires Elasticsearch and is temporarily unavailable.',
    },
}
EXPECTED_GRAPHQL_ERROR_CODES = frozenset(GRAPHQL_ERROR_DEFINITIONS.keys())


def build_expected_graphql_error(code):
    """Return a GraphQL error with a stable code and a short client-facing description."""
    detail = GRAPHQL_ERROR_DEFINITIONS[code]
    return GraphQLError(
        detail['message'],
        extensions={
            'code': code,
            'description': detail['description'],
        },
    )


def get_graphql_error_code(error):
    """Read the machine-readable error code attached to a GraphQL error when present."""
    return (getattr(error, 'extensions', None) or {}).get('code')
