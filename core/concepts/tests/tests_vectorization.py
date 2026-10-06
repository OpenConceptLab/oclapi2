"""
Release-level vectorization (OpenConceptLab/ocl_online#247): which concept docs carry vectors, reusing a doc's vectors
instead of re-encoding them, opting a version in or out, new versions inheriting vectorization, and the reindex paths
that must not strip vectors a semantic version still uses.

These run against the test Elasticsearch, with a deterministic stand-in for the language model.
"""
import hashlib
import uuid
from io import StringIO
from unittest.mock import patch, Mock

import numpy
from django.core.management import call_command, CommandError
from django.test import override_settings
from elasticsearch.helpers import streaming_bulk
from elasticsearch_dsl.connections import connections

from core.common.models import BatchIndexRun
from core.common.exceptions import BatchIndexingError
from core.common.tasks import index_source_concepts, batch_index_resources, index_concepts_mapped_codes, \
    handle_save, sync_source_concept_vectors, seed_children_to_new_version, index_concepts_locale_change, \
    get_concepts_to_index, index_in_parts
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.concepts.documents import ConceptDocument
from core.concepts.embeddings import sync_concept_vectors, ConceptVectors, get_concept_ids_needing_vectors
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.importers.models import BulkImportInline
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.tasks.models import Task
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory

DIMS = 384
MODEL = 'all-MiniLM-L6-v2'
LLM = ['es', 'llm']


def fake_vector(text):
    seed = int(hashlib.sha256(str(text).encode()).hexdigest()[:8], 16)
    return numpy.random.default_rng(seed).random(DIMS, dtype=numpy.float32)


class FakeEncoder:
    """Stands in for the language model: a deterministic vector per text, and a record of what it was asked."""
    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        texts = list(texts)
        self.calls.append(texts)
        return [fake_vector(text) for text in texts]

    @property
    def texts(self):
        return sorted(text for call in self.calls for text in call)

    def reset(self):
        self.calls = []


class VectorTestMixin:
    """
    Concepts in a source whose docs are written for real, and helpers to read them back from Elasticsearch. Tests
    index explicitly: an earlier test's pause_indexing() can leave save signals off for the rest of the run.
    """
    def setUp(self):
        super().setUp()
        self.encoder = FakeEncoder()
        patcher = patch('core.concepts.embeddings.encode_texts', self.encoder)
        patcher.start()
        self.addCleanup(patcher.stop)
        # parallel_bulk builds the documents on pool threads, whose DB connections can't see this test's rows
        patcher = patch('core.common.models.parallel_bulk', streaming_bulk)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.org = OrganizationFactory(mnemonic=f'Vec{uuid.uuid4().hex[:8]}')
        self.source = OrganizationSourceFactory(organization=self.org, default_locale='en', supported_locales=['en'])

    @staticmethod
    def es():
        return connections.get_connection()

    @staticmethod
    def index_name():
        return ConceptDocument._index._name  # pylint: disable=protected-access

    def get_doc(self, concept):
        return self.es().get(index=self.index_name(), id=str(concept.id))

    def get_source(self, concept):
        return self.get_doc(concept)['_source']

    def seq_no(self, concept):
        return self.get_doc(concept)['_seq_no']

    def refresh(self):
        self.es().indices.refresh(index=self.index_name())

    def create_concept(self, *names, source=None):
        """A concept with these English names (the first preferred); returns its latest version row."""
        concept = ConceptFactory(
            parent=source or self.source,
            names=[
                ConceptNameFactory.build(name=name, locale='en', locale_preferred=index == 0)
                for index, name in enumerate(names)
            ]
        )
        return concept.get_latest_version()

    def create_version(self, version, match_algorithms=None, concepts=(), released=True):
        repo_version = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.org, version=version, released=released,
            default_locale='en', supported_locales=['en'], match_algorithms=match_algorithms or ['es']
        )
        repo_version.concepts.add(*concepts)
        return repo_version

    def index(self, *concepts):
        ConceptDocument().update(list(concepts), refresh=True, parallel=False)

    def assert_vectors(self, concept, names):
        """The doc carries a vector for each name, recorded with the text and the model it came from."""
        source = self.get_source(concept)
        display = source['_embeddings']
        self.assertEqual(display['text'], names[0])
        self.assertEqual(source['_embeddings_model'], MODEL)
        numpy.testing.assert_allclose(display['vector'], fake_vector(names[0]), rtol=1e-6)
        synonyms = source['_synonyms_embeddings']
        self.assertEqual(sorted(entry['text'] for entry in synonyms), sorted(names[1:]))
        for entry in synonyms:
            numpy.testing.assert_allclose(entry['vector'], fake_vector(entry['text']), rtol=1e-6)

    def assert_no_vectors(self, concept):
        source = self.get_source(concept)
        self.assertFalse(source.get('_embeddings'))
        self.assertFalse(source.get('_synonyms_embeddings'))
        self.assertFalse(source.get('_embeddings_model'))

    def strip_provenance(self, concept):
        """Makes the doc look like one written before #247: vectors with no record of their text or model."""
        self.es().update(index=self.index_name(), id=str(concept.id), refresh=True, script={'source': (
            "ctx._source._embeddings.remove('text'); ctx._source.remove('_embeddings_model');"
            "for (entry in ctx._source._synonyms_embeddings) { entry.remove('text'); }"
        )})


