"""
Concept vectors (OpenConceptLab/ocl_online#247).

A concept doc carries vectors when any repo version the row belongs to is semantic, HEAD included: exactly the docs a
semantic $match can return, since it always searches one repo version's members. Each vector
records the exact text it encoded, and each doc the model, so a rebuild can reuse a vector rather than re-encode it:
only when the doc itself, or its versioned object's doc, already holds a vector for the same text from the same model.
The other texts are encoded together, in one batched call per chunk of docs. Docs written before #247 record neither,
so their vectors are re-encoded the next time they're rebuilt.
"""
from django.conf import settings
from elasticsearch_dsl.connections import connections
from pydash import get

from core.common.utils import encode_texts

SYNC_BATCH_SIZE = 500
SYNC_MAX_PASSES = 5


def needs_vectors(versions_match_algorithms):
    """
    Whether a concept row's doc carries vectors: any repo version it belongs to (HEAD included) is semantic, given
    each one's match_algorithms. get_concept_ids_needing_vectors is the same rule in SQL.
    """
    from core.sources.models import Source
    return any(Source.SEMANTIC_MATCH_ALGORITHM in (algorithms or []) for algorithms in versions_match_algorithms)


def get_concept_ids_needing_vectors(ids):
    """The ids, among these concept rows, whose docs carry vectors (needs_vectors, in SQL)."""
    from core.concepts.models import Concept
    from core.sources.models import Source
    return set(Concept.sources.through.objects.filter(
        concept_id__in=ids, source__match_algorithms__contains=[Source.SEMANTIC_MATCH_ALGORITHM]
    ).values_list('concept_id', flat=True))


def get_ids_with_vectors(index_name, ids, synonyms_too=False):
    """
    The ids, among these concept docs, that carry a display-name vector -- or with synonyms_too, any vector at all.
    Searches, so a doc written in the last refresh interval may not count yet.
    """
    paths = ['_embeddings', '_synonyms_embeddings'] if synonyms_too else ['_embeddings']
    response = connections.get_connection().search(
        index=index_name, size=len(ids), source=False, query={'bool': {
            'filter': [{'ids': {'values': [str(_id) for _id in ids]}}],
            'should': [
                {'nested': {'path': path, 'query': {'exists': {'field': f'{path}.vector'}}}} for path in paths
            ],
            'minimum_should_match': 1,
        }})
    return {int(hit['_id']) for hit in response['hits']['hits']}


class ConceptVectors:
    """
    The vectors for one chunk of concept docs being prepared. add() takes each doc's vector entries ({'text', ...},
    still without a 'vector'), and resolve() fills in every one: reused from the stored docs where it can, the rest
    encoded in one batched call.
    """
    STORED_FIELDS = ['_embeddings', '_synonyms_embeddings', '_embeddings_model']

    def __init__(self, index_name):
        self.index_name = index_name
        self.model = settings.LM_MODEL_NAME
        self.pending = []
        self.reused = 0
        self.encoded = 0

    def add(self, concept, entries):
        """Queues a concept row's entries. Its own doc and its versioned object's are where vectors are reused from."""
        doc_ids = [str(_id) for _id in (concept.versioned_object_id, concept.id) if _id]
        self.pending.append((doc_ids, entries))

    def resolve(self):
        if not self.pending:
            return
        stored = self.get_stored_vectors({doc_id for doc_ids, _ in self.pending for doc_id in doc_ids})
        missing = {}
        for doc_ids, entries in self.pending:
            reusable = {}
            for doc_id in doc_ids:  # the row's own doc last, so it wins
                reusable.update(stored.get(doc_id, {}))
            for entry in entries:
                vector = reusable.get(entry['text'])
                if vector is None:
                    missing.setdefault(entry['text'], []).append(entry)
                else:
                    entry['vector'] = vector
                    self.reused += 1
        if missing:
            texts = list(missing)
            for text, vector in zip(texts, encode_texts(texts)):
                for entry in missing[text]:
                    entry['vector'] = vector
            self.encoded += len(texts)
        self.pending = []

    def get_stored_vectors(self, doc_ids):
        """
        {doc id: {text: vector}} from the stored docs that recorded the text of each vector and this model. A doc that
        isn't there, or an index that isn't (ES answers each id with an error), gives nothing.
        """
        response = connections.get_connection().mget(
            index=self.index_name, ids=sorted(doc_ids), source_includes=self.STORED_FIELDS)
        stored = {}
        for doc in response['docs']:
            source = doc.get('_source') or {}
            if not doc.get('found') or source.get('_embeddings_model') != self.model:
                continue
            entries = [*self.as_list(source.get('_embeddings')), *self.as_list(source.get('_synonyms_embeddings'))]
            stored[doc['_id']] = {
                entry['text']: entry['vector'] for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get('text'), str) and entry.get('vector')
            }
        return stored

    @staticmethod
    def as_list(value):
        if isinstance(value, list):
            return value
        return [value] if value else []


