from core.collections.constants import EXPRESSION_NUMBER_OF_PARTS_WITH_VERSION


def is_concept(expression):
    return expression and "/concepts/" in expression


def is_mapping(expression):
    return expression and "/mappings/" in expression


def drop_version(expression):
    return '/'.join(expression.split('/')[0:7]) + '/'


def is_version_specified(expression):
    return len(expression.split('/')) == EXPRESSION_NUMBER_OF_PARTS_WITH_VERSION


def get_concept_by_expression(expression):
    from core.concepts.models import Concept
    return Concept.objects.filter(uri=expression).first()