@override_settings(LM_MODEL_NAME=MODEL)
class ConceptDocumentVectorsTest(VectorTestMixin, OCLTestCase):
    def test_vectors_when_head_is_semantic(self):
        self.source.match_algorithms = LLM
        self.source.save()
        concept = self.create_concept('Malaria', 'Paludism')

        self.index(concept)

        self.assert_vectors(concept, ['Malaria', 'Paludism'])

    def test_vectors_when_a_version_it_belongs_to_is_semantic(self):
        concept = self.create_concept('Malaria', 'Paludism')
        self.create_version('v1', LLM, [concept])

        self.index(concept)

        self.assert_vectors(concept, ['Malaria', 'Paludism'])

    def test_no_vectors_when_no_version_it_belongs_to_is_semantic(self):
        concept = self.create_concept('Malaria', 'Paludism')
        self.create_version('v1', ['es'], [concept])
        other = self.create_concept('Fever')
        self.create_version('v2', LLM, [other])

        self.index(concept)

        self.assert_no_vectors(concept)

    def test_superseded_row_under_a_semantic_head_has_vectors_only_if_a_semantic_version_holds_it(self):
        self.source.match_algorithms = LLM
        self.source.save()
        superseded = self.create_concept('Malaria', 'Paludism')
        superseded.sources.remove(self.source)  # as unmark_latest_version does once a newer version replaces it
        self.create_version('v0', ['es'], [superseded])
        kept = self.create_concept('Fever')
        kept.sources.remove(self.source)
        self.create_version('v1', LLM, [kept])

        self.index(superseded, kept)

        self.assert_no_vectors(superseded)  # only in a lexical release: no semantic $match can return it
        self.assert_vectors(kept, ['Fever'])

    def test_a_row_in_no_version_yet_counts_as_its_heads(self):
        concept = self.create_concept('Malaria', 'Paludism')
        concept.sources.clear()  # as while it's being created, before it joins HEAD
        self.create_version('v1', LLM, [])

        self.index(concept)
        self.assert_no_vectors(concept)  # HEAD isn't semantic

        self.source.match_algorithms = LLM
        self.source.save()
        self.index(Concept.objects.get(id=concept.id))
        self.assert_vectors(concept, ['Malaria', 'Paludism'])  # so a write that races its HEAD membership agrees
        self.assertEqual(get_concept_ids_needing_vectors([concept.id]), {concept.id})

    def test_reuses_vectors_from_the_concepts_index(self):
        self.assertEqual(ConceptVectors().index_name, self.index_name())

    def test_no_stored_vectors_to_reuse_without_an_index(self):
        with patch.object(ConceptVectors, 'index_name', f'concepts-missing-{uuid.uuid4().hex[:8]}'):
            self.assertEqual(ConceptVectors().get_stored_vectors({'1'}), {})

    def test_prepare_outside_a_batch_resolves_vectors_for_that_doc(self):
        concept = self.create_concept('Malaria', 'Paludism')
        self.create_version('v1', LLM, [concept])

        data = ConceptDocument().prepare(concept)

        self.assertEqual(data['_embeddings']['text'], 'Malaria')
        numpy.testing.assert_allclose(data['_embeddings']['vector'], fake_vector('Malaria'))
        self.assertEqual([entry['text'] for entry in data['_synonyms_embeddings']], ['Paludism'])
        self.assertEqual(data['_embeddings_model'], MODEL)
        self.assertIn('v1', data['source_version'])


@override_settings(LM_MODEL_NAME=MODEL)
class VectorReuseTest(VectorTestMixin, OCLTestCase):
    def setUp(self):
        super().setUp()
        self.source.match_algorithms = LLM
        self.source.save()

    def test_reindex_reuses_vectors_when_text_and_model_are_unchanged(self):
        concept = self.create_concept('Malaria', 'Paludism', 'Marsh fever')
        self.index(concept)
        self.encoder.reset()

        self.index(Concept.objects.get(id=concept.id))

        self.assertEqual(self.encoder.texts, [])
        self.assert_vectors(concept, ['Malaria', 'Paludism', 'Marsh fever'])

    def test_reindex_encodes_only_the_changed_text(self):
        concept = self.create_concept('Malaria', 'Paludism')
        self.index(concept)
        self.encoder.reset()
        name = concept.names.get(name='Paludism')
        name.name = 'Marsh fever'
        name.save()

        self.index(Concept.objects.get(id=concept.id))

        self.assertEqual(self.encoder.texts, ['Marsh fever'])
        self.assert_vectors(concept, ['Malaria', 'Marsh fever'])

    def test_reindex_does_not_reuse_vectors_from_another_model(self):
        concept = self.create_concept('Malaria', 'Paludism')
        with override_settings(LM_MODEL_NAME='some-older-model'):
            self.index(concept.versioned_object)
            self.index(concept)
        self.encoder.reset()

        self.index(Concept.objects.get(id=concept.id))

        self.assertEqual(self.encoder.texts, ['Malaria', 'Paludism'])
        self.assert_vectors(concept, ['Malaria', 'Paludism'])

    def test_reindex_does_not_reuse_vectors_that_record_no_text(self):
        concept = self.create_concept('Malaria', 'Paludism')
        self.index(concept.versioned_object, concept)
        self.strip_provenance(concept.versioned_object)
        self.strip_provenance(concept)
        self.encoder.reset()

        self.index(Concept.objects.get(id=concept.id))

        self.assertEqual(self.encoder.texts, ['Malaria', 'Paludism'])
        self.assert_vectors(concept, ['Malaria', 'Paludism'])

    def test_new_version_row_reuses_its_versioned_objects_vectors(self):
        concept = self.create_concept('Malaria', 'Paludism')
        versioned_object = concept.versioned_object
        self.index(versioned_object)
        new_row = versioned_object.clone()
        new_row.save()
        new_row.version = new_row.id
        new_row.save()
        new_row.set_locales(versioned_object.names.all(), type(versioned_object.names.first()))
        new_row.sources.add(self.source)  # an edit's new version joins HEAD
        self.encoder.reset()

        self.index(Concept.objects.get(id=new_row.id))

        self.assertEqual(self.encoder.texts, [])
        self.assert_vectors(new_row, ['Malaria', 'Paludism'])

    def test_encodes_a_chunk_of_docs_in_one_batched_call(self):
        self.source.match_algorithms = ['es']
        self.source.save()  # so that creating them writes no vectors
        concepts = [self.create_concept(f'Name {index}', f'Synonym {index}', 'Shared') for index in range(5)]
        self.source.match_algorithms = LLM
        self.source.save()
        concepts = list(Concept.objects.filter(id__in=[concept.id for concept in concepts]).order_by('id'))
        self.encoder.reset()

        self.index(*concepts)

        self.assertEqual(len(self.encoder.calls), 1)
        self.assertEqual(
            sorted(self.encoder.calls[0]),
            sorted(['Shared', *[f'Name {index}' for index in range(5)], *[f'Synonym {index}' for index in range(5)]])
        )
        for index, concept in enumerate(concepts):
            self.assert_vectors(concept, [f'Name {index}', f'Synonym {index}', 'Shared'])