def get_repo_flags(version):
    """The match_algorithms of every version of the repo, HEAD included: what decides which of its docs need vectors."""
    return list(version.versions.order_by('id').values_list('id', 'match_algorithms'))


def sync_concept_vectors(version, parallel=True):
    """
    Gives a repo version's concept docs the vectors they need, and leaves every other doc alone:
    - a doc that needs vectors and has none is rebuilt, which embeds it (reusing what it can);
    - a doc that has vectors, though no version it belongs to is semantic any more, is rebuilt without them;
    - the rest aren't touched. So opting a version in embeds only what's missing, and opting it out never strips the
      vectors another semantic version (HEAD included) still uses.
    Checks 500 rows at a time. Every batch is attempted, and a failed one fails the run (BatchIndexRun). Returns the
    run's summary, plus the docs filled and stripped, the vectors reused and texts encoded, the passes it took, and
    whether the flags were still settled after the last one.

    Concurrency: a doc is prepared from the flags as they are at that moment, then written, so a flag change in
    between (on this version or another of the same repo) can leave a doc wrong after another sync has already
    checked it. So a pass ends by comparing the match_algorithms of every version of the repo with what they were
    when it began, and runs again if any changed, up to SYNC_MAX_PASSES: the last pass that saw no change wrote every
    doc from the flags as they are. Every change also queues its own sync (index_concepts_async never lets QueueOnce
    drop one). Each pass refreshes the index first, so it sees what the previous one wrote.
    """
    if get(settings, 'TEST_MODE', False):
        return None

    from core.common.models import BatchIndexRun
    from core.concepts.documents import ConceptDocument
    from core.concepts.models import Concept

    doc = ConceptDocument()
    index_name = doc._index._name  # pylint: disable=protected-access
    run = BatchIndexRun(ConceptDocument)
    counts = {'filled': 0, 'stripped': 0}
    ids = sorted(Concept.sources.through.objects.filter(
        source_id=version.id).values_list('concept_id', flat=True), reverse=True)

    def sync_batch(batch):
        needing = get_concept_ids_needing_vectors(batch)
        with_vectors = get_ids_with_vectors(index_name, batch)
        fill = [_id for _id in batch if _id in needing and _id not in with_vectors]
        not_needing = [_id for _id in batch if _id not in needing]
        strip = list(get_ids_with_vectors(index_name, not_needing, synonyms_too=True)) if not_needing else []
        if fill or strip:
            concepts = list(Concept.objects.filter(id__in=fill + strip).select_related(
                'parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'
            ).prefetch_related('names'))
            run.retry_rejected(lambda: BatchIndexRun.bulk(
                doc, doc._get_actions(concepts, 'index'), parallel, refresh=False))  # pylint: disable=protected-access
        counts['filled'] += len(fill)
        counts['stripped'] += len(strip)

    passes = 0
    settled = False
    while not settled and passes < SYNC_MAX_PASSES:
        passes += 1
        flags = get_repo_flags(version)
        connections.get_connection().indices.refresh(index=index_name)
        for start in range(0, len(ids), SYNC_BATCH_SIZE):
            run.attempt(start, ids[start:start + SYNC_BATCH_SIZE], sync_batch)
        settled = get_repo_flags(version) == flags

    return {
        **run.finish(), **counts, 'vectors_reused': doc.vectors_reused, 'texts_encoded': doc.texts_encoded,
        'passes': passes, 'settled': settled
    }
