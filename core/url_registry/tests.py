from mock.mock import patch
from rest_framework.exceptions import ErrorDetail

from core.common.tests import OCLTestCase, OCLAPITestCase
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.repos.serializers import RepoListSerializer
from core.sources.tests.factories import OrganizationSourceFactory
from core.url_registry.factories import OrganizationURLRegistryFactory, UserURLRegistryFactory, GlobalURLRegistryFactory
from core.url_registry.models import URLRegistry
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class URLRegistryTest(OCLTestCase):
    def test_owner(self):
        org = Organization()
        user = UserProfile()
        self.assertEqual(URLRegistry().owner, None)
        self.assertEqual(URLRegistry(organization=org).owner, org)
        self.assertEqual(URLRegistry(user=user).owner, user)

    def test_owner_type(self):
        org = Organization()
        user = UserProfile()
        self.assertEqual(URLRegistry().owner_type, None)
        self.assertEqual(URLRegistry(organization=org).owner_type, 'Organization')
        self.assertEqual(URLRegistry(user=user).owner_type, 'User')

    def test_owner_url(self):
        org = Organization(uri='/orgs/foo/')
        user = UserProfile(uri='/users/foo/')
        self.assertEqual(URLRegistry().owner_url, '/')
        self.assertEqual(URLRegistry(organization=org).owner_url, '/orgs/foo/')
        self.assertEqual(URLRegistry(user=user).owner_url, '/users/foo/')

    @patch('core.common.models.ConceptContainerModel.resolve_reference_expression')
    def test_lookup(self, resolve_mock):
        resolve_mock.return_value = 'something'

        org = OrganizationFactory()
        user = UserProfileFactory()
        OrganizationURLRegistryFactory(url='https://foo.com', namespace='org1', organization=org)
        OrganizationURLRegistryFactory(url='https://foo1.com', namespace='org2', organization=org)
        UserURLRegistryFactory(url='https://foo.com', user=user, namespace='user1')
        UserURLRegistryFactory(url='https://foo2.com', user=user, namespace='user2')
        GlobalURLRegistryFactory(url='https://foo.com', namespace='global1')
        GlobalURLRegistryFactory(url='https://foo1.com', namespace='global2')
        GlobalURLRegistryFactory(url='https://foo3.com', namespace='global3')

        self.assertEqual(URLRegistry.lookup('https://foo.com', org), 'something')
        resolve_mock.assert_called_with(url='https://foo.com', namespace='org1')

        self.assertEqual(URLRegistry.lookup('https://foo.com'), 'something')
        resolve_mock.assert_called_with(url='https://foo.com', namespace='global1')

        self.assertEqual(URLRegistry.lookup('https://foo2.com', org), None)

        self.assertEqual(URLRegistry.lookup('https://foo2.com', user), 'something')
        resolve_mock.assert_called_with(url='https://foo2.com', namespace='user2')

        self.assertEqual(URLRegistry.lookup('https://foo2.com'), None)

        self.assertEqual(URLRegistry.lookup('https://foo1.com'), 'something')
        resolve_mock.assert_called_with(url='https://foo1.com', namespace='global2')

        self.assertEqual(URLRegistry.lookup('https://foo1.com', org), 'something')
        resolve_mock.assert_called_with(url='https://foo1.com', namespace='org2')

        self.assertEqual(URLRegistry.lookup('https://foo3.com', org), None)

        self.assertEqual(URLRegistry.lookup('https://foo3.com', user), None)
        self.assertEqual(URLRegistry.lookup('https://foo3.com'), 'something')
        resolve_mock.assert_called_with(url='https://foo3.com', namespace='global3')


class URLRegistryLookupViewTest(OCLAPITestCase):
    @patch('core.url_registry.views.URLRegistry.lookup')
    def test_post(self, lookup_mock):
        source = OrganizationSourceFactory()
        token = source.created_by.get_token()
        lookup_mock.return_value = source

        response = self.client.post(
            '/url-registry/$lookup/',
            {'url': ''},
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {'detail': ErrorDetail(string='url is required in query params', code='bad_request')}
        )

        response = self.client.post(
            '/url-registry/$lookup/',
            {'url': 'https://foo.com'},
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, RepoListSerializer(source).data)
        lookup_mock.assert_called_with('https://foo.com', None)

        lookup_mock.return_value = None
        response = self.client.post(
            '/url-registry/$lookup/',
            {'url': 'https://foo.com'},
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 404)
        lookup_mock.assert_called_with('https://foo.com', None)

        lookup_mock.return_value = source
        response = self.client.post(
            source.organization.uri + 'url-registry/$lookup/',
            {'url': 'https://foo.com'},
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, RepoListSerializer(source).data)
        lookup_mock.assert_called_with('https://foo.com', source.organization)
