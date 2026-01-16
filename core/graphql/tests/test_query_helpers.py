import datetime
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, TransactionTestCase
from rest_framework.exceptions import AuthenticationFailed
from strawberry.exceptions import GraphQLError
from strawberry.django.views import AsyncGraphQLView

from core.common.constants import ACCESS_TYPE_NONE, HEAD
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
    search_concepts_in_es,
    concepts_for_ids,
    concepts_for_query,
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
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
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
                self.organization.mnemonic, None, self.source.mnemonic, None
            )
        self.assertEqual(success, self.source)

        with patch('core.graphql.queries.Source.get_version', return_value=None), patch(
            'core.graphql.queries.Source.find_latest_released_version_by', return_value=fallback_only
        ):
            resolved = async_to_sync(resolve_source_version)(
                self.organization.mnemonic, None, fallback_only.mnemonic, None
            )
        self.assertEqual(resolved, fallback_only)
        with self.assertRaises(GraphQLError):
            async_to_sync(resolve_source_version)(
                self.organization.mnemonic, None, 'missing-source', 'v-does-not-exist'
            )

        base_qs = build_base_queryset(self.source)
        mapping_prefetch = build_mapping_prefetch(self.source, self.audit_user)
        global_prefetch = build_global_mapping_prefetch(self.audit_user)
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
                async_to_sync(resolve_source_version)('ORG', None, 'SRC', None)

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

    def test_search_concepts_in_es_paths(self):
        hits, total = search_concepts_in_es('   ', self.source, None, user=self.audit_user)
        self.assertEqual(hits, [])
        self.assertEqual(total, 0)

        class FakeResponse:
            def __init__(self, items, total):
                self.hits = SimpleNamespace(total=SimpleNamespace(value=total))
                self._items = items

            def __iter__(self):
                for item in self._items:
                    yield SimpleNamespace(meta=SimpleNamespace(id=item), to_dict=lambda: {})

            def __len__(self):
                return len(self._items)

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
            hits, total = search_concepts_in_es(
                'search text',
                self.source,
                {'start': 0, 'end': 1},
                user=self.audit_user,
            )
        self.assertEqual([int(h.meta.id) for h in hits], [self.concept1.id])
        self.assertEqual(total, 2)

        with patch('core.graphql.queries.ConceptDocument.search', side_effect=Exception('boom')):
            hits, total = search_concepts_in_es('text', self.source, None, user=self.audit_user)
            self.assertIsNone(hits)

    def test_concepts_queries_behavior(self):
        base_qs = build_base_queryset(self.source)
        mapping_prefetch = build_mapping_prefetch(self.source, self.audit_user)
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

        class FakeHit:
            def __init__(self, id):
                self.meta = SimpleNamespace(id=id)
            def to_dict(self):
                return {'id': 'UTIL-2', 'datatype': 'Numeric', 'extras': {}}

        with patch('core.graphql.queries.search_concepts_in_es', return_value=([FakeHit(self.concept2.id)], 1)):
            concepts, total = async_to_sync(concepts_for_query)(
                base_qs, 'anything', self.source, None, mapping_prefetch
            )
        self.assertEqual(total, 1)
        self.assertEqual(concepts[0].id, self.concept2.id)

        with patch('core.graphql.queries.search_concepts_in_es', return_value=(None, 0)):
            concepts, total = async_to_sync(concepts_for_query)(
                base_qs, 'UTIL', self.source, normalize_pagination(1, 1), mapping_prefetch
            )
        self.assertEqual(total, 2)
        self.assertEqual(len(concepts), 1)

        with patch('core.graphql.queries.search_concepts_in_es', return_value=([], 2)):
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

        info_valid = SimpleNamespace(
            context=SimpleNamespace(auth_status='valid', user=self.audit_user),
            selected_fields=[SimpleNamespace(name='results', selections=[SimpleNamespace(name='conceptId', selections=[])])]
        )
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

        with patch('core.graphql.queries.search_concepts_in_es', return_value=(None, 0)):
            result_query = async_to_sync(Query().concepts)(
                info_valid,
                query='UTIL',
            )
        self.assertEqual(result_query.total_count, 2)
        self.assertEqual([item.concept_id for item in result_query.results], ['UTIL-1', 'UTIL-2'])

        with self.settings(TEST_MODE=False), patch(
            'core.graphql.queries.search_concepts_in_es', return_value=([], 2)
        ), patch('core.graphql.queries.resolve_source_version', return_value=self.source):
            result_es_empty = async_to_sync(Query().concepts)(info_valid, query='UTIL')
        self.assertEqual(result_es_empty.total_count, 2)
        self.assertEqual(result_es_empty.results, [])

        with patch('core.graphql.queries.resolve_source_version', return_value=self.source):
            result_global = async_to_sync(Query().concepts)(info_valid, query='UTIL')
        self.assertIsNone(result_global.org)
        self.assertIsNone(result_global.source)