@override_settings(LM_MODEL_NAME=MODEL)
class VersionVectorSyncTest(VectorTestMixin, OCLTestCase):
    """HEAD isn't semantic: only a version's own flag decides whether its docs carry vectors."""
    def setUp(self):
        super().setUp()
        self.shared = [self.create_concept('Malaria', 'Paludism'), self.create_concept('Fever', 'Pyrexia')]
        self.own = self.create_concept('Cholera', 'Asiatic cholera')

    def sync(self, version):
        self.refresh()
        self.encoder.reset()
        with override_settings(TEST_MODE=False):
            summary = sync_concept_vectors(version, parallel=False)
        self.refresh()
        self.assertEqual(summary['failed_docs'], 0)
        self.assertEqual(summary['texts_encoded'], len(set(self.encoder.texts)))
        return {key: summary[key] for key in ('docs', 'filled', 'stripped')}

    def test_opting_a_version_in_embeds_only_its_docs_without_vectors(self):
        self.create_version('v1', LLM, self.shared)
        v2 = self.create_version('v2', ['es'], [*self.shared, self.own])
        self.index(*self.shared, self.own)
        self.assert_no_vectors(self.own)
        seq_nos = [self.seq_no(concept) for concept in self.shared]
        v2.match_algorithms = LLM
        v2.save()

        summary = self.sync(v2)

        self.assertEqual(summary, {'docs': 3, 'filled': 1, 'stripped': 0})
        self.assertEqual(self.encoder.texts, ['Asiatic cholera', 'Cholera'])
        self.assert_vectors(self.own, ['Cholera', 'Asiatic cholera'])
        self.assertEqual([self.seq_no(concept) for concept in self.shared], seq_nos)  # not rewritten

    def test_opting_a_version_out_keeps_the_vectors_another_semantic_version_uses(self):
        self.create_version('v1', LLM, self.shared)
        v2 = self.create_version('v2', LLM, [*self.shared, self.own])
        self.index(*self.shared, self.own)
        self.assert_vectors(self.own, ['Cholera', 'Asiatic cholera'])
        seq_nos = [self.seq_no(concept) for concept in self.shared]
        v2.match_algorithms = ['es']
        v2.save()

        summary = self.sync(v2)

        self.assertEqual(summary, {'docs': 3, 'filled': 0, 'stripped': 1})
        self.assertEqual(self.encoder.texts, [])
        self.assert_vectors(self.shared[0], ['Malaria', 'Paludism'])
        self.assert_vectors(self.shared[1], ['Fever', 'Pyrexia'])
        self.assertEqual([self.seq_no(concept) for concept in self.shared], seq_nos)  # not rewritten
        self.assert_no_vectors(self.own)

    def test_opting_a_version_out_keeps_vectors_when_head_is_semantic(self):
        v2 = self.create_version('v2', LLM, [*self.shared, self.own])
        self.index(*self.shared, self.own)
        self.source.match_algorithms = LLM
        self.source.save()
        v2.match_algorithms = ['es']
        v2.save()

        summary = self.sync(v2)

        self.assertEqual(summary, {'docs': 3, 'filled': 0, 'stripped': 0})
        self.assert_vectors(self.own, ['Cholera', 'Asiatic cholera'])

    def test_opting_head_in_fills_its_members_only(self):
        superseded = self.create_concept('Ague')
        superseded.sources.remove(self.source)
        self.create_version('v0', ['es'], [superseded])  # superseded: only in an old lexical release
        self.index(*self.shared, self.own, superseded)
        self.source.match_algorithms = LLM
        self.source.save()

        summary = self.sync(self.source)

        self.assertEqual(summary['filled'], summary['docs'])  # every HEAD member: latest rows and versioned objects
        self.assert_vectors(self.own, ['Cholera', 'Asiatic cholera'])
        self.assert_no_vectors(superseded)  # in no version, so no semantic $match can return it

    def test_sync_is_a_no_op_when_docs_already_match(self):
        v1 = self.create_version('v1', LLM, self.shared)
        self.index(*self.shared, self.own)

        summary = self.sync(v1)

        self.assertEqual(summary, {'docs': 2, 'filled': 0, 'stripped': 0})
        self.assertEqual(self.encoder.calls, [])

    def during_the_next_write(self, change):
        """Patches the sync's write: after its docs are prepared, and before they're sent, runs change()."""
        real_bulk = BatchIndexRun.bulk
        pending = [change]

        def bulk(doc, actions, *args, **kwargs):
            actions = list(actions)  # prepared from the flags as they were
            if pending:
                pending.pop()()
            return real_bulk(doc, iter(actions), *args, **kwargs)
        return patch('core.common.models.BatchIndexRun.bulk', side_effect=bulk)

    def opt_in_and_sync(self, version):
        """A version opts in, and the sync that queues runs at once: it finds the doc's vectors still there."""
        def change():
            Source.objects.filter(id=version.id).update(match_algorithms=LLM)
            with override_settings(TEST_MODE=False):
                self.assertEqual(sync_concept_vectors(version, parallel=False)['filled'], 0)
        return change

    def test_an_opt_in_during_another_versions_opt_out_is_repaired_by_its_recheck(self):
        v1 = self.create_version('v1', LLM, [*self.shared, self.own])
        v2 = self.create_version('v2', ['es'], [self.own])
        self.index(*self.shared, self.own)
        v1.match_algorithms = ['es']
        v1.save()

        # v1's sync prepares own's doc without vectors; v2 opts in and v2's sync runs before that write lands
        with self.during_the_next_write(self.opt_in_and_sync(v2)):
            self.sync(v1)
        self.assert_no_vectors(self.own)  # the race: v2 is semantic and own has no vectors

        self.assertEqual(self.sync(v2), {'docs': 1, 'filled': 1, 'stripped': 0})  # v2's recheck, minutes later
        self.assert_vectors(self.own, ['Cholera', 'Asiatic cholera'])
        self.assert_no_vectors(self.shared[0])

    def test_an_opt_out_and_back_in_during_a_sync_is_repaired_by_the_recheck(self):
        v1 = self.create_version('v1', LLM, [self.own])
        self.index(self.own)
        Source.objects.filter(id=v1.id).update(match_algorithms=['es'])  # v1 opts out

        # its sync prepares the strip; v1 opts back in and that sync runs before the strip lands
        with self.during_the_next_write(self.opt_in_and_sync(v1)):
            self.sync(v1)
        self.assert_no_vectors(self.own)

        self.assertEqual(self.sync(v1)['filled'], 1)  # the opt-in's recheck
        self.assert_vectors(self.own, ['Cholera', 'Asiatic cholera'])

    def test_sync_sees_vectors_written_since_the_last_refresh(self):
        v1 = self.create_version('v1', LLM, self.shared)
        self.es().indices.put_settings(index=self.index_name(), settings={'index': {'refresh_interval': '-1'}})
        self.addCleanup(
            self.es().indices.put_settings, index=self.index_name(), settings={'index': {'refresh_interval': None}})
        ConceptDocument().update(self.shared, refresh=False, parallel=False)  # vectors written, not yet searchable
        seq_nos = [self.seq_no(concept) for concept in self.shared]
        self.encoder.reset()

        with override_settings(TEST_MODE=False):
            summary = sync_concept_vectors(v1, parallel=False)

        self.assertEqual((summary['filled'], summary['stripped']), (0, 0))
        self.assertEqual([self.seq_no(concept) for concept in self.shared], seq_nos)
        self.assertEqual(self.encoder.calls, [])

    def test_sync_does_nothing_in_test_mode(self):
        v1 = self.create_version('v1', LLM, self.shared)

        self.assertIsNone(sync_concept_vectors(v1))

    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.Source.sync_concept_vectors_async')
    def test_seeding_a_semantic_version_queues_its_sync(self, sync_async_mock):
        v1 = self.create_version('v1', ['es'], [])
        v2 = self.create_version('v2', ['es'], [])  # v1 is no longer the latest release: nothing indexes it
        Source.objects.filter(id=v1.id).update(match_algorithms=LLM)

        seed_children_to_new_version('source', v1.id, False)

        sync_async_mock.assert_called_once_with(v1.created_by)
        self.assertEqual(
            set(v1.concepts.values_list('id', flat=True)), {self.shared[0].id, self.shared[1].id, self.own.id})
        sync_async_mock.reset_mock()
        seed_children_to_new_version('source', v2.id, False)
        sync_async_mock.assert_not_called()  # lexical

    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.Source.sync_concept_vectors_async')
    def test_seeding_reads_the_flag_after_seeding(self, sync_async_mock):
        v1 = self.create_version('v1', ['es'], [])

        def opt_in_meanwhile(*_):  # the opt-in's own sync, and its recheck, find no members yet
            Source.objects.filter(id=v1.id).update(match_algorithms=LLM)
        with patch('core.sources.models.Source.update_children_counts', side_effect=opt_in_meanwhile):
            seed_children_to_new_version('source', v1.id, False)

        sync_async_mock.assert_called_once_with(v1.created_by)

    def test_a_seeded_semantic_release_gets_its_vectors_from_the_sync(self):
        v1 = self.create_version('v1', ['es'], [*self.shared, self.own])
        self.index(*self.shared, self.own)  # HEAD's docs as they were: no vectors
        Source.objects.filter(id=v1.id).update(match_algorithms=LLM)  # v1 was created vectorized

        self.assertEqual(self.sync(v1), {'docs': 3, 'filled': 3, 'stripped': 0})  # the sync its seeding queued

        self.assert_vectors(self.shared[0], ['Malaria', 'Paludism'])
        self.assert_vectors(self.own, ['Cholera', 'Asiatic cholera'])


