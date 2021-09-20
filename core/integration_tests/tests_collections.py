import json
import zipfile

from celery_once import AlreadyQueued
from mock import patch, Mock, ANY
from rest_framework.exceptions import ErrorDetail

from core.collections.models import CollectionReference, Collection
from core.collections.serializers import CollectionVersionExportSerializer, CollectionReferenceSerializer
from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory
from core.common.tasks import export_collection
from core.common.tests import OCLAPITestCase
from core.common.utils import get_latest_dir_in_path
from core.concepts.serializers import ConceptVersionExportSerializer
from core.concepts.tests.factories import ConceptFactory
from core.mappings.serializers import MappingDetailSerializer
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
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
            f'/orgs/{coll.parent.mnemonic}/collections/?verbose=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['short_code'], 'coll1')
        self.assertEqual(response.data[0]['id'], 'coll1')
        self.assertEqual(response.data[0]['url'], coll.uri)
        for attr in ['active_concepts', 'active_mappings', 'versions', 'summary']:
            self.assertFalse(attr in response.data[0])

        concept = ConceptFactory()
        reference = CollectionReference(expression=concept.uri)
        reference.full_clean()
        reference.save()
        coll.references.add(reference)
        coll.concepts.set(reference.concepts)

        response = self.client.get(
            f'/orgs/{coll.parent.mnemonic}/collections/?contains={concept.get_latest_version().uri}'
            f'&includeReferences=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        response = self.client.get(
            f'/orgs/{coll.parent.mnemonic}/collections/?verbose=true&includeSummary=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['short_code'], 'coll1')
        self.assertEqual(response.data[0]['id'], 'coll1')
        self.assertEqual(response.data[0]['url'], coll.uri)
        self.assertEqual(response.data[0]['summary'], dict(versions=1, active_concepts=1, active_mappings=0))

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
            '/orgs/org/collections/',
            dict(
                default_locale='en', supported_locales='en,fr', id='coll',
            ),
            format='json'
        )

        self.assertEqual(response.status_code, 405)


class CollectionRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def test_get_200(self):
        coll = OrganizationCollectionFactory(mnemonic='coll1')

        response = self.client.get(coll.uri, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(coll.id))
        self.assertEqual(response.data['short_code'], 'coll1')
        self.assertEqual(response.data['url'], coll.uri)
        self.assertEqual(response.data['type'], 'Collection')

        response = self.client.get(
            coll.uri,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(coll.id))
        self.assertEqual(response.data['short_code'], 'coll1')

        response = self.client.get(
            coll.uri + 'summary/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(coll.id))
        self.assertEqual(response.data['active_concepts'], 0)
        self.assertEqual(response.data['active_mappings'], 0)
        self.assertEqual(response.data['versions'], 1)

    def test_get_404(self):
        response = self.client.get(
            '/orgs/foobar/collections/coll1/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.common.services.S3.delete_objects', Mock())
    def test_delete(self):
        coll = OrganizationCollectionFactory(mnemonic='coll1')
        coll_v1 = OrganizationCollectionFactory(
            version='v1', is_latest_version=True, mnemonic='coll1', organization=coll.organization
        )
        user = UserProfileFactory(organizations=[coll.organization])

        self.assertEqual(coll.versions.count(), 2)

        response = self.client.delete(
            coll_v1.uri,
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(coll.versions.count(), 1)

        response = self.client.delete(
            coll.uri,
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(coll.versions.count(), 0)
        self.assertFalse(Collection.objects.filter(mnemonic='coll1').exists())

    def test_put_401(self):
        coll = OrganizationCollectionFactory(mnemonic='coll1', name='Collection')
        self.assertEqual(coll.versions.count(), 1)

        response = self.client.put(
            coll.uri,
            dict(name='Collection1'),
            format='json'
        )

        self.assertEqual(response.status_code, 401)

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
        self.user = UserProfileFactory(username='foobar')
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
            '/users/foobar/collections/foobar/references/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_get_200(self):
        response = self.client.get(
            self.collection.uri + 'references/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['expression'], self.concept.get_latest_version().uri)
        self.assertEqual(response.data[0]['reference_type'], 'concepts')

        response = self.client.get(
            self.collection.uri + f'references/?q={self.concept.get_latest_version().uri}&search_sort=desc',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['expression'], self.concept.get_latest_version().uri)
        self.assertEqual(response.data[0]['reference_type'], 'concepts')

        response = self.client.get(
            self.collection.uri + 'references/?q=/concepts/&search_sort=desc',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        response = self.client.get(
            self.collection.uri + 'references/?q=/mappings/&search_sort=desc',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_delete_400(self):
        response = self.client.delete(
            self.collection.uri + 'references/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)

    def test_delete_204_random(self):
        response = self.client.delete(
            self.collection.uri + 'references/',
            dict(expressions=['/foo/']),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.references.count(), 1)
        self.assertEqual(self.collection.concepts.count(), 1)

    def test_delete_204_all_expressions(self):
        response = self.client.delete(
            self.collection.uri + 'references/',
            dict(expressions='*'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.references.count(), 0)
        self.assertEqual(self.collection.concepts.count(), 0)

    def test_delete_204_specific_expression(self):
        response = self.client.delete(
            self.collection.uri + 'references/',
            dict(expressions=[self.concept.get_latest_version().uri]),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.references.count(), 0)
        self.assertEqual(self.collection.concepts.count(), 0)

        concept = ConceptFactory()
        latest_version = concept.get_latest_version()
        MappingFactory(from_concept=latest_version, parent=concept.parent)
        response = self.client.put(
            self.collection.uri + 'references/?cascade=sourcemappings',
            dict(data=dict(mappings=[latest_version.uri])),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 2)
        self.assertEqual(self.collection.concepts.count(), 1)
        self.assertEqual(self.collection.mappings.count(), 1)

        response = self.client.delete(
            self.collection.uri + 'references/?cascade=sourcemappings',
            dict(expressions=[latest_version.uri]),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 204)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 0)
        self.assertEqual(self.collection.concepts.count(), 0)
        self.assertEqual(self.collection.mappings.count(), 0)

    @patch('core.collections.views.add_references')
    def test_put_202_all(self, add_references_mock):
        add_references_mock.delay = Mock()

        response = self.client.put(
            self.collection.uri + 'references/',
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
            self.collection.uri + 'references/',
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
            self.collection.uri + 'references/',
            dict(data=dict(concepts=[concept2.uri])),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 2)
        self.assertEqual(self.collection.concepts.count(), 2)
        self.assertEqual(self.collection.active_concepts, 2)
        self.assertEqual(self.collection.active_mappings, 0)
        self.assertTrue(self.collection.references.filter(expression=concept2.get_latest_version().uri).exists())
        self.assertEqual(
            response.data,
            [
                dict(
                    added=True, expression=concept2.get_latest_version().uri,
                    message='Added the latest versions of concept to the collection. Future updates will not be added'
                            ' automatically.'
                )
            ]
        )

        mapping = MappingFactory(from_concept=concept2, to_concept=self.concept, parent=self.concept.parent)

        response = self.client.put(
            self.collection.uri + 'references/',
            dict(data=dict(mappings=[mapping.uri])),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 3)
        self.assertEqual(self.collection.concepts.count(), 2)
        self.assertEqual(self.collection.mappings.count(), 1)
        self.assertEqual(self.collection.active_concepts, 2)
        self.assertEqual(self.collection.active_mappings, 1)
        self.assertTrue(self.collection.references.filter(expression=mapping.get_latest_version().uri).exists())
        self.assertEqual(
            response.data,
            [
                dict(
                    added=True, expression=mapping.get_latest_version().uri,
                    message='Added the latest versions of mapping to the collection. Future updates will not be added'
                            ' automatically.'
                )
            ]
        )

        concept3 = ConceptFactory()
        latest_version = concept3.get_latest_version()
        mapping2 = MappingFactory(from_concept=latest_version, parent=concept3.parent)

        response = self.client.put(
            self.collection.uri + 'references/?cascade=sourcemappings',
            dict(data=dict(concepts=[latest_version.uri])),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 5)
        self.assertEqual(self.collection.concepts.count(), 3)
        self.assertEqual(self.collection.mappings.count(), 2)
        self.assertEqual(self.collection.active_concepts, 3)
        self.assertEqual(self.collection.active_mappings, 2)
        self.assertTrue(self.collection.references.filter(expression=mapping2.get_latest_version().uri).exists())
        self.assertTrue(self.collection.references.filter(expression=latest_version.uri).exists())


class CollectionVersionRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user)
        self.collection_v1 = UserCollectionFactory(version='v1', mnemonic='coll', user=self.user)

    def test_get_200(self):
        response = self.client.get(
            self.collection_v1.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.collection_v1.id))
        self.assertEqual(response.data['id'], 'v1')
        self.assertEqual(response.data['short_code'], 'coll')
        self.assertEqual(response.data['type'], 'Collection Version')

    def test_get_404(self):
        response = self.client.get(
            '/users/foobar/collections/coll/v2/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        self.assertEqual(self.collection.versions.count(), 2)
        self.assertIsNone(self.collection_v1.external_id)

        external_id = 'EXT-123'
        response = self.client.put(
            self.collection_v1.uri,
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
            self.collection_v1.uri,
            dict(version=None),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'version': [ErrorDetail(string='This field may not be null.', code='null')]})

    @patch('core.common.services.S3.delete_objects', Mock())
    def test_delete(self):
        response = self.client.delete(
            self.collection_v1.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.versions.count(), 1)
        self.assertTrue(self.collection.versions.first().is_latest_version)

        response = self.client.delete(
            f'/users/{self.collection.parent.mnemonic}/collections/{self.collection.mnemonic}/{self.collection.version}/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Collection.objects.filter(id=self.collection.id).exists())


class CollectionLatestVersionRetrieveUpdateViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user)
        self.collection_v1 = UserCollectionFactory(version='v1', mnemonic='coll', user=self.user)

    def test_get_404(self):
        response = self.client.get(
            '/users/foobar/collections/coll/latest/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_get_200(self):
        self.collection_v1.released = True
        self.collection_v1.save()

        response = self.client.get(
            self.collection.uri + 'latest/',
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
            self.collection.uri + 'latest/',
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
            self.collection.uri + 'latest/',
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
            self.collection.uri + 'extras/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.extras)

    def test_get_404(self):
        response = self.client.get(
            '/users/foobar/collections/foobar/extras/',
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
            self.collection.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='bar'))

    def test_get_404(self):
        response = self.client.get(
            self.collection.uri + 'extras/bar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        response = self.client.put(
            self.collection.uri + 'extras/foo/',
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
            self.collection.uri + 'extras/foo/',
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
            self.collection.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 204)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.extras, dict(tao='ching'))

    def test_delete_404(self):
        response = self.client.delete(
            self.collection.uri + 'extras/bar/',
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
        self.v1_updated_at = self.collection_v1.updated_at.strftime('%Y%m%d%H%M%S')

    def test_get_404(self):
        response = self.client.get(
            '/users/foo/collections/coll/v2/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.common.services.S3.exists')
    def test_get_204(self, s3_exists_mock):
        s3_exists_mock.return_value = False

        response = self.client.get(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        s3_exists_mock.assert_called_once_with(f"username/coll_v1.{self.v1_updated_at}.zip")

    @patch('core.common.services.S3.url_for')
    @patch('core.common.services.S3.exists')
    def test_get_303(self, s3_exists_mock, s3_url_for_mock):
        s3_exists_mock.return_value = True
        s3_url = f"https://s3/username/coll_v1.{self.v1_updated_at}.zip"
        s3_url_for_mock.return_value = s3_url

        response = self.client.get(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['Location'], s3_url)
        self.assertEqual(response['Last-Updated'], str(self.collection_v1.last_child_update.isoformat()))
        self.assertEqual(response['Last-Updated-Timezone'], 'America/New_York')
        s3_exists_mock.assert_called_once_with(f"username/coll_v1.{self.v1_updated_at}.zip")
        s3_url_for_mock.assert_called_once_with(f"username/coll_v1.{self.v1_updated_at}.zip")

    def test_get_405(self):
        response = self.client.get(
            f'/users/{self.collection.parent.mnemonic}/collections/{self.collection.mnemonic}/{"HEAD"}/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    def test_post_405(self):
        response = self.client.post(
            f'/users/{self.collection.parent.mnemonic}/collections/{self.collection.mnemonic}/{"HEAD"}/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    @patch('core.common.services.S3.exists')
    def test_post_303(self, s3_exists_mock):
        s3_exists_mock.return_value = True
        response = self.client.post(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['URL'], self.collection_v1.uri + 'export/')
        s3_exists_mock.assert_called_once_with(f"username/coll_v1.{self.v1_updated_at}.zip")

    @patch('core.collections.views.export_collection')
    @patch('core.common.services.S3.exists')
    def test_post_202(self, s3_exists_mock, export_collection_mock):
        s3_exists_mock.return_value = False
        response = self.client.post(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        s3_exists_mock.assert_called_once_with(f"username/coll_v1.{self.v1_updated_at}.zip")
        export_collection_mock.delay.assert_called_once_with(self.collection_v1.id)

    @patch('core.collections.views.export_collection')
    @patch('core.common.services.S3.exists')
    def test_post_409(self, s3_exists_mock, export_collection_mock):
        s3_exists_mock.return_value = False
        export_collection_mock.delay.side_effect = AlreadyQueued('already-queued')
        response = self.client.post(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        s3_exists_mock.assert_called_once_with(f"username/coll_v1.{self.v1_updated_at}.zip")
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
            self.collection.uri + 'versions/?verbose=true',
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
            self.collection.uri + 'versions/?released=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['version'], 'v1')

    def test_post_201(self):
        response = self.client.post(
            self.collection.uri + 'versions/',
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


class ExportCollectionTaskTest(OCLAPITestCase):
    @patch('core.common.utils.S3')
    def test_export_collection(self, s3_mock):  # pylint: disable=too-many-locals
        s3_mock.url_for = Mock(return_value='https://s3-url')
        s3_mock.upload_file = Mock()
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept2, to_concept=concept1, parent=source)
        collection = OrganizationCollectionFactory()
        collection.add_references([concept1.uri, concept2.uri, mapping.uri])
        collection.refresh_from_db()

        export_collection(collection.id)  # pylint: disable=no-value-for-parameter

        latest_temp_dir = get_latest_dir_in_path('/tmp/')
        zipped_file = zipfile.ZipFile(latest_temp_dir + '/export.zip')
        exported_data = json.loads(zipped_file.read('export.json').decode('utf-8'))

        self.assertEqual(
            exported_data,
            {**CollectionVersionExportSerializer(collection).data, 'concepts': ANY, 'mappings': ANY, 'references': ANY}
        )

        exported_concepts = exported_data['concepts']
        expected_concepts = ConceptVersionExportSerializer(
            [concept2.get_latest_version(), concept1.get_latest_version()], many=True
        ).data

        self.assertEqual(len(exported_concepts), 2)
        self.assertIn(expected_concepts[0], exported_concepts)
        self.assertIn(expected_concepts[1], exported_concepts)

        exported_mappings = exported_data['mappings']
        expected_mappings = MappingDetailSerializer([mapping.get_latest_version()], many=True).data

        self.assertEqual(len(exported_mappings), 1)
        self.assertEqual(expected_mappings, exported_mappings)

        exported_references = exported_data['references']
        expected_references = CollectionReferenceSerializer(collection.references.all(), many=True).data

        self.assertEqual(len(exported_references), 3)
        self.assertIn(exported_references[0], expected_references)
        self.assertIn(exported_references[1], expected_references)
        self.assertIn(exported_references[2], expected_references)

        s3_upload_key = collection.export_path
        s3_mock.upload_file.assert_called_once_with(
            key=s3_upload_key, file_path=latest_temp_dir + '/export.zip', binary=True,
            metadata={'ContentType': 'application/zip'}, headers={'content-type': 'application/zip'}
        )
        s3_mock.url_for.assert_called_once_with(s3_upload_key)

        import shutil
        shutil.rmtree(latest_temp_dir)


class CollectionConceptsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.collection = UserCollectionFactory(user=self.user)
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(
            self.collection.concepts_url,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source1, mnemonic='concept')
        concept2 = ConceptFactory(parent=source2, mnemonic='concept')
        self.collection.add_references([concept1.uri, concept2.uri])

        response = self.client.get(
            self.collection.concepts_url,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_get_duplicate_concept_name_from_multiple_sources_200(self):
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source1, mnemonic='concept')
        concept2 = ConceptFactory(parent=source2, mnemonic='concept')
        self.collection.add_references([concept1.uri, concept2.uri])

        response = self.client.get(
            self.collection.concepts_url + 'concept/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)

        response = self.client.get(
            self.collection.concepts_url + 'concept/?uri=' + concept2.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(concept2.get_latest_version().id))


class CollectionLogoViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory(username='username')
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll1', user=self.user)

    @patch('core.common.services.S3.upload_base64')
    def test_post_200(self, upload_base64_mock):
        upload_base64_mock.return_value = 'users/username/collections/coll1/logo.png'
        self.assertIsNone(self.collection.logo_url)
        self.assertIsNone(self.collection.logo_path)

        response = self.client.post(
            self.collection.uri + 'logo/',
            dict(base64='base64-data'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        expected_logo_url = 'http://oclapi2-dev.s3.amazonaws.com/users/username/collections/coll1/logo.png'
        self.assertEqual(response.data['logo_url'].replace('https://', 'http://'), expected_logo_url)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.logo_url.replace('https://', 'http://'), expected_logo_url)
        self.assertEqual(self.collection.logo_path, 'users/username/collections/coll1/logo.png')
        upload_base64_mock.assert_called_once_with(
            'base64-data', 'users/username/collections/coll1/logo.png', False, True
        )
