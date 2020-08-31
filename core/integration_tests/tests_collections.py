from rest_framework.exceptions import ErrorDetail

from core.collections.tests.factories import CollectionFactory
from core.common.tests import OCLAPITestCase
from core.orgs.tests.factories import OrganizationFactory
from core.users.tests.factories import UserProfileFactory


class CollectionListViewTest(OCLAPITestCase):
    def test_get_200(self):
        coll = CollectionFactory(mnemonic='coll1')

        response = self.client.get(
            '/collections/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['short_code'], 'coll1')
        self.assertEqual(response.data[0]['id'], 'coll1')
        self.assertEqual(response.data[0]['url'], coll.uri)

        response = self.client.get(
            '/orgs/{}/collections/'.format(coll.parent.mnemonic),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['short_code'], 'coll1')
        self.assertEqual(response.data[0]['id'], 'coll1')
        self.assertEqual(response.data[0]['url'], coll.uri)

    def test_post_201(self):
        org = OrganizationFactory(mnemonic='org')
        user = UserProfileFactory(organizations=[org], username='user')

        response = self.client.post(
            '/orgs/org/collections/',
            dict(
                default_locale='en', supported_locales='en,fr', id='coll', name='Collection', mnemonic='coll',
                extras=dict(foo='bar')
            ),
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['uuid'])
        self.assertEqual(response.data['id'], 'coll')
        self.assertEqual(response.data['name'], 'Collection')
        self.assertEqual(response.data['default_locale'], 'en')
        self.assertEqual(response['Location'], '/orgs/org/collections/coll/')
        self.assertEqual(org.collection_set.count(), 1)

        response = self.client.post(
            '/users/user/collections/',
            dict(
                default_locale='en', supported_locales='en,fr', id='coll', name='Collection', mnemonic='coll',
                extras=dict(foo='bar')
            ),
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['uuid'])
        self.assertEqual(response.data['id'], 'coll')
        self.assertEqual(response.data['name'], 'Collection')
        self.assertEqual(response.data['default_locale'], 'en')
        self.assertEqual(response['Location'], '/users/user/collections/coll/')
        self.assertEqual(user.collection_set.count(), 1)

        org_collection = org.collection_set.first()
        user_collection = user.collection_set.first()

        self.assertNotEqual(org_collection.id, user_collection.id)

    def test_post_400(self):
        org = OrganizationFactory(mnemonic='org')
        user = UserProfileFactory(organizations=[org])

        response = self.client.post(
            '/orgs/org/collections/',
            dict(
                default_locale='en', supported_locales='en,fr', id='coll',
                extras=dict(foo='bar')
            ),
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(name=[ErrorDetail(string='This field is required.', code='required')]))
        self.assertEqual(org.collection_set.count(), 0)

    def test_post_403(self):
        OrganizationFactory(mnemonic='org')

        response = self.client.post(
            '/orgs/org/collections/',
            dict(
                default_locale='en', supported_locales='en,fr', id='coll',
            ),
            format='json'
        )

        self.assertEqual(response.status_code, 403)

    def test_post_405(self):
        response = self.client.post(
            '/collections/',
            dict(
                default_locale='en', supported_locales='en,fr', id='coll',
            ),
            format='json'
        )

        self.assertEqual(response.status_code, 405)


class CollectionRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def test_get_200(self):
        coll = CollectionFactory(mnemonic='coll1')

        response = self.client.get(
            '/collections/coll1/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(coll.id))
        self.assertEqual(response.data['short_code'], 'coll1')
        self.assertEqual(response.data['url'], coll.uri)

        response = self.client.get(
            coll.uri,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(coll.id))
        self.assertEqual(response.data['short_code'], 'coll1')

    def test_get_404(self):
        response = self.client.get(
            '/collections/coll1/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_delete(self):
        coll = CollectionFactory(mnemonic='coll1')
        CollectionFactory(
            version='v1', is_latest_version=True, mnemonic='coll1', organization=coll.organization
        )
        user = UserProfileFactory(organizations=[coll.organization])

        self.assertEqual(coll.versions.count(), 2)

        response = self.client.delete(
            '/collections/coll1/',
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(coll.versions.count(), 1)

        response = self.client.delete(
            '/collections/coll1/',
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(coll.versions.count(), 1)

    def test_put_401(self):
        coll = CollectionFactory(mnemonic='coll1', name='Collection')
        self.assertEqual(coll.versions.count(), 1)

        response = self.client.put(
            '/collections/coll1/',
            dict(name='Collection1'),
            format='json'
        )

        self.assertEqual(response.status_code, 401)

    def test_put_405(self):
        coll = CollectionFactory(mnemonic='coll1', name='Collection')
        user = UserProfileFactory(organizations=[coll.organization])
        self.assertEqual(coll.versions.count(), 1)

        response = self.client.put(
            '/collections/coll1/',
            dict(name='Collection1'),
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    def test_put_200(self):
        coll = CollectionFactory(mnemonic='coll1', name='Collection')
        user = UserProfileFactory(organizations=[coll.organization])
        self.assertEqual(coll.versions.count(), 1)

        response = self.client.put(
            coll.uri,
            dict(name='Collection1'),
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'Collection1')
        coll.refresh_from_db()
        self.assertEqual(coll.name, 'Collection1')
        self.assertEqual(coll.versions.count(), 1)

    def test_put_400(self):
        coll = CollectionFactory(mnemonic='coll1', name='Collection')
        user = UserProfileFactory(organizations=[coll.organization])
        self.assertEqual(coll.versions.count(), 1)

        response = self.client.put(
            coll.uri,
            dict(name=''),
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(name=[ErrorDetail(string='This field may not be blank.', code='blank')]))