@override_settings(LM_MODEL_NAME=MODEL)
class ReindexKeepsSharedVectorsTest(VectorTestMixin, OCLTestCase):
    """HEAD isn't semantic, and a release that shares HEAD's rows is: rebuilding those rows keeps their vectors."""
    def setUp(self):
        super().setUp()
        self.concept = self.create_concept('Malaria', 'Paludism')
        self.plain = self.create_concept('Fever')
        self.release = self.create_version('v1', LLM, [self.concept])
        self.index(self.concept, self.plain)
        self.assert_vectors(self.concept, ['Malaria', 'Paludism'])
        self.encoder.reset()

    def test_manual_reindex_keeps_the_vectors_of_rows_in_a_semantic_release(self):
        with override_settings(TEST_MODE=False):
            index_source_concepts(self.source.id)

        self.assert_vectors(self.concept, ['Malaria', 'Paludism'])
        self.assert_no_vectors(self.plain)
        self.assertEqual(self.encoder.texts, [])  # reused

    def test_import_reindex_keeps_the_vectors_of_rows_in_a_semantic_release(self):
        self.import_update_and_check(index=True)

    def test_small_import_reindex_keeps_the_vectors_of_rows_in_a_semantic_release(self):
        self.import_update_and_check()  # no `index`: imports of up to IMPORT_INDEX_MAX_LINES lines index anyway

    def test_edit_keeps_the_vectors_of_the_row_it_supersedes(self):
        errors = Concept.create_new_version_for(
            instance=self.concept.clone(),
            data={'names': [{'locale': 'en', 'name': 'Malaria', 'locale_preferred': True},
                            {'locale': 'en', 'name': 'Paludism'}, {'locale': 'en', 'name': 'Swamp fever'}]},
            user=self.concept.created_by
        )
        self.assertEqual(errors, {})
        previous_row = Concept.objects.get(id=self.concept.id)
        self.assertFalse(previous_row.is_latest_version)

        handle_save('concepts', 'Concept', previous_row.id)  # what the edit queues for it (Concept.index)

        self.assert_vectors(previous_row, ['Malaria', 'Paludism'])  # still in v1: not stripped
        self.assertEqual(self.encoder.texts, [])

    def import_update_and_check(self, **importer_kwargs):
        with patch('core.importers.models.batch_index_resources') as batch_index_mock, \
                patch('core.importers.models.index_concepts_mapped_codes') as mapped_codes_mock:
            importer = BulkImportInline(
                content=None, username='ocladmin', update_if_exists=True, input_list=[{
                    'type': 'Concept', 'id': self.concept.mnemonic, 'concept_class': 'Misc', 'datatype': 'None',
                    'owner_type': 'Organization', 'owner': self.org.mnemonic, 'source': self.source.mnemonic,
                    'names': [
                        {'name': 'Malaria', 'locale': 'en', 'locale_preferred': True, 'name_type': 'Fully Specified'},
                        {'name': 'Paludism', 'locale': 'en', 'name_type': 'Synonym'},
                        {'name': 'Swamp fever', 'locale': 'en', 'name_type': 'Synonym'},
                    ]
                }], **importer_kwargs)
            importer.run()
            self.assertEqual(len(importer.updated), 1)
            with override_settings(TEST_MODE=False):
                for task_mock, task in [(batch_index_mock, batch_index_resources),
                                        (mapped_codes_mock, index_concepts_mapped_codes)]:
                    for call_args in task_mock.apply_async.call_args_list:
                        task(*call_args[0][0])

        previous_row = Concept.objects.get(id=self.concept.id)
        self.assertFalse(previous_row.is_latest_version)
        self.assert_vectors(previous_row, ['Malaria', 'Paludism'])  # still in v1: not stripped
        new_row = previous_row.versioned_object.get_latest_version()
        self.assert_no_vectors(new_row)  # in no semantic version
        self.assertEqual(self.encoder.texts, [])  # the previous row's vectors were reused

    def change_locale(self):
        """The repo's default locale becomes French, in which the concept has a preferred name."""
        ConceptNameFactory(concept=self.concept, name='Paludisme', locale='fr', locale_preferred=True)
        self.index(Concept.objects.get(id=self.concept.id))
        self.encoder.reset()
        self.source.default_locale = 'fr'
        self.source.supported_locales = ['fr', 'en']
        self.source.save()

    def test_locale_change_rebuilds_rows_with_vectors_so_their_display_vector_follows(self):
        self.change_locale()

        with override_settings(TEST_MODE=False):
            index_source_concepts(self.source.id, None, locales=['en', 'fr'])

        source = self.get_source(self.concept)
        self.assertEqual(source['_embeddings']['text'], 'Paludisme')
        self.assertEqual(source['name'], 'Paludisme')
        self.assertEqual(self.encoder.texts, [])  # every name already had a vector
        self.assert_no_vectors(self.plain)

    def test_locale_change_summary_counts_the_rows_with_vectors_and_the_rest(self):
        self.change_locale()
        queryset = get_concepts_to_index(self.source, ['en', 'fr'])

        with override_settings(TEST_MODE=False):
            summary = index_concepts_locale_change(self.source, queryset, False, False)

        self.assertEqual(summary['docs'], queryset.count())
        self.assertEqual((summary['batches'], summary['failed_docs']), (2, 0))  # one batch of each

    @patch('core.common.tasks.update_concepts_locale_fields', side_effect=BatchIndexingError(
        'failed', {'batches': 1, 'failed_batches': 1, 'docs': 3, 'failed_docs': 3}))
    def test_locale_change_rebuilds_rows_with_vectors_even_when_the_rest_fail(self, _):
        self.change_locale()

        with override_settings(TEST_MODE=False), self.assertRaises(BatchIndexingError) as raised:
            index_source_concepts(self.source.id, None, locales=['en', 'fr'])

        self.assertEqual(self.get_source(self.concept)['_embeddings']['text'], 'Paludisme')
        self.assertEqual(raised.exception.summary, {'batches': 2, 'failed_batches': 1, 'docs': 4, 'failed_docs': 3})


