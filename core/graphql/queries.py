from __future__ import annotations

import logging
from typing import Iterable, List, Optional, Sequence

import strawberry
from asgiref.sync import sync_to_async
from django.db.models import Case, IntegerField, Prefetch, Q, When
from elasticsearch import ConnectionError as ESConnectionError, TransportError
from elasticsearch_dsl import Q as ES_Q
from strawberry.exceptions import GraphQLError

from core.common.constants import HEAD
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.mappings.models import Mapping
from core.sources.models import Source

from .types import ConceptType, MappingType, ToSourceType

logger = logging.getLogger(__name__)
ES_MAX_WINDOW = 10_000


@strawberry.type
class ConceptSearchResult:
    org: str
    source: str
    version_resolved: str = strawberry.field(name="versionResolved")
    page: Optional[int]
    limit: Optional[int]
    total_count: int = strawberry.field(name="totalCount")
    has_next_page: bool = strawberry.field(name="hasNextPage")
    results: List[ConceptType]


async def resolve_source_version(org: str, source: str, version: Optional[str]) -> Source:
    filters = {'organization__mnemonic': org}
    target_version = version or HEAD
    instance = await sync_to_async(Source.get_version)(source, target_version, filters)

    if not instance and version is None:
        instance = await sync_to_async(Source.find_latest_released_version_by)({**filters, 'mnemonic': source})

    if not instance:
        raise GraphQLError(
            f"Source '{source}' with version '{version or 'HEAD'}' was not found for org '{org}'."
        )

    return instance


def build_base_queryset(source_version: Source):
    return source_version.get_concepts_queryset().filter(is_active=True, retired=False)


def build_mapping_prefetch(source_version: Source) -> Prefetch:
    mapping_qs = (
        Mapping.objects.filter(
            sources__id=source_version.id,
            from_concept_id__isnull=False,
            is_active=True,
            retired=False,
        )
        .select_related('to_source', 'to_concept', 'to_concept__parent')
        .order_by('map_type', 'to_concept_code', 'to_concept__mnemonic')
        .distinct()
    )

    return Prefetch('mappings_from', queryset=mapping_qs, to_attr='graphql_mappings')


def normalize_pagination(page: Optional[int], limit: Optional[int]) -> Optional[dict]:
    if page is None or limit is None:
        return None
    if page < 1 or limit < 1:
        raise GraphQLError('page and limit must be >= 1 when provided.')
    start = (page - 1) * limit
    end = start + limit
    return {'page': page, 'limit': limit, 'start': start, 'end': end}


def has_next(total: int, pagination: Optional[dict]) -> bool:
    if not pagination:
        return False
    return total > pagination['end']


def apply_slice(qs, pagination: Optional[dict]):
    if not pagination:
        return qs
    return qs[pagination['start']:pagination['end']]


def serialize_mappings(concept: Concept) -> List[MappingType]:
    mappings = getattr(concept, 'graphql_mappings', []) or []
    result: List[MappingType] = []
    for mapping in mappings:
        result.append(
            MappingType(
                map_type=str(mapping.map_type),
                to_source=ToSourceType(
                    url=mapping.to_source_url,
                    name=mapping.to_source_name
                ) if mapping.to_source_url or mapping.to_source_name else None,
                to_code=mapping.get_to_concept_code(),
                comment=mapping.comment,
            )
        )
    return result


def serialize_concepts(concepts: Iterable[Concept]) -> List[ConceptType]:
    output: List[ConceptType] = []
    for concept in concepts:
        output.append(
            ConceptType(
                concept_id=concept.mnemonic,
                display=concept.display_name,
                mappings=serialize_mappings(concept),
            )
        )
    return output


def concept_ids_from_es(
        query: str,
        source_version: Source,
        pagination: Optional[dict],
) -> Optional[tuple[list[int], int]]:
    trimmed = query.strip()
    if not trimmed:
        return [], 0

    try:
        search = ConceptDocument.search()
        search = search.filter('term', source=source_version.mnemonic.lower())
        search = search.filter('term', source_version=source_version.version)
        search = search.filter('term', retired=False)

        should_queries = [
            ES_Q('match', id={'query': trimmed, 'boost': 6, 'operator': 'AND'}),
            ES_Q('match_phrase_prefix', name={'query': trimmed, 'boost': 4}),
            ES_Q('match', synonyms={'query': trimmed, 'boost': 2, 'operator': 'AND'}),
        ]
        search = search.query(ES_Q('bool', should=should_queries, minimum_should_match=1))

        if pagination:
            search = search[pagination['start']:pagination['end']]
        else:
            search = search[0:ES_MAX_WINDOW]

        search = search.params(track_total_hits=True)
        response = search.execute()
        total_meta = getattr(getattr(response.hits, 'total', None), 'value', None)
        total = int(total_meta) if total_meta is not None else len(response.hits)
        concept_ids = [int(hit.meta.id) for hit in response]
        return concept_ids, total
    except (TransportError, ESConnectionError) as exc:  # pragma: no cover - depends on ES at runtime
        logger.warning('Falling back to DB search due to Elasticsearch error: %s', exc)
    except Exception as exc:  # pragma: no cover - unexpected ES error should not break API
        logger.warning('Unexpected Elasticsearch error, falling back to DB search: %s', exc)
    return None


