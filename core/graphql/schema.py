import strawberry
from django.conf import settings
from graphql import GraphQLError
from strawberry.utils.logging import StrawberryLogger
from strawberry_django.optimizer import DjangoOptimizerExtension

from .queries import Query


class Schema(strawberry.Schema):
    def process_errors(self, errors, execution_context=None):
        """
        Strawberry logs each error with the lines of the query it points at, and an error the client caused echoes
        the values it sent. Unless settings.LOG_REQUEST_BODIES, only an unexpected exception (in a resolver, or one
        strawberry caught around execution) is logged, with its traceback but not its path, which holds the client's
        aliases. A rejected query, bad variables or a GraphQLError a resolver raises go to the client only.
        """
        if settings.LOG_REQUEST_BODIES:
            super().process_errors(errors, execution_context)
            return
        for error in errors:
            if error.original_error and not isinstance(error.original_error, GraphQLError):
                StrawberryLogger.logger.error('GraphQL execution error', exc_info=error.original_error)


schema = Schema(
    query=Query,
    extensions=[DjangoOptimizerExtension],
)
