from celery_once import AlreadyQueued
from mock import patch, Mock
from rest_framework.exceptions import ErrorDetail

from core.collections.models import CollectionReference, Collection
from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory
from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.users.tests.factories import UserProfileFactory


class CollectionListViewTest(OCLAPITestCase):
    def test_get_200(self):
        coll = OrganizationCollectionFactory(mnemonic='coll1')

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
            '/orgs/{}/collections/?verbose=true'.format(coll.parent.mnemonic),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['short_code'], 'coll1')
        self.assertEqual(response.data[0]['id'], 'coll1')
        self.assertEqual(response.data[0]['url'], coll.uri)

        concept = ConceptFactory()
        reference = CollectionReference(expression=concept.uri)
        reference.full_clean()
        reference.save()
        coll.references.add(reference)
        coll.concepts.set(reference.concepts)

        response = self.client.get(
            '/orgs/{}/collections/?contains={}&includeReferences=true'.format(coll.parent.mnemonic, concept.uri),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

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
        coll = OrganizationCollectionFactory(mnemonic='coll1')

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
        coll = OrganizationCollectionFactory(mnemonic='coll1')
        OrganizationCollectionFactory(
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
        coll = OrganizationCollectionFactory(mnemonic='coll1', name='Collection')
        self.assertEqual(coll.versions.count(), 1)

        response = self.client.put(
            '/collections/coll1/',
            dict(name='Collection1'),
            format='json'
        )

        self.assertEqual(response.status_code, 401)

    def test_put_405(self):
        coll = OrganizationCollectionFactory(mnemonic='coll1', name='Collection')
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
        coll = OrganizationCollectionFactory(mnemonic='coll1', name='Collection')
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
        coll = OrganizationCollectionFactory(mnemonic='coll1', name='Collection')
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


class CollectionReferencesViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user)
        self.concept = ConceptFactory()
        self.reference = CollectionReference(expression=self.concept.uri)
        self.reference.full_clean()
        self.reference.save()
        self.collection.references.add(self.reference)
        self.collection.concepts.set(self.reference.concepts)
        self.assertEqual(self.collection.references.count(), 1)
        self.assertEqual(self.collection.concepts.count(), 1)

    def test_get_404(self):
        response = self.client.get(
            '/collections/foobar/references/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_get_200(self):
        response = self.client.get(
            '/collections/coll/references/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['expression'], self.concept.uri)
        self.assertEqual(response.data[0]['reference_type'], 'concepts')

        response = self.client.get(
            '/collections/coll/references/?q={}&search_sort=desc'.format(self.concept.uri),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['expression'], self.concept.uri)
        self.assertEqual(response.data[0]['reference_type'], 'concepts')

        response = self.client.get(
            '/collections/coll/references/?q=/concepts/&search_sort=desc',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_delete_400(self):
        response = self.client.delete(
            '/collections/coll/references/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)

    def test_delete_204_random(self):
        response = self.client.delete(
            '/collections/coll/references/',
            dict(expressions=['/foo/']),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.references.count(), 1)
        self.assertEqual(self.collection.concepts.count(), 1)

    def test_delete_204_all_expressions(self):
        response = self.client.delete(
            '/collections/coll/references/',
            dict(expressions='*'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.references.count(), 0)
        self.assertEqual(self.collection.concepts.count(), 0)

    def test_delete_204_specific_expression(self):
        response = self.client.delete(
            '/collections/coll/references/',
            dict(expressions=[self.concept.uri]),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.references.count(), 0)
        self.assertEqual(self.collection.concepts.count(), 0)

    @patch('core.collections.views.add_references')
    def test_put_202_all(self, add_references_mock):
        add_references_mock.delay = Mock()

        response = self.client.put(
            '/collections/coll/references/',
            dict(data=dict(concepts='*')),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, [])
        add_references_mock.delay.assert_called_once_with(
            self.user, dict(concepts='*'), self.collection, 'http://testserver', False
        )

    def test_put_200_specific_expression(self):
        response = self.client.put(
            '/collections/coll/references/',
            dict(data=dict(concepts=[self.concept.uri])),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                dict(
                    added=False, expression=self.concept.uri,
                    message=['Concept or Mapping reference name must be unique in a collection.']
                )
            ]
        )

        concept2 = ConceptFactory()
        response = self.client.put(
            '/collections/coll/references/',
            dict(data=dict(concepts=[concept2.uri])),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.collection.references.count(), 2)
        self.assertEqual(self.collection.concepts.count(), 2)
        self.assertTrue(self.collection.references.filter(expression=concept2.uri).exists())
        self.assertEqual(
            response.data,
            [
                dict(
                    added=True, expression=concept2.uri,
                    message='Added the latest versions of concept to the collection. Future updates will not be added'
                            ' automatically.'
                )
            ]
        )

        mapping = MappingFactory(from_concept=concept2, to_concept=self.concept, parent=self.concept.parent)

        response = self.client.put(
            '/collections/coll/references/',
            dict(data=dict(mappings=[mapping.uri])),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.collection.references.count(), 3)
        self.assertEqual(self.collection.concepts.count(), 2)
        self.assertEqual(self.collection.mappings.count(), 1)
        self.assertTrue(self.collection.references.filter(expression=mapping.uri).exists())
        self.assertEqual(
            response.data,
            [
                dict(
                    added=True, expression=mapping.uri,
                    message='Added the latest versions of mapping to the collection. Future updates will not be added'
                            ' automatically.'
                )
            ]
        )


class CollectionVersionRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user)
        self.collection_v1 = UserCollectionFactory(version='v1', mnemonic='coll', user=self.user)

    def test_get_200(self):
        response = self.client.get(
            '/collections/coll/v1/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.collection_v1.id))
        self.assertEqual(response.data['id'], 'v1')
        self.assertEqual(response.data['short_code'], 'coll')

    def test_get_404(self):
        response = self.client.get(
            '/collections/coll/v2/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        self.assertEqual(self.collection.versions.count(), 2)
        self.assertIsNone(self.collection_v1.external_id)

        external_id = 'EXT-123'
        response = self.client.put(
            '/collections/coll/v1/',
            dict(external_id=external_id),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.collection_v1.id))
        self.assertEqual(response.data['id'], 'v1')
        self.assertEqual(response.data['short_code'], 'coll')
        self.assertEqual(response.data['external_id'], external_id)
        self.collection_v1.refresh_from_db()
        self.assertEqual(self.collection_v1.external_id, external_id)
        self.assertEqual(self.collection.versions.count(), 2)

    def test_put_400(self):
        response = self.client.put(
            '/collections/coll/v1/',
            dict(version=None),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'version': [ErrorDetail(string='This field may not be null.', code='null')]})

    def test_delete(self):
        response = self.client.delete(
            '/collections/coll/v1/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.versions.count(), 1)
        self.assertTrue(self.collection.versions.first().is_latest_version)

        response = self.client.delete(
            '/collections/coll/{}/'.format(self.collection.version),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'detail': ['Cannot delete only version.']})
        self.assertEqual(self.collection.versions.count(), 1)
        self.assertTrue(self.collection.versions.first().is_latest_version)


class CollectionLatestVersionRetrieveUpdateViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user)
        self.collection_v1 = UserCollectionFactory(version='v1', mnemonic='coll', user=self.user)

    def test_get_404(self):
        response = self.client.get(
            '/collections/coll/latest/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_get_200(self):
        self.collection_v1.released = True
        self.collection_v1.save()

        response = self.client.get(
            '/collections/coll/latest/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.collection_v1.id))
        self.assertEqual(response.data['id'], 'v1')
        self.assertEqual(response.data['short_code'], 'coll')

    def test_put_200(self):
        self.collection_v1.released = True
        self.collection_v1.save()
        self.assertEqual(self.collection.versions.count(), 2)
        self.assertIsNone(self.collection_v1.external_id)

        external_id = 'EXT-123'
        response = self.client.put(
            '/collections/coll/latest/',
            dict(external_id=external_id),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.collection_v1.id))
        self.assertEqual(response.data['id'], 'v1')
        self.assertEqual(response.data['short_code'], 'coll')
        self.assertEqual(response.data['external_id'], external_id)
        self.collection_v1.refresh_from_db()
        self.assertEqual(self.collection_v1.external_id, external_id)
        self.assertEqual(self.collection.versions.count(), 2)

    def test_put_400(self):
        self.collection_v1.released = True
        self.collection_v1.save()

        response = self.client.put(
            '/collections/coll/latest/',
            dict(version=None),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'version': [ErrorDetail(string='This field may not be null.', code='null')]})


class CollectionExtrasViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.extras = dict(foo='bar', tao='ching')
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user, extras=self.extras)

    def test_get_200(self):
        response = self.client.get(
            '/collections/coll/extras/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.extras)

    def test_get_404(self):
        response = self.client.get(
            '/collections/foobar/extras/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 404)


class CollectionExtraRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.extras = dict(foo='bar', tao='ching')
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user, extras=self.extras)

    def test_get_200(self):
        response = self.client.get(
            '/collections/coll/extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='bar'))

    def test_get_404(self):
        response = self.client.get(
            '/collections/coll/extras/bar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        response = self.client.put(
            '/collections/coll/extras/foo/',
            dict(foo='barbar'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='barbar'))
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.extras['foo'], 'barbar')

    def test_put_400(self):
        response = self.client.put(
            '/collections/coll/extras/foo/',
            dict(foo=None),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, ['Must specify foo param in body.'])
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.extras, self.extras)

    def test_delete_204(self):
        response = self.client.delete(
            '/collections/coll/extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 204)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.extras, dict(tao='ching'))

    def test_delete_404(self):
        response = self.client.delete(
            '/collections/coll/extras/bar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 404)


class CollectionVersionExportViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory(username='username')
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user)
        self.collection_v1 = UserCollectionFactory(version='v1', mnemonic='coll', user=self.user)

    def test_get_404(self):
        response = self.client.get(
            '/collections/coll/v2/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.common.services.S3.url_for')
    def test_get_204(self, s3_url_for_mock):
        Collection.objects.filter(id=self.collection_v1.id).update(last_child_update='2020-01-01 10:00:00')

        s3_url_for_mock.return_value = None

        response = self.client.get(
            '/collections/coll/v1/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        s3_url_for_mock.assert_called_once_with("username/coll_v1.20200101100000.zip")

    @patch('core.common.services.S3.url_for')
    def test_get_303(self, s3_url_for_mock):
        Collection.objects.filter(id=self.collection_v1.id).update(last_child_update='2020-01-01 10:00:00')

        s3_url = 'https://s3/username/coll_v1.20200101100000.zip'
        s3_url_for_mock.return_value = s3_url

        response = self.client.get(
            '/collections/coll/v1/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['Location'], s3_url)
        self.assertEqual(response['Last-Updated'], '2020-01-01T10:00:00+00:00')
        self.assertEqual(response['Last-Updated-Timezone'], 'America/New_York')
        s3_url_for_mock.assert_called_once_with("username/coll_v1.20200101100000.zip")

    def test_get_405(self):
        response = self.client.get(
            '/collections/coll/HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    def test_post_405(self):
        response = self.client.post(
            '/collections/coll/HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    @patch('core.common.services.S3.url_for')
    def test_post_303(self, s3_url_for_mock):
        Collection.objects.filter(id=self.collection_v1.id).update(last_child_update='2020-01-01 10:00:00')
        s3_url = 'https://s3/username/coll_v1.20200101100000.zip'
        s3_url_for_mock.return_value = s3_url
        response = self.client.post(
            '/collections/coll/v1/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['URL'], self.collection_v1.uri + 'export/')
        s3_url_for_mock.assert_called_once_with("username/coll_v1.20200101100000.zip")

    @patch('core.collections.views.export_collection')
    @patch('core.common.services.S3.url_for')
    def test_post_202(self, s3_url_for_mock, export_collection_mock):
        Collection.objects.filter(id=self.collection_v1.id).update(last_child_update='2020-01-01 10:00:00')

        s3_url_for_mock.return_value = None
        export_collection_mock.delay = Mock()
        response = self.client.post(
            '/collections/coll/v1/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        s3_url_for_mock.assert_called_once_with("username/coll_v1.20200101100000.zip")
        export_collection_mock.delay.assert_called_once_with(self.collection_v1.id)

    @patch('core.collections.views.export_collection')
    @patch('core.common.services.S3.url_for')
    def test_post_409(self, s3_url_for_mock, export_collection_mock):
        Collection.objects.filter(id=self.collection_v1.id).update(last_child_update='2020-01-01 10:00:00')

        s3_url_for_mock.return_value = None
        export_collection_mock.delay.side_effect = AlreadyQueued('already-queued')
        response = self.client.post(
            '/collections/coll/v1/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        s3_url_for_mock.assert_called_once_with("username/coll_v1.20200101100000.zip")
        export_collection_mock.delay.assert_called_once_with(self.collection_v1.id)


class CollectionVersionListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user)
        self.concept = ConceptFactory()
        self.reference = CollectionReference(expression=self.concept.uri)
        self.reference.full_clean()
        self.reference.save()
        self.collection.references.add(self.reference)
        self.collection.concepts.set(self.reference.concepts)
        self.assertEqual(self.collection.references.count(), 1)
        self.assertEqual(self.collection.concepts.count(), 1)

    def test_get_200(self):
        response = self.client.get(
            '/collections/coll/versions/?verbose=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['version'], 'HEAD')

        UserCollectionFactory(
            mnemonic=self.collection.mnemonic, user=self.user, version='v1', released=True
        )

        response = self.client.get(
            '/collections/coll/versions/?released=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['version'], 'v1')

    @patch('core.collections.views.export_collection')
    def test_post_201(self, export_collection_mock):
        export_collection_mock.delay = Mock()
        response = self.client.post(
            '/collections/coll/versions/',
            dict(id='v1', description='version1'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['version'], 'v1')
        self.assertEqual(self.collection.versions.count(), 2)

        last_created_version = self.collection.versions.order_by('created_at').last()
        self.assertEqual(last_created_version.version, 'v1')
        self.assertEqual(last_created_version.description, 'version1')
        self.assertEqual(last_created_version.concepts.count(), 1)
        self.assertEqual(last_created_version.references.count(), 1)
        self.assertEqual(last_created_version, self.collection.get_latest_version())
        export_collection_mock.delay.assert_called_once_with(str(last_created_version.id))
