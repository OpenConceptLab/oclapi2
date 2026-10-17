"""
The versions holding mappings from or to a source, as OCL's mappings index (core.mappings.documents) lists them, for
ConceptMap source-system and target-system search (core.fhir.search), so a search doesn't join a heavily mapped
source's mappings with every version's links.

- A mapping's document names the source of each side by owner type, owner and mnemonic ({side}_concept_owner_type,
  {side}_concept_owner, {side}_concept_source), and the versions of its own repository that hold it (owner_type, owner,
  source, source_version; owner and source in lowercase).
- One request per search parameter aggregates the documents whose side is one of the sources named, by owner type,
  owner, repository and version (HEAD left out), listing up to EXAMPLES mapping ids in each version: live ones first,
  then the newest. The database then checks those mappings (core.fhir.search.mapped): whether they're live, in the
  version and from or to the source, so it alone decides what a version shows, and tells apart two owners whose names
  differ only in case.
- Bounded: at most MAX_BUCKETS owners, repositories of an owner and versions of a repository, and the time the search
  has left, without retries. Past either the search is too costly.
- The index is written after the database: a version whose mappings aren't indexed yet (a new release while its
  indexing runs) isn't found until they are, as in OCL's own mapping search.
"""
import time

from elasticsearch import ConnectionTimeout
from elasticsearch_dsl.connections import connections

from core.mappings.documents import MappingDocument

MAX_BUCKETS = 1000  # the most owners, repositories of an owner and versions of a repository an aggregation lists
EXAMPLES = 3  # the mappings listed for each version
OWNER_TYPES = 2  # Organization and User


def side_query(side, repositories):
    """The documents whose `side` ('from' or 'to') source is one of `repositories`: (owner type, owner, mnemonic)."""
    return {'bool': {'should': [{'bool': {'filter': [
        {'term': {f'{side}_concept_owner_type': owner_type}}, {'term': {f'{side}_concept_owner': owner}},
        {'term': {f'{side}_concept_source': mnemonic}}]}} for owner_type, owner, mnemonic in sorted(repositories)],
        'minimum_should_match': 1}}


def terms(field, size, aggs, **options):
    return {'terms': {'field': field, 'size': size, **options}, 'aggs': aggs}


def by_version():
    """Owner type, owner, repository and version (HEAD left out), with a few of each version's mappings."""
    listed = {'example': {'top_hits': {
        'size': EXAMPLES, '_source': False, 'sort': [{'retired': 'asc'}, {'last_update': 'desc'}]}}}
    return {'owner_type': terms('owner_type', OWNER_TYPES, {'owner': terms('owner', MAX_BUCKETS, {
        'source': terms('source', MAX_BUCKETS, {
            'version': terms('source_version', MAX_BUCKETS, listed, exclude='HEAD')})})})}


def example_ids(aggregation, too_costly, inner=('owner', 'source', 'version')):
    """
    The mapping ids a level of by_version() lists, `inner` the levels below it; too costly when any level left
    buckets out.
    """
    if aggregation['sum_other_doc_count']:
        raise too_costly()
    if not inner:
        return [int(hit['_id']) for bucket in aggregation['buckets'] for hit in bucket['example']['hits']['hits']]
    return [mapping for bucket in aggregation['buckets']
            for mapping in example_ids(bucket[inner[0]], too_costly, inner[1:])]


def examples(side, repositories, deadline, too_costly):
    """
    {key: [mapping ids]}, for each key's repositories ((owner type, owner, mnemonic) of its sources): up to EXAMPLES
    mappings of each version (HEAD left out) holding a mapping whose `side` ('from' or 'to') source is one of them.
    One request, by `deadline` (time.monotonic()), else too_costly().
    """
    keys = sorted(key for key, value in repositories.items() if value)
    if not keys:
        return {}
    left = deadline - time.monotonic()
    if left <= 0:
        raise too_costly()
    client = connections.get_connection().options(request_timeout=left, max_retries=0, retry_on_timeout=False)
    try:
        response = client.search(
            index=MappingDocument.Index.name, size=0, track_total_hits=False,
            query={'bool': {'should': [side_query(side, repositories[key]) for key in keys],
                            'minimum_should_match': 1}},
            aggs={str(number): {'filter': side_query(side, repositories[key]), 'aggs': by_version()}
                  for number, key in enumerate(keys)})
    except ConnectionTimeout as ex:
        raise too_costly() from ex
    return {key: sorted(set(example_ids(response['aggregations'][str(number)]['owner_type'], too_costly)))
            for number, key in enumerate(keys)}