class IndexInPartsTest(OCLTestCase):
    """Batch indexing calls run one after another, each attempted whatever happened to the others."""
    def test_sums_the_parts_summaries(self):
        self.assertEqual(
            index_in_parts(lambda: {'docs': 2, 'batches': 1}, lambda: None, lambda: {'docs': 1, 'batches': 1}),
            {'docs': 3, 'batches': 2})
        self.assertIsNone(index_in_parts(lambda: None))  # TEST_MODE

    def test_attempts_every_part_then_raises_their_failures_with_the_summary_of_all(self):
        last = Mock(return_value={'docs': 5, 'failed_docs': 0})

        with self.assertRaises(BatchIndexingError) as raised:
            index_in_parts(
                Mock(side_effect=BatchIndexingError('first failed', {'docs': 2, 'failed_docs': 1})),
                Mock(side_effect=BatchIndexingError('second failed', {'docs': 3, 'failed_docs': 3}, rejected=True)),
                last)

        last.assert_called_once()
        self.assertEqual(raised.exception.summary, {'docs': 10, 'failed_docs': 4})
        self.assertTrue(raised.exception.rejected)
        self.assertIn('first failed', str(raised.exception))
        self.assertIn('second failed', str(raised.exception))

    def test_attempts_every_part_before_raising_any_other_error(self):
        last = Mock(return_value={'docs': 5})

        with self.assertRaisesRegex(ValueError, 'broken'):
            index_in_parts(Mock(side_effect=ValueError('broken')), last)

        last.assert_called_once()


class VectorSyncQueuingTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory(default_locale='en', supported_locales=['en'])
        self.version = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.source.organization, version='v1',
            match_algorithms=['es'], released=False)

    @patch('core.sources.models.Source.sync_concept_vectors_async')
    @patch('core.sources.models.Source.index_concepts_async')
    def test_flag_flip_queues_a_vector_sync_not_a_reindex(self, index_concepts_async_mock, sync_async_mock):
        self.version.match_algorithms = LLM
        errors = Source.persist_changes(self.version, self.version.created_by, None)

        self.assertEqual(errors, {})
        sync_async_mock.assert_called_once_with(self.version.updated_by)
        index_concepts_async_mock.assert_not_called()

    @patch('core.sources.models.Source.sync_concept_vectors_async')
    @patch('core.sources.models.Source.index_concepts_async')
    def test_flag_flip_with_a_full_locale_reindex_queues_both(self, index_concepts_async_mock, sync_async_mock):
        self.version.match_algorithms = LLM
        self.version.supported_locales = None
        self.version.default_locale = 'es'
        errors = Source.persist_changes(self.version, self.version.created_by, None)

        self.assertEqual(errors, {})
        index_concepts_async_mock.assert_called_once_with(self.version.updated_by)  # every doc
        sync_async_mock.assert_called_once_with(self.version.updated_by)

    @patch('core.sources.models.Source.sync_concept_vectors_async')
    @patch('core.sources.models.Source.index_resources_for_self_as_latest_released')
    def test_releasing_and_vectorizing_in_one_change_does_both(self, index_released_mock, sync_async_mock):
        self.version.match_algorithms = LLM
        self.version.released = True
        errors = Source.persist_changes(self.version, self.version.created_by, None)

        self.assertEqual(errors, {})
        index_released_mock.assert_called_once_with(only_update=True)
        sync_async_mock.assert_called_once_with(self.version.updated_by)

    @patch('core.common.tasks.sync_concept_vectors')
    @patch('core.sources.models.Source.sync_concept_vectors_async')
    def test_sync_task_syncs_then_queues_one_delayed_recheck(self, sync_async_mock, sync_mock):
        sync_source_concept_vectors(self.version.id)  # pylint: disable=no-value-for-parameter

        self.assertEqual(sync_mock.call_args[0][0].id, self.version.id)
        sync_async_mock.assert_called_once_with(recheck=False, countdown=600)

    @patch('core.common.tasks.sync_concept_vectors')
    @patch('core.sources.models.Source.sync_concept_vectors_async')
    def test_the_recheck_queues_nothing_more(self, sync_async_mock, sync_mock):
        sync_source_concept_vectors(self.version.id, False)

        sync_mock.assert_called_once()
        sync_async_mock.assert_not_called()

    @patch('core.common.tasks.sync_concept_vectors', side_effect=BatchIndexingError('failed'))
    @patch('core.sources.models.Source.sync_concept_vectors_async')
    def test_a_failed_sync_still_queues_its_recheck(self, sync_async_mock, _):
        with self.assertRaises(BatchIndexingError):
            sync_source_concept_vectors(self.version.id)  # pylint: disable=no-value-for-parameter

        sync_async_mock.assert_called_once_with(recheck=False, countdown=600)

    def run_sync_task(self):
        """
        Runs the sync task's body under its own Task row's id, as a worker would. Returns that row, the recheck's, and
        what the sync raised.
        """
        user = self.version.created_by
        parent = Task.new(queue='indexing', user=user, name='core.common.tasks.sync_source_concept_vectors')
        recheck = Task.new(queue='indexing', user=user, name='core.common.tasks.sync_source_concept_vectors')
        raised = None
        with patch('core.sources.models.Source.sync_concept_vectors_async', return_value=recheck) as async_mock:
            sync_source_concept_vectors.push_request(id=parent.id)
            try:
                sync_source_concept_vectors.run(self.version.id)
            except BatchIndexingError as ex:
                raised = ex
            finally:
                sync_source_concept_vectors.pop_request()
        async_mock.assert_called_once_with(recheck=False, countdown=600)
        return Task.objects.get(id=parent.id), recheck, raised

    @patch('core.common.tasks.sync_concept_vectors', Mock(return_value={}))
    def test_the_recheck_is_a_child_of_the_sync_task(self):
        parent, recheck, raised = self.run_sync_task()

        self.assertIsNone(raised)
        self.assertEqual(parent.children, [recheck.id])
        self.assertEqual(list(parent.child_tasks), [recheck])  # so revoking or rerunning the sync revokes it too

    @patch('core.common.tasks.sync_concept_vectors', Mock(side_effect=BatchIndexingError('failed')))
    def test_a_failed_syncs_recheck_is_its_child_too(self):
        parent, recheck, raised = self.run_sync_task()

        self.assertIsInstance(raised, BatchIndexingError)
        self.assertEqual(parent.children, [recheck.id])

    @patch('celery.app.task.Task.apply_async')
    def test_a_queued_sync_keeps_its_arguments_for_recovery(self, celery_apply_async_mock):
        with override_settings(TEST_MODE=False):
            self.version.sync_concept_vectors_async(self.version.created_by, recheck=False, countdown=600)

        args, kwargs = celery_apply_async_mock.call_args
        self.assertEqual(args[0], (self.version.id, False))
        self.assertEqual(kwargs['countdown'], 600)
        self.assertEqual(kwargs['queue'], 'indexing')
        task = Task.objects.get(id=args[2])
        self.assertEqual(task.name, 'core.common.tasks.sync_source_concept_vectors')
        self.assertEqual(list(task.args), [self.version.id, False])  # a rerun is still a sync

    @patch('core.sources.models.sync_source_concept_vectors', Mock(__name__='sync_source_concept_vectors'))
    def test_in_test_mode_the_sync_runs_inline(self):
        from core.sources import models as source_models
        sync_task_mock = source_models.sync_source_concept_vectors
        tasks = Task.objects.count()

        self.assertIsNone(self.version.sync_concept_vectors_async())

        sync_task_mock.assert_called_once_with(self.version.id, True)
        self.assertEqual(Task.objects.count(), tasks)  # nothing queued, so no Task


