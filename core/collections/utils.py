from django.urls import resolve
from pydash import get

from core.collections.constants import EXPRESSION_NUMBER_OF_PARTS_WITH_VERSION
from core.common.utils import get_query_params_from_url_string


def is_concept(expression):
    return expression and "/concepts/" in expression


def is_mapping(expression):
    return expression and "/mappings/" in expression


def concepts_for(expression):
    from core.concepts.models import Concept
    queryset = Concept.objects.none()

    try:
        kwargs = get(resolve(expression), 'kwargs', dict())
        query_params = get_query_params_from_url_string(expression)  # parsing query parameters
        kwargs.update(query_params)
        queryset = Concept.get_base_queryset(kwargs)
    except:  # pylint: disable=bare-except
        pass

    return queryset


def drop_version(expression):
    return '/'.join(expression.split('/')[0:7]) + '/'


def is_version_specified(expression):
    return len(expression.split('/')) == EXPRESSION_NUMBER_OF_PARTS_WITH_VERSION


def get_concept_by_expression(expression):
    from core.concepts.models import Concept
    return Concept.objects.filter(uri=expression).first()
