"""Source projections reuse the repository's version-aware querysets and permissions."""

from core.common.permissions import user_can_view_concept_dictionary

from .types import ExternalSourceType, SourceType

# GraphQL path to model attribute, used when the ORM serializes a source.
SOURCE_FIELDS = {
    'name': ('name',),
    'description': ('description',),
    'canonicalUrl': ('canonical_url',),
    'uri': ('uri',),
}

# Payloads the source index can answer on its own. ``description`` is deliberately absent: it is
# not stored in the index, so selecting it routes the whole request through the ORM. ``uri`` maps
# to no stored field because it is rebuilt from ownership, mnemonic and version.
SOURCE_INDEX_FIELDS = {
    '__typename': (),
    'name': ('name',),
    'canonicalUrl': ('canonical_url',),
    'uri': (),
}


def serialize_source(instance, paths, user):
    """Load only the aggregates requested by the client, using existing model querysets."""
    result = SourceType(
        **{
            fields[0]: getattr(instance, fields[0])
            for path, fields in SOURCE_FIELDS.items() if path in paths
        }
    )
    if paths & {'classes', 'datatypes', 'summary.activeConcepts'}:
        concepts = instance.get_concepts_queryset().filter(is_active=True, retired=False)
        if 'classes' in paths:
            result.classes = distinct_values(concepts, 'concept_class')
        if 'datatypes' in paths:
            result.datatypes = distinct_values(concepts, 'datatype')
        if 'summary.activeConcepts' in paths:
            result.summary.active_concepts = concepts.count()
    if paths & {'mapTypes', 'summary.mappings'} or any(path.startswith('externalSources.') for path in paths):
        mappings = instance.get_mappings_queryset().filter(is_active=True, retired=False)
        if 'mapTypes' in paths:
            result.map_types = distinct_values(mappings, 'map_type')
        if 'summary.mappings' in paths:
            result.summary.mappings = mappings.count()
        if any(path.startswith('externalSources.') for path in paths):
            result.external_sources = external_sources(mappings, instance, user)
    return result


def distinct_values(queryset, field):
    """Return stable, unique, non-empty labels without instantiating child records."""
    return list(queryset.exclude(**{field: ''}).order_by(field).values_list(field, flat=True).distinct())


def external_sources(mappings, instance, user):
    """Deduplicate outbound targets and hide linked repositories the caller cannot view."""
    result = {}
    targets = mappings.order_by('to_source_id', 'to_source_url', 'to_concept__parent_id').distinct(
        'to_source_id', 'to_source_url', 'to_concept__parent_id',
    ).select_related(
        'to_source', 'to_source__organization', 'to_source__user',
        'to_concept__parent__organization', 'to_concept__parent__user',
    )
    for mapping in targets:
        target = mapping.get_to_source()
        if target:
            if target.mnemonic == instance.mnemonic and target.parent == instance.parent:
                continue
            if not user_can_view_concept_dictionary(user, target):
                continue
        uri = mapping.to_source_url or (target.uri if target else None)
        name = target.name if target else mapping.to_source_name
        if uri or name:
            result[(uri or '', name or '')] = ExternalSourceType(uri=uri, name=name)
    return [result[key] for key in sorted(result)]