class SourceConceptsIndexViewSyncVectorsTest(OCLAPITestCase):
    """Staff can re-run a version's vector sync without changing its flag, e.g. after a sync task was lost."""
    def setUp(self):
        super().setUp()
        self.token = UserProfile.objects.filter(is_superuser=True).first().get_token()
        self.source = OrganizationSourceFactory()
        self.version = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.source.organization, version='v1', match_algorithms=LLM)

    def post(self, data, task_name):
        with patch(f'core.sources.views.{task_name}') as task_mock:
            task_mock.__name__ = task_name
            response = self.client.post(
                self.version.uri + 'concepts/indexes/', data, HTTP_AUTHORIZATION='Token ' + self.token, format='json')
        self.assertEqual(response.status_code, 202, response.data)
        return task_mock.apply_async.call_args[0][0]

    @patch('celery.app.task.Task.apply_async')
    def test_sync_vectors_persists_its_arguments(self, celery_apply_async_mock):
        with override_settings(TEST_MODE=False):
            response = self.client.post(
                self.version.uri + 'concepts/indexes/', {'sync_vectors': True},
                HTTP_AUTHORIZATION='Token ' + self.token, format='json')

        self.assertEqual(response.status_code, 202, response.data)
        task = Task.objects.get(id=response.data['id'])
        self.assertEqual(task.name, 'core.common.tasks.sync_source_concept_vectors')
        self.assertEqual(list(task.args), [self.version.id, True])  # a rerun is still a sync
        self.assertEqual(celery_apply_async_mock.call_args[0][0], (self.version.id, True))

    @patch('core.sources.models.sync_source_concept_vectors', Mock(__name__='sync_source_concept_vectors'))
    def test_sync_vectors_runs_inline_in_test_mode(self):
        from core.sources import models as source_models

        response = self.client.post(
            self.version.uri + 'concepts/indexes/', {'sync_vectors': True},
            HTTP_AUTHORIZATION='Token ' + self.token, format='json')

        self.assertEqual(response.status_code, 202, response.data)
        self.assertFalse(response.data)  # no Task to describe
        source_models.sync_source_concept_vectors.assert_called_once_with(self.version.id, True)

    def test_without_sync_vectors_queues_the_full_reindex_as_before(self):
        self.assertEqual(
            self.post({}, 'index_source_concepts'), (self.version.id, None, False, True, True, True))


