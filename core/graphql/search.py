"""Pure search helpers for GraphQL: queryset/prefetch builders, pagination and DB fallback.

The orchestrators that talk to Elasticsearch (and decide DB-fallback) live in ``queries.py``
so tests can patch the ES boundary in a single place. Only side-effect-free helpers go here.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.db.models import Case, F, IntegerField, Prefetch, Q, When

from core.concepts.models import Concept
from core.mappings.models import Mapping
from core.sources.models import Source

from .constants import build_validation_error
from .permissions import filter_global_queryset, filter_parent_queryset


def build_source_version_queryset(source_version: Source):
    return source_version.get_concepts_queryset().filter(is_active=True, retired=False)


def build_global_head_queryset():
    return Concept.objects.filter(is_active=True, retired=False, id=F('versioned_object_id'))


def build_mapping_prefetch(source_version: Source, user=None) -> Prefetch:
    mapping_qs = (
        Mapping.objects.filter(
            sources__id=source_version.id,
            from_concept_id__isnull=False,
            is_active=True,
            retired=False,
        )
        .select_related('to_source', 'to_concept', 'to_concept__parent')
        .prefetch_related('to_concept__names')
        .order_by('map_type', 'to_concept_code', 'to_concept__mnemonic')
        .distinct()
    )

    mapping_qs = filter_parent_queryset(mapping_qs, user or AnonymousUser(), 'to_source')
    mapping_qs = filter_parent_queryset(mapping_qs, user or AnonymousUser(), 'to_concept__parent')
    return Prefetch('mappings_from', queryset=mapping_qs, to_attr='graphql_mappings')


def build_global_mapping_prefetch(user=None) -> Prefetch:
    """Build the global mapping prefetch using the same visibility rules as REST list endpoints."""
    mapping_qs = (
        Mapping.objects.filter(
            from_concept_id__isnull=False,
            is_active=True,
            retired=False,
        )
        .select_related('to_source', 'to_concept', 'to_concept__parent')
        .prefetch_related('to_concept__names')
        .order_by('map_type', 'to_concept_code', 'to_concept__mnemonic')
        .distinct()
    )

    # Mapping visibility must be filtered independently because a public concept can still reference private mappings.
    mapping_qs = filter_global_queryset(mapping_qs, user or AnonymousUser())
    mapping_qs = filter_parent_queryset(mapping_qs, user or AnonymousUser())
    mapping_qs = filter_parent_queryset(mapping_qs, user or AnonymousUser(), 'to_source')
    mapping_qs = filter_parent_queryset(mapping_qs, user or AnonymousUser(), 'to_concept__parent')
    return Prefetch('mappings_from', queryset=mapping_qs, to_attr='graphql_mappings')


def normalize_pagination(page: Optional[int], limit: Optional[int]) -> Optional[dict]:
    if page is None and limit is None:
        return None
    if page is None or limit is None:
        raise build_validation_error('page and limit must be supplied together.')
    if page < 1 or limit < 1:
        raise build_validation_error('page and limit must be >= 1 when provided.')
    start = (page - 1) * limit
    end = start + limit
    if end > 10_000:
        raise build_validation_error('Requested page exceeds the 10000 result window.')
    return {'page': page, 'limit': limit, 'start': start, 'end': end}


def has_next(total: int, pagination: Optional[dict]) -> bool:
    if not pagination:
        return False
    return total > pagination['end']


def apply_slice(qs, pagination: Optional[dict]):
    if not pagination:
        return qs
    return qs[pagination['start']:pagination['end']]


def with_concept_related(qs, mapping_prefetch: Prefetch, paths=None):
    """Hydrate only selected fields and relations; preserve the legacy helper default."""
    if paths is None:
        return qs.select_related('created_by', 'updated_by', 'parent').prefetch_related(
            'names', 'descriptions', mapping_prefetch,
        )
    roots = {path.split('.')[0] for path in paths}
    columns = {'id', 'mnemonic'}
    fields = {'externalId': 'external_id', 'conceptClass': 'concept_class', 'datatype': 'datatype', 'extras': 'extras'}
    columns.update(value for key, value in fields.items() if key in roots)
    relations = []
    if roots & {'display', 'description'}:
        qs = qs.select_related('parent')
        columns.add('parent')
    if roots & {'display', 'names'}:
        relations.append('names')
    if 'description' in roots:
        relations.append('descriptions')
    if 'mappings' in roots:
        relations.append(mapping_prefetch)
    if 'metadata' in roots:
        columns.update({'retired', 'extras', 'created_at', 'updated_at', 'created_by', 'updated_by'})
        qs = qs.select_related('created_by', 'updated_by')
    if any(path.startswith('datatype.details') for path in paths):
        columns.add('extras')
    return qs.only(*sorted(columns)).prefetch_related(*relations)


async def concepts_for_ids(
        base_qs,
        concept_ids: Sequence[str],
        pagination: Optional[dict],
        mapping_prefetch: Prefetch,
        paths=None,
) -> tuple[List[Concept], int]:
    """Fetch concepts by mnemonic while preserving the client-provided ordering."""
    ordered_ids = list(dict.fromkeys(concept_id for concept_id in concept_ids if concept_id))
    if not ordered_ids:
        raise build_validation_error('conceptIds must include at least one value when provided.')

    ordering = Case(
        *[When(mnemonic=concept_id, then=pos) for pos, concept_id in enumerate(ordered_ids)],
        output_field=IntegerField(),
    )
    qs = base_qs.filter(mnemonic__in=ordered_ids).order_by(ordering, 'mnemonic')
    total = await sync_to_async(qs.count)()
    qs = apply_slice(qs, pagination)
    qs = with_concept_related(qs, mapping_prefetch, paths)
    return await sync_to_async(list)(qs), total


def build_db_search_queryset(base_qs, query: str):
    """Build the database fallback used when Elasticsearch is unavailable or stale."""
    trimmed = query.strip()
    if not trimmed:
        return base_qs.none()

    return base_qs.filter(
        Q(mnemonic__icontains=trimmed) | Q(names__name__icontains=trimmed, names__retired=False)
    ).distinct()
