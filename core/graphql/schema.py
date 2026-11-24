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
        the values it sent. Outside development, only an unexpected exception in a resolver is logged, with its path
        and traceback. A rejected query, bad variables or a GraphQLError a resolver raises go to the client only.
        """
        if settings.ENV == 'development':
            super().process_errors(errors, execution_context)
            return
        for error in errors:
            if error.path and error.original_error and not isinstance(error.original_error, GraphQLError):
                StrawberryLogger.logger.error('GraphQL error at %s', error.path, exc_info=error.original_error)


schema = Schema(
    query=Query,
    extensions=[DjangoOptimizerExtension],
)
