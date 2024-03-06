from mock.mock import patch
from rest_framework.exceptions import ErrorDetail

from core.common.tests import OCLTestCase, OCLAPITestCase
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.repos.serializers import RepoListSerializer
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
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

    def test_lookup(self):
        org = OrganizationFactory()
        user = UserProfileFactory()
        source1 = OrganizationSourceFactory(organization=org, canonical_url='https://foo.com')
        source2 = OrganizationSourceFactory(organization=org, canonical_url='https://foo1.com')
        source3 = UserSourceFactory(user=user, canonical_url='https://foo2.com')
        source4 = OrganizationSourceFactory(canonical_url='https://foo3.com')

        OrganizationURLRegistryFactory(url='https://foo.com', namespace=org.uri, organization=org)
        OrganizationURLRegistryFactory(url='https://foo1.com', namespace=org.uri, organization=org)
        OrganizationURLRegistryFactory(url='https://foo2.com', namespace=None, organization=org)

        UserURLRegistryFactory(url='https://foo.com', user=user, namespace=org.uri)
        UserURLRegistryFactory(url='https://foo2.com', user=user, namespace=user.uri)
        UserURLRegistryFactory(url='https://foo3.com', user=user, namespace=source4.parent_url)

        GlobalURLRegistryFactory(url='https://foo.com', namespace=org.uri)
        GlobalURLRegistryFactory(url='https://foo1.com', namespace=org.uri)
        GlobalURLRegistryFactory(url='https://foo3.com', namespace=None)

        self.assertEqual(URLRegistry.lookup('https://foo.com', org), source1)
        self.assertEqual(URLRegistry.lookup('https://foo.com'), source1)

        self.assertEqual(URLRegistry.lookup('https://foo1.com'), source2)
        self.assertEqual(URLRegistry.lookup('https://foo1.com', org), source2)

        self.assertEqual(URLRegistry.lookup('https://foo2.com', org), None)
        self.assertEqual(URLRegistry.lookup('https://foo2.com', user), source3)
        self.assertEqual(URLRegistry.lookup('https://foo2.com'), None)

        self.assertEqual(URLRegistry.lookup('https://foo3.com', org), None)
        self.assertEqual(URLRegistry.lookup('https://foo3.com', user), source4)
        self.assertEqual(URLRegistry.lookup('https://foo3.com'), None)

        self.assertEqual(URLRegistry.lookup('https://foo4.com'), None)
        self.assertEqual(URLRegistry.lookup('https://foo4.com', org), None)
        self.assertEqual(URLRegistry.lookup('https://foo4.com', user), None)


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

        response = self.client.post(
            '/orgs/Foo/url-registry/$lookup/',
            {'url': 'https://foo.com'},
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 404)
