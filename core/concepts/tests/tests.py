import threading
from unittest.mock import ANY, Mock, patch

import factory
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404, QueryDict
from django.test import override_settings
from pydash import omit

from core.collections.models import CollectionReference
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import OPENMRS_VALIDATION_SCHEMA, HEAD, ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW, LATEST
from core.common.search import Reranker
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.concepts.constants import (
    OPENMRS_MUST_HAVE_EXACTLY_ONE_PREFERRED_NAME,
    OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE, OPENMRS_AT_LEAST_ONE_FULLY_SPECIFIED_NAME,
    OPENMRS_PREFERRED_NAME_UNIQUE_PER_SOURCE_LOCALE, OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED,
    SHORT, INDEX_TERM, OPENMRS_NAMES_EXCEPT_SHORT_MUST_BE_UNIQUE, OPENMRS_ONE_FULLY_SPECIFIED_NAME_PER_LOCALE,
    OPENMRS_NO_MORE_THAN_ONE_SHORT_NAME_PER_LOCALE, CONCEPT_IS_ALREADY_RETIRED, CONCEPT_IS_ALREADY_NOT_RETIRED,
    OPENMRS_CONCEPT_CLASS, OPENMRS_DATATYPE, OPENMRS_DESCRIPTION_TYPE, OPENMRS_NAME_LOCALE)
from core.concepts.documents import ConceptDocument
from core.concepts.models import AbstractLocalizedText, Concept
from core.concepts.serializers import ConceptListSerializer, ConceptVersionListSerializer, ConceptDetailSerializer, \
    ConceptVersionDetailSerializer, ConceptMinimalSerializer, ConceptCascadeMinimalSerializer, \
    ConceptLocaleSerializer, ConceptDescriptionSerializer, ConceptLookupListSerializer, \
    ConceptVersionExportSerializer, ConceptChildrenSerializer, ConceptParentsSerializer