class VersionCreateInheritsVectorizationTest(OCLAPITestCase):
    """Decision V1: a new version inherits HEAD's match algorithms, and only HEAD's, unless the request sets them."""
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.token = UserProfile.objects.filter(is_superuser=True).first().get_token()
        self.source = OrganizationSourceFactory(organization=self.organization)

    def create(self, token=None, **data):
        with patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts')), \
                patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings')):
            response = self.client.post(
                f'/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/versions/',
                {'id': 'v-new', 'description': 'new', 'released': True, **data},
                HTTP_AUTHORIZATION='Token ' + (token or self.token), format='json'
            )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['match_algorithms'], self.source.versions.get(version='v-new').match_algorithms

    def test_inherits_llm_from_semantic_head(self):
        self.source.match_algorithms = LLM
        self.source.save()

        api_value, stored = self.create()

        self.assertEqual(sorted(api_value), LLM)
        self.assertEqual(sorted(stored), LLM)

    def test_a_semantic_latest_release_does_not_make_the_new_version_semantic(self):
        OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.organization, version='v1', released=True,
            match_algorithms=LLM)

        api_value, stored = self.create()

        self.assertEqual(api_value, ['es'])
        self.assertEqual(stored, ['es'])

    def test_not_vectorized_when_head_is_not(self):
        _, stored = self.create()

        self.assertEqual(stored, ['es'])
        self.assertEqual(Source(match_algorithms=None).get_match_algorithms_for_new_version(), ['es'])

    def test_owner_can_opt_a_new_release_in_when_head_is_lexical(self):
        _, stored = self.create(match_algorithms=LLM)

        self.assertEqual(sorted(stored), LLM)

    def test_owner_can_opt_a_new_release_out(self):
        self.source.match_algorithms = LLM
        self.source.save()

        _, stored = self.create(match_algorithms=['es'])

        self.assertEqual(stored, ['es'])

    def test_a_member_who_is_not_staff_inherits_it_too(self):
        self.source.match_algorithms = LLM
        self.source.save()
        member = UserProfileFactory()
        self.organization.members.add(member)

        _, stored = self.create(token=member.get_token())

        self.assertEqual(sorted(stored), LLM)


class ConceptVectorMappingCommandTest(OCLTestCase):
    """The deploy step that adds the vector provenance fields to an existing concepts index (mapped as before #247)."""
    def setUp(self):
        super().setUp()
        self.es = connections.get_connection()
        self.index = f'concepts-mapping-test-{uuid.uuid4().hex[:8]}'
        vector_doc = {'type': 'nested', 'properties': {'vector': {'type': 'dense_vector'}, 'type': {'type': 'text'}}}
        self.es.indices.create(index=self.index, mappings={'properties': {
            '_embeddings': vector_doc, '_synonyms_embeddings': vector_doc, 'name': {'type': 'text'}}})
        self.addCleanup(self.es.indices.delete, index=self.index)

    def mapping(self):
        return self.es.indices.get_mapping(index=self.index)[self.index]['mappings']['properties']

    def call(self, *args):
        out = StringIO()
        call_command('concept_vector_mapping', '--index', self.index, *args, stdout=out)
        return out.getvalue()

    def test_check_fails_while_the_fields_are_missing(self):
        with self.assertRaisesRegex(CommandError, '_embeddings.text'):
            self.call('--check')
        self.assertNotIn('text', self.mapping()['_embeddings']['properties'])  # --check changed nothing

    def test_adds_the_fields_and_leaves_the_rest(self):
        output = self.call()

        mapping = self.mapping()
        for field in ('_embeddings', '_synonyms_embeddings'):
            self.assertEqual(
                mapping[field]['properties']['text'], {'type': 'keyword', 'index': False, 'doc_values': False})
            self.assertEqual(mapping[field]['properties']['type'], {'type': 'text'})
        self.assertEqual(mapping['_embeddings_model'], {'type': 'keyword'})
        self.assertIn('ok', output)
        self.assertIn('ok', self.call('--check'))
        self.call()  # again: nothing to change

    def test_refuses_a_field_already_mapped_differently(self):
        self.es.indices.put_mapping(index=self.index, properties={'_embeddings_model': {'type': 'text'}})

        with self.assertRaisesRegex(CommandError, '_embeddings_model'):
            self.call()
