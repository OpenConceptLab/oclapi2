"""Shared GraphQL error metadata used by views, resolvers, and tests."""

from typing import Optional

from strawberry.exceptions import GraphQLError

AUTHENTICATION_FAILED = 'AUTHENTICATION_FAILED'
FORBIDDEN = 'FORBIDDEN'
SEARCH_UNAVAILABLE = 'SEARCH_UNAVAILABLE'
VALIDATION_ERROR = 'VALIDATION_ERROR'

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
    VALIDATION_ERROR: {
        'message': 'Validation error',
        'description': 'Client supplied arguments that violate input validation rules.',
    },
}
EXPECTED_GRAPHQL_ERROR_CODES = frozenset(GRAPHQL_ERROR_DEFINITIONS.keys())


def build_expected_graphql_error(code, message: Optional[str] = None):
    """Return a GraphQL error with a stable code and a short client-facing description.

    Pass ``message`` to override the default human-readable message while preserving
    the machine-readable ``code``.
    """
    detail = GRAPHQL_ERROR_DEFINITIONS[code]
    return GraphQLError(
        message or detail['message'],
        extensions={
            'code': code,
            'description': detail['description'],
        },
    )


def build_validation_error(message: str):
    """Shortcut for client-side validation failures that should not be logged as server errors."""
    return build_expected_graphql_error(VALIDATION_ERROR, message=message)


def get_graphql_error_code(error):
    """Read the machine-readable error code attached to a GraphQL error when present."""
    return (getattr(error, 'extensions', None) or {}).get('code')
