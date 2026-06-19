"""GraphQL resolvers (Strawberry).

Pure helpers live in ``core/graphql/search.py`` (queryset builders, pagination, DB fallback)
and ``core/graphql/serializers.py`` (ORM → Strawberry mapping). This module hosts:

* the Elasticsearch boundary (``concept_ids_from_es`` + orchestrator ``concepts_for_query``),
* the ``Query`` Strawberry type and its resolvers,
* permissions/validation orchestration,
* and back-compat re-exports of helpers so existing imports continue to work.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import strawberry
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.db.models import Case, IntegerField, Prefetch, When
from elasticsearch import ConnectionError as ESConnectionError, TransportError
from elasticsearch_dsl import Q as ES_Q
from pydash import get
from strawberry.exceptions import GraphQLError

from core.common.constants import HEAD
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.sources.models import Source

from .constants import build_validation_error
from .permissions import (
    PermissionsMixin,
    apply_es_visibility_filter,
    check_user_permission,
    resolve_owner,
)
from .search import (
    apply_slice,
    build_db_search_queryset,
    build_global_head_queryset,
    build_global_mapping_prefetch,
    build_mapping_prefetch,
    build_source_version_queryset,
    concepts_for_ids,
    has_next,
    normalize_pagination,
    with_concept_related,
)
from .serializers import (
    _to_bool,
    _to_float,
    build_datatype,
    build_metadata,
    format_datetime_for_api,
    resolve_coded_datatype_details,
    resolve_datatype_details,
    resolve_description,
    resolve_is_set_flag,
    resolve_numeric_datatype_details,
    resolve_text_datatype_details,
    serialize_concepts,
    serialize_mappings,
    serialize_names,
)
from .types import ConceptType

logger = logging.getLogger(__name__)
ES_MAX_WINDOW = 10_000

# Back-compat re-exports for tests and callers that import these names from this module.
__all__ = [
    'ConceptSearchResult',
    'Query',
    'resolve_source_version',
    'concept_ids_from_es',
    'concepts_for_query',
    '_to_bool',
    '_to_float',
    'apply_slice',
    'build_db_search_queryset',
    'build_datatype',
    'build_global_head_queryset',
    'build_global_mapping_prefetch',
    'build_mapping_prefetch',
    'build_metadata',
    'build_source_version_queryset',
    'concepts_for_ids',
    'format_datetime_for_api',
    'has_next',
    'normalize_pagination',
    'resolve_coded_datatype_details',
    'resolve_datatype_details',
    'resolve_description',
    'resolve_is_set_flag',
    'resolve_numeric_datatype_details',
    'resolve_text_datatype_details',
    'serialize_concepts',
    'serialize_mappings',
    'serialize_names',
    'with_concept_related',
]


@strawberry.type
class ConceptSearchResult:
    org: Optional[str] = strawberry.field(
        description="Organization mnemonic that owns the searched source."
    )
    source: Optional[str] = strawberry.field(
        description="Source mnemonic against which the search was executed."
    )
    version_resolved: str = strawberry.field(
        name="versionResolved",
        description="Exact source version used (HEAD resolves to its concrete version).",
    )
    page: Optional[int] = strawberry.field(
        description="Requested page (1-indexed) if pagination parameters were supplied."
    )
    limit: Optional[int] = strawberry.field(
        description="Maximum number of records per page when pagination is applied."
    )
    total_count: int = strawberry.field(
        name="totalCount",
        description="Total number of matching concepts across all pages.",
    )
    has_next_page: bool = strawberry.field(
        name="hasNextPage",
        description="Indicates whether another page of results exists.",
    )
    results: List[ConceptType] = strawberry.field(
        description="Concepts returned for the current page."
    )


async def resolve_source_version(
    org: Optional[str],
    owner: Optional[str],
    source: str,
    version: Optional[str],
) -> Source:
    if org:
        filters = {'organization__mnemonic': org}
    elif owner:
        filters = {'user__username': owner}
    else:
        raise build_validation_error("Either org or owner must be provided to resolve a source version.")
    target_version = version or HEAD
    instance = await sync_to_async(Source.get_version)(source, target_version, filters)

    if not instance and version is None:
        instance = await sync_to_async(Source.find_latest_released_version_by)({**filters, 'mnemonic': source})

    if not instance:
        # Generic message: do not leak whether the owner exists when the source is missing.
        raise GraphQLError(f"Source '{source}' with version '{version or 'HEAD'}' was not found.")

    return instance


def concept_ids_from_es(
        query: str,
        source_version: Optional[Source],
        pagination: Optional[dict],
        owner: Optional[str] = None,
        owner_type: Optional[str] = None,
        user=None,
) -> Optional[tuple[list[int], int]]:
    trimmed = query.strip()
    if not trimmed:
        return [], 0

    try:
        search = ConceptDocument.search()
        if source_version:
            search = search.filter('term', source=source_version.mnemonic.lower())
            if owner and owner_type:
                search = search.filter('term', owner=owner.lower()).filter('term', owner_type=owner_type)

            # Always derive the effective version from the resolved Source object so that a
            # HEAD-fallback (find_latest_released_version_by) does not get filtered by the
            # client-supplied label, which would silently return zero hits.
            effective_version = source_version.version
            if effective_version == HEAD or source_version.is_head:
                search = search.filter('term', is_latest_version=True)
            else:
                search = search.filter('term', source_version=effective_version)
        else:
            search = search.filter('term', is_latest_version=True)
            search = apply_es_visibility_filter(search, user or AnonymousUser())
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


async def concepts_for_query(
        base_qs,
        query: str,
        source_version: Optional[Source],
        pagination: Optional[dict],
        mapping_prefetch: Prefetch,
        owner: Optional[str] = None,
        owner_type: Optional[str] = None,
        user=None,
) -> tuple[List[Concept], int]:
    es_result = await sync_to_async(concept_ids_from_es)(
        query,
        source_version,
        pagination,
        owner=owner,
        owner_type=owner_type,
        user=user,
    )
    if es_result is not None:
        concept_ids, total = es_result
        if not concept_ids:
            if total == 0:
                logger.info(
                    'ES returned zero hits for query="%s" in source "%s" version "%s". Falling back to DB search.',
                    query,
                    get(source_version, 'mnemonic'),
                    get(source_version, 'version'),
                )
            else:
                return [], total
        else:
            ordering = Case(
                *[When(id=pk, then=pos) for pos, pk in enumerate(concept_ids)],
                output_field=IntegerField(),
            )
            qs = base_qs.filter(id__in=concept_ids).order_by(ordering)
            qs = with_concept_related(qs, mapping_prefetch)
            return await sync_to_async(list)(qs), total

    qs = build_db_search_queryset(base_qs, query).order_by('mnemonic')
    total = await sync_to_async(qs.count)()
    qs = apply_slice(qs, pagination)
    qs = with_concept_related(qs, mapping_prefetch)
    return await sync_to_async(list)(qs), total


@strawberry.type
class Query(PermissionsMixin):
    async def resolve_source_version_for_permissions(
        self,
        org: Optional[str],
        owner: Optional[str],
        source: str,
        version: Optional[str],
    ) -> Source:
        """Resolve repository versions through the shared GraphQL helper."""
        return await resolve_source_version(org, owner, source, version)

    @strawberry.field(name="concepts")
    @check_user_permission
    async def concepts(  # pylint: disable=too-many-arguments,too-many-locals
        self,
        info,
        org: Optional[str] = None,
        owner: Optional[str] = None,
        source: Optional[str] = None,
        version: Optional[str] = None,
        conceptIds: Optional[List[str]] = None,
        query: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> ConceptSearchResult:
        concept_ids_param = conceptIds or []
        text_query = (query or '').strip()
        user = getattr(info.context, 'user', AnonymousUser())

        if not concept_ids_param and not text_query:
            raise build_validation_error('Either conceptIds or query must be provided.')

        pagination = normalize_pagination(page, limit)

        if org and owner:
            raise build_validation_error('Provide either org or owner, not both.')

        if source and not org and not owner:
            raise build_validation_error('Either org or owner must be provided when source is specified.')

        owner_value, owner_type = resolve_owner(org, owner)

        if (org or owner) and source:
            source_version = await self.get_source_version(info, org, owner, source, version)
            base_qs = build_source_version_queryset(source_version)
            mapping_prefetch = build_mapping_prefetch(source_version)
        else:
            # Global search across all repositories
            source_version = None
            base_qs = self.filter_global_queryset(build_global_head_queryset(), user)
            mapping_prefetch = build_global_mapping_prefetch(user)

        if concept_ids_param:
            concepts, total = await concepts_for_ids(base_qs, concept_ids_param, pagination, mapping_prefetch)
        else:
            concepts, total = await concepts_for_query(
                base_qs,
                text_query,
                source_version,
                pagination,
                mapping_prefetch,
                owner=owner_value,
                owner_type=owner_type,
                user=user,
            )

        serialized = await sync_to_async(serialize_concepts)(concepts)
        return ConceptSearchResult(
            org=org,
            source=source,
            version_resolved=source_version.version if source_version else '',
            page=pagination['page'] if pagination else None,
            limit=pagination['limit'] if pagination else None,
            total_count=total,
            has_next_page=has_next(total, pagination),
            results=serialized,
        )
