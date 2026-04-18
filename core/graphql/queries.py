from __future__ import annotations

from datetime import timezone as datetime_timezone
import logging
from typing import Iterable, List, Optional, Sequence

import strawberry
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.db.models import Case, F, IntegerField, Prefetch, Q, When
from django.utils import timezone
from elasticsearch import ConnectionError as ESConnectionError, TransportError
from pydash import get
from strawberry.exceptions import GraphQLError

from core.common.constants import HEAD
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.search import apply_concept_text_search
from core.mappings.models import Mapping
from core.orgs.constants import ORG_OBJECT_TYPE
from core.sources.models import Source
from core.users.constants import USER_OBJECT_TYPE

from .constants import SEARCH_UNAVAILABLE, build_expected_graphql_error
from .permissions import (
    PermissionsMixin,
    apply_es_visibility_filter,
    check_user_permission,
    filter_global_queryset,
)
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

logger = logging.getLogger(__name__)
ES_MAX_WINDOW = 10_000


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
        raise GraphQLError("Either org or owner must be provided to resolve a source version.")
    target_version = version or HEAD
    instance = await sync_to_async(Source.get_version)(source, target_version, filters)

    if not instance and version is None:
        instance = await sync_to_async(Source.find_latest_released_version_by)({**filters, 'mnemonic': source})

    if not instance:
        owner_label = org or owner
        owner_kind = "org" if org else "owner"
        raise GraphQLError(
            f"Source '{source}' with version '{version or 'HEAD'}' was not found for {owner_kind} '{owner_label}'."
        )

    return instance


def build_base_queryset(source_version: Source = None):
    if source_version:
        return source_version.get_concepts_queryset().filter(is_active=True, retired=False)
    return Concept.objects.filter(is_active=True, retired=False, id=F('versioned_object_id'))


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


def build_global_mapping_prefetch(user=None) -> Prefetch:
    """Build the global mapping prefetch using the same visibility rules as REST list endpoints."""
    mapping_qs = (
        Mapping.objects.filter(
            from_concept_id__isnull=False,
            is_active=True,
            retired=False,
        )
        .select_related('to_source', 'to_concept', 'to_concept__parent')
        .order_by('map_type', 'to_concept_code', 'to_concept__mnemonic')
        .distinct()
    )

    # Mapping visibility must be filtered independently because a public concept can still reference private mappings.
    mapping_qs = filter_global_queryset(mapping_qs, user or AnonymousUser())
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


def with_concept_related(qs, mapping_prefetch: Prefetch):
    return qs.select_related('created_by', 'updated_by').prefetch_related('names', 'descriptions', mapping_prefetch)


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


def serialize_names(concept: Concept) -> List[ConceptNameType]:
    return [
        ConceptNameType(
            name=name.name,
            locale=name.locale,
            type=name.type,
            preferred=name.locale_preferred,
        )
        for name in concept.names.all()
    ]


def resolve_description(concept: Concept) -> Optional[str]:
    descriptions = list(concept.descriptions.all())
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
            )
        )
    return output


def concept_ids_from_es(
        query: str,
        source_version: Optional[Source],
        pagination: Optional[dict],
        owner: Optional[str] = None,
        owner_type: Optional[str] = None,
        version_label: Optional[str] = None,
        user=None,
) -> Optional[tuple[list[int], int]]:
    trimmed = query.strip()
    if not trimmed:
        return [], 0

    try:
        search = ConceptDocument.search()
        search = search.filter('term', retired=False)
        if source_version:
            search = search.filter('term', source=source_version.mnemonic)
            if owner and owner_type:
                search = search.filter('term', owner=owner).filter('term', owner_type=owner_type)

            effective_version = version_label or HEAD
            if effective_version == HEAD:
                search = search.filter('term', source_version=HEAD)
                search = search.filter('term', is_latest_version=True)
            else:
                search = search.filter('term', source_version=effective_version)
        else:
            search = search.filter('term', is_latest_version=True)
            search = apply_es_visibility_filter(search, user or AnonymousUser())

        search, _ = apply_concept_text_search(search, trimmed, include_rescore=True)

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


