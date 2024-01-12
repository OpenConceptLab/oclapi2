from rest_framework.exceptions import ErrorDetail

from core.common.tests import OCLAPITestCase
from core.orgs.tests.factories import OrganizationFactory
from core.url_registry.factories import GlobalURLRegistryFactory, OrganizationURLRegistryFactory, UserURLRegistryFactory
from core.url_registry.models import URLRegistry
from core.users.tests.factories import UserProfileFactory


class URLRegistriesViewTest(OCLAPITestCase):
    def test_post_global_registry(self):
        user = UserProfileFactory()
        response = self.client.post(
            '/url-registry/',
            {
                'name': 'GlobalRegistry',
                'url': 'https://foo.bar.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['id'])
        self.assertTrue(
            URLRegistry.objects.filter(
                is_active=True, organization__isnull=True, user__isnull=True, url='https://foo.bar.com',
                namespace__isnull=True
            ).exists()
        )
        self.assertEqual(URLRegistry.objects.count(), 1)

        # duplicate entry
        response = self.client.post(
            '/url-registry/',
            {
                'name': 'GlobalRegistry',
                'url': 'https://foo.bar.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {'non_fields_error': [ErrorDetail(string='This entry already exists.', code='invalid')]}
        )
        self.assertEqual(URLRegistry.objects.count(), 1)

        # entry with same namespace and url but different name is duplicate
        response = self.client.post(
            '/url-registry/',
            {
                'name': 'GlobalRegistry2',
                'namespace': '/registry-1/',
                'url': 'https://foo.bar.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {'non_fields_error': [ErrorDetail(string='This entry already exists.', code='invalid')]}
        )
        self.assertEqual(URLRegistry.objects.count(), 1)

        # entry with same name and namespace but different url is not duplicate
        response = self.client.post(
            '/url-registry/',
            {
                'name': 'GlobalRegistry1',
                'namespace': '/registry-1/',
                'url': 'https://foo.bar.1.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['id'])
        self.assertTrue(
            URLRegistry.objects.filter(
                is_active=True, organization__isnull=True, user__isnull=True, url='https://foo.bar.1.com',
                namespace__isnull=True
            ).exists()
        )
        self.assertEqual(URLRegistry.objects.count(), 2)

    def test_post_org_registry(self):
        org = OrganizationFactory()
        user = org.created_by
        response = self.client.post(
            org.uri + 'url-registry/',
            {
                'name': 'GlobalRegistry',
                'namespace': 'Foobar',  # will be set correctly
                'url': 'https://foo.bar.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['id'])
        self.assertEqual(response.data['namespace'], org.uri)
        self.assertTrue(
            URLRegistry.objects.filter(
                is_active=True, organization=org, url='https://foo.bar.com'
            ).exists()
        )
        self.assertEqual(URLRegistry.objects.count(), 1)

        # duplicate entry
        response = self.client.post(
            org.uri + 'url-registry/',
            {
                'name': 'GlobalRegistry',
                'url': 'https://foo.bar.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {'non_fields_error': [ErrorDetail(string='This entry already exists.', code='invalid')]}
        )
        self.assertEqual(URLRegistry.objects.count(), 1)

        response = self.client.post(
            org.uri + 'url-registry/',
            {
                'name': 'GlobalRegistry',
                'url': 'https://foo.bar.1.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['id'])
        self.assertEqual(response.data['namespace'], org.uri)
        self.assertTrue(
            URLRegistry.objects.filter(
                is_active=True, organization=org, url='https://foo.bar.1.com'
            ).exists()
        )
        self.assertEqual(URLRegistry.objects.count(), 2)

        response = self.client.post(
            '/url-registry/',
            {
                'name': 'GlobalRegistry',
                'namespace': org.uri,
                'url': 'https://foo.bar.2.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['id'])
        self.assertEqual(response.data['namespace'], org.uri)
        self.assertTrue(
            URLRegistry.objects.filter(
                is_active=True, organization=org, url='https://foo.bar.2.com'
            ).exists()
        )
        self.assertEqual(URLRegistry.objects.count(), 3)

    def test_post_user_registry(self):
        user = UserProfileFactory()
        response = self.client.post(
            user.uri + 'url-registry/',
            {
                'name': 'GlobalRegistry',
                'namespace': 'Foobar',  # will be set correctly
                'url': 'https://foo.bar.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['id'])
        self.assertEqual(response.data['namespace'], user.uri)
        self.assertTrue(
            URLRegistry.objects.filter(
                is_active=True, user=user, url='https://foo.bar.com'
            ).exists()
        )
        self.assertEqual(URLRegistry.objects.count(), 1)

        # duplicate entry
        response = self.client.post(
            user.uri + 'url-registry/',
            {
                'name': 'GlobalRegistry',
                'url': 'https://foo.bar.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {'non_fields_error': [ErrorDetail(string='This entry already exists.', code='invalid')]}
        )
        self.assertEqual(URLRegistry.objects.count(), 1)

        response = self.client.post(
            user.uri + 'url-registry/',
            {
                'name': 'GlobalRegistry',
                'url': 'https://foo.bar.1.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['id'])
        self.assertEqual(response.data['namespace'], user.uri)
        self.assertTrue(
            URLRegistry.objects.filter(
                is_active=True, user=user, url='https://foo.bar.1.com'
            ).exists()
        )
        self.assertEqual(URLRegistry.objects.count(), 2)

        response = self.client.post(
            '/url-registry/',
            {
                'name': 'GlobalRegistry',
                'namespace': user.uri,
                'url': 'https://foo.bar.2.com',
            },
            HTTP_AUTHORIZATION=f"Token {user.get_token()}",
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['id'])
        self.assertEqual(response.data['namespace'], user.uri)
        self.assertTrue(
            URLRegistry.objects.filter(
                is_active=True, user=user, url='https://foo.bar.2.com'
            ).exists()
        )
        self.assertEqual(URLRegistry.objects.count(), 3)

    def test_get(self):
        global_registry = GlobalURLRegistryFactory(name='global')
        org_registry = OrganizationURLRegistryFactory(name='org')
        user_registry = UserURLRegistryFactory(name='user')

        response = self.client.get('/url-registry/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'global')
        self.assertEqual(response.data[0]['id'], global_registry.id)

        response = self.client.get(org_registry.owner.uri + 'url-registry/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'org')
        self.assertEqual(response.data[0]['id'], org_registry.id)

        response = self.client.get(user_registry.owner.uri + 'url-registry/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'user')
        self.assertEqual(response.data[0]['id'], user_registry.id)

        response = self.client.get(user_registry.owner.uri + 'orgs/url-registry/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        org_registry.organization.members.add(user_registry.user)

        response = self.client.get(user_registry.owner.uri + 'orgs/url-registry/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'org')
        self.assertEqual(response.data[0]['id'], org_registry.id)

        response = self.client.get(
            '/user/orgs/url-registry/',
            HTTP_AUTHORIZATION=f"Token {user_registry.user.get_token()}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'org')
        self.assertEqual(response.data[0]['id'], org_registry.id)


class URLRegistryViewTest(OCLAPITestCase):
    def test_get(self):
        global_registry = GlobalURLRegistryFactory(name='global')
        org_registry = OrganizationURLRegistryFactory(name='org')
        user_registry = UserURLRegistryFactory(name='user')

        response = self.client.get(f'/url-registry/{global_registry.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'global')
        self.assertEqual(response.data['url'], global_registry.url)

        response = self.client.get(f'/url-registry/{org_registry.id}/')

        self.assertEqual(response.status_code, 404)

        response = self.client.get(f'{org_registry.organization.uri}url-registry/{org_registry.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'org')
        self.assertEqual(response.data['url'], org_registry.url)

        response = self.client.get(f'{user_registry.user.uri}url-registry/{user_registry.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'user')
        self.assertEqual(response.data['url'], user_registry.url)
