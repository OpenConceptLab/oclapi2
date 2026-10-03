"""
Release-level vectorization (OpenConceptLab/ocl_online#247): which concept docs carry vectors, reusing a doc's vectors
instead of re-encoding them, opting a version in or out, new versions inheriting vectorization, and the reindex paths
that must not strip vectors a semantic version still uses.

These run against the test Elasticsearch, with a deterministic stand-in for the language model.
"""
import hashlib
import uuid
from unittest.mock import patch, Mock

import numpy
from django.test import override_settings
from elasticsearch.helpers import streaming_bulk
from elasticsearch_dsl.connections import connections

from core.common.tasks import index_source_concepts, batch_index_resources, index_concepts_mapped_codes
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.concepts.documents import ConceptDocument
from core.concepts.embeddings import sync_concept_vectors, ConceptVectors
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.importers.models import BulkImportInline
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
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

    def test_no_stored_vectors_to_reuse_without_an_index(self):
        self.assertEqual(ConceptVectors(f'concepts-missing-{uuid.uuid4().hex[:8]}').get_stored_vectors({'1'}), {})

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

    def test_sync_is_a_no_op_when_docs_already_match(self):
        v1 = self.create_version('v1', LLM, self.shared)
        self.index(*self.shared, self.own)

        summary = self.sync(v1)

        self.assertEqual(summary, {'docs': 2, 'filled': 0, 'stripped': 0})
        self.assertEqual(self.encoder.calls, [])

    def test_sync_does_nothing_in_test_mode(self):
        v1 = self.create_version('v1', LLM, self.shared)

        self.assertIsNone(sync_concept_vectors(v1))

    def test_release_indexing_gives_a_semantic_release_its_vectors(self):
        v1 = self.create_version('v1', ['es'], [*self.shared, self.own])
        self.index(*self.shared, self.own)
        v1.match_algorithms = LLM
        v1.save(update_fields=['match_algorithms'])  # as if created vectorized; nothing has indexed it yet
        for concept in [*self.shared, self.own]:
            self.assert_no_vectors(concept)
        self.refresh()
        self.encoder.reset()

        with override_settings(TEST_MODE=False):
            index_source_concepts(v1.id, {'_append_source_version': 'v1', 'is_in_latest_source_version': True})

        self.assert_vectors(self.shared[0], ['Malaria', 'Paludism'])
        self.assert_vectors(self.own, ['Cholera', 'Asiatic cholera'])
        self.assertIn('v1', self.get_source(self.own)['source_version'])

    def test_release_indexing_of_a_release_that_is_not_semantic_writes_no_vectors(self):
        v1 = self.create_version('v1', ['es'], [*self.shared, self.own])
        self.refresh()

        with override_settings(TEST_MODE=False):
            index_source_concepts(v1.id, {'_append_source_version': 'v1', 'is_in_latest_source_version': True})

        self.assert_no_vectors(self.own)
        self.assertEqual(self.encoder.calls, [])


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
                }], index=True)
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

    def test_locale_change_rebuilds_rows_with_vectors_so_their_display_vector_follows(self):
        ConceptNameFactory(concept=self.concept, name='Paludisme', locale='fr', locale_preferred=True)
        self.index(Concept.objects.get(id=self.concept.id))
        self.encoder.reset()
        self.source.default_locale = 'fr'
        self.source.supported_locales = ['fr', 'en']
        self.source.save()

        with override_settings(TEST_MODE=False):
            index_source_concepts(self.source.id, None, locales=['en', 'fr'])

        source = self.get_source(self.concept)
        self.assertEqual(source['_embeddings']['text'], 'Paludisme')
        self.assertEqual(source['name'], 'Paludisme')
        self.assertEqual(self.encoder.texts, [])  # every name already had a vector
        self.assert_no_vectors(self.plain)


class VectorSyncQueuingTest(OCLTestCase):
    @patch('core.sources.models.Source.index_concepts_async')
    def test_flag_flip_queues_a_vector_sync_not_a_full_reindex(self, index_concepts_async_mock):
        source = OrganizationSourceFactory(default_locale='en', supported_locales=['en'])
        version = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1', match_algorithms=['es'])

        version.match_algorithms = LLM
        errors = Source.persist_changes(version, version.created_by, None)

        self.assertEqual(errors, {})
        index_concepts_async_mock.assert_called_once_with(version.updated_by, sync_vectors=True)

    @patch('core.sources.models.Source.index_resources_for_self_as_latest_released')
    @patch('core.sources.models.Source.index_concepts_async')
    def test_releasing_and_vectorizing_in_one_change_does_both(self, index_concepts_async_mock, index_released_mock):
        source = OrganizationSourceFactory(default_locale='en', supported_locales=['en'])
        version = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1', match_algorithms=['es'],
            released=False)

        version.match_algorithms = LLM
        version.released = True
        errors = Source.persist_changes(version, version.created_by, None)

        self.assertEqual(errors, {})
        index_released_mock.assert_called_once_with(only_update=True)
        index_concepts_async_mock.assert_called_once_with(version.updated_by, sync_vectors=True)

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    def test_index_concepts_async_passes_sync_vectors(self):
        from core.sources import models as source_models
        index_source_concepts_mock = source_models.index_source_concepts
        source = OrganizationSourceFactory()

        source.index_concepts_async(source.created_by, sync_vectors=True)

        index_source_concepts_mock.apply_async.assert_called_once()
        args, kwargs = index_source_concepts_mock.apply_async.call_args
        self.assertEqual(args, ((source.id, None), {'sync_vectors': True}))
        self.assertEqual(kwargs['queue'], 'indexing')

    @patch('core.common.tasks.sync_concept_vectors')
    def test_index_source_concepts_with_sync_vectors_only_syncs(self, sync_mock):
        source = OrganizationSourceFactory()

        with patch('core.sources.models.Source.batch_index') as batch_index_mock:
            index_source_concepts(source.id, None, sync_vectors=True)

        sync_mock.assert_called_once()
        self.assertEqual(sync_mock.call_args[0][0].id, source.id)
        batch_index_mock.assert_not_called()


class VersionCreateInheritsVectorizationTest(OCLAPITestCase):
    """Decision V1: a new version is vectorized when HEAD or the latest release is, unless the request says not."""
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

    def test_inherits_llm_from_the_latest_semantic_release(self):
        OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.organization, version='v1', released=True,
            match_algorithms=LLM)

        _, stored = self.create()

        self.assertEqual(sorted(stored), LLM)

    def test_does_not_inherit_from_an_older_release_than_the_latest(self):
        OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.organization, version='v1', released=True,
            match_algorithms=LLM)
        OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.organization, version='v2', released=True,
            match_algorithms=['es'])

        _, stored = self.create()

        self.assertEqual(stored, ['es'])

    def test_not_vectorized_when_neither_head_nor_the_latest_release_is(self):
        _, stored = self.create()

        self.assertEqual(stored, ['es'])

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
