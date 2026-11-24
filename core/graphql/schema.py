import strawberry
from django.conf import settings
from strawberry.utils.logging import StrawberryLogger
from strawberry_django.optimizer import DjangoOptimizerExtension

from .queries import Query


class Schema(strawberry.Schema):
    def process_errors(self, errors, execution_context=None):
        """
        Strawberry logs each error with the lines of the query it points at. Outside development, only an error a
        resolver raised is logged, with its traceback and path but not the query.
        """
        if settings.ENV == 'development':
            super().process_errors(errors, execution_context)
            return
        for error in errors:
            if error.original_error:
                StrawberryLogger.logger.error('GraphQL error at %s', error.path, exc_info=error.original_error)


schema = Schema(
    query=Query,
    extensions=[DjangoOptimizerExtension],
)
