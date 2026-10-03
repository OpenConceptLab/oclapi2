"""
Concept vectors (OpenConceptLab/ocl_online#247).

A concept doc carries vectors when its repo's HEAD, or any repo version the row belongs to, is semantic. Each vector
records the exact text it encoded, and each doc the model, so a rebuild can reuse a vector rather than re-encode it:
only when the doc itself, or its versioned object's doc, already holds a vector for the same text from the same model.
The other texts are encoded together, in one batched call per chunk of docs. Docs written before #247 record neither,
so their vectors are re-encoded the next time they're rebuilt.
"""
from django.conf import settings
from elasticsearch import NotFoundError
from elasticsearch_dsl.connections import connections
from pydash import get

from core.common.utils import encode_texts

SYNC_BATCH_SIZE = 500


def needs_vectors(parent, versions_match_algorithms):
    """
    Whether a concept row's doc carries vectors: its repo's HEAD (`parent`) is semantic, or any repo version the row
    belongs to is. get_concept_ids_needing_vectors is the same rule in SQL.
    """
    return bool(parent.has_semantic_match_algorithm) or any(
        parent.SEMANTIC_MATCH_ALGORITHM in (algorithms or []) for algorithms in versions_match_algorithms)


def get_concept_ids_needing_vectors(ids):
    """The ids, among these concept rows, whose docs carry vectors (needs_vectors, in SQL)."""
    from core.concepts.models import Concept
    from core.sources.models import Source
    semantic = [Source.SEMANTIC_MATCH_ALGORITHM]
    in_semantic_version = Concept.sources.through.objects.filter(
        concept_id__in=ids, source__match_algorithms__contains=semantic).values_list('concept_id', flat=True)
    under_semantic_head = Concept.objects.filter(
        id__in=ids, parent__match_algorithms__contains=semantic).values_list('id', flat=True)
    return set(in_semantic_version) | set(under_semantic_head)


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
        """{doc id: {text: vector}} from the stored docs that recorded the text of each vector and this model."""
        try:
            response = connections.get_connection().mget(
                index=self.index_name, ids=sorted(doc_ids), source_includes=self.STORED_FIELDS)
        except NotFoundError:  # no index yet
            return {}
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


def sync_concept_vectors(version, parallel=True):
    """
    Gives a repo version's concept docs the vectors they need, and leaves every other doc alone:
    - a doc that needs vectors and has none is rebuilt, which embeds it (reusing what it can);
    - a doc that has vectors, though neither HEAD nor any version it belongs to is semantic any more, is rebuilt
      without them;
    - the rest aren't touched. So opting a version in embeds only what's missing, and opting it out never strips the
      vectors that HEAD or another semantic version still uses.
    Checks 500 rows at a time. Every batch is attempted, and a failed one fails the run (BatchIndexRun). Returns the
    run's summary, plus how many docs were filled and stripped, and how many vectors were reused and texts encoded.

    Concurrency: the index is refreshed first, so the docs the previous sync wrote are visible to this one. A row
    whose need for vectors changes while its batch is being written (another version opted in or out meanwhile) is
    rebuilt once more, so that change's own sync, which may have checked the doc before this write, can't lose it.
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

    def rebuild(concept_ids):
        concepts = list(Concept.objects.filter(id__in=concept_ids).select_related(
            'parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'
        ).prefetch_related('names'))
        run.retry_rejected(lambda: BatchIndexRun.bulk(
            doc, doc._get_actions(concepts, 'index'), parallel, refresh=False))  # pylint: disable=protected-access

    def sync_batch(batch):
        needing = get_concept_ids_needing_vectors(batch)
        with_vectors = get_ids_with_vectors(index_name, batch)
        fill = [_id for _id in batch if _id in needing and _id not in with_vectors]
        not_needing = [_id for _id in batch if _id not in needing]
        strip = list(get_ids_with_vectors(index_name, not_needing, synonyms_too=True)) if not_needing else []
        if fill or strip:
            rebuild(fill + strip)
            needing_now = get_concept_ids_needing_vectors(fill + strip)
            changed = [_id for _id in fill + strip if (_id in needing_now) != (_id in needing)]
            if changed:
                rebuild(changed)
        counts['filled'] += len(fill)
        counts['stripped'] += len(strip)

    connections.get_connection().indices.refresh(index=index_name)
    for start in range(0, len(ids), SYNC_BATCH_SIZE):
        run.attempt(start, ids[start:start + SYNC_BATCH_SIZE], sync_batch)

    return {**run.finish(), **counts, 'vectors_reused': doc.vectors_reused, 'texts_encoded': doc.texts_encoded}
