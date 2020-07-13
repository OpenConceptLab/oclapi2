from django.urls import resolve
from pydash import get


def is_concept(expression):
    return expression and "/concepts/" in expression


def is_mapping(expression):
    return expression and "/mappings/" in expression


def concepts_for(expression):
    from core.concepts.models import Concept

    kwargs = get(resolve(expression), 'kwargs')
    if kwargs:
        return Concept.get_base_queryset(kwargs)
    return Concept.objects.none()
