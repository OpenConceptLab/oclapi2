"""Elasticsearch projections for payloads fully represented in the search index."""

import logging
from types import SimpleNamespace

from elasticsearch import ApiError, ConnectionError as ESConnectionError, TransportError
from elasticsearch_dsl import Q

from core.common.constants import HEAD
from core.concepts.documents import ConceptDocument
from core.sources.documents import SourceDocument

from .permissions import apply_es_parent_visibility_filter, apply_es_visibility_filter
from .selection import index_projection
from .sources import SOURCE_INDEX_FIELDS
from .types import ConceptType, DatatypeType, SourceType

logger = logging.getLogger(__name__)
CONCEPT_INDEX_FIELDS = {
    '__typename': (),
    'datatype.__typename': ('datatype',),
    'id': (),  # Elasticsearch's metadata ID is the OCL database primary key.
    'conceptId': ('id',),
    'externalId': ('external_id',),
    'display': ('name',),
    'description': ('preferred_description',),
    'conceptClass': ('concept_class',),
    'datatype.name': ('datatype',),
}


def search_text(search, query):
    """Keep the existing GraphQL relevance clauses shared by both retrieval paths."""
    return search.query(Q('bool', should=[
        Q('match', id={'query': query, 'boost': 6, 'operator': 'AND'}),
        Q('match_phrase_prefix', name={'query': query, 'boost': 4}),
        Q('match', synonyms={'query': query, 'boost': 2, 'operator': 'AND'}),
    ], minimum_should_match=1))


def indexed_source(org, owner, source, version, user, paths):  # pylint: disable=too-many-arguments
    """Resolve a visible source from ES; missing/old indexes use the authorized ORM fallback."""
    from .permissions import resolve_owner
    owner_value, owner_type = resolve_owner(org, owner)
    fields = index_projection(paths, SOURCE_INDEX_FIELDS)
    if fields is None:
        return None
    search = SourceDocument.search().filter('term', _mnemonic=source.lower())
    search = search.filter('term', owner=owner_value.lower()).filter('term', owner_type=owner_type)
    search = search.filter('term', version=version or HEAD)
    search = apply_es_visibility_filter(search, user)
    search = search.source(sorted(set(fields) | {'is_active', 'version', 'mnemonic'}))[:1]
    try:
        hits = list(search.execute())
    except (ApiError, TransportError, ESConnectionError) as exc:
        logger.warning('Source projection unavailable; using database: %s', exc)
        return None
    if not hits or not getattr(hits[0], 'is_active', False):
        return None
    hit = hits[0]
    return SimpleNamespace(
        mnemonic=hit.mnemonic, version=hit.version, is_head=hit.version == HEAD,
        payload=SourceType(**{field: getattr(hit, field, None) for field in fields}),
    )


# The planner supplies request scope and payload independently.
# pylint: disable-next=too-many-arguments,too-many-locals
def indexed_concepts(paths, query, concept_ids, scope, pagination, owner, owner_type, user):
    """Return selected index fields directly, without fetching or serializing ORM concepts."""
    fields = index_projection(paths, CONCEPT_INDEX_FIELDS)
    if fields is None:
        return None
    search = ConceptDocument.search().filter('term', is_active=True).filter('term', retired=False)
    if scope:
        search = search.filter('term', source=scope.mnemonic.lower())
        search = search.filter('term', owner=owner.lower()).filter('term', owner_type=owner_type)
        search = search.filter('term', **({'is_head': True} if scope.is_head else {'source_version': scope.version}))
    else:
        search = apply_es_visibility_filter(search.filter('term', is_head=True), user)
        search = apply_es_parent_visibility_filter(search, user)
    if concept_ids:
        # Script-free ordering preserves the requested mnemonic order, with deterministic ties.
        search = search.filter('terms', id_raw=concept_ids).sort('id_raw')
    else:
        search = search_text(search, query).sort({'_score': 'desc'}, 'id_raw')
    start, end = (pagination['start'], pagination['end']) if pagination else (0, 10_000)
    # ID lists need ordering before slicing. Their size is validated at the API boundary.
    if not paths:
        search = search[:0]
    else:
        search = search[0:10_000] if concept_ids else search[start:end]
    search = search.source(sorted(set(fields) | ({'id'} if concept_ids else set())))
    try:
        response = search.params(track_total_hits=True).execute()
    except (ApiError, TransportError, ESConnectionError) as exc:
        logger.warning('Concept projection unavailable; using database: %s', exc)
        return None
    hits = list(response)
    total = response.hits.total.value
    if concept_ids:
        if total > 10_000:
            return None  # Preserve complete mnemonic ordering through the ORM.
        ordering = {value: index for index, value in enumerate(concept_ids)}
        hits.sort(key=lambda hit: (ordering[hit.id], int(hit.meta.id)))
        hits = hits[start:end]
    return [serialize_indexed_concept(hit) for hit in hits], total


def serialize_indexed_concept(hit):
    """Construct only index-backed values; unselected relationship fields stay unloaded."""
    datatype = getattr(hit, 'datatype', None)
    return ConceptType(
        id=str(hit.meta.id), concept_id=getattr(hit, 'id', ''),
        external_id=getattr(hit, 'external_id', None), display=getattr(hit, 'name', None) or None,
        description=getattr(hit, 'preferred_description', None), concept_class=getattr(hit, 'concept_class', None),
        datatype=DatatypeType(name=datatype, details=None) if datatype else None,
        names=[], mappings=[], metadata=None, extras={},
    )
