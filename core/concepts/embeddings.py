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
from django.db.models import Exists, OuterRef
from elasticsearch_dsl.connections import connections
from pydash import get, compact, flatten

from core.common.constants import INDEX_BATCH_SIZE
from core.common.utils import encode_texts


def needs_vectors(parent, versions_match_algorithms):
    """
    Whether a concept row's doc carries vectors: any repo version it belongs to (HEAD included) is semantic, given
    each one's match_algorithms. A row in no version yet counts as its HEAD's (`parent`): a new concept is saved, and
    indexed, before it's added to HEAD, so this keeps those writes in step with the one after, whichever lands last.
    get_concept_ids_needing_vectors is the same rule in SQL.
    """
    if not versions_match_algorithms:
        return bool(parent.has_semantic_match_algorithm)
    return parent.SEMANTIC_MATCH_ALGORITHM in compact(flatten(versions_match_algorithms))


def get_concept_ids_needing_vectors(ids):
    """The ids, among these concept rows, whose docs carry vectors (needs_vectors, in SQL)."""
    from core.concepts.models import Concept
    from core.sources.models import Source
    semantic = [Source.SEMANTIC_MATCH_ALGORITHM]
    through = Concept.sources.through.objects
    in_semantic_version = through.filter(
        concept_id__in=ids, source__match_algorithms__contains=semantic).values_list('concept_id', flat=True)
    in_no_version_of_semantic_head = Concept.objects.filter(
        id__in=ids, parent__match_algorithms__contains=semantic
    ).exclude(Exists(through.filter(concept_id=OuterRef('id')))).values_list('id', flat=True)
    return set(in_semantic_version) | set(in_no_version_of_semantic_head)


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

    def __init__(self):
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

    @property
    def index_name(self):
        """The concepts alias, which the stored docs are read from (not _index._name, which a rebuild renames)."""
        from core.concepts.documents import ConceptDocument
        return ConceptDocument.Index.name

    @staticmethod
    def as_list(value):
        if isinstance(value, list):
            return value
        return [value] if value else []


def sync_concept_vectors(version, parallel=True):
    """
    Gives a repo version's concept docs the vectors they need, and leaves every other doc alone:
    - a doc that needs vectors and has none is rebuilt, which embeds it (reusing what it can);
    - a doc that has vectors, though no version it belongs to is semantic any more, is rebuilt without them;
    - the rest aren't touched. So opting a version in embeds only what's missing, and opting it out never strips the
      vectors another semantic version (HEAD included) still uses.
    Checks INDEX_BATCH_SIZE rows at a time, from the flags as they are at that moment, after refreshing the index so
    that what an earlier sync wrote counts. Every batch is attempted, and a failed one fails the run (BatchIndexRun).
    Returns the run's summary, plus the docs filled and stripped, and the vectors reused and texts encoded.

    A doc written from flags read just before a change can land after that change's own sync has checked it, so
    every sync that a change queues runs once more a few minutes later (sync_source_concept_vectors).
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
        batch_ids = set(batch)
        needing = get_concept_ids_needing_vectors(batch_ids)
        fill = (batch_ids & needing) - get_ids_with_vectors(index_name, batch_ids)
        not_needing = batch_ids - needing
        strip = get_ids_with_vectors(index_name, not_needing, synonyms_too=True) if not_needing else set()
        if fill or strip:
            concepts = list(Concept.objects.filter(id__in=fill | strip).select_related(
                'parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'
            ).prefetch_related('names'))
            run.retry_rejected(lambda: BatchIndexRun.bulk(
                doc, doc._get_actions(concepts, 'index'), parallel, refresh=False))  # pylint: disable=protected-access
        counts['filled'] += len(fill)
        counts['stripped'] += len(strip)

    connections.get_connection().indices.refresh(index=index_name)
    for start in range(0, len(ids), INDEX_BATCH_SIZE):
        run.attempt(start, ids[start:start + INDEX_BATCH_SIZE], sync_batch)

    return {**run.finish(), **counts, 'vectors_reused': doc.vectors_reused, 'texts_encoded': doc.texts_encoded}
