"""Pure-Python serializers that map ORM Concept instances into Strawberry types.

These helpers must remain side-effect free: callers prefetch the required relations
(``names``, ``descriptions``, ``graphql_mappings``) before invoking them so the serializers
never trigger SQL.
"""

from __future__ import annotations

from datetime import timezone as datetime_timezone
from typing import Iterable, List, Optional

from django.utils import timezone

from core.concepts.models import Concept
from core.sources.models import Source

from .types import (
    CodedDatatypeDetails,
    ConceptNameType,
    ConceptType,
    DatatypeDetails,
    DatatypeType,
    MappingType,
    MetadataType,
    NumericDatatypeDetails,
    TextDatatypeDetails,
    ToSourceType,
)


def serialize_mappings(concept: Concept) -> List[MappingType]:
    mappings = getattr(concept, 'graphql_mappings', []) or []
    result: List[MappingType] = []
    for mapping in mappings:
        result.append(
            MappingType(
                map_type=str(mapping.map_type),
                to_source=ToSourceType(
                    url=mapping.to_source_url,
                    name=mapping.to_source_name,
                ) if mapping.to_source_url or mapping.to_source_name else None,
                to_code=mapping.get_to_concept_code(),
                to_concept_name=mapping.get_to_concept_name(),
                sort_weight=mapping.sort_weight,
                comment=mapping.comment,
            )
        )
    return result


def serialize_names(concept: Concept) -> List[ConceptNameType]:
    return [
        ConceptNameType(
            name=name.name,
            locale=name.locale,
            type=name.type,
            preferred=name.locale_preferred,
            retired=name.retired,
        )
        for name in concept.names.all()
    ]


def resolve_description(concept: Concept) -> Optional[str]:
    descriptions = list(concept.active_descriptions.all())
    if not descriptions:
        return None

    def pick(predicate):
        for desc in descriptions:
            if predicate(desc):
                return desc.description
        return None

    try:
        default_locale = getattr(concept.parent, 'default_locale', None)
    except Source.DoesNotExist:
        default_locale = None
    if default_locale:
        match = pick(lambda desc: desc.locale == default_locale and desc.locale_preferred)
        if match:
            return match
        match = pick(lambda desc: desc.locale == default_locale)
        if match:
            return match

    match = pick(lambda desc: desc.locale_preferred)
    if match:
        return match
    return descriptions[0].description


def resolve_is_set_flag(concept: Concept) -> Optional[bool]:
    value = getattr(concept, 'is_set', None)
    if value is None:
        extras = concept.extras or {}
        if 'is_set' not in extras:
            return None
        value = extras['is_set']

    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'true', '1', 'yes'}:
            return True
        if lowered in {'false', '0', 'no'}:
            return False
    return bool(value)


def _to_float(value) -> Optional[float]:
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'true', '1', 'yes'}:
            return True
        if lowered in {'false', '0', 'no'}:
            return False
    return None


def resolve_numeric_datatype_details(concept: Concept) -> Optional[NumericDatatypeDetails]:
    extras = concept.extras or {}
    numeric_values = {
        'low_absolute': _to_float(extras.get('low_absolute')),
        'high_absolute': _to_float(extras.get('hi_absolute')),
        'low_normal': _to_float(extras.get('low_normal')),
        'high_normal': _to_float(extras.get('hi_normal')),
        'low_critical': _to_float(extras.get('low_critical')),
        'high_critical': _to_float(extras.get('hi_critical')),
    }
    units = extras.get('units')
    if not units and not any(value is not None for value in numeric_values.values()):
        return None
    return NumericDatatypeDetails(
        units=units,
        low_absolute=numeric_values['low_absolute'],
        high_absolute=numeric_values['high_absolute'],
        low_normal=numeric_values['low_normal'],
        high_normal=numeric_values['high_normal'],
        low_critical=numeric_values['low_critical'],
        high_critical=numeric_values['high_critical'],
    )


def resolve_coded_datatype_details(concept: Concept) -> Optional[CodedDatatypeDetails]:
    extras = concept.extras or {}
    allow_multiple = extras.get('allow_multiple')
    if allow_multiple is None:
        allow_multiple = extras.get('allow_multiple_answers')
    if allow_multiple is None:
        allow_multiple = extras.get('allowMultipleAnswers')
    allow_multiple = _to_bool(allow_multiple)
    if allow_multiple is None:
        return None
    return CodedDatatypeDetails(allow_multiple=allow_multiple)


def resolve_text_datatype_details(concept: Concept) -> Optional[TextDatatypeDetails]:
    extras = concept.extras or {}
    text_format = extras.get('text_format') or extras.get('textFormat')
    if not text_format:
        return None
    return TextDatatypeDetails(text_format=text_format)


def resolve_datatype_details(concept: Concept) -> Optional[DatatypeDetails]:
    datatype = (concept.datatype or '').strip().lower()
    if datatype == 'numeric':
        return resolve_numeric_datatype_details(concept)
    if datatype == 'coded':
        return resolve_coded_datatype_details(concept)
    if datatype == 'text':
        return resolve_text_datatype_details(concept)
    return None


def format_datetime_for_api(value) -> Optional[str]:
    if not value:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, datetime_timezone.utc)
    return value.astimezone(datetime_timezone.utc).isoformat().replace('+00:00', 'Z')


def build_datatype(concept: Concept) -> Optional[DatatypeType]:
    if not concept.datatype:
        return None
    return DatatypeType(
        name=concept.datatype,
        details=resolve_datatype_details(concept),
    )


def build_metadata(concept: Concept) -> MetadataType:
    return MetadataType(
        is_set=resolve_is_set_flag(concept),
        is_retired=concept.retired,
        created_by=getattr(concept.created_by, 'username', None),
        created_at=format_datetime_for_api(concept.created_at),
        updated_by=getattr(concept.updated_by, 'username', None),
        updated_at=format_datetime_for_api(concept.updated_at),
    )


def serialize_concepts(concepts: Iterable[Concept]) -> List[ConceptType]:
    output: List[ConceptType] = []
    for concept in concepts:
        output.append(
            ConceptType(
                id=str(concept.id),
                external_id=concept.external_id,
                concept_id=concept.mnemonic,
                display=concept.display_name,
                names=serialize_names(concept),
                mappings=serialize_mappings(concept),
                description=resolve_description(concept),
                concept_class=concept.concept_class,
                datatype=build_datatype(concept),
                metadata=build_metadata(concept),
                extras=concept.extras or {},
            )
        )
    return output
