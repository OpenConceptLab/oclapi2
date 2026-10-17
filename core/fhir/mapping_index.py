"""
The versions that may hold mappings from or to a source, as OCL's mappings index (core.mappings.documents) lists them:
the candidates ConceptMap source-system and target-system search (core.fhir.search) then decides in the database, so a
search doesn't join a heavily mapped source's mappings with every version's links.

- A mapping's document names the source of each side by owner type, owner and mnemonic ({side}_concept_owner_type,
  {side}_concept_owner, {side}_concept_source), and the versions of its own repository that hold it (owner_type, owner,
  source, source_version; owner and source in lowercase).
- One request per search parameter aggregates the documents whose side is one of the sources named, by owner type,
  owner, repository and version: the repositories holding such mappings, and their versions. What it reads and lists
  is scoped to the ConceptMap versions the database shows the caller at the base (scope_of: their owner types,
  owners, repositories and version names, and HEAD), never by a document's own copy of its access, and only what
  the base shows is kept: a ConceptMap or a version the caller isn't shown can't change the answer, or exhaust the
  aggregation's limits unless its names are those of one the caller is shown.
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

from core.common.constants import HEAD
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


def scope_of(shown):
    """
    {field: sorted values} of the documents of the repositories and versions `shown` ({(owner type, owner, mnemonic):
    {version names}}), as the index keeps them: owner and repository in lowercase; the version names with HEAD.
    """
    return {'owner_type': sorted({owner_type for owner_type, _, _ in shown}),
            'owner': sorted({owner.lower() for _, owner, _ in shown}),
            'source': sorted({mnemonic.lower() for _, _, mnemonic in shown}),
            'source_version': sorted(set().union(*shown.values()) | {HEAD})}


def side_query(side, repositories):
    """The documents whose `side` ('from' or 'to') source is one of `repositories`: (owner type, owner, mnemonic)."""
    return {'bool': {'should': [{'bool': {'filter': [
        {'term': {f'{side}_concept_owner_type': owner_type}}, {'term': {f'{side}_concept_owner': owner}},
        {'term': {f'{side}_concept_source': mnemonic}}]}} for owner_type, owner, mnemonic in sorted(repositories)],
        'minimum_should_match': 1}}


def terms(field, size, scope, aggs=None):
    """A terms aggregation listing only the values of `scope`, before its size is counted."""
    return {'terms': {'field': field, 'size': size, 'include': scope[field]}, **({'aggs': aggs} if aggs else {})}


def by_version(scope):
    """Owner type, owner, repository and version (HEAD included), each among `scope`'s."""
    return {'owner_type': terms('owner_type', OWNER_TYPES, scope, {'owner': terms('owner', MAX_BUCKETS, scope, {
        'source': terms('source', MAX_BUCKETS, scope, {'version': terms('source_version', MAX_BUCKETS, scope)})})})}


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


def request(side, alternatives, scope, left, too_costly):
    """The index's response for each of `alternatives`' repositories among `scope`, within `left` seconds."""
    client = connections.get_connection().options(request_timeout=left, max_retries=0, retry_on_timeout=False)
    try:
        return client.search(
            index=MappingDocument.Index.name, size=0, track_total_hits=False, allow_partial_search_results=False,
            timeout=f'{max(1, math.floor(left * 900))}ms',
            query={'bool': {'filter': [
                {'bool': {'should': [side_query(side, repositories) for repositories in alternatives],
                          'minimum_should_match': 1}},
                *[{'terms': {field: values}} for field, values in scope.items()]]}},
            aggs={str(number): {'filter': side_query(side, repositories), 'aggs': by_version(scope)}
                  for number, repositories in enumerate(alternatives)})
    except ConnectionTimeout as ex:
        raise too_costly() from ex
    except (ApiError, TransportError) as ex:
        logger.warning('The mappings index did not answer a FHIR search: %s', ex)
        raise IndexUnavailableError(UNAVAILABLE) from ex


def kept(found, shown):
    """The repositories and versions of `found` that `shown` lists, HEAD with them; twins in lowercase share theirs."""
    listed = {}
    for (owner_type, owner, mnemonic), versions in shown.items():
        listed.setdefault((owner_type, owner.lower(), mnemonic.lower()), {HEAD}).update(versions)
    return {repository: versions & listed[repository] for repository, versions in found.items() if repository in listed}


def holding(side, repositories, shown, deadline, too_costly):  # pylint: disable=too-many-arguments
    """
    {key: {(owner type, owner, repository): {version names}}}, owner and repository in lowercase: for each key's
    repositories ((owner type, owner, mnemonic) of its sources), the repositories and versions among `shown` (the
    ConceptMap versions the base shows the caller, {(owner type, owner, mnemonic): {version names}}) holding a mapping
    whose `side` ('from' or 'to') source is one of them, with HEAD when the index lists it. One request, by `deadline`
    (time.monotonic()), else too_costly(); none when nothing is named or shown.
    """
    keys = sorted(key for key, value in repositories.items() if value)
    if not keys or not shown:
        return {}
    left = deadline - time.monotonic()
    if left <= 0:
        raise too_costly()
    aggregations = whole(request(side, [repositories[key] for key in keys], scope_of(shown), left, too_costly),
                         too_costly)
    try:
        return {key: kept(versions_of(aggregations[str(number)], too_costly), shown)
                for number, key in enumerate(keys)}
    except (KeyError, TypeError) as ex:
        logger.warning('The mappings index answered a FHIR search without its aggregations')
        raise IndexUnavailableError(UNAVAILABLE) from ex