class GraphQLAccessControlTests(TransactionTestCase):
    maxDiff = None

    def setUp(self):
        self._old_async_flag = os.environ.get('DJANGO_ALLOW_ASYNC_UNSAFE')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
        self.super_user = bootstrap_super_user()
        self.member_user, _ = create_user_with_token('graphql-member', super_user=self.super_user)
        self.outsider_user, _ = create_user_with_token('graphql-outsider', super_user=self.super_user)
        self.owner_user, _ = create_user_with_token('graphql-owner', super_user=self.super_user)

        self.private_org = OrganizationFactory(
            mnemonic='PRIVATE',
            created_by=self.super_user,
            updated_by=self.super_user,
            public_access=ACCESS_TYPE_NONE,
        )
        self.private_org.members.add(self.member_user)

        self.private_source = OrganizationSourceFactory(
            organization=self.private_org,
            mnemonic='PRIVSRC',
            name='Private Source',
            public_access=ACCESS_TYPE_NONE,
            created_by=self.super_user,
            updated_by=self.super_user,
        )
        self.private_source.organization = self.private_org
        self.private_source.user = None
        self.private_source.save(update_fields=['organization', 'user'])
        self.private_concept = ConceptFactory(
            parent=self.private_source,
            mnemonic='SECRET-1',
            created_by=self.super_user,
            updated_by=self.super_user,
        )
        self.private_concept.public_access = ACCESS_TYPE_NONE
        self.private_concept.save(update_fields=['public_access'])
        ConceptNameFactory(concept=self.private_concept, name='Secret Concept', locale='en', locale_preferred=True)

        self.public_org = OrganizationFactory(
            mnemonic='PUBLIC',
            created_by=self.super_user,
            updated_by=self.super_user,
        )
        self.public_source = OrganizationSourceFactory(
            organization=self.public_org,
            mnemonic='PUBSRC',
            name='Public Source',
            created_by=self.super_user,
            updated_by=self.super_user,
        )
        self.public_source.organization = self.public_org
        self.public_source.user = None
        self.public_source.save(update_fields=['organization', 'user'])
        self.public_concept = ConceptFactory(
            parent=self.public_source,
            mnemonic='SECRET-2',
            created_by=self.super_user,
            updated_by=self.super_user,
        )
        ConceptNameFactory(concept=self.public_concept, name='Secret Public', locale='en', locale_preferred=True)

        self.user_private_source = UserSourceFactory(
            user=self.owner_user,
            mnemonic='USERPRIV',
            name='User Private',
            public_access=ACCESS_TYPE_NONE,
            created_by=self.super_user,
            updated_by=self.super_user,
        )
        self.user_private_source.user = self.owner_user
        self.user_private_source.organization = None
        self.user_private_source.save(update_fields=['organization', 'user'])
        self.user_private_concept = ConceptFactory(
            parent=self.user_private_source,
            mnemonic='USER-SECRET',
            created_by=self.owner_user,
            updated_by=self.owner_user,
        )
        self.user_private_concept.public_access = ACCESS_TYPE_NONE
        self.user_private_concept.save(update_fields=['public_access'])

    def tearDown(self):
        if self._old_async_flag is None:
            os.environ.pop('DJANGO_ALLOW_ASYNC_UNSAFE', None)
        else:
            os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = self._old_async_flag

    def _info_for(self, user, selections):
        return SimpleNamespace(
            context=SimpleNamespace(auth_status='valid', user=user),
            selected_fields=[SimpleNamespace(name='results', selections=selections)],
        )

    def test_private_repo_requires_membership(self):
        info = self._info_for(self.outsider_user, [SimpleNamespace(name='conceptId', selections=[])])
        with self.assertRaises(GraphQLError):
            async_to_sync(Query().concepts)(
                info,
                org=self.private_org.mnemonic,
                source=self.private_source.mnemonic,
                conceptIds=[self.private_concept.mnemonic],
            )

    def test_private_repo_allows_member(self):
        info = self._info_for(self.member_user, [SimpleNamespace(name='conceptId', selections=[])])
        result = async_to_sync(Query().concepts)(
            info,
            org=self.private_org.mnemonic,
            source=self.private_source.mnemonic,
            conceptIds=[self.private_concept.mnemonic],
        )
        self.assertEqual(result.results[0].concept_id, self.private_concept.mnemonic)

    def test_private_repo_allows_owner(self):
        info = self._info_for(self.owner_user, [SimpleNamespace(name='conceptId', selections=[])])
        result = async_to_sync(Query().concepts)(
            info,
            owner=self.owner_user.username,
            source=self.user_private_source.mnemonic,
            conceptIds=[self.user_private_concept.mnemonic],
        )
        self.assertEqual(result.results[0].concept_id, self.user_private_concept.mnemonic)

    def test_global_search_filters_private_for_outsider(self):
        info = self._info_for(self.outsider_user, [SimpleNamespace(name='description', selections=[])])
        result = async_to_sync(Query().concepts)(info, query='Secret')
        self.assertEqual(
            {item.concept_id for item in result.results},
            {self.public_concept.mnemonic},
        )

    def test_global_search_includes_private_for_member(self):
        info = self._info_for(self.member_user, [SimpleNamespace(name='description', selections=[])])
        result = async_to_sync(Query().concepts)(info, query='Secret')
        self.assertEqual(
            {item.concept_id for item in result.results},
            {self.public_concept.mnemonic, self.private_concept.mnemonic},
        )

    def test_es_optimization_runs_for_authenticated(self):
        info = self._info_for(self.member_user, [SimpleNamespace(name='conceptId', selections=[])])

        class FakeHit:
            def __init__(self, id):
                self.meta = SimpleNamespace(id=id)
            def to_dict(self):
                return {'id': 'SECRET-2', 'datatype': 'Text', 'extras': {}}

        with patch(
            'core.graphql.queries.search_concepts_in_es',
            return_value=([FakeHit(self.public_concept.id)], 1),
        ) as mock:
            with self.settings(TEST_MODE=False):
                result = async_to_sync(Query().concepts)(info, query='Secret')
        mock.assert_called_once()
        self.assertEqual(result.total_count, 1)
        self.assertEqual(result.results[0].concept_id, 'SECRET-2')