async def concepts_for_ids(
        base_qs,
        concept_ids: Sequence[str],
        pagination: Optional[dict],
        mapping_prefetch: Prefetch,
) -> tuple[List[Concept], int]:
    """Fetch concepts by mnemonic while preserving the client-provided ordering."""
    ordered_ids = list(dict.fromkeys(concept_id for concept_id in concept_ids if concept_id))
    if not ordered_ids:
        raise GraphQLError('conceptIds must contain at least one value.')

    ordering = Case(
        *[When(mnemonic=concept_id, then=pos) for pos, concept_id in enumerate(ordered_ids)],
        output_field=IntegerField(),
    )
    qs = base_qs.filter(mnemonic__in=ordered_ids).order_by(ordering)
    total = await sync_to_async(qs.count)()
    qs = apply_slice(qs, pagination)
    qs = with_concept_related(qs, mapping_prefetch)
    return await sync_to_async(list)(qs), total


def build_db_search_queryset(base_qs, query: str):
    """Build the database fallback used when Elasticsearch is unavailable or stale."""
    trimmed = query.strip()
    if not trimmed:
        return base_qs.none()

    return base_qs.filter(
        Q(names__name__icontains=trimmed) | Q(descriptions__name__icontains=trimmed)
    ).distinct()


async def concepts_for_query(
        base_qs,
        query: str,
        source_version: Source,
        pagination: Optional[dict],
        mapping_prefetch: Prefetch,
        owner: Optional[str] = None,
        owner_type: Optional[str] = None,
        version_label: Optional[str] = None,
        user=None,
) -> tuple[List[Concept], int]:
    es_result = await sync_to_async(concept_ids_from_es)(
        query,
        source_version,
        pagination,
        owner=owner,
        owner_type=owner_type,
        version_label=version_label,
        user=user,
    )
    if es_result is not None:
        concept_ids, total = es_result
        if concept_ids:
            ordering = Case(
                *[When(id=pk, then=pos) for pos, pk in enumerate(concept_ids)],
                output_field=IntegerField()
            )
            qs = base_qs.filter(id__in=concept_ids).order_by(ordering)
            qs = with_concept_related(qs, mapping_prefetch)
            concepts = await sync_to_async(list)(qs)
            if len(concepts) == len(concept_ids):
                return concepts, total
        elif total > 0:
            return [], total

    if source_version is None:
        # Global search is ES-backed because the DB fallback is both broader and much more expensive under outage.
        raise build_expected_graphql_error(SEARCH_UNAVAILABLE)

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
        info,  # pylint: disable=unused-argument
        org: Optional[str] = None,
        owner: Optional[str] = None,
        source: Optional[str] = None,
        version: Optional[str] = None,
        conceptIds: Optional[List[str]] = None,
        query: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> ConceptSearchResult:
        permission_target = self or Query()
        concept_ids_param = conceptIds or []
        text_query = (query or '').strip()
        user = getattr(info.context, 'user', AnonymousUser())

        if not concept_ids_param and not text_query:
            raise GraphQLError('Either conceptIds or query must be provided.')

        pagination = normalize_pagination(page, limit)

        if org and owner:
            raise GraphQLError('Provide either org or owner, not both.')

        if source and not org and not owner:
            raise GraphQLError('Either org or owner must be provided when source is specified.')

        owner_value = org or owner
        owner_type = ORG_OBJECT_TYPE if org else (USER_OBJECT_TYPE if owner else None)

        if (org or owner) and source:
            source_version = await permission_target.get_source_version(info, org, owner, source, version)
            base_qs = build_base_queryset(source_version)
            mapping_prefetch = build_mapping_prefetch(source_version)
        else:
            # Global search across all repositories
            source_version = None
            base_qs = permission_target.filter_global_queryset(build_base_queryset(), user)
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
                version_label=version or HEAD if source_version else None,
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