from core.concepts.tests.factories import ConceptNameFactory, ConceptFactory, ConceptDescriptionFactory
from core.concepts.validators import ValidatorSpecifier
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class ConceptViewsAPITest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.admin_token = self.admin.get_token()
        self.source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)

    def test_list_checksums_brief(self):
        response = self.client.get(
            f'{self.source.uri}concepts/?brief=true&checksums=true',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_list_only_hierarchy_root(self):
        response = self.client.get(
            f'{self.source.uri}concepts/?onlyHierarchyRoot=true',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_list_only_parent_less(self):
        response = self.client.get(
            f'{self.source.uri}concepts/?onlyParentLess=true',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_list_non_head_source_version(self):
        source_v1 = OrganizationSourceFactory(
            organization=self.source.organization, mnemonic=self.source.mnemonic, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        concept = ConceptFactory(parent=self.source)
        concept.sources.add(source_v1)

        response = self.client.get(
            f'{source_v1.uri}concepts/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 200)

    def test_list_anonymous_excludes_private(self):
        response = self.client.get('/concepts/')
        self.assertEqual(response.status_code, 200)

    def test_list_authenticated_non_staff_applies_user_criteria(self):
        user = UserProfileFactory()
        response = self.client.get('/concepts/', HTTP_AUTHORIZATION=f"Token {user.get_token()}")
        self.assertEqual(response.status_code, 200)

    def test_list_fuzzy_search_with_source_version(self):
        response = self.client.get(
            f'{self.source.uri}concepts/?fuzzy=true&q=test&source_version={self.source.uri}',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_post_list_data_404(self):
        response = self.client.post(
            f'{self.source.uri}concepts/', [], format='json', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_summary_view_collection_kwarg_405(self):
        from core.concepts.views import ConceptSummaryView
        view = ConceptSummaryView()
        view.kwargs = {'collection': 'some-collection'}

        response = view.get_object()

        self.assertEqual(response.status_code, 405)

    def test_summary_view_not_found_404(self):
        response = self.client.get(
            f'{self.source.uri}concepts/does-not-exist/summary/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_collection_membership_not_found_404(self):
        response = self.client.get(
            f'{self.source.uri}concepts/does-not-exist/collection-versions/',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_update_version_specified_405(self):
        source_v1 = OrganizationSourceFactory(
            organization=self.source.organization, mnemonic=self.source.mnemonic, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        concept = ConceptFactory(parent=self.source)
        concept.sources.add(source_v1)

        response = self.client.put(
            f'{source_v1.uri}concepts/{concept.mnemonic}/', {}, format='json',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 405)

    def test_update_parent_not_latest_400(self):
        from core.concepts.constants import PARENT_VERSION_NOT_LATEST_CANNOT_UPDATE_CONCEPT
        from core.concepts.views import ConceptRetrieveUpdateDestroyView

        stale_parent = Mock()
        stale_parent.head = Mock()
        concept_mock = Mock(parent=stale_parent)
        view = ConceptRetrieveUpdateDestroyView()
        view.kwargs = {}
        view.get_object = Mock(return_value=concept_mock)
        view.request = Mock(data={})

        response = view.update(view.request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data, {'non_field_errors': PARENT_VERSION_NOT_LATEST_CANNOT_UPDATE_CONCEPT})

    def test_db_hard_delete_not_found_404(self):
        response = self.client.delete(
            f'{self.source.uri}concepts/does-not-exist/?hardDelete=true&db=true',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_hard_delete_versioned_concept_not_found_404(self):
        from core.concepts.views import ConceptRetrieveUpdateDestroyView
        concept = ConceptFactory(parent=self.source)
        view = ConceptRetrieveUpdateDestroyView()
        view.request = Mock(user=self.admin)

        with patch('core.concepts.views.Concept.objects.select_for_update') as select_for_update_mock:
            select_for_update_mock.return_value.filter.return_value = []
            with self.assertRaises(Http404):
                view._hard_delete(view.request, concept)  # pylint: disable=protected-access

    def test_cascade_uri_param_filters(self):
        concept = ConceptFactory(parent=self.source)

        response = self.client.get(
            f'{self.source.uri}concepts/{concept.mnemonic}/cascade/?uri={self.source.uri}',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_cascade_not_found_404(self):
        response = self.client.get(
            f'{self.source.uri}concepts/does-not-exist/cascade/',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_children_not_found_404(self):
        response = self.client.get(
            f'{self.source.uri}concepts/does-not-exist/children/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_parents_not_found_404(self):
        response = self.client.get(
            f'{self.source.uri}concepts/does-not-exist/parents/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_reactivate_not_found_404(self):
        response = self.client.put(
            f'{self.source.uri}concepts/does-not-exist/reactivate/', {}, format='json',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_versions_not_found_404(self):
        response = self.client.get(
            f'{self.source.uri}concepts/does-not-exist/versions/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_mappings_not_found_404(self):
        response = self.client.get(
            f'{self.source.uri}concepts/does-not-exist/mappings/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_names_list_not_found_404(self):
        response = self.client.get(
            f'{self.source.uri}concepts/does-not-exist/names/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_label_list_create_view_no_parent_list_attribute(self):
        from core.concepts.views import ConceptLabelListCreateView
        view = ConceptLabelListCreateView()
        view.parent_list_attribute = None

        self.assertIsNone(view.get_queryset())

    def test_locale_retrieve_update_destroy_view_no_parent_list_attribute(self):
        from core.concepts.views import ConceptLocaleRetrieveUpdateDestroyView
        view = ConceptLocaleRetrieveUpdateDestroyView()
        view.parent_list_attribute = None

        self.assertIsNone(view.get_queryset())

    def test_create_description(self):
        concept = ConceptFactory(parent=self.source)
        ConceptNameFactory(concept=concept)

        response = self.client.post(
            f'{self.source.uri}concepts/{concept.mnemonic}/descriptions/',
            {'description': 'Some description', 'locale': 'en'}, format='json',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 201)

    def test_create_description_errors_400(self):
        concept = ConceptFactory(parent=self.source)

        with patch('core.concepts.models.Concept.save_as_new_version') as save_mock:
            save_mock.return_value = {'errors': ['some error']}
            response = self.client.post(
                f'{self.source.uri}concepts/{concept.mnemonic}/descriptions/',
                {'description': 'Some description', 'locale': 'en'}, format='json',
                HTTP_AUTHORIZATION=f"Token {self.admin_token}"
            )

        self.assertEqual(response.status_code, 400)

    def test_update_name_invalid_400(self):
        concept = ConceptFactory(parent=self.source)
        name = ConceptNameFactory(concept=concept)

        response = self.client.put(
            f'{self.source.uri}concepts/{concept.mnemonic}/names/{name.id}/', {'name': ''}, format='json',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 400)

    @patch('core.concepts.views.Reranker')
    def test_rerank_concepts_success(self, reranker_mock):
        from core.users.constants import MAPPER_APPROVED_GROUP
        from django.contrib.auth.models import Group
        reranker_instance_mock = Mock()
        reranker_instance_mock.rerank.return_value = [{'id': 1}]
        reranker_mock.return_value = reranker_instance_mock
        user = UserProfileFactory()
        user.groups.add(Group.objects.get_or_create(name=MAPPER_APPROVED_GROUP)[0])

        response = self.client.post(
            '/concepts/$rerank/', {'rows': [{'id': 1}], 'q': 'some text'}, format='json',
            HTTP_AUTHORIZATION=f"Token {user.get_token()}"
        )

        self.assertEqual(response.status_code, 200)

    def test_rerank_concepts_waitlisted_403(self):
        user = UserProfileFactory()

        response = self.client.post(
            '/concepts/$rerank/', {'rows': [{'id': 1}], 'q': 'some text'}, format='json',
            HTTP_AUTHORIZATION=f"Token {user.get_token()}"
        )

        self.assertEqual(response.status_code, 403)

    def test_rerank_concepts_missing_rows_400(self):
        from core.users.constants import MAPPER_APPROVED_GROUP
        from django.contrib.auth.models import Group
        user = UserProfileFactory()
        user.groups.add(Group.objects.get_or_create(name=MAPPER_APPROVED_GROUP)[0])

        response = self.client.post(
            '/concepts/$rerank/', {'rows': [], 'q': 'some text'}, format='json',
            HTTP_AUTHORIZATION=f"Token {user.get_token()}"
        )

        self.assertEqual(response.status_code, 400)

    def test_rerank_concepts_missing_query_400(self):
        from core.users.constants import MAPPER_APPROVED_GROUP
        from django.contrib.auth.models import Group
        user = UserProfileFactory()
        user.groups.add(Group.objects.get_or_create(name=MAPPER_APPROVED_GROUP)[0])

        response = self.client.post(
            '/concepts/$rerank/', {'rows': [{'id': 1}]}, format='json',
            HTTP_AUTHORIZATION=f"Token {user.get_token()}"
        )

        self.assertEqual(response.status_code, 400)

    @patch('core.concepts.views.Reranker')
    def test_rerank_concepts_reranker_error_400(self, reranker_mock):
        from core.users.constants import MAPPER_APPROVED_GROUP
        from django.contrib.auth.models import Group
        reranker_mock.side_effect = ValueError('bad model')
        user = UserProfileFactory()
        user.groups.add(Group.objects.get_or_create(name=MAPPER_APPROVED_GROUP)[0])

        response = self.client.post(
            '/concepts/$rerank/', {'rows': [{'id': 1}], 'q': 'some text'}, format='json',
            HTTP_AUTHORIZATION=f"Token {user.get_token()}"
        )

        self.assertEqual(response.status_code, 400)

    @patch('core.concepts.views.Reranker')
    def test_rerank_concepts_unexpected_error_500(self, reranker_mock):
        from core.users.constants import MAPPER_APPROVED_GROUP
        from django.contrib.auth.models import Group
        reranker_instance_mock = Mock()
        reranker_instance_mock.rerank.side_effect = KeyError('boom')
        reranker_mock.return_value = reranker_instance_mock
        user = UserProfileFactory()
        user.groups.add(Group.objects.get_or_create(name=MAPPER_APPROVED_GROUP)[0])

        response = self.client.post(
            '/concepts/$rerank/', {'rows': [{'id': 1}], 'q': 'some text'}, format='json',
            HTTP_AUTHORIZATION=f"Token {user.get_token()}"
        )

        self.assertEqual(response.status_code, 500)


class LocalizedTextTest(OCLTestCase):
    def test_clone(self):
        saved_locale = ConceptNameFactory.build()
        cloned_locale = saved_locale.clone()
        self.assertEqual(
            omit(saved_locale.__dict__, ['_state', 'id', 'created_at']),
            omit(cloned_locale.__dict__, ['_state', 'id', 'created_at'])
        )

    def test_build_base_is_a_noop(self):
        # AbstractLocalizedText._build is only reached when a concrete subclass fails to override it.
        self.assertIsNone(AbstractLocalizedText._build({}))  # pylint: disable=protected-access

    def test_is_fully_specified_after_clean(self):
        self.assertTrue(ConceptNameFactory.build(type='Fully Specified').is_fully_specified_after_clean)
        self.assertFalse(ConceptNameFactory.build(type=None).is_fully_specified_after_clean)


class ConceptTest(OCLTestCase):
    def test_concept(self):
        self.assertEqual(Concept().concept, '')
        self.assertEqual(Concept(mnemonic='foobar').concept, 'foobar')

    def test_get_search_document(self):
        self.assertEqual(Concept.get_search_document(), ConceptDocument)

    def test_is_versioned(self):
        self.assertTrue(Concept().is_versioned)

    def test_display_name(self):
        source = OrganizationSourceFactory(default_locale='fr', supported_locales=['fr', 'ti'])
        concept = ConceptFactory(
            parent=source, names=1, names__locale_preferred=True, names__locale='ch', names__name='ch')
        en_locale = ConceptNameFactory(locale_preferred=True, locale='en', concept=concept, name='en')

        self.assertEqual(concept.display_name, en_locale.name)  # locale preferred order by created at desc

        source.supported_locales = ['fr', 'ti', 'ch']
        source.save()
        self.assertEqual(concept.display_name, 'ch')  # locale preferred parent's supported locale

        # taking scenarios for ciel 1366 concept
        concept = ConceptFactory(
            parent=source,
            names=[
                ConceptNameFactory.build(locale_preferred=True, locale='en', name='MALARIA SMEAR, QUALITATIVE'),
                ConceptNameFactory.build(type='SHORT', locale_preferred=False, locale='en', name='malaria sm, qual'),
                ConceptNameFactory.build(locale_preferred=False, locale='en', name='Jungle fever smear'),
                ConceptNameFactory.build(locale_preferred=True, locale='fr', name='FROTTIS POUR DÉTECTER PALUDISME'),
                ConceptNameFactory.build(locale_preferred=False, locale='ht', name='tès MALARYA , kalitatif'),
                ConceptNameFactory.build(locale_preferred=False, locale='es', name='frotis de malaria (cualitativo)'),
                ConceptNameFactory.build(locale_preferred=False, locale='es', name='Frotis de paludismo'),
            ]
        )

        source.default_locale = 'en'
        source.supported_locales = ['en']
        source.save()
        self.assertEqual(concept.display_name, 'MALARIA SMEAR, QUALITATIVE')

        source.default_locale = 'fr'
        source.supported_locales = ['fr', 'en']
        source.save()
        self.assertEqual(concept.display_name, 'FROTTIS POUR DÉTECTER PALUDISME')

        source.default_locale = 'es'
        source.supported_locales = ['es']
        source.save()
        self.assertEqual(concept.display_name, 'Frotis de paludismo')

        source.default_locale = 'ht'
        source.supported_locales = ['ht', 'en']
        source.save()
        self.assertEqual(concept.display_name, 'tès MALARYA , kalitatif')

        source.default_locale = 'ti'
        source.supported_locales = ['ti']
        source.save()
        self.assertEqual(concept.display_name, 'MALARIA SMEAR, QUALITATIVE')  # system default locale = en

        source.default_locale = 'ti'
        source.supported_locales = ['ti', 'en']
        source.save()
        self.assertEqual(concept.display_name, 'MALARIA SMEAR, QUALITATIVE')

    def test_display_locale(self):
        preferred_locale = ConceptNameFactory.build(locale_preferred=True)
        concept = ConceptFactory(names=(preferred_locale,))

        self.assertEqual(concept.display_locale, preferred_locale.locale)

    @patch.object(Reranker, '_get_encoder_state')
    def test_reranker_uses_finite_fallback_score_for_missing_candidate_text(self, get_encoder_state_mock):
        get_encoder_state_mock.return_value = {'encoder': None, 'predict_lock': Mock()}
        reranker = Reranker()

        scores = reranker._predict_scores(  # pylint: disable=protected-access
            hits=[{'_source': {'name': '  '}}, {'_source': {'name': None}}],
            txt='malaria',
            name_key='name',
            source_attr='_source',
            should_convert_source_to_dict=True,
        )

        self.assertEqual(scores, [Reranker.MISSING_SCORE, Reranker.MISSING_SCORE])

    @patch.object(Reranker, '_get_encoder_state')
    def test_reranker_uses_sigmoid_activation_for_qwen_models(self, get_encoder_state_mock):
        encoder_mock = Mock()
        encoder_mock.predict.return_value = [0.42]
        get_encoder_state_mock.return_value = {'encoder': encoder_mock, 'predict_lock': threading.Lock()}
        reranker = Reranker(model_name='Qwen/Qwen3-Reranker-0.6B')

        scores = reranker._predict_scores(  # pylint: disable=protected-access
            hits=[{'_source': {'name': 'malaria test'}}],
            txt='malaria',
            name_key='name',
            source_attr='_source',
            should_convert_source_to_dict=True,
        )

        self.assertEqual(scores, [0.42])
        _, kwargs = encoder_mock.predict.call_args
        self.assertIsNotNone(kwargs['activation_fn'])
        self.assertEqual(kwargs['activation_fn'].__class__.__name__, 'Sigmoid')

    @override_settings(RERANKER_SIGMOID_MODEL_PREFIXES=['myorg/qwen3-reranker-ft'])
    @patch.object(Reranker, '_get_encoder_state')
    def test_reranker_uses_configured_sigmoid_prefixes(self, get_encoder_state_mock):
        encoder_mock = Mock()
        encoder_mock.predict.return_value = [0.42]
        get_encoder_state_mock.return_value = {'encoder': encoder_mock, 'predict_lock': threading.Lock()}
        reranker = Reranker(model_name='myorg/qwen3-reranker-ft-v1')

        scores = reranker._predict_scores(  # pylint: disable=protected-access
            hits=[{'_source': {'name': 'malaria test'}}],
            txt='malaria',
            name_key='name',
            source_attr='_source',
            should_convert_source_to_dict=True,
        )

        self.assertEqual(scores, [0.42])
        _, kwargs = encoder_mock.predict.call_args
        self.assertIsNotNone(kwargs['activation_fn'])
        self.assertEqual(kwargs['activation_fn'].__class__.__name__, 'Sigmoid')

    @override_settings(RERANKER_CUSTOM_ENCODER_CACHE_SIZE=1, RERANKER_CUSTOM_ENCODER_CACHE_TTL=300)
    @patch.object(Reranker, '_load_encoder')
    def test_custom_reranker_encoder_is_cached_between_requests(self, load_encoder_mock):
        encoder = Mock()
        load_encoder_mock.return_value = encoder
        Reranker.CUSTOM_ENCODER_CACHE.clear()

        first = Reranker(model_name='Qwen/Qwen3-Reranker-0.6B')
        second = Reranker(model_name='Qwen/Qwen3-Reranker-0.6B')

        self.assertIs(first.encoder, second.encoder)
        load_encoder_mock.assert_called_once_with('Qwen/Qwen3-Reranker-0.6B')
        Reranker.CUSTOM_ENCODER_CACHE.clear()

    @override_settings(RERANKER_CUSTOM_ENCODER_CACHE_SIZE=1, RERANKER_CUSTOM_ENCODER_CACHE_TTL=300)
    @patch.object(Reranker, '_release_memory')
    @patch.object(Reranker, '_load_encoder')
    def test_custom_reranker_encoder_eviction_releases_previous_model(self, load_encoder_mock, release_memory_mock):
        old_encoder = Mock()
        new_encoder = Mock()
        load_encoder_mock.side_effect = [old_encoder, new_encoder]
        Reranker.CUSTOM_ENCODER_CACHE.clear()

        first = Reranker(model_name='Qwen/Qwen3-Reranker-0.6B')
        second = Reranker(model_name='BAAI/bge-reranker-v2-m3-custom')

        self.assertIs(first.encoder, old_encoder)
        self.assertIs(second.encoder, new_encoder)
        release_memory_mock.assert_called_once_with()
        self.assertEqual(list(Reranker.CUSTOM_ENCODER_CACHE.keys()), ['BAAI/bge-reranker-v2-m3-custom'])
        Reranker.CUSTOM_ENCODER_CACHE.clear()

    @override_settings(RERANKER_CUSTOM_ENCODER_CACHE_SIZE=1, RERANKER_CUSTOM_ENCODER_CACHE_TTL=10)
    @patch.object(Reranker, '_release_memory')
    @patch.object(Reranker, '_load_encoder')
    def test_custom_reranker_encoder_ttl_expiry_reloads_model(
            self, load_encoder_mock, release_memory_mock):
        first_encoder = Mock()
        second_encoder = Mock()
        load_encoder_mock.side_effect = [first_encoder, second_encoder]
        Reranker.CUSTOM_ENCODER_CACHE.clear()

        first = Reranker(model_name='Qwen/Qwen3-Reranker-0.6B')
        Reranker.CUSTOM_ENCODER_CACHE['Qwen/Qwen3-Reranker-0.6B']['expires_at'] = 0
        second = Reranker(model_name='Qwen/Qwen3-Reranker-0.6B')

        self.assertIs(first.encoder, first_encoder)
        self.assertIs(second.encoder, second_encoder)
        release_memory_mock.assert_called_once_with()
        self.assertEqual(load_encoder_mock.call_count, 2)
        Reranker.CUSTOM_ENCODER_CACHE.clear()

    def test_default_name_locales(self):
        es_locale = ConceptNameFactory.build(locale='es')
        en_locale = ConceptNameFactory.build(locale='en')
        concept = ConceptFactory(names=(es_locale, en_locale))

        default_name_locales = concept.default_name_locales

        self.assertEqual(default_name_locales.count(), 1)
        self.assertEqual(default_name_locales.first(), en_locale)

    def test_default_description_locales(self):
        es_locale = ConceptDescriptionFactory.build(locale='es')
        en_locale = ConceptDescriptionFactory.build(locale='en')
        concept = ConceptFactory(descriptions=(es_locale, en_locale))

        default_description_locales = concept.default_description_locales

        self.assertEqual(default_description_locales.count(), 1)
        self.assertEqual(default_description_locales.first(), en_locale)

    def test_names_for_default_locale(self):
        es_locale = ConceptNameFactory.build(locale='es', name='Not English')
        en_locale = ConceptNameFactory.build(locale='en', name='English')
        concept = ConceptFactory(names=(es_locale, en_locale))

        self.assertEqual(concept.names_for_default_locale, [en_locale.name])

    def test_descriptions_for_default_locale(self):
        es_locale = ConceptDescriptionFactory.build(locale='es', name='Not English')
        en_locale = ConceptDescriptionFactory.build(locale='en', name='English')
        concept = ConceptFactory(descriptions=(es_locale, en_locale))

        self.assertEqual(concept.descriptions_for_default_locale, [en_locale.name])

    def test_persist_new(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.version, str(concept.id))
        self.assertEqual(source.concepts_set.count(), 2)
        self.assertEqual(source.concepts.count(), 2)
        self.assertEqual(
            concept.uri,
            f'/orgs/{source.organization.mnemonic}/sources/{source.mnemonic}/concepts/{concept.mnemonic}/'
        )

    @patch('core.concepts.models.process_hierarchy_for_new_concept')
    def test_persist_new_with_skip_hierarchy_tasks_flag(self, process_hierarchy_mock):
        source = OrganizationSourceFactory(version=HEAD)
        parent_concept = ConceptFactory(parent=source)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri],
            '_skip_hierarchy_tasks': True,
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        process_hierarchy_mock.assert_not_called()

    def test_persist_new_with_autoid_sequential(self):
        source = OrganizationSourceFactory(
            version=HEAD, autoid_concept_mnemonic='sequential', autoid_concept_external_id='sequential')
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '1')
        self.assertEqual(concept.external_id, '1')

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '2')
        self.assertEqual(concept.external_id, '2')

        for concept in Concept.objects.filter(mnemonic='1'):
            concept.delete()

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory),
            'parent': source,
            'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '3')
        self.assertEqual(concept.external_id, '3')

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory),
            'mnemonic': '1',
            'external_id': '1',
            'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '1')
        self.assertEqual(concept.external_id, '1')

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory),
            'mnemonic': None,
            'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '4')
        self.assertEqual(concept.external_id, '4')

        source.autoid_concept_mnemonic_start_from = 100
        source.save()

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory),
            'mnemonic': None,
            'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '100')
        self.assertEqual(concept.external_id, '5')

        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory),
            'mnemonic': None,
            'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)
        self.assertEqual(concept.mnemonic, '101')
        self.assertEqual(concept.external_id, '6')

    def test_persist_new_with_autoid_uuid(self):
        source = OrganizationSourceFactory(
            version=HEAD, autoid_concept_mnemonic='uuid', autoid_concept_external_id='uuid')
        concept1 = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept1.errors, {})
        self.assertIsNotNone(concept1.id)
        self.assertTrue(len(concept1.mnemonic), 36)
        self.assertTrue(len(concept1.external_id), 36)

        concept2 = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept2.errors, {})
        self.assertIsNotNone(concept2.id)
        self.assertTrue(len(concept2.mnemonic), 36)
        self.assertTrue(len(concept2.external_id), 36)
        self.assertIsNone(concept2.names.first().external_id)

        self.assertNotEqual(concept1.mnemonic, concept2.mnemonic)
        self.assertNotEqual(concept1.external_id, concept2.external_id)

    def test_persist_new_with_locale_autoid_uuid(self):
        source = OrganizationSourceFactory(
            version=HEAD, autoid_concept_mnemonic='uuid', autoid_concept_external_id='uuid',
            autoid_concept_name_external_id='uuid', autoid_concept_description_external_id='uuid'
        )
        concept1 = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'descriptions': [ConceptDescriptionFactory.build(locale='en', name='English', locale_preferred=True)]
        })

        self.assertEqual(concept1.errors, {})
        self.assertIsNotNone(concept1.id)
        self.assertTrue(len(concept1.mnemonic), 36)
        self.assertTrue(len(concept1.external_id), 36)
        self.assertTrue(len(concept1.names.first().external_id), 36)
        self.assertTrue(len(concept1.descriptions.first().external_id), 36)

        concept2 = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [
                ConceptNameFactory.build(locale='en', name='English', locale_preferred=True, external_id=None)
            ],
            'descriptions': [
                ConceptDescriptionFactory.build(locale='en', name='English', locale_preferred=True, external_id=None)
            ]
        })

        self.assertEqual(concept2.errors, {})
        self.assertIsNotNone(concept2.id)
        self.assertTrue(len(concept2.mnemonic), 36)
        self.assertTrue(len(concept2.external_id), 36)
        self.assertTrue(len(concept2.names.first().external_id), 36)
        self.assertTrue(len(concept2.descriptions.first().external_id), 36)

        self.assertNotEqual(concept1.mnemonic, concept2.mnemonic)
        self.assertNotEqual(concept1.external_id, concept2.external_id)
        self.assertNotEqual(concept1.names.first().external_id, concept2.names.first().external_id)
        self.assertNotEqual(concept1.descriptions.first().external_id, concept2.descriptions.first().external_id)

        concept3 = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'parent': source, 'mnemonic': None,
            'names': [
                ConceptNameFactory.build(
                    locale='en', name='English', locale_preferred=True, external_id='name-ext-id')
            ],
            'descriptions': [
                ConceptDescriptionFactory.build(
                    locale='en', name='English', locale_preferred=True, external_id='desc-ext-id')
            ]
        })

        self.assertEqual(concept3.errors, {})
        self.assertTrue(concept3.names.first().external_id, 'name-ext-id')
        self.assertTrue(concept3.descriptions.first().external_id, 'desc-ext-id')

    def test_hierarchy_one_parent_child(self):
        parent_concept = ConceptFactory(
            names=[ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)])
        source = parent_concept.parent
        child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertEqual(child_concept.errors, {})
        self.assertIsNotNone(child_concept.id)
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])
        self.assertEqual(list(parent_concept_latest_version.child_concept_urls), [child_concept.uri])
        self.assertEqual(list(parent_concept_latest_version.prev_version.child_concept_urls), [])

        another_child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c2', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertEqual(another_child_concept.errors, {})
        self.assertIsNotNone(another_child_concept.id)
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(another_child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(another_child_concept.get_latest_version().parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(another_child_concept.child_concept_urls), [])
        self.assertEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri, another_child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri, another_child_concept.uri])
        )
        self.assertEqual(list(parent_concept_latest_version.prev_version.child_concept_urls), [child_concept.uri])

    def test_hierarchy(self):  # pylint: disable=too-many-statements
        # Av1
        parent_concept = ConceptFactory(
            mnemonic='A', names=[ConceptNameFactory.build(locale='en', name='Av1', locale_preferred=True)])
        self.assertEqual(parent_concept.versions.count(), 1)
        source = parent_concept.parent

        # Av1 -> None and Av2 -> Bv1
        child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'B', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='Bv1', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertEqual(child_concept.errors, {})
        self.assertIsNotNone(child_concept.id)
        self.assertEqual(parent_concept.versions.count(), 2)
        self.assertEqual(child_concept.versions.count(), 1)
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])
        self.assertEqual(list(parent_concept_latest_version.child_concept_urls), [child_concept.uri])
        self.assertEqual(list(parent_concept_latest_version.prev_version.child_concept_urls), [])

        # Av1 -> None and Av2 -> Bv1,Bv2 and Bv2 -> Cv1
        child_child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'C', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='Cv1', locale_preferred=True)],
            'parent_concept_urls': [child_concept.uri]
        })

        self.assertEqual(child_child_concept.errors, {})
        self.assertIsNotNone(child_child_concept.id)
        self.assertEqual(parent_concept.versions.count(), 2)
        self.assertEqual(child_concept.versions.count(), 2)
        self.assertEqual(child_child_concept.versions.count(), 1)
        self.assertEqual(list(child_child_concept.parent_concept_urls), [child_concept.uri])
        self.assertEqual(list(child_child_concept.get_latest_version().parent_concept_urls), [child_concept.uri])
        self.assertEqual(list(child_child_concept.child_concept_urls), [])
        self.assertEqual(list(child_concept.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])
        # Av1 -> None and Av2 -> Bv1,Bv2 -> Cv1 and Av3 -> Bv3,Cv2
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [parent_concept.uri],
                'names': [{'locale': 'en', 'name': 'Cv2', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by
        )

        self.assertEqual(parent_concept.versions.count(), 3)
        self.assertEqual(child_concept.versions.count(), 3)
        self.assertEqual(child_child_concept.versions.count(), 2)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(list(child_child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_child_latest_version.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_child_latest_version.prev_version.parent_concept_urls), [child_concept.url])

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )
        self.assertEqual(list(parent_concept_latest_version.prev_version.child_concept_urls), [child_concept.url])

        child_latest_version = child_concept.get_latest_version()

        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.child_concept_urls), [])

        self.assertEqual(list(child_latest_version.prev_version.child_concept_urls), [child_child_concept.uri])

        # Av1 -> None and Av2 -> Bv1,Bv2 -> Cv1 and Av3 -> Bv3,Cv2 and Av4 -> Bv4 -> Cv3
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [child_concept.uri],
                'names': [{'locale': 'en', 'name': 'Cv3', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by
        )

        self.assertEqual(parent_concept.versions.count(), 4)
        self.assertEqual(child_concept.versions.count(), 4)
        self.assertEqual(child_child_concept.versions.count(), 3)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(
            list(child_child_concept.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.prev_version.parent_concept_urls), [parent_concept.url])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.parent_concept_urls), [child_concept.url])

        child_latest_version = child_concept.get_latest_version()
        self.assertEqual(list(child_concept.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_latest_version.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(
            list(child_latest_version.prev_version.child_concept_urls), []
        )
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.child_concept_urls),
            [child_child_concept.uri]
        )
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.prev_version.child_concept_urls),
            []
        )

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.prev_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )
        self.assertEqual(
            list(parent_concept_latest_version.prev_version.prev_version.child_concept_urls),
            [child_concept.uri]
        )
        self.assertEqual(
            list(parent_concept_latest_version.prev_version.prev_version.prev_version.child_concept_urls),
            []
        )

        # Av1 -> None and Av2 -> Bv1,Bv2 -> Cv1 and Av3 -> Bv3,Cv2 and Av4 -> Bv4 -> Cv3 and Av4 -> Bv5 -> None and Cv4
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [],
                'names': [{'locale': 'en', 'name': 'Cv4', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by
        )

        self.assertEqual(parent_concept.versions.count(), 4)
        self.assertEqual(child_concept.versions.count(), 5)
        self.assertEqual(child_child_concept.versions.count(), 4)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(
            list(child_child_concept.parent_concept_urls), [])
        self.assertEqual(
            list(child_child_latest_version.parent_concept_urls), [])
        self.assertEqual(
            list(child_child_latest_version.prev_version.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.parent_concept_urls), [parent_concept.url])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.prev_version.parent_concept_urls),
            [child_concept.url]
        )

        child_latest_version = child_concept.get_latest_version()
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.prev_version.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.child_concept_urls), []
        )
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.prev_version.child_concept_urls),
            [child_child_concept.uri]
        )
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.prev_version.prev_version.child_concept_urls),
            []
        )

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.prev_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )
        self.assertEqual(
            list(parent_concept_latest_version.prev_version.prev_version.child_concept_urls),
            [child_concept.uri]
        )
        self.assertEqual(
            list(parent_concept_latest_version.prev_version.prev_version.prev_version.child_concept_urls),
            []
        )

    def test_hierarchy_without_multiple_parent_versions(self):  # pylint: disable=too-many-statements
        # Av1
        parent_concept = ConceptFactory(mnemonic='A',
            names=[ConceptNameFactory.build(locale='en', name='Av1', locale_preferred=True)])
        self.assertEqual(parent_concept.versions.count(), 1)
        self.assertEqual(list(parent_concept.get_latest_version().child_concept_urls), [])
        source = parent_concept.parent

        # Av1 to Av1 -> Bv1
        child_concept = Concept.persist_new(data={
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'B', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='Bv1', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        }, create_parent_version=False)

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertEqual(child_concept.errors, {})
        self.assertIsNotNone(child_concept.id)
        self.assertEqual(parent_concept.versions.count(), 1)
        self.assertEqual(child_concept.versions.count(), 1)
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])
        self.assertEqual(list(parent_concept_latest_version.child_concept_urls), [child_concept.uri])

        # Av1 to Av1 -> Bv1 to Av1 -> Bv1 -> Cv1
        child_child_concept = Concept.persist_new(data={
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'C', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='Cv1', locale_preferred=True)],
            'parent_concept_urls': [child_concept.uri]
        }, create_parent_version=False)

        self.assertEqual(child_child_concept.errors, {})
        self.assertIsNotNone(child_child_concept.id)
        self.assertEqual(parent_concept.versions.count(), 1)
        self.assertEqual(child_concept.versions.count(), 1)
        self.assertEqual(child_child_concept.versions.count(), 1)
        self.assertEqual(list(child_child_concept.parent_concept_urls), [child_concept.uri])
        self.assertEqual(list(child_child_concept.get_latest_version().parent_concept_urls), [child_concept.uri])
        self.assertEqual(list(child_child_concept.child_concept_urls), [])
        self.assertEqual(list(child_concept.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_concept.get_latest_version().parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])

        # Av1 to Av1 -> Bv1 to Av1 -> Bv1 -> Cv1 to Av1 -> Bv2,Cv2 and Bv1 -> Cv1
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [parent_concept.uri],
                'names': [{'locale': 'en', 'name': 'Cv2', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by,
            create_parent_version=False
        )

        self.assertEqual(parent_concept.versions.count(), 1)
        self.assertEqual(child_concept.versions.count(), 2)
        self.assertEqual(child_child_concept.versions.count(), 2)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(list(child_child_concept.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_child_latest_version.parent_concept_urls), [parent_concept.uri])
        self.assertEqual(list(child_child_latest_version.prev_version.parent_concept_urls), [child_concept.url])

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )

        child_latest_version = child_concept.get_latest_version()
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.prev_version.child_concept_urls), [child_child_concept.uri])

        # Av1 -> Bv1 -> Cv1 to Av1 -> Bv2,Cv2 and Bv1 -> Cv1 to Av2 -> Bv2 -> Cv3 and Av1 -> Bv1, Cv2 and Bv1 -> Cv1
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [child_concept.uri],
                'names': [{'locale': 'en', 'name': 'Cv3', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by,
            create_parent_version=False
        )

        self.assertEqual(parent_concept.versions.count(), 2)
        self.assertEqual(child_concept.versions.count(), 2)
        self.assertEqual(child_child_concept.versions.count(), 3)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(
            list(child_child_concept.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.prev_version.parent_concept_urls), [parent_concept.url])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.parent_concept_urls), [child_concept.url])

        child_latest_version = child_concept.get_latest_version()
        self.assertEqual(list(child_concept.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(list(child_latest_version.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(
            list(child_latest_version.prev_version.child_concept_urls), [child_child_concept.uri]
        )
        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.prev_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )

        # Av1 -> Bv1 -> Cv1 to Av1 -> Bv2,Cv2 and Bv1 -> Cv1 to Av2 -> Bv2 -> Cv3 and Av1 -> Bv1, Cv2 and Bv1 -> Cv1 to
        # Av2 -> Bv3 and Bv2 -> Cv3 and Av1 -> Bv1, Cv2 and Bv1 -> Cv1 and Cv4
        Concept.create_new_version_for(
            instance=child_child_concept.clone(),
            data={
                'parent_concept_urls': [],
                'names': [{'locale': 'en', 'name': 'Cv4', 'locale_preferred': True}]
            },
            user=child_child_concept.created_by,
            create_parent_version=False
        )

        self.assertEqual(parent_concept.versions.count(), 2)
        self.assertEqual(child_concept.versions.count(), 3)
        self.assertEqual(child_child_concept.versions.count(), 4)

        child_child_latest_version = child_child_concept.get_latest_version()
        self.assertEqual(
            list(child_child_concept.parent_concept_urls), [])
        self.assertEqual(
            list(child_child_latest_version.parent_concept_urls), [])
        self.assertEqual(
            list(child_child_latest_version.prev_version.parent_concept_urls), [child_concept.uri])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.parent_concept_urls), [parent_concept.url])
        self.assertEqual(
            list(child_child_latest_version.prev_version.prev_version.prev_version.parent_concept_urls),
            [child_concept.url]
        )

        child_latest_version = child_concept.get_latest_version()
        self.assertEqual(list(child_concept.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.child_concept_urls), [])
        self.assertEqual(list(child_latest_version.prev_version.child_concept_urls), [child_child_concept.uri])
        self.assertEqual(
            list(child_latest_version.prev_version.prev_version.child_concept_urls), [child_child_concept.uri]
        )

        parent_concept_latest_version = parent_concept.get_latest_version()
        self.assertListEqual(
            sorted(list(parent_concept.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.child_concept_urls)),
            sorted([child_concept.uri])
        )
        self.assertEqual(
            sorted(list(parent_concept_latest_version.prev_version.child_concept_urls)),
            sorted([child_concept.uri, child_child_concept.uri])
        )

    def test_clone(self):
        en_locale = ConceptNameFactory.build(locale='en', name='English')
        es_locale_description = ConceptDescriptionFactory.build(locale='es', name='Not English')
        en_locale_description = ConceptDescriptionFactory.build(locale='en', name='English')

        concept = ConceptFactory(
            descriptions=(es_locale_description, en_locale_description), names=(en_locale,), released=True)
        cloned_concept = concept.clone()

        self.assertTrue(cloned_concept.version.startswith('--TEMP--'))
        self.assertEqual(cloned_concept.mnemonic, concept.mnemonic)
        self.assertEqual(cloned_concept.parent, concept.parent)
        self.assertEqual(len(cloned_concept.cloned_names), concept.names.count())
        self.assertEqual(len(cloned_concept.cloned_descriptions), concept.descriptions.count())
        self.assertTrue(cloned_concept.released)

    def test_version_for_concept(self):
        concept = ConceptFactory(released=True)
        source = OrganizationSourceFactory()

        concept_version = Concept.version_for_concept(concept, 'v1.0', source)

        self.assertEqual(concept_version.parent, source)
        self.assertEqual(concept_version.version, 'v1.0')
        self.assertEqual(concept_version.created_by_id, concept.created_by_id)
        self.assertEqual(concept_version.updated_by_id, concept.updated_by_id)
        self.assertEqual(concept_version.mnemonic, concept.mnemonic)
        self.assertFalse(concept_version.released)

    def test_save_as_new_version(self):
        es_description = ConceptDescriptionFactory.build(locale='es', name='Not English')
        en_description = ConceptDescriptionFactory.build(locale='en', name='English')
        en_name = ConceptNameFactory.build(locale='en', name='English')

        source_head = OrganizationSourceFactory(version=HEAD)
        source_version0 = OrganizationSourceFactory(
            version='v0', mnemonic=source_head.mnemonic, organization=source_head.organization
        )

        self.assertEqual(source_head.versions.count(), 2)

        concept = ConceptFactory(
            descriptions=(es_description, en_description),
            names=(en_name,),
            parent=source_head
        )
        source_version0.concepts.add(concept)
        cloned_concept = Concept.version_for_concept(concept, 'v1', source_head)
        cloned_concept.datatype = 'foobar'

        self.assertEqual(cloned_concept.save_as_new_version(concept.created_by), {})

        persisted_concept = Concept.objects.filter(
            mnemonic=cloned_concept.mnemonic, version=cloned_concept.version
        ).first()
        self.assertEqual(persisted_concept.names.count(), 1)
        self.assertEqual(persisted_concept.descriptions.count(), 2)
        self.assertEqual(persisted_concept.parent, source_head)
        self.assertEqual(persisted_concept.sources.count(), 1)
        self.assertEqual(
            persisted_concept.uri,
            f'/orgs/{source_head.organization.mnemonic}/sources/{source_head.mnemonic}/'
            f'concepts/{persisted_concept.mnemonic}/{persisted_concept.version}/'
        )
        self.assertEqual(
            persisted_concept.version_url, persisted_concept.uri
        )

    def test_retire(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })
        concept_v1 = concept.clone()
        concept_v1.datatype = 'foobar'
        concept_v1.save_as_new_version(concept.created_by)
        concept_v1 = Concept.objects.order_by('-created_at').first()
        concept.refresh_from_db()

        self.assertEqual(concept.versions.count(), 2)
        self.assertFalse(concept.retired)
        self.assertFalse(concept.is_latest_version)
        self.assertTrue(concept.is_versioned_object)
        self.assertTrue(concept_v1.is_latest_version)

        concept_v1.retire(concept_v1.created_by, None, 'Forceful retirement')  # concept will become old/prev version
        concept.refresh_from_db()
        concept_v1.refresh_from_db()

        self.assertFalse(concept_v1.is_latest_version)
        self.assertEqual(concept.versions.count(), 3)
        self.assertTrue(concept.retired)
        latest_version = concept.get_latest_version()
        self.assertTrue(latest_version.retired)
        self.assertEqual(latest_version.retire_reason, 'Forceful retirement')
        self.assertEqual(latest_version.comment, 'Concept was retired')

        self.assertEqual(
            concept.retire(concept.created_by),
            {'__all__': CONCEPT_IS_ALREADY_RETIRED}
        )

    def test_retire_without_retire_reason(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })
        concept_v1 = concept.clone()
        concept_v1.datatype = 'foobar'
        concept_v1.save_as_new_version(concept.created_by)
        concept_v1 = Concept.objects.order_by('-created_at').first()
        concept.refresh_from_db()

        self.assertEqual(concept.versions.count(), 2)
        self.assertFalse(concept.retired)
        self.assertFalse(concept.is_latest_version)
        self.assertTrue(concept.is_versioned_object)
        self.assertTrue(concept_v1.is_latest_version)

        concept_v1.retire(concept_v1.created_by, 'Forceful retirement')  # concept will become old/prev version
        concept.refresh_from_db()
        concept_v1.refresh_from_db()

        self.assertFalse(concept_v1.is_latest_version)
        self.assertEqual(concept.versions.count(), 3)
        self.assertTrue(concept.retired)
        latest_version = concept.get_latest_version()
        self.assertTrue(latest_version.retired)
        self.assertEqual(latest_version.retire_reason, None)
        self.assertEqual(latest_version.comment, 'Forceful retirement')

        self.assertEqual(
            concept.retire(concept.created_by),
            {'__all__': CONCEPT_IS_ALREADY_RETIRED}
        )

    def test_retire_with_default_comment(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
        })
        concept_v1 = concept.clone()
        concept_v1.datatype = 'foobar'
        concept_v1.save_as_new_version(concept.created_by)
        concept_v1 = Concept.objects.order_by('-created_at').first()
        concept.refresh_from_db()

        self.assertEqual(concept.versions.count(), 2)
        self.assertFalse(concept.retired)
        self.assertFalse(concept.is_latest_version)
        self.assertTrue(concept.is_versioned_object)
        self.assertTrue(concept_v1.is_latest_version)

        concept_v1.retire(concept_v1.created_by)  # concept will become old/prev version
        concept.refresh_from_db()
        concept_v1.refresh_from_db()

        self.assertFalse(concept_v1.is_latest_version)
        self.assertEqual(concept.versions.count(), 3)
        self.assertTrue(concept.retired)
        latest_version = concept.get_latest_version()
        self.assertTrue(latest_version.retired)
        self.assertEqual(latest_version.retire_reason, None)
        self.assertEqual(latest_version.comment, 'Concept was retired')

        self.assertEqual(
            concept.retire(concept.created_by),
            {'__all__': CONCEPT_IS_ALREADY_RETIRED}
        )

    def test_unretire(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'retire_reason': 'unwanted', 'retired': True
        })
        concept_v1 = concept.clone()
        concept_v1.datatype = 'foobar'
        concept_v1.save_as_new_version(concept.created_by)
        concept_v1 = Concept.objects.order_by('-created_at').first()
        concept.refresh_from_db()

        self.assertEqual(concept.versions.count(), 2)
        self.assertTrue(concept.retired)
        self.assertFalse(concept.is_latest_version)
        self.assertTrue(concept.is_versioned_object)
        self.assertTrue(concept_v1.is_latest_version)

        concept_v1.unretire(concept.created_by, 'World needs you!')  # concept will become old/prev version
        concept.refresh_from_db()
        concept_v1.refresh_from_db()

        self.assertFalse(concept_v1.is_latest_version)
        self.assertEqual(concept.versions.count(), 3)
        self.assertFalse(concept.retired)
        latest_version = concept.get_latest_version()
        self.assertFalse(latest_version.retired)
        self.assertEqual(latest_version.comment, 'World needs you!')
        self.assertEqual(latest_version.retire_reason, 'unwanted')

        self.assertEqual(
            concept.unretire(concept.created_by),
            {'__all__': CONCEPT_IS_ALREADY_NOT_RETIRED}
        )

    def test_concept_access_changes_with_source(self):
        source = OrganizationSourceFactory(version=HEAD)
        self.assertEqual(source.public_access, ACCESS_TYPE_EDIT)
        concept = ConceptFactory(parent=source, public_access=ACCESS_TYPE_EDIT)

        self.assertEqual(concept.public_access, ACCESS_TYPE_EDIT)

        source.public_access = ACCESS_TYPE_VIEW
        source._should_update_public_access = True  # pylint: disable=protected-access
        source.save()
        concept.refresh_from_db()

        self.assertEqual(source.public_access, ACCESS_TYPE_VIEW)
        self.assertEqual(source.public_access, concept.public_access)

    def test_get_latest_versions_for_queryset(self):  # pylint: disable=too-many-locals
        self.assertEqual(Concept.get_latest_versions_for_queryset(Concept.objects.none()).count(), 0)

        source1 = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source1, mnemonic='common-name-1')
        concept1_latest = concept1.get_latest_version()
        ConceptFactory(version='v1', parent=source1, is_latest_version=False, mnemonic=concept1.mnemonic)

        concept2 = ConceptFactory(parent=source1)
        concept2_latest = concept2.get_latest_version()
        ConceptFactory(version='v1', parent=source1, is_latest_version=False, mnemonic=concept2.mnemonic)

        concept3 = ConceptFactory(parent=source1, mnemonic='common-name-2')
        concept3_latest = concept3.get_latest_version()
        ConceptFactory(version='v1', parent=source1, is_latest_version=False, mnemonic=concept3.mnemonic)

        source2 = OrganizationSourceFactory()

        concept4 = ConceptFactory(parent=source2, mnemonic='common-name-1')
        concept4_latest = concept4.get_latest_version()
        ConceptFactory(version='v1', parent=source2, is_latest_version=False, mnemonic=concept4.mnemonic)

        concept5 = ConceptFactory(parent=source2)
        concept5_latest = concept5.get_latest_version()
        ConceptFactory(version='v1', parent=source2, is_latest_version=False, mnemonic=concept5.mnemonic)

        concept6 = ConceptFactory(parent=source2, mnemonic='common-name-2')
        concept6_latest = concept6.get_latest_version()
        ConceptFactory(version='v1', parent=source2, is_latest_version=False, mnemonic=concept6.mnemonic)

        latest_versions = Concept.get_latest_versions_for_queryset(Concept.objects.filter(parent=source1))

        self.assertEqual(latest_versions.count(), 3)
        self.assertEqual(
            list(latest_versions.order_by('created_at')),
            [concept1_latest, concept2_latest, concept3_latest]
        )

        latest_versions = Concept.get_latest_versions_for_queryset(Concept.objects.filter(parent=source2))

        self.assertEqual(latest_versions.count(), 3)
        self.assertEqual(
            list(latest_versions.order_by('created_at')),
            [concept4_latest, concept5_latest, concept6_latest]
        )

        latest_versions = Concept.get_latest_versions_for_queryset(Concept.objects.filter(mnemonic='common-name-1'))

        self.assertEqual(latest_versions.count(), 2)
        self.assertEqual(
            list(latest_versions.order_by('created_at')),
            [concept1_latest, concept4_latest]
        )

        latest_versions = Concept.get_latest_versions_for_queryset(
            Concept.objects.filter(mnemonic='common-name-2', version='v1')
        )

        self.assertEqual(latest_versions.count(), 2)
        self.assertEqual(
            list(latest_versions.order_by('created_at')),
            [concept3_latest, concept6_latest]
        )

    def test_custom_validation_schema(self):
        from core.sources.models import Source
        self.assertEqual(
            Concept(parent=Source(custom_validation_schema='foobar')).custom_validation_schema,
            'foobar'
        )

    def test_get_mappings(self):   # pylint: disable=too-many-locals
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source1)
        concept2 = ConceptFactory(parent=source1)
        concept3 = ConceptFactory(parent=source2)
        concept4 = ConceptFactory(parent=source2)

        mapping1 = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source1)
        mapping2 = MappingFactory(from_concept=concept1, to_concept=concept3, parent=source1)
        mapping3 = MappingFactory(from_concept=concept1, to_concept=concept3, parent=source2)
        mapping4 = MappingFactory(from_concept=concept4, to_concept=concept1, parent=source1)
        mapping5 = MappingFactory(from_concept=concept4, to_concept=concept1, parent=source2)
        MappingFactory(from_concept=concept1, to_concept=concept2, parent=source2)

        mappings = concept1.get_unidirectional_mappings()
        self.assertCountEqual(list(mappings), [mapping2, mapping1])

        mappings = concept1.get_indirect_mappings()
        self.assertCountEqual(list(mappings), [mapping4])

        mappings = concept1.get_bidirectional_mappings()
        self.assertCountEqual(list(mappings), [mapping4, mapping2, mapping1])

        mappings = concept2.get_unidirectional_mappings()
        self.assertEqual(mappings.count(), 0)

        mappings = concept2.get_indirect_mappings()
        self.assertCountEqual(list(mappings), [mapping1])

        mappings = concept3.get_unidirectional_mappings()
        self.assertEqual(mappings.count(), 0)

        mappings = concept3.get_indirect_mappings()
        self.assertCountEqual(list(mappings), [mapping3])

        mappings = concept4.get_unidirectional_mappings()
        self.assertCountEqual(list(mappings), [mapping5])

        mappings = concept4.get_indirect_mappings()
        self.assertEqual(mappings.count(), 0)

    def test_get_parent_and_owner_filters_from_uri(self):
        self.assertEqual(Concept.get_parent_and_owner_filters_from_uri(None), {})
        self.assertEqual(Concept.get_parent_and_owner_filters_from_uri(''), {})
        self.assertEqual(Concept.get_parent_and_owner_filters_from_uri('/bar/'), {})
        self.assertEqual(Concept.get_parent_and_owner_filters_from_uri('/concepts/'), {})
        self.assertEqual(Concept.get_parent_and_owner_filters_from_uri('/concepts/concept1/'), {})

        self.assertEqual(
            Concept.get_parent_and_owner_filters_from_uri('/users/foo/sources/bar/concepts/'),
            {
                'parent__mnemonic': 'bar',
                'parent__user__username': 'foo'
            }
        )
        self.assertEqual(
            Concept.get_parent_and_owner_filters_from_uri('/users/foo/sources/bar/concepts/concept1/'),
            {
                'parent__mnemonic': 'bar',
                'parent__user__username': 'foo'
            }
        )
        self.assertEqual(
            Concept.get_parent_and_owner_filters_from_uri('/orgs/foo/sources/bar/concepts/concept1/'),
            {
                'parent__mnemonic': 'bar',
                'parent__organization__mnemonic': 'foo'
            }
        )

    def test_get_hierarchy_path(self):
        parent_concept = ConceptFactory()
        self.assertEqual(parent_concept.get_hierarchy_path(), [])

        child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })

        self.assertEqual(parent_concept.get_hierarchy_path(), [])
        self.assertEqual(child_concept.get_hierarchy_path(), [parent_concept.uri])

        child_child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c2', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [child_concept.uri]
        })

        self.assertEqual(parent_concept.get_hierarchy_path(), [])
        self.assertEqual(child_concept.get_hierarchy_path(), [parent_concept.uri])
        self.assertEqual(child_child_concept.get_hierarchy_path(), [parent_concept.uri, child_concept.uri])

    def test_child_concept_queryset(self):
        parent_concept = ConceptFactory()
        self.assertEqual(parent_concept.child_concept_queryset().count(), 0)
        self.assertEqual(parent_concept.parent_concept_urls, [])

        child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })
        self.assertEqual(
            list(parent_concept.child_concept_queryset().values_list('uri', flat=True)), [child_concept.uri])
        self.assertEqual(
            list(child_concept.child_concept_queryset().values_list('uri', flat=True)), [])
        self.assertEqual(child_concept.parent_concept_urls, [parent_concept.uri])

        child_child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c2', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [child_concept.uri]
        })
        self.assertEqual(
            list(parent_concept.child_concept_queryset().values_list('uri', flat=True)), [child_concept.uri])
        self.assertEqual(
            list(child_concept.child_concept_queryset().values_list('uri', flat=True)), [child_child_concept.uri])
        self.assertEqual(
            list(child_child_concept.child_concept_queryset().values_list('uri', flat=True)), [])
        self.assertEqual(child_child_concept.parent_concept_urls, [child_concept.uri])
        self.assertEqual(parent_concept.children_concepts_count, 1)

    def test_parent_concept_queryset(self):
        parent_concept = ConceptFactory()
        self.assertEqual(parent_concept.parent_concept_queryset().count(), 0)
        self.assertEqual(parent_concept.parent_concept_urls, [])

        child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parent_concept.uri]
        })
        self.assertEqual(
            list(parent_concept.parent_concept_queryset().values_list('uri', flat=True)), [])
        self.assertEqual(
            list(child_concept.parent_concept_queryset().values_list('uri', flat=True)), [parent_concept.uri])
        self.assertEqual(child_concept.parent_concept_urls, [parent_concept.uri])

        child_child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c2', 'parent': parent_concept.parent,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [child_concept.uri]
        })
        self.assertEqual(
            list(parent_concept.parent_concept_queryset().values_list('uri', flat=True)), [])
        self.assertEqual(
            list(child_concept.parent_concept_queryset().values_list('uri', flat=True)), [parent_concept.uri])
        self.assertEqual(
            list(child_child_concept.parent_concept_queryset().values_list('uri', flat=True)), [child_concept.uri])
        self.assertEqual(child_child_concept.parent_concept_urls, [child_concept.uri])
        self.assertEqual(child_child_concept.parent_concepts_count, 1)

    def test_has_children(self):
        concept = ConceptFactory()

        self.assertFalse(concept.has_children)

        concept2 = ConceptFactory()
        concept2.parent_concepts.add(concept)

        self.assertTrue(concept.has_children)
        self.assertFalse(concept2.has_children)

    def test_get_serializer_class(self):
        self.assertEqual(Concept.get_serializer_class(), ConceptListSerializer)
        self.assertEqual(Concept.get_serializer_class(version=True), ConceptVersionListSerializer)
        self.assertEqual(Concept.get_serializer_class(verbose=True), ConceptDetailSerializer)
        self.assertEqual(Concept.get_serializer_class(verbose=True, version=True), ConceptVersionDetailSerializer)
        self.assertEqual(Concept.get_serializer_class(brief=True), ConceptMinimalSerializer)

    def test_from_uri_queryset_for_source_and_source_version(self):
        source = OrganizationSourceFactory()
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
        })
        self.assertEqual(concept.versions.count(), 1)

        concepts = Concept.from_uri_queryset(source.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept.get_latest_version().id)

        source_version1 = OrganizationSourceFactory(
            version='v1', mnemonic=source.mnemonic, organization=source.organization)
        source_version1.seed_concepts(index=False)
        self.assertEqual(source_version1.concepts.count(), 1)

        concepts = Concept.from_uri_queryset(source_version1.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept.get_latest_version().id)

        source_version2 = OrganizationSourceFactory(
            version='v2', mnemonic=source.mnemonic, organization=source.organization)

        cloned_concept = Concept.version_for_concept(concept, 'v1', source)
        cloned_concept.datatype = 'foobar'
        cloned_concept.save_as_new_version(concept.created_by)

        self.assertEqual(concept.versions.count(), 2)

        concept_v1 = concept.get_latest_version()
        self.assertTrue(concept_v1.is_latest_version)
        concepts = Concept.from_uri_queryset(concept_v1.version_url)
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_v1.id)

        concept_prev_version = concept_v1.prev_version
        self.assertFalse(concept_prev_version.is_latest_version)
        concepts = Concept.from_uri_queryset(concept_prev_version.version_url)
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_prev_version.id)

        concepts = Concept.from_uri_queryset(source.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_v1.id)

        concepts = Concept.from_uri_queryset(source_version2.uri + 'concepts/')

        self.assertEqual(concepts.count(), 0)

        concepts = Concept.from_uri_queryset(source_version1.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_prev_version.id)

    def test_from_uri_queryset_for_collection_and_collection_version(self):
        source = OrganizationSourceFactory()
        concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
        })
        source_version1 = OrganizationSourceFactory(
            version='v1', mnemonic=source.mnemonic, organization=source.organization)
        source_version1.seed_concepts(index=False)
        OrganizationSourceFactory(
            version='v2', mnemonic=source.mnemonic, organization=source.organization)
        cloned_concept = Concept.version_for_concept(concept, 'v1', source)
        cloned_concept.datatype = 'foobar'
        cloned_concept.save_as_new_version(concept.created_by)
        self.assertEqual(concept.versions.count(), 2)

        concept_v1 = concept.get_latest_version()
        collection = OrganizationCollectionFactory()
        reference = CollectionReference(
            expression=concept_v1.version_url, collection=collection, system=concept_v1.parent.uri, version='HEAD',
            code=concept_v1.mnemonic, resource_version=concept_v1.version
        )
        reference.clean()
        reference.save()

        collection_version1 = OrganizationCollectionFactory(
            version='v1', mnemonic=collection.mnemonic, organization=collection.organization)
        collection_version1.seed_references()
        expansion = ExpansionFactory(collection_version=collection_version1)
        expansion.seed_children(index=False)

        self.assertEqual(expansion.concepts.count(), 1)

        concepts = Concept.from_uri_queryset(expansion.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_v1.id)

        concepts = Concept.from_uri_queryset(collection_version1.uri + 'concepts/')
        self.assertEqual(concepts.count(), 0)

        collection_version1.expansion_uri = expansion.uri
        collection_version1.save()

        concepts = Concept.from_uri_queryset(collection_version1.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_v1.id)

        concepts = Concept.from_uri_queryset(collection.uri + 'concepts/')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept_v1.id)

    def test_cascade_as_hierarchy(self):
        source = OrganizationSourceFactory()
        root = ConceptFactory(parent=source, mnemonic='root')
        root_child = ConceptFactory(parent=source, mnemonic='root-child')
        root_child.parent_concepts.add(root)
        root_child_child1 = ConceptFactory(parent=source, mnemonic='root-child-child1')
        root_child_child1.parent_concepts.add(root_child)
        root_child_child2 = ConceptFactory(parent=source, mnemonic='root-child-child2')
        root_child_child2.parent_concepts.add(root_child)
        root_child_child2_child = ConceptFactory(parent=source, mnemonic='root-child-child2-child')
        root_child_child2_child.parent_concepts.add(root_child_child2)

        root_cascaded = root.cascade_as_hierarchy(root.sources.filter(version='HEAD').first())

        self.assertTrue(isinstance(root_cascaded, Concept))
        self.assertEqual(root_cascaded.uri, root.uri)

        root_cascaded_children = root_cascaded.cascaded_entries
        self.assertEqual(len(root_cascaded_children['concepts']), 1)
        self.assertEqual(root_cascaded_children['mappings'].count(), 0)

        root_child_cascaded = root_cascaded_children['concepts'][0]
        root_child_cascaded_children = root_child_cascaded.cascaded_entries
        self.assertEqual(len(root_child_cascaded_children['concepts']), 2)
        self.assertEqual(root_child_cascaded_children['mappings'].count(), 0)

        root_child_child1_cascaded_children = [
            child for child in root_child_cascaded_children['concepts'] if child.mnemonic == 'root-child-child1'
        ][0].cascaded_entries
        root_child_child2_cascaded_children = [
            child for child in root_child_cascaded_children['concepts'] if child.mnemonic == 'root-child-child2'
        ][0].cascaded_entries

        self.assertEqual(len(root_child_child1_cascaded_children['concepts']), 0)
        self.assertEqual(len(root_child_child1_cascaded_children['mappings']), 0)

        self.assertEqual(len(root_child_child2_cascaded_children['concepts']), 1)
        self.assertEqual(len(root_child_child2_cascaded_children['mappings']), 0)

    def test_cascade_as_hierarchy_reverse(self):
        source = OrganizationSourceFactory()
        root = ConceptFactory(parent=source, mnemonic='root')
        root_child = ConceptFactory(parent=source, mnemonic='root-child')
        root_child.parent_concepts.add(root)
        root_child_child1 = ConceptFactory(parent=source, mnemonic='root-child-child1')
        root_child_child1.parent_concepts.add(root_child)
        root_child_child2 = ConceptFactory(parent=source, mnemonic='root-child-child2')
        root_child_child2.parent_concepts.add(root_child)
        root_child_child2_child = ConceptFactory(parent=source, mnemonic='root-child-child2-child')
        root_child_child2_child.parent_concepts.add(root_child_child2)

        root_child_child2_child_cascaded = root_child_child2_child.cascade_as_hierarchy(
            root_child_child2_child.sources.filter(version='HEAD').first(), reverse=True)

        self.assertEqual(root_child_child2_child_cascaded.uri, root_child_child2_child.uri)
        root_child_child2_child_cascaded_entries = root_child_child2_child_cascaded.cascaded_entries
        self.assertEqual(len(root_child_child2_child_cascaded_entries['concepts']), 1)
        self.assertEqual(len(root_child_child2_child_cascaded_entries['mappings']), 0)
        self.assertEqual(root_child_child2_child_cascaded_entries['concepts'][0].url, root_child_child2.url)
        self.assertEqual(
            root_child_child2_child_cascaded_entries['concepts'][0].cascaded_entries['concepts'][0].url,
            root_child.url
        )
        self.assertEqual(
            root_child_child2_child_cascaded_entries['concepts'][0].cascaded_entries['concepts'][
                0].cascaded_entries['concepts'][0].url,
            root.url
        )

    @patch('core.common.checksums.ChecksumBase.generate')
    def test_checksum(self, checksum_generate_mock):
        checksum_generate_mock.side_effect = [
            'standard-checksum', 'smart-checksum'
        ]
        concept = ConceptFactory()

        self.assertEqual(concept.checksums, {})
        self.assertEqual(concept.checksum, 'standard-checksum')
        self.assertEqual(
            concept.checksums,
            {
                'standard': 'standard-checksum',
                'smart': 'smart-checksum',
            }
        )
        checksum_generate_mock.assert_called()

    def test_get_checksums(self):
        parent = OrganizationSourceFactory()
        concept = ConceptFactory(parent=parent)
        ConceptDescriptionFactory(concept=concept)
        ConceptNameFactory(concept=concept)
        MappingFactory(from_concept=concept, parent=parent)

        checksums = concept.get_checksums()
        concept.refresh_from_db()

        self.assertEqual(
            checksums,
            {'standard': ANY, 'smart': ANY}
        )
        self.assertTrue(checksums['standard'] == concept.checksums['standard'] == concept.checksum)
        self.assertTrue(checksums['smart'] == concept.checksums['smart'])

    def test_properties_and_filters(self):  # pylint: disable=too-many-statements
        source = OrganizationSourceFactory(properties=[], filters=[])
        concept1 = ConceptFactory(parent=source, concept_class='Diagnosis', datatype='N/A')

        concept1.refresh_from_db()

        for concept in [concept1, concept1.get_latest_version()]:
            self.assertEqual(concept.extras, {})
            self.assertEqual(concept.properties, [])
            self.assertEqual(concept.datatype, 'N/A')
            self.assertEqual(concept.concept_class, 'Diagnosis')

        source.properties = [
            {
                "code": "concept_class",
                "description": "Type of concept",
                "type": "code",   # e.g. from /orgs/OCL/collections/Classes/
            },
            {
                "code": "datatype",
                "description": "Type of data captured for this concept",
                "type": "code",   # e.g. from /orgs/OCL/collections/Datatypes/
            },
            {
                "code": "units",
                "description": "Units of measurement",
                "type": "string"
            }
        ]
        source.filters = [
            {'code': 'concept_class', 'operator': ['='], 'value': 'blah'},
            {'code': 'datatype', 'operator': ['='], 'value': 'blah'},
        ]

        source.meta = {
            'display': {
                'concept_summary_properties': ['datatype', 'concept_class'],
                'concept_filter_order': ['concept_class', 'datatype'],
            }
        }
        source.save()

        self.assertEqual(
            source.filters_ordered,
            [
                {'code': 'concept_class', 'operator': ['='], 'value': 'blah'},
                {'code': 'datatype', 'operator': ['='], 'value': 'blah'},
            ]
        )

        concept2 = ConceptFactory(parent=source, concept_class='Diagnosis', datatype='N/A')
        concept3 = ConceptFactory(
            parent=source, concept_class='Diagnosis', datatype='N/A', extras={'foo': 'bar', 'units': 'parts/microliter'}
        )

        concept2_latest_version = concept2.get_latest_version()
        concept3_latest_version = concept3.get_latest_version()

        concept2s = [concept2, concept2_latest_version]
        concept3s = [concept3, concept3_latest_version]

        for concept in concept2s:
            concept.refresh_from_db()
            self.assertEqual(concept.extras, {})
            self.assertEqual(
                concept.properties,
                [
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                ]
            )
            self.assertEqual(
                concept.summary_properties,
                [
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                ]
            )
            self.assertEqual(
                concept.filters_ordered,
                [
                    {'code': 'concept_class', 'operator': ['='], 'value': 'blah'},
                    {'code': 'datatype', 'operator': ['='], 'value': 'blah'},
                ]
            )
        for concept in concept3s:
            concept.refresh_from_db()
            self.assertEqual(
                concept.extras,
                {'foo': 'bar', 'units': 'parts/microliter'})
            self.assertEqual(
                concept.properties,
                [
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'units', 'valueString': 'parts/microliter', 'display': 'units'}
                ]
            )
            self.assertEqual(
                concept.summary_properties,
                [
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                ]
            )
            self.assertEqual(
                concept.filters_ordered,
                [
                    {'code': 'concept_class', 'operator': ['='], 'value': 'blah'},
                    {'code': 'datatype', 'operator': ['='], 'value': 'blah'},
                ]
            )

        source.meta = {
            'display': {
                'concept_summary_properties': ['concept_class', 'datatype'],
                'concept_filter_order': ['datatype', 'concept_class'],
            }
        }
        source.save()

        self.assertEqual(
            source.filters_ordered,
            [
                {'code': 'datatype', 'operator': ['='], 'value': 'blah'},
                {'code': 'concept_class', 'operator': ['='], 'value': 'blah'},
            ]
        )

        for concept in concept2s:
            concept.refresh_from_db()
            self.assertEqual(concept.extras, {})
            self.assertEqual(
                concept.properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                ]
            )
            self.assertEqual(
                concept.summary_properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                ]
            )
        self.assertEqual(
            concept.filters_ordered,
            [
                {'code': 'datatype', 'operator': ['='], 'value': 'blah'},
                {'code': 'concept_class', 'operator': ['='], 'value': 'blah'},
            ]
        )
        for concept in concept3s:
            concept.refresh_from_db()
            self.assertEqual(
                concept.extras,
                {'foo': 'bar', 'units': 'parts/microliter'})
            self.assertEqual(
                concept.properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                    {'code': 'units', 'valueString': 'parts/microliter', 'display': 'units'}
                ]
            )
            self.assertEqual(
                concept.summary_properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                ]
            )
            self.assertEqual(
                concept.filters_ordered,
                [
                    {'code': 'datatype', 'operator': ['='], 'value': 'blah'},
                    {'code': 'concept_class', 'operator': ['='], 'value': 'blah'},
                ]
            )

        source.meta = {
            'display': {
                'concept_summary_properties': ['concept_class', 'datatype', 'foobar'],
                'concept_filter_order': ['datatype', 'concept_class', 'foobar', 'barbar', 'bar1'],
            },
        }
        source.filters = [
            {'code': 'concept_class', 'operator': ['='], 'value': 'blah'},
            {'code': 'datatype', 'operator': ['='], 'value': 'blah'},
            {'code': 'bar2', 'operator': ['='], 'value': 'blah'},
            {'code': 'bar0', 'operator': ['='], 'value': 'blah'},
            {'code': 'bar1', 'operator': ['='], 'value': 'blah'},
            {'code': 'barbar', 'operator': ['='], 'value': 'blah'},
        ]
        source.save()
        self.assertEqual(
            source.filters_ordered,
            [
                {'code': 'datatype', 'operator': ['='], 'value': 'blah'},
                {'code': 'concept_class', 'operator': ['='], 'value': 'blah'},
                {'code': 'barbar', 'operator': ['='], 'value': 'blah'},
                {'code': 'bar1', 'operator': ['='], 'value': 'blah'},
                {'code': 'bar0', 'operator': ['='], 'value': 'blah'},
                {'code': 'bar2', 'operator': ['='], 'value': 'blah'},
            ]
        )

        for concept in concept2s:
            concept.refresh_from_db()
            self.assertEqual(concept.extras, {})
            self.assertEqual(
                concept.properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'}
                ]
            )
            self.assertEqual(
                concept.summary_properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                ]
            )
        for concept in concept3s:
            concept.refresh_from_db()
            self.assertEqual(
                concept.extras,
                {'foo': 'bar', 'units': 'parts/microliter'})
            self.assertEqual(
                concept.properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                    {'code': 'units', 'valueString': 'parts/microliter', 'display': 'units'}
                ]
            )
            self.assertEqual(
                concept.summary_properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                ]
            )

        source.meta = {'display': {'concept_summary_properties': []}}
        source.save()

        for concept in concept2s:
            concept.refresh_from_db()
            self.assertEqual(concept.extras, {})
            self.assertEqual(
                concept.properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'}
                ]
            )
            self.assertEqual(
                concept.summary_properties,
                []
            )
        for concept in concept3s:
            concept.refresh_from_db()
            self.assertEqual(
                concept.extras,
                {'foo': 'bar', 'units': 'parts/microliter'})
            self.assertEqual(
                concept.properties,
                [
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                    {'code': 'units', 'valueString': 'parts/microliter', 'display': 'units'}
                ]
            )
            self.assertEqual(
                concept.summary_properties,
                []
            )

    def test_properties_with_display(self):  # pylint: disable=too-many-statements
        source = OrganizationSourceFactory(properties=[], filters=[])
        concept1 = ConceptFactory(parent=source, concept_class='Diagnosis', datatype='N/A')

        concept1.refresh_from_db()

        source.properties = [
            {
                "code": "concept_class",
                "description": "Type of concept",
                "type": "code",   # e.g. from /orgs/OCL/collections/Classes/
            },
            {
                "code": "datatype",
                "description": "Type of data captured for this concept",
                "type": "code",   # e.g. from /orgs/OCL/collections/Datatypes/
            },
            {
                "code": "units",
                "description": "Units of measurement",
                "type": "string",
                "display": "UoM"
            }
        ]

        source.meta = {
            'display': {
                'concept_summary_properties': ['datatype', 'concept_class', 'units'],
            }
        }
        source.save()

        concept2 = ConceptFactory(parent=source, concept_class='Diagnosis', datatype='N/A')
        concept3 = ConceptFactory(
            parent=source, concept_class='Diagnosis', datatype='N/A', extras={'foo': 'bar', 'units': 'parts/microliter'}
        )

        concept2_latest_version = concept2.get_latest_version()
        concept3_latest_version = concept3.get_latest_version()

        concept2s = [concept2, concept2_latest_version]
        concept3s = [concept3, concept3_latest_version]

        for concept in concept2s:
            concept.refresh_from_db()
            self.assertEqual(concept.extras, {})
            self.assertEqual(
                concept.properties,
                [
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'}
                ]
            )
            self.assertEqual(
                concept.summary_properties,
                [
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                ]
            )
        for concept in concept3s:
            concept.refresh_from_db()
            self.assertEqual(
                concept.extras,
                {'foo': 'bar', 'units': 'parts/microliter'})
            self.assertEqual(
                concept.properties,
                [
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'units', 'valueString': 'parts/microliter', 'display': 'UoM'}
                ]
            )
            self.assertEqual(
                concept.summary_properties,
                [
                    {'code': 'datatype', 'valueCode': 'N/A', 'display': 'datatype'},
                    {'code': 'concept_class', 'valueCode': 'Diagnosis', 'display': 'concept_class'},
                    {'code': 'units', 'valueString': 'parts/microliter', 'display': 'UoM'},
                ]
            )

    def test_properties_with_extras_reference_value(self):
        source = OrganizationSourceFactory(
            properties=[
                {
                    "code": "reference",
                    "description": "A reference to something",
                    "type": "string",
                    "display": "Reference"
                }
            ],
            filters=[]
        )

        concept_without_extra = ConceptFactory(parent=source)
        concept_with_plain_extra = ConceptFactory(parent=source, extras={'reference': 'plain-value'})
        concept_with_reference_extra = ConceptFactory(
            parent=source,
            extras={
                'reference': {
                    'display': 'Concept Reference Display',
                    'type': 'Default value type, can be replaced by property type in the definition',
                    'value': 'the-actual-value',
                    'anything_else': 'my-precious-description-uuid'
                }
            }
        )

        self.assertEqual(concept_without_extra.properties, [])
        self.assertEqual(
            concept_with_plain_extra.properties,
            [{'code': 'reference', 'valueString': 'plain-value', 'display': 'Reference'}]
        )
        self.assertEqual(
            concept_with_reference_extra.properties,
            [{'code': 'reference', 'valueString': 'the-actual-value', 'display': 'Reference'}]
        )

    def test_get_resource_url_kwarg(self):
        self.assertEqual(Concept.get_resource_url_kwarg(), 'concept')

    def test_get_version_url_kwarg(self):
        self.assertEqual(Concept.get_version_url_kwarg(), 'concept_version')

    def test_get_brief_serializer(self):
        self.assertEqual(Concept.get_brief_serializer(), ConceptMinimalSerializer)

    def test_get_serializer_class_brief_cascade(self):
        self.assertEqual(Concept.get_serializer_class(brief=True, cascade=True), ConceptCascadeMinimalSerializer)

    def test_preferred_locale_returns_none_on_exception(self):
        concept = ConceptFactory()
        with patch.object(
                Concept, '_Concept__get_parent_default_locale_name', side_effect=Exception('boom')):
            self.assertIsNone(concept.preferred_locale)

    def test_retired_descriptions(self):
        concept = ConceptFactory(descriptions=[ConceptDescriptionFactory.build(retired=True)])
        self.assertEqual(concept.retired_descriptions.count(), 1)

    def test_saved_unsaved_descriptions_without_id(self):
        concept = Concept()
        concept.cloned_descriptions = [ConceptDescriptionFactory.build()]
        self.assertEqual(len(concept.saved_unsaved_descriptions), 1)

    def test_saved_unsaved_names_without_id(self):
        concept = Concept()
        concept.cloned_names = [ConceptNameFactory.build()]
        self.assertEqual(len(concept.saved_unsaved_names), 1)

    def test_get_base_queryset_latest_released_source(self):
        source = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(
            organization=source.organization, mnemonic=source.mnemonic, version='v1', released=True)
        concept = ConceptFactory(parent=source)
        latest_concept_version = concept.get_latest_version()
        latest_concept_version.sources.add(source_v1)

        queryset = Concept.get_base_queryset({
            'org': source.organization.mnemonic, 'source': source.mnemonic, 'version': LATEST
        })

        self.assertIn(latest_concept_version.id, queryset.values_list('id', flat=True))

    def test_get_base_queryset_latest_released_not_found_returns_none(self):
        queryset = Concept.get_base_queryset({
            'org': 'NoSuchOrg', 'source': 'NoSuchSource', 'version': LATEST
        })
        self.assertEqual(queryset.count(), 0)

    def test_get_base_queryset_latest_released_collection(self):
        collection = OrganizationCollectionFactory()
        collection_v1 = OrganizationCollectionFactory(
            organization=collection.organization, mnemonic=collection.mnemonic, version='v1', released=True)
        expansion = ExpansionFactory(collection_version=collection_v1)
        concept = ConceptFactory()
        expansion.concepts.add(concept)

        queryset = Concept.get_base_queryset({
            'org': collection.organization.mnemonic, 'collection': collection.mnemonic, 'version': LATEST
        })

        self.assertIn(concept.id, queryset.values_list('id', flat=True))

    def test_create_mappings_handles_unexpected_exception(self):
        concept = ConceptFactory()

        results, any_with_errors = concept.create_mappings(['not-a-dict'])

        self.assertTrue(any_with_errors)
        self.assertIn('__all__', results[0]['errors'])

    def test_create_mappings_appends_generic_error_when_creation_silently_fails(self):
        concept = ConceptFactory()
        fake_mapping = Mock(errors={}, id=None)
        with patch.object(Concept, '_create_mapping_from_self', return_value=fake_mapping):
            results, any_with_errors = concept.create_mappings([{'map_type': 'Same As'}])

        self.assertTrue(any_with_errors)
        self.assertEqual(results[0]['errors'], {'__all__': ['Something bad happened while creating the mapping.']})

    def test_validate_mapping_create_from_self(self):
        # _validate_mapping_create_from_self never sets `version` on the candidate Mapping,
        # so full_clean() always raises -- there are no callers of this method anywhere.
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)

        with self.assertRaises(ValidationError) as context:
            concept1._validate_mapping_create_from_self(  # pylint: disable=protected-access
                {'map_type': 'Same As', 'to_concept_url': concept2.uri}, concept1.created_by
            )
        self.assertIn('version', context.exception.message_dict)

    def test_validate_mapping_create_from_self_with_parent_concept_sentinel(self):
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source)

        with self.assertRaises(ValidationError) as context:
            concept._validate_mapping_create_from_self(  # pylint: disable=protected-access
                {'map_type': 'Same As', 'to_concept': '__parent_concept'}, concept.created_by
            )
        self.assertIn('version', context.exception.message_dict)

    def test_rollback_mapping_operations(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source)

        Concept._rollback_mapping_operations([  # pylint: disable=protected-access
            {'__action': None, 'versioned_object_id': None},  # continue branch: no op_type/mapping_id
            {'__action': 'create', 'versioned_object_id': mapping.versioned_object_id},
        ])

        self.assertFalse(Mapping.objects.filter(versioned_object_id=mapping.versioned_object_id).exists())

    def test_rollback_latest_version_to_noop_without_prev(self):
        concept = ConceptFactory()
        self.assertIsNone(concept.rollback_latest_version_to(None))

    def test_upsert_or_delete_mappings_mapping_not_found(self):
        concept = ConceptFactory()

        results, any_with_errors = concept.upsert_or_delete_mappings(
            [{'id': 'no-such-mapping-id', 'action': '__delete'}], concept.created_by
        )

        self.assertTrue(any_with_errors)
        self.assertIn('id', results[0]['errors'])

    def test_upsert_or_delete_mappings_delete_missing_id_raises(self):
        concept = ConceptFactory()

        results, any_with_errors = concept.upsert_or_delete_mappings(
            [{'action': '__delete'}], concept.created_by
        )

        self.assertTrue(any_with_errors)
        self.assertIn('id', results[0]['errors'])

    def test_upsert_or_delete_mappings_generic_exception(self):
        concept = ConceptFactory()
        source = OrganizationSourceFactory()
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept, to_concept=concept2, parent=concept.parent)

        with patch.object(Concept, 'find_direct_mapping', side_effect=Exception('boom')):
            results, any_with_errors = concept.upsert_or_delete_mappings(
                [{'id': mapping.mnemonic}], concept.created_by
            )

        self.assertTrue(any_with_errors)
        self.assertEqual(results[0]['errors'], {'__all__': ['boom']})

    def test_remove_mappings_just_created_ignores_integrity_error(self):
        fake_instance = Mock(id=1)
        fake_instance.delete = Mock(side_effect=IntegrityError('boom'))

        Concept._remove_mappings_just_created(  # pylint: disable=protected-access
            [{'instance': fake_instance}]
        )

        fake_instance.delete.assert_called_once()

    def test_validate_locales_limit_raises_for_too_many_names(self):
        with self.assertRaises(ValidationError):
            Concept.validate_locales_limit([{'name': 'x'}] * 501, [])

    def test_validate_locales_limit_raises_for_too_many_descriptions(self):
        with self.assertRaises(ValidationError):
            Concept.validate_locales_limit([], [{'name': 'x'}] * 501)

    def test_get_unidirectional_mappings_for_collection(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        expansion.mappings.add(mapping)

        results = concept1.get_unidirectional_mappings_for_collection(collection.uri)

        self.assertIn(mapping.id, results.values_list('id', flat=True))

    def test_get_indirect_mappings_for_collection(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        expansion.mappings.add(mapping)

        results = concept2.get_indirect_mappings_for_collection(collection.uri)

        self.assertIn(mapping.id, results.values_list('id', flat=True))

    def test_get_hierarchy_concept_urls_versioned(self):
        source = OrganizationSourceFactory()
        parent_concept = ConceptFactory(parent=source)
        child_concept = ConceptFactory(parent=source)
        child_concept.parent_concepts.add(parent_concept)

        urls = child_concept.get_hierarchy_concept_urls('parent_concepts', versioned=True)

        self.assertEqual(urls, [])  # parent_concept's uri here is unversioned, so nothing qualifies

    def test_has_children_via_versioned_object(self):
        source = OrganizationSourceFactory()
        parent_concept = ConceptFactory(parent=source)
        child_concept = ConceptFactory(parent=source)
        latest_parent = parent_concept.get_latest_version()
        latest_child = child_concept.get_latest_version()
        latest_child.parent_concepts.add(latest_parent)

        self.assertTrue(latest_parent.versioned_object.has_children)

    def test_cascade_returns_self_without_repo_version(self):
        concept = ConceptFactory()
        result = concept.cascade(repo_version=None)
        self.assertEqual(list(result['concepts'].values_list('id', flat=True)), [concept.id])

    def test_cascade_with_string_repo_version_no_match_returns_self(self):
        concept = ConceptFactory()
        result = concept.cascade(repo_version='no-such-version')
        self.assertEqual(list(result['concepts'].values_list('id', flat=True)), [concept.id])

    def test_cascade_as_hierarchy_with_string_repo_version_no_match(self):
        concept = ConceptFactory()
        result = concept.cascade_as_hierarchy(repo_version='no-such-version')
        self.assertEqual(result, concept)

    def test_cascaded_resources_reverse_for_collection_version_without_expansion(self):
        collection = OrganizationCollectionFactory()
        concept = ConceptFactory()

        result = concept.cascaded_resources_reverse_for_collection_version(collection)

        self.assertEqual(list(result['concepts'].values_list('id', flat=True)), [concept.id])

    @patch('core.common.models.ConceptContainerModel.update_concepts_count')
    @patch('core.common.models.handle_m2m_changed')
    @patch('core.common.models.handle_save')
    @patch('core.concepts.models.update_mappings_concept')
    def test_persist_new_queues_update_mappings_concept_task(
            self, update_mappings_concept_mock, _handle_save_mock, _handle_m2m_mock,
            _update_concepts_count_mock):
        source = OrganizationSourceFactory(version=HEAD)
        with patch('core.concepts.models.settings.TEST_MODE', False):
            Concept.persist_new({
                **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'queued-c1', 'parent': source,
                'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
            })

        update_mappings_concept_mock.apply_async.assert_called_once_with(
            (ANY,), queue='default', permanent=False)

    @patch('core.common.models.ConceptContainerModel.update_concepts_count')
    @patch('core.common.models.handle_m2m_changed')
    @patch('core.common.models.handle_save')
    @patch('core.concepts.models.process_hierarchy_for_new_concept')
    @patch('core.concepts.models.update_mappings_concept')
    def test_persist_new_queues_process_hierarchy_task(
            self, _update_mappings_mock, process_hierarchy_mock, _handle_save_mock, _handle_m2m_mock,
            _update_concepts_count_mock):
        source = OrganizationSourceFactory(version=HEAD)
        parent_concept = ConceptFactory(parent=source)
        with patch('core.concepts.models.settings.TEST_MODE', False):
            Concept.persist_new({
                **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'queued-c2', 'parent': source,
                'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
                'parent_concept_urls': [parent_concept.uri],
            })

        process_hierarchy_mock.apply_async.assert_called_once()

    def test_persist_new_rolls_back_on_integrity_error(self):
        source = OrganizationSourceFactory(version=HEAD)
        with patch.object(Concept, 'full_clean', side_effect=IntegrityError('boom')):
            concept = Concept.persist_new({
                **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'ie-c1', 'parent': source,
                'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)]
            })

        self.assertEqual(concept.errors, {'__all__': ('boom',)})
        self.assertIsNone(concept.id)

    @override_settings(TEST_MODE=False)
    @patch('core.concepts.models.process_hierarchy_for_new_parent_concept_version')
    def test_process_prev_latest_version_hierarchy_queues_task(self, process_hierarchy_mock):
        source = OrganizationSourceFactory(version=HEAD)
        concept = ConceptFactory(parent=source)
        prev_latest = concept.get_latest_version()

        concept._process_prev_latest_version_hierarchy(prev_latest)  # pylint: disable=protected-access

        process_hierarchy_mock.apply_async.assert_called_once_with(
            (prev_latest.id, concept.id), queue='concurrent', permanent=False)

    @override_settings(TEST_MODE=False)
    @patch('core.concepts.models.process_hierarchy_for_concept_version')
    def test_process_latest_version_hierarchy_queues_task(self, process_hierarchy_mock):
        source = OrganizationSourceFactory(version=HEAD)
        concept = ConceptFactory(parent=source)

        concept._process_latest_version_hierarchy(None)  # pylint: disable=protected-access

        process_hierarchy_mock.apply_async.assert_called_once_with(
            (concept.id, None, None, True), queue='concurrent', permanent=False)


class ConceptSerializersTest(OCLTestCase):
    @staticmethod
    def build_context(query_string='', instance=None, view_kwargs=None):
        request = Mock(query_params=QueryDict(query_string), instance=instance, path='/concepts/')
        return {'request': request, 'view': Mock(kwargs=view_kwargs or {})}

    @staticmethod
    def create_reference(collection, concept):
        reference = CollectionReference(expression=concept.uri, collection=collection)
        reference.save()
        reference.concepts.add(concept)
        return reference

    def test_concept_locale_serializer_get_locale_type_ignores_wrapper_type(self):
        locale = ConceptNameFactory.build(type='Short')
        result = ConceptLocaleSerializer.get_locale_type({'type': 'ConceptName'}, locale)
        self.assertEqual(result, 'Short')

    def test_concept_description_serializer_to_representation(self):
        description = ConceptDescriptionFactory.build()
        serializer = ConceptDescriptionSerializer(context=self.build_context())
        ret = serializer.to_representation(description)
        self.assertEqual(ret['type'], 'ConceptDescription')

    def test_concept_detail_serializer_create_parent_version_defaults_true(self):
        serializer = ConceptDetailSerializer(context=self.build_context())
        self.assertTrue(serializer.create_parent_version)

    def test_get_references_returns_none_without_collection_instance(self):
        concept = ConceptFactory()
        serializer = ConceptListSerializer(context=self.build_context(instance=None))
        self.assertIsNone(serializer.get_references(concept))

    def test_get_references_verbose(self):
        collection = OrganizationCollectionFactory()
        concept = ConceptFactory()
        self.create_reference(collection, concept)
        serializer = ConceptListSerializer(
            context=self.build_context('includeReferences=true', instance=collection))
        result = serializer.get_references(concept)
        self.assertEqual(len(result), 1)

    def test_get_references_non_verbose(self):
        collection = OrganizationCollectionFactory()
        concept = ConceptFactory()
        reference = self.create_reference(collection, concept)
        serializer = ConceptListSerializer(context=self.build_context(instance=collection))
        result = serializer.get_references(concept)
        self.assertEqual(result, [f"{collection.uri}references/{reference.id}/"])

    def test_get_mappings_direct_with_map_types_and_target_repo_urls_filters(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        MappingFactory(from_concept=concept1, to_concept=concept2, parent=source, map_type='Same As')

        serializer = ConceptListSerializer(context=self.build_context('includeMappings=true&mapTypes=Same As'))
        result = serializer.get_mappings(concept1)
        self.assertEqual(len(result), 1)

        serializer = ConceptListSerializer(context=self.build_context(
            'includeMappings=true&mapTypes=Different Type'))
        result = serializer.get_mappings(concept1)
        self.assertEqual(result, [])

        serializer = ConceptListSerializer(context=self.build_context(
            'includeMappings=true&targetRepoUrls=https://example.com/no-such-source/'))
        result = serializer.get_mappings(concept1)
        self.assertEqual(result, [])

    def test_get_mappings_returns_empty_list_by_default(self):
        concept = ConceptFactory()
        serializer = ConceptListSerializer(context=self.build_context())
        self.assertEqual(serializer.get_mappings(concept), [])

    def test_get_child_concepts(self):
        source = OrganizationSourceFactory()
        parent_concept = ConceptFactory(parent=source)
        child_concept = ConceptFactory(parent=source)
        parent_concept.child_concepts.add(child_concept)

        serializer = ConceptListSerializer(context=self.build_context('includeChildConcepts=true'))
        self.assertEqual(len(serializer.get_child_concepts(parent_concept)), 1)

        serializer = ConceptListSerializer(context=self.build_context())
        self.assertIsNone(serializer.get_child_concepts(parent_concept))

    def test_get_parent_concepts(self):
        source = OrganizationSourceFactory()
        parent_concept = ConceptFactory(parent=source)
        child_concept = ConceptFactory(parent=source)
        child_concept.parent_concepts.add(parent_concept)

        serializer = ConceptListSerializer(context=self.build_context('includeParentConcepts=true'))
        self.assertEqual(len(serializer.get_parent_concepts(child_concept)), 1)

        serializer = ConceptListSerializer(context=self.build_context())
        self.assertIsNone(serializer.get_parent_concepts(child_concept))

    def test_get_hierarchy_path(self):
        concept = ConceptFactory()

        serializer = ConceptListSerializer(context=self.build_context('includeHierarchyPath=true'))
        self.assertEqual(serializer.get_hierarchy_path(concept), concept.get_hierarchy_path())

        serializer = ConceptListSerializer(context=self.build_context())
        self.assertIsNone(serializer.get_hierarchy_path(concept))

    def test_get_summary(self):
        concept = ConceptFactory()

        serializer = ConceptListSerializer(context=self.build_context('includeSummary=true'))
        self.assertIsNotNone(serializer.get_summary(concept))

        serializer = ConceptListSerializer(context=self.build_context())
        self.assertIsNone(serializer.get_summary(concept))

    def test_concept_lookup_list_serializer_verbose(self):
        concept = ConceptFactory()
        serializer = ConceptLookupListSerializer(concept, context=self.build_context('verbose=true'))
        self.assertIn('display_name', serializer.data)
        self.assertIn('locale', serializer.data)

    def test_concept_lookup_list_serializer_non_verbose(self):
        concept = ConceptFactory()
        serializer = ConceptLookupListSerializer(concept, context=self.build_context())
        self.assertNotIn('display_name', serializer.data)
        self.assertNotIn('locale', serializer.data)

    def test_concept_version_export_serializer_get_previous_version_url_fallback(self):
        concept = ConceptFactory()
        serializer = ConceptVersionExportSerializer(context={})
        self.assertEqual(serializer.get_previous_version_url(concept), concept.prev_version_uri)

    def test_concept_version_detail_serializer_get_references(self):
        collection = OrganizationCollectionFactory()
        concept = ConceptFactory()
        reference = self.create_reference(collection, concept)

        serializer = ConceptVersionDetailSerializer(context=self.build_context(
            'includeReferences=true', instance=collection))
        result = serializer.get_references(concept)
        self.assertEqual(len(result), 1)

        serializer = ConceptVersionDetailSerializer(context=self.build_context(instance=collection))
        result = serializer.get_references(concept)
        self.assertEqual(result, [f"{collection.uri}references/{reference.id}/"])

        serializer = ConceptVersionDetailSerializer(context=self.build_context(instance=None))
        self.assertIsNone(serializer.get_references(concept))

    def test_concept_version_detail_serializer_get_mappings(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        MappingFactory(from_concept=concept1, to_concept=concept2, parent=source)

        serializer = ConceptVersionDetailSerializer(context=self.build_context('includeInverseMappings=true'))
        self.assertEqual(len(serializer.get_mappings(concept2)), 1)

        serializer = ConceptVersionDetailSerializer(context=self.build_context('includeMappings=true'))
        self.assertEqual(len(serializer.get_mappings(concept1)), 1)

    def test_concept_version_detail_serializer_get_child_concepts(self):
        source = OrganizationSourceFactory()
        parent_concept = ConceptFactory(parent=source)
        child_concept = ConceptFactory(parent=source)
        parent_concept.child_concepts.add(child_concept)

        serializer = ConceptVersionDetailSerializer(context=self.build_context('includeChildConcepts=true'))
        self.assertEqual(len(serializer.get_child_concepts(parent_concept)), 1)

        serializer = ConceptVersionDetailSerializer(context=self.build_context())
        self.assertIsNone(serializer.get_child_concepts(parent_concept))

    def test_concept_version_detail_serializer_get_parent_concepts(self):
        source = OrganizationSourceFactory()
        parent_concept = ConceptFactory(parent=source)
        child_concept = ConceptFactory(parent=source)
        child_concept.parent_concepts.add(parent_concept)

        serializer = ConceptVersionDetailSerializer(context=self.build_context('includeParentConcepts=true'))
        self.assertEqual(len(serializer.get_parent_concepts(child_concept)), 1)

        serializer = ConceptVersionDetailSerializer(context=self.build_context())
        self.assertIsNone(serializer.get_parent_concepts(child_concept))

    def test_concept_children_serializer_get_children(self):
        source = OrganizationSourceFactory()
        parent_concept = ConceptFactory(parent=source)
        child_concept = ConceptFactory(parent=source)
        parent_concept.child_concepts.add(child_concept)

        serializer = ConceptChildrenSerializer(context=self.build_context('includeChildConcepts=true'))
        self.assertEqual(serializer.get_children(parent_concept), parent_concept.child_concept_urls)

        serializer = ConceptChildrenSerializer(context=self.build_context())
        self.assertIsNone(serializer.get_children(parent_concept))

    def test_concept_parents_serializer_get_parents(self):
        source = OrganizationSourceFactory()
        parent_concept = ConceptFactory(parent=source)
        child_concept = ConceptFactory(parent=source)
        child_concept.parent_concepts.add(parent_concept)

        serializer = ConceptParentsSerializer(context=self.build_context('includeParentConcepts=true'))
        self.assertEqual(serializer.get_parents(child_concept), child_concept.parent_concept_urls)

        serializer = ConceptParentsSerializer(context=self.build_context())
        self.assertIsNone(serializer.get_parents(child_concept))


class OpenMRSConceptValidatorTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.create_lookup_concept_classes()

    def test_concept_class_is_valid_attribute_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'XYZQWERT',
                'datatype': 'None',
                'names': [ConceptNameFactory.build(name='Grip', locale='es', locale_preferred=True)]
            }
        )

        self.assertEqual(concept.errors, {'concept_class': [OPENMRS_CONCEPT_CLASS]})

    def test_data_type_is_valid_attribute_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'XYZWERRTR',
                'names': [ConceptNameFactory.build(name='Grip', locale='es', locale_preferred=True)]
            }
        )
        self.assertEqual(
            concept.errors,
            {'data_type': [OPENMRS_DATATYPE]}
        )

    def test_description_type_is_valid_attribute_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [ConceptNameFactory.build(locale_preferred=True)],
                'descriptions': [ConceptDescriptionFactory.build(type='XYZWERRTR')]
            }
        )

        self.assertEqual(
            concept.errors,
            {'descriptions': [OPENMRS_DESCRIPTION_TYPE]}
        )

    def test_name_locale_is_valid_attribute_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [ConceptNameFactory.build(locale_preferred=True, locale='FOOBAR')],
                'descriptions': [ConceptDescriptionFactory.build(locale_preferred=True, type='Definition')]
            }
        )

        self.assertEqual(
            concept.errors,
            {'names': [OPENMRS_NAME_LOCALE]}
        )

    def test_description_locale_is_valid_attribute_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [ConceptNameFactory.build(locale_preferred=True)],
                'descriptions': [ConceptDescriptionFactory.build(locale_preferred=True, locale='FOOBAR')]
            }
        )
        self.assertEqual(
            concept.errors,
            {'descriptions': [OPENMRS_DESCRIPTION_TYPE]}
        )

    def test_concept_should_have_exactly_one_preferred_name_per_locale(self):
        name_en1 = ConceptNameFactory.build(name='PreferredName1', locale_preferred=True)
        name_en2 = ConceptNameFactory.build(name='PreferredName2', locale_preferred=True)
        name_tr = ConceptNameFactory.build(name='PreferredName3', locale="tr", locale_preferred=True)
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [name_en1, name_en2, name_tr]
            }
        )

        self.assertEqual(
            concept.errors,
            {
                'names': [
                    OPENMRS_MUST_HAVE_EXACTLY_ONE_PREFERRED_NAME + ': PreferredName2 (locale: en, preferred: yes)']
            }
        )

    def test_concepts_should_have_unique_fully_specified_name_per_locale(self):
        name_fully_specified1 = ConceptNameFactory.build(name='FullySpecifiedName1')

        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)
        concept1_data = {
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c1', 'parent': source,
            'names': [name_fully_specified1]
        }
        concept2_data = {
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'c2', 'parent': source,
            'names': [name_fully_specified1]
        }
        concept1 = Concept.persist_new(concept1_data)
        concept2 = Concept.persist_new(concept2_data)

        self.assertEqual(concept1.errors, {})
        self.assertEqual(
            concept2.errors,
            {
                'names': [OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE +
                          ': FullySpecifiedName1 (locale: en, preferred: no)']
            }
        )

    def test_duplicate_fully_specified_name_should_not_fail_when_existing_name_is_retired(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept1 = Concept.persist_new(
            {
                'mnemonic': 'c1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(
                        name='FullySpecifiedName1', locale='en', locale_preferred=True,
                        type='Fully Specified', retired=True
                    ),
                    ConceptNameFactory.build(
                        name='FullySpecifiedName2', locale='en', locale_preferred=False,
                        type='Fully Specified'
                    ),
                ]
            }
        )
        concept2 = Concept.persist_new(
            {
                'mnemonic': 'c2',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(
                        name='FullySpecifiedName1', locale='en', locale_preferred=False, type='Fully Specified'
                    ),
                ]
            }
        )

        self.assertEqual(concept1.errors, {})
        self.assertTrue(concept1.id is not None)
        self.assertTrue(
            concept1.names.filter(name='FullySpecifiedName1', retired=True).exists()
        )
        self.assertEqual(concept2.errors, {})
        self.assertTrue(concept2.id is not None)

    # def test_duplicate_fully_specified_name_per_source_should_fail_case_insensitively_even_with_null_typed_name(self):
    #     """Regression for #2406: duplicate FSNs must be rejected case-insensitively even with NULL-typed names."""
    #     source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)
    #
    #     concept1 = Concept.persist_new(
    #         {
    #             'mnemonic': 'cerebral-malaria-existing',
    #             'version': HEAD,
    #             'parent': source,
    #             'concept_class': 'Diagnosis',
    #             'datatype': 'None',
    #             'names': [
    #                 ConceptNameFactory.build(
    #                     name='Cerebral malaria', locale='en', locale_preferred=True, type='Fully Specified'
    #                 ),
    #                 ConceptNameFactory.build(
    #                     name='Unrelated synonym with NULL type', locale='en', locale_preferred=False, type=None
    #                 ),
    #             ]
    #         }
    #     )
    #     concept2 = Concept.persist_new(
    #         {
    #             'mnemonic': 'cerebral-malaria-duplicate',
    #             'version': HEAD,
    #             'parent': source,
    #             'concept_class': 'Diagnosis',
    #             'datatype': 'None',
    #             'names': [
    #                 ConceptNameFactory.build(
    #                     name='cerebral malaria', locale='en', locale_preferred=True, type='Fully Specified'
    #                 ),
    #             ]
    #         }
    #     )
    #
    #     self.assertEqual(concept1.errors, {})
    #     self.assertEqual(
    #         concept2.errors,
    #         {
    #             'names': [OPENMRS_FULLY_SPECIFIED_NAME_UNIQUE_PER_SOURCE_LOCALE +
    #                       ': cerebral malaria (locale: en, preferred: yes)']
    #         }
    #     )

    def test_at_least_one_fully_specified_name_per_concept_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name='Fully Specified Name 1', locale='tr', type='Short'),
                    ConceptNameFactory.build(name='Fully Specified Name 2', locale='en', type='Short')
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {'names': [OPENMRS_AT_LEAST_ONE_FULLY_SPECIFIED_NAME]}
        )

    def test_duplicate_preferred_name_per_source_should_fail(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)
        concept1 = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(
                        name='Concept Non Unique Preferred Name', locale='en',
                        locale_preferred=True, type='Fully Specified'
                    ),
                ]
            }
        )
        concept2 = Concept.persist_new(
            {
                'mnemonic': 'concept2',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(
                        name='Concept Non Unique Preferred Name', locale='en', locale_preferred=True, type='None'
                    ),
                    ConceptNameFactory.build(
                        name='any name', locale='en', locale_preferred=False, type='Fully Specified'
                    ),
                ]
            }
        )

        self.assertEqual(concept1.errors, {})
        self.assertEqual(
            concept2.errors,
            {
                'names': [OPENMRS_PREFERRED_NAME_UNIQUE_PER_SOURCE_LOCALE +
                          ': Concept Non Unique Preferred Name (locale: en, preferred: yes)']
            }
        )

    def test_unique_preferred_name_per_locale_within_concept_negative(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept1',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(
                        name='Concept Non Unique Preferred Name', locale='es',
                        locale_preferred=True, type='FULLY_SPECIFIED'
                    ),
                    ConceptNameFactory.build(
                        name='Concept Non Unique Preferred Name', locale='es',
                        locale_preferred=True, type='FULLY_SPECIFIED'
                    ),
                ]
            }
        )

        self.assertEqual(
            concept.errors,
            {'names': ['A concept may not have more than one preferred name (per locale): '
                       'Concept Non Unique Preferred Name (locale: es, preferred: yes)']}
        )

    def test_a_preferred_name_can_not_be_a_short_name(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="ShortName", locale_preferred=True, type="Short", locale='fr'),
                    ConceptNameFactory.build(name='Fully Specified Name'),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                'names': [OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED + ': ShortName (locale: fr, preferred: yes)']
            }
        )

    def test_a_preferred_name_can_not_be_an_index_search_term(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="IndexTermName", locale_preferred=True, type=INDEX_TERM),
                    ConceptNameFactory.build(name='Fully Specified Name'),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                'names': [OPENMRS_SHORT_NAME_CANNOT_BE_PREFERRED + ': IndexTermName (locale: en, preferred: yes)']
            }
        )

    def test_a_name_can_be_equal_to_a_short_name(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="aName", type=SHORT),
                    ConceptNameFactory.build(name='aName'),
                ]
            }
        )

        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)

    def test_a_name_should_be_unique(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="aName"),
                    ConceptNameFactory.build(name='aName'),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                'names': [OPENMRS_NAMES_EXCEPT_SHORT_MUST_BE_UNIQUE]
            }
        )

    def test_only_one_fully_specified_name_per_locale(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="fully specified name1", locale='en'),
                    ConceptNameFactory.build(name='fully specified name2', locale='en'),
                    ConceptNameFactory.build(name='fully specified name3', locale='fr'),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                'names': [OPENMRS_ONE_FULLY_SPECIFIED_NAME_PER_LOCALE +
                          ': fully specified name2 (locale: en, preferred: no)']
            }
        )

    def test_no_more_than_one_short_name_per_locale(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="fully specified name1", locale='en', type='Short'),
                    ConceptNameFactory.build(name='fully specified name2', locale='en', type='Short'),
                    ConceptNameFactory.build(name='fully specified name3', locale='fr'),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                'names': [OPENMRS_NO_MORE_THAN_ONE_SHORT_NAME_PER_LOCALE +
                          ': fully specified name2 (locale: en, preferred: no)']
            }
        )

    def test_locale_preferred_name_uniqueness_doesnt_apply_to_shorts(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)

        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="mg", locale='en', locale_preferred=True),
                    ConceptNameFactory.build(name='mg', locale='en', type='Short'),
                ]
            }
        )
        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)

    def test_external_id_length(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new({
            'mnemonic': 'concept',
            'version': HEAD,
            'parent': source,
            'external_id': '1' * 37,
            'concept_class': 'Diagnosis',
            'datatype': 'None',
            'names': [
                ConceptNameFactory.build(name="mg", locale='en', locale_preferred=True)
            ]
        })
        self.assertEqual(concept.errors, {'external_id': ['Concept External ID cannot be more than 36 characters.']})
        self.assertIsNone(concept.id)

        concept = Concept.persist_new({
            'mnemonic': 'concept',
            'version': HEAD,
            'parent': source,
            'external_id': '1' * 36,
            'concept_class': 'Diagnosis',
            'datatype': 'None',
            'names': [
                ConceptNameFactory.build(name="mg", locale='en', locale_preferred=True),
            ]
        })
        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept.id)

        concept1 = Concept.persist_new({
            'mnemonic': 'concept1',
            'version': HEAD,
            'parent': source,
            'external_id': '1' * 10,
            'concept_class': 'Diagnosis',
            'datatype': 'None',
            'names': [
                ConceptNameFactory.build(name="mg1", locale='en', locale_preferred=True),
            ]
        })
        self.assertEqual(concept.errors, {})
        self.assertIsNotNone(concept1.id)

    def test_names_external_id_length(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new({
            'mnemonic': 'concept',
            'version': HEAD,
            'parent': source,
            'external_id': '1' * 36,
            'concept_class': 'Diagnosis',
            'datatype': 'None',
            'names': [
                ConceptNameFactory.build(name="mg", locale='en', locale_preferred=True, external_id='2' * 37),
            ]
        })
        self.assertEqual(
            concept.errors,
            {
                "names": ["Concept name's External ID cannot be more than 36 characters.: "
                          "mg (locale: en, preferred: yes)"],
            }
        )
        self.assertIsNone(concept.id)

    def test_description_external_id_length(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        concept = Concept.persist_new(
            {
                'mnemonic': 'concept',
                'version': HEAD,
                'parent': source,
                'external_id': '1' * 36,
                'concept_class': 'Diagnosis',
                'datatype': 'None',
                'names': [
                    ConceptNameFactory.build(name="mg", locale='en', locale_preferred=True, external_id='2' * 36),
                ],
                'descriptions': [
                    ConceptDescriptionFactory.build(name="mg", locale='en', external_id='2' * 37),
                ]
            }
        )
        self.assertEqual(
            concept.errors,
            {
                "descriptions": ["Concept description's External ID cannot be more than 36 characters.: "
                                 "mg (locale: ""en, preferred: no)"],
            }
        )
        self.assertIsNone(concept.id)


class ValidatorSpecifierTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.create_lookup_concept_classes()

    def test_specifier_should_initialize_openmrs_validator_with_reference_values(self):
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA, version=HEAD)
        expected_reference_values = {
            'DescriptionTypes': ['None', 'FULLY_SPECIFIED', 'Definition'],
            'Datatypes': ['None', 'N/A', 'Numeric', 'Coded', 'Text'],
            'Classes': ['Diagnosis', 'Drug', 'Test', 'Procedure'],
            'Locales': ['en', 'es', 'fr', 'tr', 'Abkhazian', 'English'],
            'NameTypes': ['FULLY_SPECIFIED', 'Fully Specified', 'Short', 'SHORT', 'INDEX_TERM', 'Index Term', 'None']}

        validator = ValidatorSpecifier().with_validation_schema(
            OPENMRS_VALIDATION_SCHEMA
        ).with_repo(source).with_reference_values().get()

        actual_reference_values = validator.reference_values

        self.assertEqual(sorted(expected_reference_values['Datatypes']), sorted(actual_reference_values['Datatypes']))
        self.assertEqual(sorted(expected_reference_values['Classes']), sorted(actual_reference_values['Classes']))
        self.assertEqual(sorted(expected_reference_values['Locales']), sorted(actual_reference_values['Locales']))
        self.assertEqual(sorted(expected_reference_values['NameTypes']), sorted(actual_reference_values['NameTypes']))
        self.assertEqual(
            sorted(expected_reference_values['DescriptionTypes']), sorted(actual_reference_values['DescriptionTypes'])
        )