def fallback_db_search(base_qs, query: str):
    trimmed = query.strip()
    if not trimmed:
        return base_qs.none()
    return base_qs.filter(
        Q(mnemonic__icontains=trimmed) | Q(names__name__icontains=trimmed)
    ).distinct()


async def concepts_for_ids(
        base_qs,
        concept_ids: Sequence[str],
        pagination: Optional[dict],
        mapping_prefetch: Prefetch,
) -> tuple[List[Concept], int]:
    unique_ids = list(dict.fromkeys([cid for cid in concept_ids if cid]))
    if not unique_ids:
        raise GraphQLError('conceptIds must include at least one value when provided.')

    qs = base_qs.filter(mnemonic__in=unique_ids)
    total = await sync_to_async(qs.count)()
    ordering = Case(
        *[When(mnemonic=value, then=pos) for pos, value in enumerate(unique_ids)],
        output_field=IntegerField()
    )
    qs = qs.order_by(ordering, 'mnemonic')
    qs = apply_slice(qs, pagination)
    qs = qs.prefetch_related('names', mapping_prefetch)
    return await sync_to_async(list)(qs), total


async def concepts_for_query(
        base_qs,
        query: str,
        source_version: Source,
        pagination: Optional[dict],
        mapping_prefetch: Prefetch,
) -> tuple[List[Concept], int]:
    es_result = await sync_to_async(concept_ids_from_es)(query, source_version, pagination)
    if es_result is not None:
        concept_ids, total = es_result
        if not concept_ids:
            if total == 0:
                logger.info(
                    'ES returned zero hits for query="%s" in source "%s" version "%s". Falling back to DB search.',
                    query,
                    source_version.mnemonic,
                    source_version.version,
                )
            else:
                return [], total
        else:
            ordering = Case(
                *[When(id=pk, then=pos) for pos, pk in enumerate(concept_ids)],
                output_field=IntegerField()
            )
            qs = base_qs.filter(id__in=concept_ids).order_by(ordering)
            qs = qs.prefetch_related('names', mapping_prefetch)
            return await sync_to_async(list)(qs), total

    qs = fallback_db_search(base_qs, query).order_by('mnemonic')
    total = await sync_to_async(qs.count)()
    qs = apply_slice(qs, pagination)
    qs = qs.prefetch_related('names', mapping_prefetch)
    return await sync_to_async(list)(qs), total


@strawberry.type
class Query:
    @strawberry.field(name="conceptsFromSource")
    async def concepts_from_source(
        self,
        info,  # pylint: disable=unused-argument
        org: str,
        source: str,
        version: Optional[str] = None,
        conceptIds: Optional[List[str]] = None,
        query: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> ConceptSearchResult:
        if info.context.auth_status == 'none':
            raise GraphQLError('Authentication required')
        elif info.context.auth_status == 'invalid':
            raise GraphQLError('Authentication failure')

        concept_ids_param = conceptIds or []
        text_query = (query or '').strip()

        if not concept_ids_param and not text_query:
            raise GraphQLError('Either conceptIds or query must be provided.')

        pagination = normalize_pagination(page, limit)
        source_version = await resolve_source_version(org, source, version)
        base_qs = build_base_queryset(source_version)
        mapping_prefetch = build_mapping_prefetch(source_version)

        if concept_ids_param:
            concepts, total = await concepts_for_ids(base_qs, concept_ids_param, pagination, mapping_prefetch)
        else:
            concepts, total = await concepts_for_query(base_qs, text_query, source_version, pagination, mapping_prefetch)

        return ConceptSearchResult(
            org=org,
            source=source,
            version_resolved=source_version.version,
            page=pagination['page'] if pagination else None,
            limit=pagination['limit'] if pagination else None,
            total_count=total,
            has_next_page=has_next(total, pagination),
            results=serialize_concepts(concepts),
        )
