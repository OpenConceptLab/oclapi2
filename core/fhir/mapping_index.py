"""
The versions that may hold mappings from or to a source, as OCL's mappings index (core.mappings.documents) lists them:
the candidates ConceptMap source-system and target-system search (core.fhir.search) then decides in the database, so a
search doesn't join a heavily mapped source's mappings with every version's links.

- A mapping's document names the source of each side by owner type, owner and mnemonic ({side}_concept_owner_type,
  {side}_concept_owner, {side}_concept_source), the versions of its own repository that hold it (owner_type, owner,
  source, source_version; owner and source in lowercase), and whether it's public (public_can_view).
- One request per search parameter aggregates the documents of the ConceptMaps the base shows the caller (visible_to)
  whose side is one of the sources named, by owner type, owner, repository and version, HEAD included: the
  repositories holding such mappings, and their versions. Only those documents are counted, so a ConceptMap the
  caller isn't shown can't change the answer.
- The index names candidates; it never decides. The database decides each candidate version by its own mappings
  (core.fhir.search.mapped), so it alone says what's live, deleted or in the version, and tells apart two owners whose
  names differ only in case.
- Bounded: at most MAX_BUCKETS owners, repositories of an owner and versions of a repository; a whole answer only (no
  partial results), within the time the search has left and without retries. Past the limit or the time the search is
  too costly; a failed shard, a missing index or any other error of the index is IndexUnavailableError.
- The index is written after the database. A release created within LAG_WINDOW, of a repository the index lists, is a
  candidate whether the index lists the release yet or not; past that window, a release whose indexing never
  completed is missed until it's indexed again.
"""
import logging
import math
import time
from datetime import timedelta

from elasticsearch import ApiError, ConnectionTimeout, TransportError
from elasticsearch_dsl.connections import connections

from core.fhir import outcomes
from core.mappings.documents import MappingDocument

logger = logging.getLogger('oclapi')

MAX_BUCKETS = 1000  # the most owners, repositories of an owner and versions of a repository an aggregation lists
OWNER_TYPES = 2  # Organization and User
LAG_WINDOW = timedelta(days=7)  # how long after its creation a release may still be missing from the index
UNAVAILABLE = 'The search could not be completed: the mappings index did not answer in full. Try again later.'


class IndexUnavailableError(outcomes.FhirError):
    """The mappings index answered only in part, or not at all: an error that isn't the request's (422 exception)."""
    issue_code = 'exception'


def visible_to(namespace, may_view_private):
    """
    Filters on the documents of the ConceptMaps a base shows the caller: at the global base public ones; at an owner
    base the owner's, public ones unless the caller may view its private ones.
    """
    shown = []
    if namespace:
        kind, owner = namespace.strip('/').split('/')
        shown += [{'term': {'owner_type': 'Organization' if kind == 'orgs' else 'User'}},
                  {'term': {'owner': owner.lower()}}]
    if not (namespace and may_view_private):
        shown.append({'term': {'public_can_view': True}})
    return shown


def side_query(side, repositories):
    """The documents whose `side` ('from' or 'to') source is one of `repositories`: (owner type, owner, mnemonic)."""
    return {'bool': {'should': [{'bool': {'filter': [
        {'term': {f'{side}_concept_owner_type': owner_type}}, {'term': {f'{side}_concept_owner': owner}},
        {'term': {f'{side}_concept_source': mnemonic}}]}} for owner_type, owner, mnemonic in sorted(repositories)],
        'minimum_should_match': 1}}


def terms(field, size, aggs=None):
    return {'terms': {'field': field, 'size': size}, **({'aggs': aggs} if aggs else {})}


def by_version():
    """Owner type, owner, repository and version, HEAD included."""
    return {'owner_type': terms('owner_type', OWNER_TYPES, {'owner': terms('owner', MAX_BUCKETS, {
        'source': terms('source', MAX_BUCKETS, {'version': terms('source_version', MAX_BUCKETS)})})})}


def versions_of(aggregation, too_costly):
    """
    {(owner type, owner, repository): {version names}} for an aggregation by_version(), owner and repository in
    lowercase; too costly when any level left buckets out.
    """
    def buckets(level):
        if level['sum_other_doc_count']:
            raise too_costly()
        return level['buckets']

    return {(kind['key'], owner['key'], repository['key']): {
        version['key'] for version in buckets(repository['version'])}
        for kind in buckets(aggregation['owner_type']) for owner in buckets(kind['owner'])
        for repository in buckets(owner['source'])}


def whole(response, too_costly):
    """A response's aggregations, if the index answered in full; else too costly (its time ran out) or unavailable."""
    if response.get('timed_out'):
        raise too_costly()
    shards = response.get('_shards') or {}
    if not shards or shards.get('failed') or shards.get('successful') != shards.get('total'):
        logger.warning('The mappings index answered a FHIR search in part: %s', shards)
        raise IndexUnavailableError(UNAVAILABLE)
    return response.get('aggregations') or {}


def holding(side, repositories, shown, deadline, too_costly):
    """
    {key: {(owner type, owner, repository): {version names}}}: for each key's repositories ((owner type, owner,
    mnemonic) of its sources), those holding a mapping whose `side` ('from' or 'to') source is one of them, among the
    documents `shown` (visible_to) lets through. One request, by `deadline` (time.monotonic()), else too_costly().
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
            index=MappingDocument.Index.name, size=0, track_total_hits=False, allow_partial_search_results=False,
            timeout=f'{max(1, math.floor(left * 900))}ms',
            query={'bool': {'filter': [{'bool': {'should': [side_query(side, repositories[key]) for key in keys],
                                                 'minimum_should_match': 1}}, *shown]}},
            aggs={str(number): {'filter': side_query(side, repositories[key]), 'aggs': by_version()}
                  for number, key in enumerate(keys)})
    except ConnectionTimeout as ex:
        raise too_costly() from ex
    except (ApiError, TransportError) as ex:
        logger.warning('The mappings index did not answer a FHIR search: %s', ex)
        raise IndexUnavailableError(UNAVAILABLE) from ex
    aggregations = whole(response, too_costly)
    try:
        return {key: versions_of(aggregations[str(number)], too_costly) for number, key in enumerate(keys)}
    except (KeyError, TypeError) as ex:
        logger.warning('The mappings index answered a FHIR search without its aggregations')
        raise IndexUnavailableError(UNAVAILABLE) from ex
