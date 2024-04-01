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

        org_entry1 = OrganizationURLRegistryFactory(url='https://foo.com', namespace=org.uri, organization=org)
        org_entry2 = OrganizationURLRegistryFactory(url='https://foo1.com', namespace=org.uri, organization=org)
        org_entry3 = OrganizationURLRegistryFactory(url='https://foo2.com', namespace=None, organization=org)

        UserURLRegistryFactory(url='https://foo.com', user=user, namespace=org.uri)
        user_entry2 = UserURLRegistryFactory(url='https://foo2.com', user=user, namespace=user.uri)
        user_entry3 = UserURLRegistryFactory(url='https://foo3.com', user=user, namespace=source4.parent_url)

        global_entry1 = GlobalURLRegistryFactory(url='https://foo.com', namespace=org.uri)
        global_entry2 = GlobalURLRegistryFactory(url='https://foo1.com', namespace=org.uri)
        global_entry3 = GlobalURLRegistryFactory(url='https://foo3.com', namespace=None)

        repo, entry = URLRegistry.lookup('https://foo.com', org)
        self.assertEqual(repo, source1)
        self.assertEqual(entry, org_entry1)

        repo, entry = URLRegistry.lookup('https://foo.com')
        self.assertEqual(repo, source1)
        self.assertEqual(entry, global_entry1)

        repo, entry = URLRegistry.lookup('https://foo1.com')
        self.assertEqual(repo, source2)
        self.assertEqual(entry, global_entry2)

        repo, entry = URLRegistry.lookup('https://foo1.com', org)
        self.assertEqual(repo, source2)
        self.assertEqual(entry, org_entry2)

        repo, entry = URLRegistry.lookup('https://foo2.com', org)
        self.assertEqual(repo, None)
        self.assertEqual(entry, org_entry3)

        repo, entry = URLRegistry.lookup('https://foo2.com', user)
        self.assertEqual(repo, source3)
        self.assertEqual(entry, user_entry2)

        repo, entry = URLRegistry.lookup('https://foo2.com')
        self.assertEqual(repo, None)
        self.assertEqual(entry, None)

        repo, entry = URLRegistry.lookup('https://foo3.com', org)
        self.assertEqual(repo, None)
        self.assertEqual(entry, None)

        repo, entry = URLRegistry.lookup('https://foo3.com', user)
        self.assertEqual(repo, source4)
        self.assertEqual(entry, user_entry3)

        repo, entry = URLRegistry.lookup('https://foo3.com')
        self.assertEqual(repo, None)
        self.assertEqual(entry, global_entry3)

        repo, entry = URLRegistry.lookup('https://foo4.com')
        self.assertEqual(repo, None)
        self.assertEqual(entry, None)

        repo, entry = URLRegistry.lookup('https://foo4.com', org)
        self.assertEqual(repo, None)
        self.assertEqual(entry, None)

        repo, entry = URLRegistry.lookup('https://foo4.com', user)
        self.assertEqual(repo, None)
        self.assertEqual(entry, None)


class URLRegistryLookupViewTest(OCLAPITestCase):
    @patch('core.url_registry.views.URLRegistry.lookup')
    def test_post(self, lookup_mock):
        entry = UserURLRegistryFactory(url='https://foo.com', user=None, organization=None)
        source = OrganizationSourceFactory()
        token = source.created_by.get_token()
        lookup_mock.return_value = source, entry

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
        self.assertEqual(
            response.data, {**RepoListSerializer(source).data, 'url_registry_entry': entry.relative_uri})
        lookup_mock.assert_called_with('https://foo.com', None)

        lookup_mock.return_value = None, entry
        response = self.client.post(
            '/url-registry/$lookup/',
            {'url': 'https://foo.com'},
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'url_registry_entry': entry.relative_uri})
        lookup_mock.assert_called_with('https://foo.com', None)

        lookup_mock.return_value = None, None
        response = self.client.post(
            '/url-registry/$lookup/',
            {'url': 'https://foo.com'},
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 204)
        lookup_mock.assert_called_with('https://foo.com', None)

        lookup_mock.return_value = source, entry
        response = self.client.post(
            source.organization.uri + 'url-registry/$lookup/',
            {'url': 'https://foo.com'},
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,  {**RepoListSerializer(source).data, 'url_registry_entry': entry.relative_uri})
        lookup_mock.assert_called_with('https://foo.com', source.organization)

        response = self.client.post(
            '/orgs/Foo/url-registry/$lookup/',
            {'url': 'https://foo.com'},
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 404)
