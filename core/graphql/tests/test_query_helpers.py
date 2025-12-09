import datetime
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from rest_framework.exceptions import AuthenticationFailed
from strawberry.exceptions import GraphQLError
from strawberry.django.views import AsyncGraphQLView

from core.common.constants import HEAD
from core.concepts.tests.factories import (
    ConceptDescriptionFactory,
    ConceptFactory,
    ConceptNameFactory,
)
from core.concepts.models import Concept
from core.graphql.queries import (
    Query,
    _to_bool,
    _to_float,
    apply_slice,
    build_base_queryset,
    build_datatype,
    build_global_mapping_prefetch,
    build_mapping_prefetch,
    concept_ids_from_es,
    concepts_for_ids,
    concepts_for_query,
    fallback_db_search,
    format_datetime_for_api,
    has_next,
    normalize_pagination,
    resolve_coded_datatype_details,
    resolve_datatype_details,
    resolve_description,
    resolve_is_set_flag,
    resolve_numeric_datatype_details,
    resolve_source_version,
    resolve_text_datatype_details,
    serialize_concepts,
    serialize_mappings,
    serialize_names,
    with_concept_related,
)
from core.graphql.schema import schema
from core.graphql.tests.conftest import bootstrap_super_user, create_user_with_token
from core.graphql.views import AuthenticatedGraphQLView
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.sources.models import Source


class AuthenticatedGraphQLViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.super_user = bootstrap_super_user()
        self.user, self.token = create_user_with_token('graphql-view-user', super_user=self.super_user)

    def test_dispatch_enforces_csrf_without_auth_header(self):
        view = AuthenticatedGraphQLView(schema=schema)
        request = self.factory.post('/graphql/', data='{}', content_type='application/json')
        view.setup(request)

        response = async_to_sync(view.dispatch)(request)

        self.assertEqual(response.status_code, 403)

    def test_dispatch_skips_csrf_when_auth_header_present(self):
        view = AuthenticatedGraphQLView(schema=schema)
        request = self.factory.post(
            '/graphql/',
            data='{}',
            content_type='application/json',
            HTTP_AUTHORIZATION='Token abc',
        )
        view.setup(request)
        with patch.object(AsyncGraphQLView, 'dispatch', new=AsyncMock(return_value=HttpResponse(status=200))) as mock:
            response = async_to_sync(view.dispatch)(request)
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once()

    def test_get_sets_csrf_cookie_for_anonymous(self):
        view = AuthenticatedGraphQLView(schema=schema)
        request = self.factory.get('/graphql/')
        with patch.object(AsyncGraphQLView, 'dispatch', new=AsyncMock(return_value=HttpResponse(status=200))):
            response = async_to_sync(view.dispatch)(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn('csrftoken', response.cookies)

    def test_get_context_handles_session_and_token_states(self):
        view = AuthenticatedGraphQLView(schema=schema)

        with patch.object(AsyncGraphQLView, 'get_context', new=AsyncMock(return_value=SimpleNamespace())):
            # Authenticated via session
            session_request = self.factory.post('/graphql/', data='{}', content_type='application/json')
            session_request.user = self.user
            context = async_to_sync(view.get_context)(session_request)
            self.assertEqual(context.user, self.user)
            self.assertEqual(context.auth_status, 'valid')

            # Missing auth header
            anon_request = self.factory.post('/graphql/', data='{}', content_type='application/json')
            context = async_to_sync(view.get_context)(anon_request)
            self.assertIsInstance(context.user, AnonymousUser)
            self.assertEqual(context.auth_status, 'none')

            # Invalid token
            invalid_request = self.factory.post(
                '/graphql/', data='{}', content_type='application/json', HTTP_AUTHORIZATION='Token bad'
            )
            with patch('core.graphql.views.OCLAuthentication.authenticate', side_effect=AuthenticationFailed('boom')):
                context = async_to_sync(view.get_context)(invalid_request)
            self.assertEqual(context.auth_status, 'invalid')
            self.assertIsInstance(context.user, AnonymousUser)

            # Valid token
            valid_request = self.factory.post(
                '/graphql/', data='{}', content_type='application/json', HTTP_AUTHORIZATION='Token good'
            )
            with patch('core.graphql.views.OCLAuthentication.authenticate', return_value=(self.user, 'auth')):
                context = async_to_sync(view.get_context)(valid_request)
            self.assertEqual(context.user, self.user)
            self.assertEqual(context.auth_status, 'valid')


class QueryHelperTests(TestCase):
    maxDiff = None

    def setUp(self):
        self._old_async_flag = os.environ.get('DJANGO_ALLOW_ASYNC_UNSAFE')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
        self.super_user = bootstrap_super_user()
        self.audit_user, _ = create_user_with_token('graphql-helper', super_user=self.super_user)
        self.organization = OrganizationFactory(
            mnemonic='UTILS',
            created_by=self.super_user,
            updated_by=self.super_user,
        )
        self.source = OrganizationSourceFactory(
            organization=self.organization,
            mnemonic='UTILS',
            name='Utils',
            version=HEAD,
            created_by=self.super_user,
            updated_by=self.super_user,
        )
        self.release_version = OrganizationSourceFactory(
            organization=self.organization,
            mnemonic=self.source.mnemonic,
            name=self.source.name,
            version='2024.02',
            released=True,
            is_latest_version=True,
            created_by=self.super_user,
            updated_by=self.super_user,
        )
        self.concept1 = ConceptFactory(
            parent=self.source,
            mnemonic='UTIL-1',
            datatype='Text',
            concept_class='Diagnosis',
            extras={'is_set': 'yes', 'text_format': 'markdown'},
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        ConceptNameFactory(concept=self.concept1, name='Name EN', locale='en', locale_preferred=True)
        ConceptDescriptionFactory(
            concept=self.concept1, name='FR description', locale='fr', locale_preferred=False
        )
        ConceptDescriptionFactory(
            concept=self.concept1, name='EN description', locale='en', locale_preferred=True
        )
        self.concept2 = ConceptFactory(
            parent=self.source,
            mnemonic='UTIL-2',
            datatype='Numeric',
            extras={
                'units': 'mg',
                'low_normal': 1,
                'hi_normal': 5,
                'low_critical': 0.5,
                'hi_critical': 9,
            },
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        ConceptNameFactory(concept=self.concept2, name='Numeric Name', locale='en', locale_preferred=True)
        ConceptDescriptionFactory(
            concept=self.concept2, name='Numeric description', locale='en', locale_preferred=True
        )
        self.mapping = MappingFactory(
            parent=self.source,
            from_concept=self.concept1,
            to_concept=self.concept2,
            map_type='Same As',
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )

    def tearDown(self):
        if self._old_async_flag is None:
            os.environ.pop('DJANGO_ALLOW_ASYNC_UNSAFE', None)
        else:
            os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = self._old_async_flag

    def test_resolve_source_version_and_base_queries(self):
        fallback_only = OrganizationSourceFactory(
            organization=self.organization,
            mnemonic='FALLBACK',
            version='2024.05',
            released=True,
            is_latest_version=True,
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        with patch('core.graphql.queries.Source.get_version', return_value=self.source):
            success = async_to_sync(resolve_source_version)(
                self.organization.mnemonic, self.source.mnemonic, None
            )
        self.assertEqual(success, self.source)

        with patch('core.graphql.queries.Source.get_version', return_value=None), patch(
            'core.graphql.queries.Source.find_latest_released_version_by', return_value=fallback_only
        ):
            resolved = async_to_sync(resolve_source_version)(
                self.organization.mnemonic, fallback_only.mnemonic, None
            )
        self.assertEqual(resolved, fallback_only)
        with self.assertRaises(GraphQLError):
            async_to_sync(resolve_source_version)(
                self.organization.mnemonic, 'missing-source', 'v-does-not-exist'
            )

        base_qs = build_base_queryset(self.source)
        mapping_prefetch = build_mapping_prefetch(self.source)
        global_prefetch = build_global_mapping_prefetch()
        self.assertIsNotNone(mapping_prefetch)
        self.assertIsNotNone(global_prefetch)

        pagination = normalize_pagination(2, 1)
        self.assertTrue(has_next(5, pagination))
        with self.assertRaises(GraphQLError):
            normalize_pagination(0, 0)

        sliced = apply_slice(base_qs.order_by('mnemonic'), pagination)
        self.assertEqual(list(sliced.values_list('mnemonic', flat=True)), ['UTIL-2'])
        related_qs = with_concept_related(base_qs, mapping_prefetch)
        self.assertGreaterEqual(related_qs.count(), 2)

    def test_resolve_source_version_error_path_and_pagination_defaults(self):
        with patch('core.graphql.queries.Source.get_version', return_value=None), patch(
            'core.graphql.queries.Source.find_latest_released_version_by', return_value=None
        ):
            with self.assertRaises(GraphQLError):
                async_to_sync(resolve_source_version)('ORG', 'SRC', None)

        self.assertIsNone(normalize_pagination(None, None))
        self.assertFalse(has_next(10, None))
        qs = Concept.objects.all()
        self.assertEqual(apply_slice(qs, None), qs)

    def test_serializers_and_resolvers(self):
        self.source.default_locale = 'fr'
        self.source.save(update_fields=['default_locale'])
        self.concept1.graphql_mappings = [self.mapping]
        serialized = serialize_concepts([self.concept1])[0]
        self.assertEqual(serialized.mappings[0].to_code, self.concept2.mnemonic)
        self.assertEqual(serialized.metadata.created_by, self.audit_user.username)
        self.assertEqual(serialized.description, 'FR description')

        concept_no_desc = ConceptFactory(
            parent=self.source,
            mnemonic='NO-DESC',
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        self.assertIsNone(resolve_description(concept_no_desc))
        names = serialize_names(self.concept1)
        self.assertTrue(any(item.locale == 'en' for item in names))
        mappings = serialize_mappings(self.concept1)
        self.assertEqual(mappings[0].map_type, 'Same As')

        self.assertTrue(resolve_is_set_flag(self.concept1))
        self.concept1.extras = {}
        self.concept1.save(update_fields=['extras'])
        self.assertIsNone(resolve_is_set_flag(self.concept1))
        self.assertTrue(resolve_is_set_flag(SimpleNamespace(is_set='yes')))

        class MissingParent:
            def __getattr__(self, _):
                raise Source.DoesNotExist

        no_pref_desc = SimpleNamespace(
            descriptions=SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(description='No preferred', locale='es', locale_preferred=False)
                ]
            ),
            parent=MissingParent(),
        )
        self.assertEqual(resolve_description(no_pref_desc), 'No preferred')

        self.assertFalse(resolve_is_set_flag(SimpleNamespace(is_set='false')))

    def test_datatype_helpers(self):
        self.assertIsNone(_to_float(None))
        self.assertIsNone(_to_float('bad'))
        self.assertEqual(_to_float('3.5'), 3.5)

        self.assertTrue(_to_bool(True))
        self.assertEqual(_to_bool(0), False)
        self.assertEqual(_to_bool('yes'), True)
        self.assertEqual(_to_bool('no'), False)
        self.assertIsNone(_to_bool('maybe'))

        numeric_details = resolve_numeric_datatype_details(self.concept2)
        self.assertEqual(numeric_details.units, 'mg')

        coded = ConceptFactory(
            parent=self.source,
            mnemonic='CODED-1',
            datatype='Coded',
            extras={'allowMultipleAnswers': 'Yes'},
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        coded_details = resolve_coded_datatype_details(coded)
        self.assertTrue(coded_details.allow_multiple)

        text_details = resolve_text_datatype_details(self.concept1)
        self.assertEqual(text_details.text_format, 'markdown')

        self.assertIsNotNone(resolve_datatype_details(self.concept2))
        self.assertIsNone(resolve_datatype_details(SimpleNamespace(datatype='Unknown')))

        naive = datetime.datetime(2024, 1, 1, 12, 0, 0)
        formatted = format_datetime_for_api(naive)
        self.assertTrue(formatted.endswith('Z'))

        no_datatype = ConceptFactory(
            parent=self.source,
            mnemonic='NO-DATATYPE',
            datatype='',
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        self.assertIsNone(build_datatype(no_datatype))

        empty_numeric = ConceptFactory(
            parent=self.source,
            mnemonic='EMPTY-NUM',
            datatype='Numeric',
            extras={},
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        self.assertIsNone(resolve_numeric_datatype_details(empty_numeric))
        self.assertIsNone(resolve_coded_datatype_details(SimpleNamespace(extras={})))
        self.assertIsNone(resolve_text_datatype_details(SimpleNamespace(extras={})))
        self.assertIsNone(format_datetime_for_api(None))

    def test_concept_ids_from_es_paths(self):
        ids, total = concept_ids_from_es('   ', self.source, None)
        self.assertEqual(ids, [])
        self.assertEqual(total, 0)

        class FakeResponse:
            def __init__(self, items, total):
                self.hits = SimpleNamespace(total=SimpleNamespace(value=total))
                self._items = items

            def __iter__(self):
                for item in self._items:
                    yield SimpleNamespace(meta=SimpleNamespace(id=item))

        class FakeSearch:
            def __init__(self, items, total=None):
                self._items = items
                self._total = len(items) if total is None else total

            def filter(self, *_args, **_kwargs):
                return self

            def query(self, *_args, **_kwargs):
                return self

            def __getitem__(self, key):
                if isinstance(key, slice):
                    return FakeSearch(self._items[key], total=self._total)
                return self

            def params(self, **_kwargs):
                return self

            def execute(self):
                return FakeResponse(self._items, self._total)

        with patch(
            'core.graphql.queries.ConceptDocument.search',
            return_value=FakeSearch([self.concept1.id, self.concept2.id]),
        ):
            ids, total = concept_ids_from_es('search text', self.source, {'start': 0, 'end': 1})
        self.assertEqual(ids, [self.concept1.id])
        self.assertEqual(total, 2)

        with patch('core.graphql.queries.ConceptDocument.search', side_effect=Exception('boom')):
            self.assertIsNone(concept_ids_from_es('text', self.source, None))

    def test_fallback_and_concepts_queries(self):
        base_qs = build_base_queryset(self.source)
        self.assertEqual(fallback_db_search(base_qs, '   ').count(), 0)
        self.assertIn(self.concept1.id, list(fallback_db_search(base_qs, 'UTIL').values_list('id', flat=True)))

        mapping_prefetch = build_mapping_prefetch(self.source)
        with self.assertRaises(GraphQLError):
            async_to_sync(concepts_for_ids)(base_qs, [], normalize_pagination(1, 1), mapping_prefetch)

        concepts, total = async_to_sync(concepts_for_ids)(
            base_qs,
            ['UTIL-2', 'UTIL-1', 'UTIL-2'],
            normalize_pagination(1, 2),
            mapping_prefetch,
        )
        self.assertEqual(total, 2)
        self.assertEqual([c.mnemonic for c in concepts], ['UTIL-2', 'UTIL-1'])

        with patch('core.graphql.queries.concept_ids_from_es', return_value=([self.concept2.id], 1)):
            concepts, total = async_to_sync(concepts_for_query)(
                base_qs, 'anything', self.source, None, mapping_prefetch
            )
        self.assertEqual(total, 1)
        self.assertEqual(concepts[0].id, self.concept2.id)

        with patch('core.graphql.queries.concept_ids_from_es', return_value=None):
            concepts, total = async_to_sync(concepts_for_query)(
                base_qs, 'UTIL', self.source, normalize_pagination(1, 1), mapping_prefetch
            )
        self.assertGreaterEqual(total, 1)

        with patch('core.graphql.queries.concept_ids_from_es', return_value=([], 2)):
            concepts, total = async_to_sync(concepts_for_query)(
                base_qs, 'UTIL', self.source, None, mapping_prefetch
            )
        self.assertEqual(total, 2)
        self.assertEqual(concepts, [])

    def test_query_concepts_auth_and_results(self):
        info_none = SimpleNamespace(context=SimpleNamespace(auth_status='none'))
        with self.assertRaises(GraphQLError):
            async_to_sync(Query().concepts)(info_none)

        info_invalid = SimpleNamespace(context=SimpleNamespace(auth_status='invalid'))
        with self.assertRaises(GraphQLError):
            async_to_sync(Query().concepts)(info_invalid, query='test')

        info_valid = SimpleNamespace(context=SimpleNamespace(auth_status='valid'))
        with self.assertRaises(GraphQLError):
            async_to_sync(Query().concepts)(info_valid)

        with patch('core.graphql.queries.resolve_source_version', return_value=self.source):
            result_ids = async_to_sync(Query().concepts)(
                info_valid,
                org=self.organization.mnemonic,
                source=self.source.mnemonic,
                conceptIds=['UTIL-1'],
                page=1,
                limit=1,
            )
        self.assertEqual(result_ids.total_count, 1)
        self.assertEqual(result_ids.results[0].concept_id, 'UTIL-1')
        self.assertEqual(result_ids.page, 1)
        self.assertEqual(result_ids.limit, 1)

        with patch('core.graphql.queries.concept_ids_from_es', return_value=None):
            result_query = async_to_sync(Query().concepts)(
                info_valid,
                query='UTIL',
            )
        self.assertGreaterEqual(result_query.total_count, 1)
        self.assertFalse(result_query.has_next_page)

        with patch('core.graphql.queries.concept_ids_from_es', return_value=([], 2)), patch(
            'core.graphql.queries.resolve_source_version', return_value=self.source
        ):
            result_es_empty = async_to_sync(Query().concepts)(info_valid, query='UTIL')
        self.assertEqual(result_es_empty.total_count, 2)
        self.assertEqual(result_es_empty.results, [])

        with patch('core.graphql.queries.resolve_source_version', return_value=self.source):
            result_global = async_to_sync(Query().concepts)(info_valid, query='UTIL')
        self.assertIsNone(result_global.org)
        self.assertIsNone(result_global.source)
