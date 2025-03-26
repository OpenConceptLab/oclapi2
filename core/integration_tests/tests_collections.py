import json
import zipfile

from celery_once import AlreadyQueued
from django.http import StreamingHttpResponse
from mock import patch, Mock, ANY
from rest_framework.exceptions import ErrorDetail

from core.collections.models import CollectionReference, Collection
from core.collections.serializers import CollectionVersionExportSerializer, CollectionReferenceDetailSerializer
from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory, ExpansionFactory
from core.common.tasks import export_collection
from core.common.tests import OCLAPITestCase
from core.common.utils import get_latest_dir_in_path, drop_version
from core.concepts.serializers import ConceptVersionExportSerializer
from core.concepts.tests.factories import ConceptFactory
from core.mappings.serializers import MappingDetailSerializer
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.tasks.models import Task
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class CollectionListViewTest(OCLAPITestCase):
    def test_get_200(self):
        coll = OrganizationCollectionFactory(mnemonic='coll1')
        expansion = ExpansionFactory(collection_version=coll)
        coll.expansion_uri = expansion.uri
        coll.save()

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
        coll.add_expressions({'expressions': [concept.uri]}, coll.created_by)
        response = self.client.get(
            f'/orgs/{coll.parent.mnemonic}/collections/?contains={concept.uri}'
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
        self.assertEqual(
            response.data[0]['summary'],
            {
                'versions': 1,
                'active_concepts': 1,
                'active_mappings': 0,
                'active_references': 1,
                'expansions': 1
            }
        )

        response = self.client.get(
            f'/orgs/{coll.parent.mnemonic}/collections/?includeSummary=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['short_code'], 'coll1')
        self.assertEqual(response.data[0]['id'], 'coll1')
        self.assertEqual(response.data[0]['url'], coll.uri)
        self.assertEqual(
            response.data[0]['summary'],
            {
                'versions': 1,
                'active_concepts': 1,
                'active_mappings': 0,
                'active_references': 1,
                'expansions': 1
            }
        )

        response = self.client.get(
            f'/orgs/{coll.parent.mnemonic}/collections/?brief=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0],
            {
                'id': coll.mnemonic,
                'url': coll.uri,
                'type': 'Collection',
                'name': coll.name,
                'description': coll.description
            }
        )

    def test_post_201(self):
        org = OrganizationFactory(mnemonic='org')
        user = UserProfileFactory(organizations=[org], username='user')

        response = self.client.post(
            '/orgs/org/collections/',
            {
                'default_locale': 'en',
                'supported_locales': 'en,fr',
                'id': 'coll',
                'name': 'Collection',
                'mnemonic': 'coll',
                'extras': {
                    'foo': 'bar'
                }
            },
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
            {
                'default_locale': 'en',
                'supported_locales': 'en,fr',
                'id': 'coll',
                'name': 'Collection',
                'mnemonic': 'coll',
                'extras': {
                    'foo': 'bar'
                }
            },
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

    def test_post_201_with_autoexpand_head(self):
        org = OrganizationFactory(mnemonic='org')
        user = UserProfileFactory(organizations=[org], username='user')
        token = user.get_token()
        response = self.client.post(
            '/orgs/org/collections/',
            {
                'default_locale': 'en',
                'supported_locales': 'en,fr',
                'id': 'coll',
                'name': 'Collection',
                'mnemonic': 'coll',
                'extras': {
                    'foo': 'bar'
                }
            },
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['uuid'])
        collection = Collection.objects.last()
        self.assertEqual(str(collection.id), response.data['uuid'])
        self.assertEqual(collection.expansions.count(), 1)
        expansion = collection.expansions.first()
        self.assertEqual(collection.expansion_uri, expansion.uri)
        self.assertEqual(collection.expansion.id, expansion.id)
        self.assertEqual(collection.expansion.mnemonic, 'autoexpand-HEAD')
        self.assertEqual(collection.expansion.concepts.count(), 0)

        source = OrganizationSourceFactory(organization=org)
        concept = ConceptFactory(parent=source)
        response = self.client.put(
            '/orgs/org/collections/coll/references/',
            {'data': {'concepts': [concept.uri]}},
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        collection.refresh_from_db()
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.expansion.active_concepts, 1)
        self.assertEqual(collection.expansion.active_mappings, 0)
        self.assertTrue(collection.references.filter(expression=concept.uri).exists())
        self.assertEqual(
            response.data,
            [
                {
                    'added': True,
                    'expression': concept.uri,
                    'message': f'The concept {concept.mnemonic} is successfully added to collection Collection'
                }
            ]
        )

    def test_post_201_with_autoexpand_head_false(self):
        org = OrganizationFactory(mnemonic='org')
        user = UserProfileFactory(organizations=[org], username='user')
        token = user.get_token()
        response = self.client.post(
            '/orgs/org/collections/',
            {
                'default_locale': 'en',
                'supported_locales': 'en,fr',
                'id': 'coll',
                'name': 'Collection',
                'mnemonic': 'coll',
                'extras': {
                    'foo': 'bar'
                },
                'autoexpand_head': False
            },
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['uuid'])
        collection = Collection.objects.last()
        self.assertEqual(str(collection.id), response.data['uuid'])
        self.assertIsNone(collection.expansion_uri)
        self.assertEqual(collection.expansions.count(), 0)
        source = OrganizationSourceFactory(organization=org)
        concept = ConceptFactory(parent=source)
        response = self.client.put(
            '/orgs/org/collections/coll/references/',
            {'data': {'concepts': [concept.uri]}},
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        collection.refresh_from_db()
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.expansions.count(), 0)
        self.assertEqual(collection.active_concepts, None)
        self.assertEqual(collection.active_mappings, None)
        self.assertTrue(collection.references.filter(expression=concept.uri).exists())
        self.assertEqual(
            response.data,
            [
                {
                    'added': True,
                    'expression': concept.uri,
                    'message': f'The concept {concept.mnemonic} is successfully added to collection Collection'
                }
            ]
        )

    def test_post_400(self):
        org = OrganizationFactory(mnemonic='org')
        user = UserProfileFactory(organizations=[org])

        response = self.client.post(
            '/orgs/org/collections/',
            {
                'default_locale': 'en',
                'supported_locales': 'en,fr',
                'id': 'coll',
                'extras': {
                    'foo': 'bar'
                }
            },
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'name': [ErrorDetail(string='This field is required.', code='required')]})
        self.assertEqual(org.collection_set.count(), 0)

    def test_post_403(self):
        OrganizationFactory(mnemonic='org')

        response = self.client.post(
            '/orgs/org/collections/',
            {
                'default_locale': 'en',
                'supported_locales': 'en,fr',
                'id': 'coll'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 403)

    def test_post_405(self):
        response = self.client.post(
            '/orgs/org/collections/',
            {
                'default_locale': 'en',
                'supported_locales': 'en,fr',
                'id': 'coll'
            },
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
        self.assertEqual(response.data['active_concepts'], None)
        self.assertEqual(response.data['active_mappings'], None)
        self.assertEqual(response.data['versions'], 1)

    def test_get_200_with_resources(self):
        concept = ConceptFactory()
        mapping = MappingFactory()
        coll = OrganizationCollectionFactory(mnemonic='coll1')
        expansion = ExpansionFactory(collection_version=coll)
        coll.expansion_uri = expansion.uri
        coll.save()

        response = self.client.get(
            coll.uri + '?includeConcepts=true&includeMappings=true&includeReferences=true', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(coll.id))
        self.assertEqual(response.data['short_code'], 'coll1')
        self.assertEqual(response.data['url'], coll.uri)
        self.assertEqual(response.data['type'], 'Collection')
        self.assertEqual(len(response.data['concepts']), 0)
        self.assertEqual(len(response.data['mappings']), 0)
        self.assertEqual(len(response.data['references']), 0)

        expansion.concepts.add(concept)
        expansion.mappings.add(mapping)
        coll_ref = CollectionReference(expression='/foo/bar', collection=coll)
        coll_ref.save()

        response = self.client.get(
            coll.uri + '?includeConcepts=true&includeMappings=true&includeReferences=true', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(coll.id))
        self.assertEqual(response.data['short_code'], 'coll1')
        self.assertEqual(response.data['url'], coll.uri)
        self.assertEqual(response.data['type'], 'Collection')
        self.assertEqual(len(response.data['concepts']), 1)
        self.assertEqual(len(response.data['mappings']), 1)
        self.assertEqual(len(response.data['references']), 1)

    def test_get_404(self):
        response = self.client.get(
            '/orgs/foobar/collections/coll1/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.common.models.delete_s3_objects')
    def test_delete_204(self, delete_s3_objects_mock):  # sync delete
        coll = OrganizationCollectionFactory(mnemonic='coll1')
        OrganizationCollectionFactory(
            version='v1', is_latest_version=True, mnemonic='coll1', organization=coll.organization)
        user = UserProfileFactory(organizations=[coll.organization])

        self.assertEqual(coll.versions.count(), 2)

        response = self.client.delete(
            coll.uri + '?inline=true',
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(coll.versions.count(), 0)
        self.assertFalse(Collection.objects.filter(mnemonic='coll1').exists())
        delete_s3_objects_mock.apply_async.assert_called_once_with(
            (f'orgs/{coll.organization.mnemonic}/{coll.organization.mnemonic}_coll1_vHEAD.',),
            queue='default', permanent=False)

    @patch('core.collections.views.delete_collection')
    def test_delete_202(self, delete_collection_task_mock):  # async delete
        delete_collection_task_mock.__name__ = 'delete_collection'
        coll = OrganizationCollectionFactory(mnemonic='coll1')
        OrganizationCollectionFactory(
            version='v1', is_latest_version=True, mnemonic='coll1', organization=coll.organization)
        user = UserProfileFactory(organizations=[coll.organization])

        response = self.client.delete(
            coll.uri + '?async=true',
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data,
            {
                'id': ANY,
                'task': ANY,
                'queue': 'default',
                'state': 'PENDING',
                'username': user.username,
                'name': 'delete_collection'
            }
        )
        delete_collection_task_mock.apply_async.assert_called_once_with((coll.id, ), task_id=ANY)

    def test_put_401(self):
        coll = OrganizationCollectionFactory(mnemonic='coll1', name='Collection')
        self.assertEqual(coll.versions.count(), 1)

        response = self.client.put(
            coll.uri,
            {'name': 'Collection1'},
            format='json'
        )

        self.assertEqual(response.status_code, 401)

    def test_put_200(self):
        coll = OrganizationCollectionFactory(mnemonic='coll1', name='Collection')
        user = UserProfileFactory(organizations=[coll.organization])
        self.assertEqual(coll.versions.count(), 1)

        response = self.client.put(
            coll.uri,
            {'name': 'Collection1'},
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
            {'name': ''},
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'name': [ErrorDetail(string='This field may not be blank.', code='blank')]})


class CollectionReferencesViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory(username='foobar')
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user)
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
        self.concept = ConceptFactory()
        self.reference = CollectionReference(
            expression=self.concept.uri, collection=self.collection, system=self.concept.parent.uri, version='HEAD')
        self.reference.full_clean()
        self.reference.save()
        self.expansion.concepts.set(self.reference.concepts.all())
        self.assertEqual(self.collection.references.count(), 1)
        self.assertEqual(self.expansion.concepts.count(), 1)

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
        self.assertEqual(response.data[0]['expression'], self.concept.uri)
        self.assertEqual(response.data[0]['reference_type'], 'concepts')

        response = self.client.get(
            self.collection.uri + f'references/?q={self.concept.uri}&search_sort=desc',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['expression'], self.concept.uri)
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

    @patch('core.collections.models.batch_index_resources', Mock(apply_async=Mock()))
    def test_delete_204_random(self):
        response = self.client.delete(
            self.collection.uri + 'references/',
            {'expressions': ['/foo/']},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.references.count(), 1)
        self.assertEqual(self.collection.expansion.concepts.count(), 1)

    @patch('core.collections.models.batch_index_resources', Mock(apply_async=Mock()))
    def test_delete_204_all_expressions(self):
        response = self.client.delete(
            self.collection.uri + 'references/',
            {'expressions': '*'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.references.count(), 0)
        self.assertEqual(self.collection.expansion.concepts.count(), 0)

    @patch('core.collections.models.batch_index_resources', Mock(apply_async=Mock()))
    def test_delete_204_bulk_reference_ids(self):
        response = self.client.delete(
            self.collection.uri + 'references/',
            {'ids': [self.reference.id]},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.references.count(), 0)
        self.assertEqual(self.collection.expansion.concepts.count(), 0)

    @patch('core.collections.models.batch_index_resources', Mock(apply_async=Mock()))
    def test_delete_204_specific_expression(self):
        response = self.client.delete(
            self.collection.uri + 'references/',
            {
                'expressions': [self.concept.uri]
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.references.count(), 0)
        self.assertEqual(self.collection.expansion.concepts.count(), 0)

        concept = ConceptFactory()
        MappingFactory(from_concept=concept, parent=concept.parent)
        response = self.client.put(
            self.collection.uri + 'references/?cascade=sourcemappings',
            {
                'data': {'mappings': [concept.uri]}
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 2)
        self.assertEqual(self.collection.expansion.concepts.count(), 1)
        self.assertEqual(self.collection.expansion.mappings.count(), 1)

        response = self.client.delete(
            self.collection.uri + 'references/',
            {
                'expressions': [concept.uri]
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 204)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 1)
        self.assertEqual(self.collection.expansion.concepts.count(), 0)
        self.assertEqual(self.collection.expansion.mappings.count(), 1)

    @patch('core.collections.views.add_references')
    def test_put_202_all(self, add_references_mock):
        add_references_mock.__name__ = 'add_references'

        response = self.client.put(
            self.collection.uri + 'references/?async=true',
            {'data': {'concepts': '*'}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data,
            {
                'id': ANY,
                'task': ANY,
                'state': 'PENDING',
                'name': 'add_references',
                'queue': 'default',
                'username': 'foobar'
            }
        )
        add_references_mock.apply_async.assert_called_once()
        self.assertEqual(
            add_references_mock.apply_async.call_args[0],
            ((self.user.id, {'concepts': '*'}, self.collection.id, '', ''),)
        )
        self.assertEqual(
            add_references_mock.apply_async.call_args[1],
            {'task_id': ANY}
        )
        self.assertTrue(
            '-foobar~default' in add_references_mock.apply_async.call_args[1]['task_id'],
        )
        self.assertEqual(
            len(add_references_mock.apply_async.call_args[1]['task_id']), 36 + 1 + 7 + 1 + 6
        )

    def test_put_200_specific_expression(self):  # pylint: disable=too-many-statements
        response = self.client.put(
            self.collection.uri + 'references/',
            {
                'data': {'concepts': [self.concept.uri]}
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {
                    'added': False,
                    'expression': self.concept.uri,
                    'message': {
                        self.concept.uri: {
                            'errors': [{
                                'description': 'Concept or Mapping reference name must be unique in a collection.',
                                'conflicting_references': [self.reference.uri]
                            }]
                        }
                    }
                }
            ]
        )

        concept2 = ConceptFactory()
        response = self.client.put(
            self.collection.uri + 'references/',
            {
                'data': {
                    'concepts': [concept2.uri]
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 2)
        self.assertEqual(self.collection.expansion.concepts.count(), 2)
        self.assertEqual(self.collection.active_concepts, 2)
        self.assertEqual(self.collection.active_mappings, 0)
        self.assertTrue(self.collection.references.filter(expression=concept2.uri).exists())
        self.assertEqual(
            response.data,
            [
                {
                    'added': True,
                    'expression': concept2.uri,
                    'message': f'The concept {concept2.mnemonic} is successfully added to '
                               f'collection {self.collection.name}'
                }
            ]
        )

        mapping = MappingFactory(from_concept=concept2, to_concept=self.concept, parent=self.concept.parent)

        response = self.client.put(
            self.collection.uri + 'references/',
            {
                'data': {
                    'mappings': [mapping.uri]
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 3)
        self.assertEqual(self.collection.expansion.concepts.count(), 2)
        self.assertEqual(self.collection.expansion.mappings.count(), 1)
        self.assertEqual(self.collection.active_concepts, 2)
        self.assertEqual(self.collection.active_mappings, 1)
        self.assertTrue(self.collection.references.filter(expression=mapping.uri).exists())
        self.assertEqual(
            response.data,
            [
                {
                    'added': True,
                    'expression': mapping.uri,
                    'message': f'The mapping {mapping.mnemonic} is successfully added to '
                               f'collection {self.collection.name}'
                }
            ]
        )

        concept3 = ConceptFactory()
        latest_version = concept3.get_latest_version()
        MappingFactory(from_concept=concept3, parent=concept3.parent)

        response = self.client.put(
            self.collection.uri + 'references/?cascade=sourcemappings',
            {
                'data': {
                    'concepts': [latest_version.uri]
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 5)
        self.assertEqual(self.collection.expansion.concepts.count(), 3)
        self.assertEqual(self.collection.expansion.mappings.count(), 2)
        self.assertEqual(self.collection.active_concepts, 3)
        self.assertEqual(self.collection.active_mappings, 2)
        self.assertTrue(self.collection.references.filter(expression=latest_version.uri).exists())
        reference = self.collection.references.last()
        self.assertTrue(reference.cascade, 'sourcemappings')
        self.assertTrue(
            reference.translation,
            'Include latest version "330827" of concept "concept2" from org2/source2 PLUS its mappings'
        )

        concept4 = ConceptFactory()
        latest_version = concept4.get_latest_version()
        MappingFactory(from_concept=concept4, parent=concept4.parent)

        response = self.client.put(
            self.collection.uri + 'references/',
            {
                'data': {
                    'system': latest_version.parent.url,
                    'code': latest_version.mnemonic,
                    'resource_version': latest_version.version,
                    'cascade': 'sourcemappings'
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 6)
        self.assertEqual(self.collection.expansion.concepts.count(), 4)
        self.assertEqual(self.collection.expansion.mappings.count(), 3)
        self.assertEqual(self.collection.active_concepts, 4)
        self.assertEqual(self.collection.active_mappings, 3)
        self.assertTrue(self.collection.references.filter(expression=latest_version.uri).exists())

        response = self.client.put(
            self.collection.uri + 'references/',
            {
                'data': {
                    'system': latest_version.parent.url,
                    'code': latest_version.mnemonic,
                    'resource_version': latest_version.version,
                    'cascade': 'sourcemappings',
                    'exclude': True
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.references.count(), 7)
        self.assertEqual(self.collection.expansion.concepts.count(), 3)
        self.assertEqual(self.collection.expansion.mappings.count(), 2)
        self.assertEqual(self.collection.active_concepts, 3)
        self.assertEqual(self.collection.active_mappings, 2)

    def test_put_expression_with_cascade_to_concepts(self):
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source1, mnemonic='concept1')
        concept2 = ConceptFactory(parent=source1)
        concept3 = ConceptFactory(parent=source2)
        concept4 = ConceptFactory(parent=source2)

        mapping1 = MappingFactory(
            mnemonic='m1-c1-c2-s1', from_concept=concept1.get_latest_version(),
            to_concept=concept2.get_latest_version(), parent=source1
        )
        MappingFactory(
            mnemonic='m2-c2-c1-s1', from_concept=concept2.get_latest_version(),
            to_concept=concept1.get_latest_version(), parent=source1
        )
        MappingFactory(
            mnemonic='m3-c1-c3-s2', from_concept=concept1.get_latest_version(),
            to_concept=concept3.get_latest_version(), parent=source2
        )
        mapping4 = MappingFactory(
            mnemonic='m4-c4-c3-s2', from_concept=concept4.get_latest_version(),
            to_concept=concept3.get_latest_version(), parent=source2
        )
        MappingFactory(
            mnemonic='m5-c4-c1-s1', from_concept=concept4.get_latest_version(),
            to_concept=concept1.get_latest_version(), parent=source1
        )

        response = self.client.put(
            self.collection.uri + 'references/?cascade=sourceToConcepts',
            {
                'data': {
                    'concepts': [concept1.get_latest_version().uri]
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)
        self.assertTrue(all(data['added'] for data in response.data))
        self.assertEqual(
            sorted([data['expression'] for data in response.data]),
            sorted([
                concept1.get_latest_version().uri, mapping1.uri,
                mapping1.to_concept.versioned_object.uri
            ])
        )
        reference = self.collection.references.last()
        self.assertEqual(reference.cascade, 'sourcetoconcepts')
        self.assertEqual(
            reference.translation,
            f'Include version "{concept1.get_latest_version().version}" of concept "concept1" from {concept1.parent.parent.mnemonic}/{concept1.parent.mnemonic} PLUS its mappings and their target concepts'  # pylint: disable=line-too-long
        )

        response = self.client.put(
            self.collection.uri + 'references/?cascade=sourceToConcepts',
            {
                'data': {
                    'concepts': [concept4.get_latest_version().uri]
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)
        self.assertTrue(all(data['added'] for data in response.data))
        self.assertEqual(
            sorted([data['expression'] for data in response.data]),
            sorted([
                concept4.get_latest_version().uri, mapping4.uri,
                mapping4.to_concept.versioned_object.uri
            ])
        )

        random_concept = ConceptFactory()

        response = self.client.put(
            self.collection.uri + 'references/?transformReferences=extensional',
            {
                'data': {'expression': random_concept.parent.uri},
                'cascade': {
                    'method': 'sourcetoconcepts',
                    'cascade levels': '*',
                    'map types': 'Q AND A,CONCEPT SET',
                    'return map types': '*'
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [{
                 'added': False,
                 'expression': random_concept.parent.uri,
                 'message': [
                     'This field cannot be null.',
                     'Invalid cascade schema. Either "code" or "filter" must be provided'
                 ]
             }]
        )

        expr = random_concept.parent.uri + 'v1/concepts/excludeWildcard=true&excludeFuzzy=true&includeSearchMETA=true'
        response = self.client.put(
            self.collection.uri + 'references/?cascade=sourcemappings',
            {
                'data': [{
                    'expression': expr
                }]
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [{
                 'added': False,
                 'expression': expr,
                 'message': [
                     'Invalid cascade schema. Either "code" or "filter" must be provided'
                 ]
             }]
        )

    def test_put_expression_transform_to_latest_version(self):
        concept2 = ConceptFactory()
        concept2_latest_version = concept2.get_latest_version()
        concept3 = ConceptFactory()
        concept3_latest_version = concept3.get_latest_version()

        self.assertNotEqual(concept2.uri, concept2_latest_version.uri)
        self.assertNotEqual(concept3.uri, concept3_latest_version.uri)

        response = self.client.put(
            self.collection.uri + 'references/?transformReferences=resourceVersions',
            {
                'data': {
                    'concepts': [concept2.uri]
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {
                    'added': True,
                    'expression': concept2_latest_version.uri,
                    'message': ANY
                }
            ]
        )

        response = self.client.put(
            self.collection.uri + 'references/',
            {
                'data': {
                    'concepts': [concept3.uri]
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {
                    'added': True,
                    'expression': concept3.uri,
                    'message': ANY
                }
            ]
        )

        self.assertFalse(self.collection.references.filter(expression=concept2.uri).exists())
        self.assertFalse(self.collection.references.filter(expression=concept3_latest_version.uri).exists())
        self.assertTrue(self.collection.references.filter(expression=concept2_latest_version.uri).exists())
        self.assertTrue(self.collection.references.filter(expression=concept3.uri).exists())

    def test_put_expression_transform_to_extensional(self):
        concept2 = ConceptFactory()
        concept2_latest_version = concept2.get_latest_version()
        concept3 = ConceptFactory()
        concept3_latest_version = concept3.get_latest_version()

        self.assertNotEqual(concept2.uri, concept2_latest_version.uri)
        self.assertNotEqual(concept3.uri, concept3_latest_version.uri)

        response = self.client.put(
            self.collection.uri + 'references/?transformReferences=extensional',
            {
                'data': {
                    'concepts': [concept2.uri]
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {
                    'added': True,
                    'expression': concept2.uri,
                    'message': ANY
                }
            ]
        )

        response = self.client.put(
            self.collection.uri + 'references/',
            {
                'data': {
                    'concepts': [concept3.uri]
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {
                    'added': True,
                    'expression': concept3.uri,
                    'message': ANY
                }
            ]
        )

        self.assertTrue(self.collection.references.filter(expression=concept2.uri).exists())
        self.assertFalse(self.collection.references.filter(expression=concept2_latest_version.uri).exists())
        self.assertTrue(self.collection.references.filter(expression=concept3.uri).exists())
        self.assertFalse(self.collection.references.filter(expression=concept3_latest_version.uri).exists())

    def test_put_expression_cascade_and_transform_to_resource_versions_generate_multiple_references(self):
        source = OrganizationSourceFactory()
        concept2 = ConceptFactory(parent=source)
        concept2_latest_version = concept2.get_latest_version()
        concept3 = ConceptFactory(parent=source)
        concept3_latest_version = concept3.get_latest_version()
        mapping = MappingFactory(from_concept=concept2, to_concept=concept3, parent=source)
        mapping_latest_version = mapping.get_latest_version()

        self.assertNotEqual(concept2.uri, concept2_latest_version.uri)
        self.assertNotEqual(concept3.uri, concept3_latest_version.uri)
        self.assertNotEqual(mapping.uri, mapping_latest_version.uri)
        response = self.client.put(
            self.collection.uri + 'references/?transformReferences=resourceVersions',
            {
                'data': {
                    'expressions': [concept2.uri]
                },
                'cascade': {
                    'cascade_levels': "*",
                    'return_map_types': '*',
                    'method': 'sourcetoconcepts'
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)
        self.assertEqual(
            [data for data in response.data if data['expression'] == concept2_latest_version.uri],
            [
                {
                    'added': True,
                    'expression': concept2_latest_version.uri,
                    'message': ANY
                }
            ]
        )
        self.assertEqual(
            [data for data in response.data if data['expression'] == concept3_latest_version.uri],
            [
                {
                    'added': True,
                    'expression': concept3_latest_version.uri,
                    'message': ANY
                }
            ]
        )
        self.assertEqual(
            [data for data in response.data if data['expression'] == mapping_latest_version.uri],
            [
                {
                    'added': True,
                    'expression': mapping_latest_version.uri,
                    'message': ANY
                }
            ]
        )

        self.assertEqual(self.collection.references.count(), 4)
        self.assertEqual(self.collection.expansion.concepts.count(), 3)
        self.assertEqual(self.collection.expansion.mappings.count(), 1)
        self.assertTrue(
            self.collection.references.filter(
                expression=concept2_latest_version.uri,
                code=concept2_latest_version.mnemonic,
                resource_version=concept2_latest_version.version,
                reference_type='concepts',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=concept3_latest_version.uri,
                code=concept3_latest_version.mnemonic,
                resource_version=concept3_latest_version.version,
                reference_type='concepts',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=mapping_latest_version.uri,
                code=mapping_latest_version.mnemonic,
                resource_version=mapping_latest_version.version,
                reference_type='mappings',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )

        # excluding one of them should keep rest same -- bug
        response = self.client.put(
            self.collection.uri + 'references/',
            {
                "data": {
                    "concepts": [drop_version(concept3.uri)],
                    "exclude": True
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.collection.references.count(), 5)
        self.assertEqual(self.collection.expansion.concepts.count(), 2)
        self.assertEqual(self.collection.expansion.mappings.count(), 1)
        self.assertTrue(
            self.collection.references.filter(
                expression=concept2_latest_version.uri,
                code=concept2_latest_version.mnemonic,
                resource_version=concept2_latest_version.version,
                reference_type='concepts',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=mapping_latest_version.uri,
                code=mapping_latest_version.mnemonic,
                resource_version=mapping_latest_version.version,
                reference_type='mappings',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=concept3_latest_version.uri,
                code=concept3_latest_version.mnemonic,
                resource_version=concept3_latest_version.version,
                reference_type='concepts',
                cascade__isnull=True,
                transform__isnull=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=concept3_latest_version.uri,
                code=concept3_latest_version.mnemonic,
                resource_version=concept3_latest_version.version,
                reference_type='concepts',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=drop_version(concept3_latest_version.uri),
                code=concept3_latest_version.mnemonic,
                reference_type='concepts',
                include=False,
            ).exists()
        )

    def test_put_expression_cascade_and_transform_to_extensional_generate_multiple_references(self):
        source = OrganizationSourceFactory()
        concept2 = ConceptFactory(parent=source)
        concept2_latest_version = concept2.get_latest_version()
        concept3 = ConceptFactory(parent=source)
        concept3_latest_version = concept3.get_latest_version()
        mapping = MappingFactory(from_concept=concept2, to_concept=concept3, parent=source)
        mapping_latest_version = mapping.get_latest_version()

        self.assertNotEqual(concept2.uri, concept2_latest_version.uri)
        self.assertNotEqual(concept3.uri, concept3_latest_version.uri)
        self.assertNotEqual(mapping.uri, mapping_latest_version.uri)
        response = self.client.put(
            self.collection.uri + 'references/?transformReferences=extensional',
            {
                'data': {
                    'expressions': [concept2.uri]
                },
                'cascade': {
                    'cascade_levels': "*",
                    'return_map_types': '*',
                    'method': 'sourcetoconcepts'
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)
        self.assertEqual(
            [data for data in response.data if data['expression'] == concept2.uri],
            [
                {
                    'added': True,
                    'expression': concept2.uri,
                    'message': ANY
                }
            ]
        )
        self.assertEqual(
            [data for data in response.data if data['expression'] == concept3.uri],
            [
                {
                    'added': True,
                    'expression': concept3.uri,
                    'message': ANY
                }
            ]
        )
        self.assertEqual(
            [data for data in response.data if data['expression'] == mapping.uri],
            [
                {
                    'added': True,
                    'expression': mapping.uri,
                    'message': ANY
                }
            ]
        )

        self.assertEqual(self.collection.references.count(), 4)
        self.assertEqual(self.collection.expansion.concepts.count(), 3)
        self.assertEqual(self.collection.expansion.mappings.count(), 1)
        self.assertTrue(
            self.collection.references.filter(
                expression=concept2.uri,
                code=concept2.mnemonic,
                resource_version=None,
                reference_type='concepts',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=concept3.uri,
                code=concept3.mnemonic,
                resource_version=None,
                reference_type='concepts',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=mapping.uri,
                code=mapping.mnemonic,
                resource_version=None,
                reference_type='mappings',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )

        # excluding one of them should keep rest same -- bug
        response = self.client.put(
            self.collection.uri + 'references/',
            {
                "data": {
                    "concepts": [drop_version(concept3.uri)],
                    "exclude": True
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.collection.references.count(), 5)
        self.assertEqual(self.collection.expansion.concepts.count(), 2)
        self.assertEqual(self.collection.expansion.mappings.count(), 1)
        self.assertTrue(
            self.collection.references.filter(
                expression=concept2.uri,
                code=concept2.mnemonic,
                resource_version=None,
                reference_type='concepts',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=mapping.uri,
                code=mapping.mnemonic,
                resource_version=None,
                reference_type='mappings',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=concept3.uri,
                code=concept3.mnemonic,
                resource_version=None,
                reference_type='concepts',
                cascade__isnull=True,
                transform__isnull=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=concept3.uri,
                code=concept3.mnemonic,
                resource_version=None,
                reference_type='concepts',
                cascade__isnull=True,
                transform__isnull=True,
                include=True,
            ).exists()
        )
        self.assertTrue(
            self.collection.references.filter(
                expression=drop_version(concept3_latest_version.uri),
                code=concept3_latest_version.mnemonic,
                reference_type='concepts',
                include=False,
            ).exists()
        )

    def test_put_bad_expressions(self):
        expression = {
           "data": {
                "url": [
                    "http://worldhealthorganization.github.io/ddcc/ValueSet/DDCC-QR-Format-ValueSet"
                ]
            }
        }
        response = self.client.put(
            self.collection.uri + 'references/',
            expression,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

        response = self.client.put(
            self.collection.uri + 'references/',
            {
                "data": {
                    "concepts": [],
                    "mappings": [],
                    "exclude": True
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 400)


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

        response = self.client.get(
            self.collection_v1.uri + '?includeSummary=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.collection_v1.id))
        self.assertEqual(response.data['id'], 'v1')
        self.assertEqual(response.data['short_code'], 'coll')
        self.assertEqual(response.data['type'], 'Collection Version')
        self.assertEqual(
            response.data['summary'],
            {'active_mappings': None, 'active_concepts': None, 'active_references': 0, 'expansions': 0}
        )

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
            {'external_id': external_id},
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
            {'version': None},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'version': [ErrorDetail(string='This field may not be null.', code='null')]})

    @patch('core.common.models.delete_s3_objects')
    def test_delete(self, delete_s3_objects_mock):
        response = self.client.delete(
            self.collection_v1.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.versions.count(), 1)
        self.assertTrue(self.collection.versions.first().is_latest_version)
        delete_s3_objects_mock.apply_async.assert_called_once_with(
            (f'users/{self.collection.parent.mnemonic}/{self.collection.parent.mnemonic}_coll_v1.',),
            queue='default', permanent=False)


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
            {'external_id': external_id},
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
            {'version': None},
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
        self.extras = {'foo': 'bar', 'tao': 'ching'}
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


class CollectionVersionExtrasViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.extras = {'foo': 'bar', 'tao': 'ching'}
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user, extras=self.extras)
        self.collection_v1 = UserCollectionFactory(mnemonic='coll', user=self.user, extras=self.extras, version='v1')

    def test_get_200(self):
        response = self.client.get(
            self.collection_v1.uri + 'extras/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.extras)

    def test_get_404(self):
        response = self.client.get(
            '/users/foobar/collections/foobar/v1/extras/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 404)


class CollectionExtraRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.extras = {'foo': 'bar', 'tao': 'ching'}
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user, extras=self.extras)

    def test_get_200(self):
        response = self.client.get(
            self.collection.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'foo': 'bar'})

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
            {'foo': 'barbar'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'foo': 'barbar'})
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.extras['foo'], 'barbar')

    def test_put_400(self):
        response = self.client.put(
            self.collection.uri + 'extras/foo/',
            {'foo': None},
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
        self.assertEqual(self.collection.extras, {'tao': 'ching'})

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
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.admin_token = self.admin.get_token()
        self.user = UserProfileFactory(username='username')
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user)
        self.collection_v1 = UserCollectionFactory(version='v1', mnemonic='coll', user=self.user)
        self.v1_updated_at = self.collection_v1.updated_at.strftime('%Y-%m-%d_%H%M%S')
        self.HEAD_updated_at = self.collection.updated_at.strftime('%Y-%m-%d_%H%M%S')

    def test_get_404(self):
        response = self.client.get(
            '/users/foo/collections/coll/v2/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.services.storages.cloud.aws.S3.exists')
    def test_get_204_head(self, s3_exists_mock):
        s3_exists_mock.return_value = False

        response = self.client.get(
            self.collection.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.admin_token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        s3_exists_mock.assert_called_once_with(f"users/username/username_coll_vHEAD.{self.HEAD_updated_at}.zip")

    @patch('core.services.storages.cloud.aws.S3.has_path')
    def test_get_204_for_version(self, s3_has_path_mock):
        s3_has_path_mock.return_value = False

        response = self.client.get(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        s3_has_path_mock.assert_called_once_with("users/username/username_coll_v1.")

    @patch('core.services.storages.cloud.aws.S3.get_streaming_response')
    @patch('core.services.storages.cloud.aws.S3.get_last_key_from_path')
    @patch('core.services.storages.cloud.aws.S3.has_path')
    def test_get_200_version(self, s3_has_path_mock, s3_get_last_key_from_path_mock, s3_streaming_response):
        s3_has_path_mock.return_value = True
        s3_streaming_response.return_value = StreamingHttpResponse(content_type='application/zip')
        s3_get_last_key_from_path_mock.return_value = f'users/username/username_coll_v1.{self.v1_updated_at}.zip'

        response = self.client.get(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        s3_has_path_mock.assert_called_once_with("users/username/username_coll_v1.")
        s3_get_last_key_from_path_mock.assert_called_once_with("users/username/username_coll_v1.")

    @patch('core.services.storages.cloud.aws.S3.get_streaming_response')
    @patch('core.services.storages.cloud.aws.S3.exists')
    def test_get_200_head(self, s3_exists_mock, s3_streaming_response):
        s3_exists_mock.return_value = True
        s3_streaming_response.return_value = StreamingHttpResponse(content_type='application/zip')

        response = self.client.get(
            self.collection.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.admin_token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        s3_exists_mock.assert_called_once_with(f"users/username/username_coll_vHEAD.{self.HEAD_updated_at}.zip")

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

    @patch('core.services.storages.cloud.aws.S3.exists')
    def test_post_303_head(self, s3_exists_mock):
        s3_exists_mock.return_value = True
        response = self.client.post(
            self.collection.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.admin_token,
            format='json'
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['URL'], self.collection.uri + 'export/')
        s3_exists_mock.assert_called_once_with(f"users/username/username_coll_vHEAD.{self.HEAD_updated_at}.zip")

    @patch('core.services.storages.cloud.aws.S3.has_path')
    def test_post_303_version(self, s3_has_path_mock):
        s3_has_path_mock.return_value = True
        response = self.client.post(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['URL'], self.collection_v1.uri + 'export/')
        s3_has_path_mock.assert_called_once_with("users/username/username_coll_v1.")

    @patch('core.collections.views.export_collection')
    @patch('core.services.storages.cloud.aws.S3.exists')
    def test_post_202_head(self, s3_exists_mock, export_collection_mock):
        s3_exists_mock.return_value = False
        export_collection_mock.__name__ = 'export_collection'
        response = self.client.post(
            self.collection.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.admin_token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        s3_exists_mock.assert_called_once_with(f"users/username/username_coll_vHEAD.{self.HEAD_updated_at}.zip")
        export_collection_mock.apply_async.assert_called_once_with((self.collection.id,), task_id=ANY, queue='default')

    @patch('core.collections.views.export_collection')
    @patch('core.services.storages.cloud.aws.S3.has_path')
    def test_post_202_version(self, s3_has_path_mock, export_collection_mock):
        s3_has_path_mock.return_value = False
        export_collection_mock.__name__ = 'export_collection'
        response = self.client.post(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        s3_has_path_mock.assert_called_once_with("users/username/username_coll_v1.")
        export_collection_mock.apply_async.assert_called_once_with(
            (self.collection_v1.id,), task_id=ANY, queue='default')
        self.assertEqual(
            Task.objects.filter(created_by=self.user, state='PENDING', name='export_collection').count(), 1)

    @patch('core.collections.views.export_collection')
    @patch('core.services.storages.cloud.aws.S3.exists')
    def test_post_409_head(self, s3_exists_mock, export_collection_mock):
        s3_exists_mock.return_value = False
        export_collection_mock.__name__ = 'export_collection'
        export_collection_mock.apply_async.side_effect = AlreadyQueued('already-queued')
        response = self.client.post(
            self.collection.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.admin_token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        s3_exists_mock.assert_called_once_with(f"users/username/username_coll_vHEAD.{self.HEAD_updated_at}.zip")
        export_collection_mock.apply_async.assert_called_once_with((self.collection.id,), task_id=ANY, queue='default')
        self.assertEqual(
            Task.objects.filter(created_by=self.admin, state='PENDING', name='export_collection').count(), 0)

    @patch('core.collections.views.export_collection')
    @patch('core.services.storages.cloud.aws.S3.has_path')
    def test_post_409_version(self, s3_has_path_mock, export_collection_mock):
        s3_has_path_mock.return_value = False
        export_collection_mock.apply_async.side_effect = AlreadyQueued('already-queued')
        export_collection_mock.__name__ = 'export_collection'
        response = self.client.post(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        s3_has_path_mock.assert_called_once_with("users/username/username_coll_v1.")
        export_collection_mock.apply_async.assert_called_once_with(
            (self.collection_v1.id,), task_id=ANY, queue='default')
        self.assertEqual(
            Task.objects.filter(created_by=self.user, state='PENDING', name='export_collection').count(), 0)


class CollectionVersionListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll', user=self.user)
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
        self.concept = ConceptFactory()
        self.reference = CollectionReference(
            expression=self.concept.uri, collection=self.collection, system=self.concept.parent.uri, version='HEAD')
        self.reference.full_clean()
        self.reference.save()
        self.expansion.concepts.set(self.reference.concepts.all())
        self.assertEqual(self.collection.references.count(), 1)
        self.assertEqual(self.expansion.concepts.count(), 1)

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
            {
                'id': 'v1',
                'description': 'version1'
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['version'], 'v1')
        self.assertEqual(self.collection.versions.count(), 2)

        last_created_version = self.collection.versions.order_by('created_at').last()
        self.assertEqual(last_created_version, self.collection.get_latest_version())
        self.assertEqual(last_created_version.version, 'v1')
        self.assertEqual(last_created_version.description, 'version1')
        self.assertIsNotNone(last_created_version.expansion_uri)
        self.assertEqual(last_created_version.expansions.count(), 1)
        self.assertEqual(last_created_version.references.count(), 1)

        expansion = last_created_version.expansions.first()
        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.mappings.count(), 0)

    def test_post_201_autoexpand_false(self):
        response = self.client.post(
            self.collection.uri + 'versions/',
            {
                'id': 'v1',
                'description': 'version1',
                'autoexpand': False
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['version'], 'v1')
        self.assertEqual(self.collection.versions.count(), 2)

        last_created_version = self.collection.versions.order_by('created_at').last()
        self.assertEqual(last_created_version, self.collection.get_latest_version())
        self.assertEqual(last_created_version.version, 'v1')
        self.assertEqual(last_created_version.description, 'version1')
        self.assertIsNone(last_created_version.expansion_uri)
        self.assertEqual(last_created_version.expansions.count(), 0)
        self.assertEqual(last_created_version.references.count(), 1)


class ExportCollectionTaskTest(OCLAPITestCase):
    @patch('core.common.utils.get_export_service')
    def test_export_collection(self, export_service_mock):  # pylint: disable=too-many-locals
        s3_mock = Mock()
        export_service_mock.return_value = s3_mock
        s3_mock.url_for = Mock(return_value='https://s3-url')
        s3_mock.upload_file = Mock()
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept2, to_concept=concept1, parent=source)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()

        collection.add_expressions(
            data={
                'expressions': [concept1.uri, concept2.uri, mapping.uri]
            }, user=collection.created_by,
            transform='resourceversions'
        )
        collection.refresh_from_db()

        export_collection(collection.id)  # pylint: disable=no-value-for-parameter

        latest_temp_dir = get_latest_dir_in_path('/tmp/')
        zipped_file = zipfile.ZipFile(latest_temp_dir + '/export.zip')
        exported_data = json.loads(zipped_file.read('export.json').decode('utf-8'))

        self.assertEqual(
            exported_data,
            {
                **CollectionVersionExportSerializer(collection).data,
                'concepts': ANY,
                'mappings': ANY,
                'references': ANY,
                'export_time': ANY
            }
        )

        time_taken = exported_data['export_time']
        self.assertTrue('secs' in time_taken)
        time_taken = float(time_taken.replace('secs', ''))
        self.assertTrue(time_taken > 2)

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
        self.assertEqual(expected_mappings[0]['checksums'], exported_mappings[0]['checksums'])
        self.assertEqual(
            {k: v for k, v in expected_mappings[0].items() if k not in ['checksums', 'updated_on']},
            {k: v for k, v in exported_mappings[0].items() if k not in ['checksums', 'updated_on']}
        )

        exported_references = exported_data['references']
        expected_references = CollectionReferenceDetailSerializer(collection.references.all(), many=True).data

        self.assertEqual(len(exported_references), 3)
        self.assertIn(exported_references[0], expected_references)
        self.assertIn(exported_references[1], expected_references)
        self.assertIn(exported_references[2], expected_references)

        s3_upload_key = collection.version_export_path
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
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
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
        concept3 = ConceptFactory(parent=source2, mnemonic='concept3')
        self.collection.add_expressions(
            {'expressions': [concept1.uri, concept2.uri, concept3.uri]}, self.collection.created_by)

        response = self.client.get(
            self.collection.concepts_url,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)

        response = self.client.get(
            self.collection.uri + 'concepts/concept3/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], 'concept3')
        self.assertEqual(response.data['url'], concept3.uri)

        response = self.client.get(
            self.collection.uri + 'concepts/concept/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 409)

        response = self.client.get(
            self.collection.uri + f'concepts/concept/?uri={concept2.uri}',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], 'concept')
        self.assertEqual(response.data['url'], concept2.uri)

    def test_get_duplicate_concept_name_from_multiple_sources_200(self):
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source1, mnemonic='concept')
        concept2 = ConceptFactory(parent=source2, mnemonic='concept')
        self.collection.add_expressions({'expressions': [concept1.uri, concept2.uri]}, self.collection.created_by)

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
        self.assertEqual(response.data['uuid'], str(concept2.id))

        response = self.client.get(
            self.collection.concepts_url + f'concept/{concept2.version}/?uri=' + concept2.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(concept2.id))


class CollectionMappingsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.collection = UserCollectionFactory(user=self.user)
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(
            self.collection.mappings_url,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        mapping1 = MappingFactory(parent=source1, mnemonic='mapping')
        mapping2 = MappingFactory(parent=source2, mnemonic='mapping')
        mapping3 = MappingFactory(parent=source2, mnemonic='mapping3')
        self.collection.add_expressions(
            {'expressions': [mapping1.uri, mapping2.uri, mapping3.uri]}, self.collection.created_by)

        response = self.client.get(
            self.collection.mappings_url,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)

        response = self.client.get(
            self.collection.uri + 'mappings/mapping3/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], 'mapping3')
        self.assertEqual(response.data['url'], mapping3.uri)

        response = self.client.get(
            self.collection.uri + 'mappings/mapping/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 409)

        response = self.client.get(
            self.collection.uri + f'mappings/mapping/?uri={mapping2.uri}',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], 'mapping')
        self.assertEqual(response.data['url'], mapping2.uri)

    def test_get_duplicate_mapping_name_from_multiple_sources_200(self):
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        mapping1 = MappingFactory(parent=source1, mnemonic='mapping')
        mapping2 = MappingFactory(parent=source2, mnemonic='mapping')
        self.collection.add_expressions({'expressions': [mapping1.uri, mapping2.uri]}, self.collection.created_by)
        response = self.client.get(
            self.collection.mappings_url + 'mapping/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)

        response = self.client.get(
            self.collection.mappings_url + 'mapping/?uri=' + mapping2.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(mapping2.id))

        response = self.client.get(
            self.collection.mappings_url + f'mapping/{mapping2.version}/?uri=' + mapping2.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(mapping2.id))


class CollectionLogoViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory(username='username')
        self.token = self.user.get_token()
        self.collection = UserCollectionFactory(mnemonic='coll1', user=self.user)

    @patch('core.services.storages.cloud.aws.S3.upload_base64')
    def test_post_200(self, upload_base64_mock):
        upload_base64_mock.return_value = 'users/username/collections/coll1/logo.png'
        self.assertIsNone(self.collection.logo_url)
        self.assertIsNone(self.collection.logo_path)

        response = self.client.post(
            self.collection.uri + 'logo/',
            {'base64': 'base64-data'},
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


class CollectionSummaryViewTest(OCLAPITestCase):
    @patch('core.collections.models.Collection.update_children_counts')
    def test_put(self, update_children_counts_mock):
        collection = OrganizationCollectionFactory(version='HEAD')
        admin = UserProfile.objects.get(username='ocladmin')

        response = self.client.put(
            collection.uri + 'summary/',
            HTTP_AUTHORIZATION='Token ' + admin.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        update_children_counts_mock.assert_called_once()


class CollectionVersionSummaryViewTest(OCLAPITestCase):
    @patch('core.collections.models.Collection.update_children_counts')
    def test_put(self, update_children_counts_mock):
        collection = OrganizationCollectionFactory(version='v1')
        admin = UserProfile.objects.get(username='ocladmin')

        response = self.client.put(
            collection.uri + 'summary/',
            HTTP_AUTHORIZATION='Token ' + admin.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        update_children_counts_mock.assert_called_once()

    def test_get(self):
        collection = OrganizationCollectionFactory(version='v1')

        response = self.client.get(
            collection.uri + 'summary/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], collection.version)


class CollectionLatestVersionSummaryViewTest(OCLAPITestCase):
    def test_get(self):
        collection = OrganizationCollectionFactory(version='HEAD')
        OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1')
        version2 = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v2')
        OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v3')

        response = self.client.get(collection.uri + 'latest/summary/')
        self.assertEqual(response.status_code, 404)

        version2.released = True
        version2.save()

        response = self.client.get(collection.uri + 'latest/summary/',)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(version2.id))
        self.assertEqual(response.data['id'], 'v2')


class ReferenceExpressionResolveViewTest(OCLAPITestCase):
    def test_post_200(self):
        admin = UserProfile.objects.get(username='ocladmin')
        token = admin.get_token()
        collection = OrganizationCollectionFactory()
        mapping = MappingFactory()

        response = self.client.post(
            '/$resolveReference/',
            [{'url': collection.uri}, mapping.parent.uri, '/orgs/foobar/'],
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)

        collection_resolution = response.data[0]
        self.assertTrue(collection_resolution['resolved'])
        self.assertIsNotNone(collection_resolution['timestamp'])
        self.assertEqual(collection_resolution['resolution_url'], collection.uri)
        self.assertEqual(collection_resolution['request'], {'url': collection.uri})
        self.assertEqual(collection_resolution['result']['short_code'], collection.mnemonic)
        self.assertEqual(collection_resolution['result']['id'], collection.mnemonic)
        self.assertEqual(collection_resolution['result']['url'], collection.uri)
        self.assertEqual(collection_resolution['result']['type'], 'Collection')

        source_resolution = response.data[1]
        self.assertTrue(source_resolution['resolved'])
        self.assertIsNotNone(source_resolution['timestamp'])
        self.assertEqual(source_resolution['resolution_url'], mapping.parent.uri)
        self.assertEqual(source_resolution['request'], mapping.parent.uri)
        self.assertEqual(source_resolution['result']['short_code'], mapping.parent.mnemonic)
        self.assertEqual(source_resolution['result']['id'], mapping.parent.mnemonic)
        self.assertEqual(source_resolution['result']['url'], mapping.parent.uri)

        unknown_resolution = response.data[2]
        self.assertFalse(unknown_resolution['resolved'])
        self.assertIsNotNone(unknown_resolution['timestamp'])
        self.assertEqual(unknown_resolution['resolution_url'], '/orgs/foobar/')
        self.assertEqual(unknown_resolution['request'], '/orgs/foobar/')
        self.assertFalse('result' in unknown_resolution)

        response = self.client.post(
            '/$resolveReference/',
            '/orgs/foobar/',
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        unknown_resolution = response.data[0]
        self.assertFalse(unknown_resolution['resolved'])
        self.assertIsNotNone(unknown_resolution['timestamp'])
        self.assertEqual(unknown_resolution['resolution_url'], '/orgs/foobar/')
        self.assertEqual(unknown_resolution['request'], '/orgs/foobar/')
        self.assertFalse('result' in unknown_resolution)


class CollectionVersionExpansionMappingRetrieveViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        self.mapping = MappingFactory()
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.reference = CollectionReference(
            expression=self.mapping.url, collection=self.collection, system=self.mapping.parent.uri, version='HEAD')
        self.reference.save()
        self.expansion.mappings.add(self.mapping)
        self.reference.mappings.add(self.mapping)

    def test_get_200(self):
        response = self.client.get(self.expansion.url + f'mappings/{self.mapping.mnemonic}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], str(self.mapping.mnemonic))
        self.assertEqual(response.data['type'], 'Mapping')

    def test_get_404(self):
        response = self.client.get(self.collection.url + f'expansions/e1/mappings/{self.mapping.mnemonic}/')
        self.assertEqual(response.status_code, 404)

        response = self.client.get(self.expansion.url + f'mappings/{self.mapping.mnemonic}/1234/')
        self.assertEqual(response.status_code, 404)

    def test_get_409(self):
        mapping2 = MappingFactory(mnemonic=self.mapping.mnemonic)
        self.expansion.mappings.add(mapping2)
        self.reference.mappings.add(mapping2)

        response = self.client.get(self.expansion.url + f'mappings/{self.mapping.mnemonic}/')
        self.assertEqual(response.status_code, 409)

        response = self.client.get(
            self.expansion.url + f'mappings/{self.mapping.mnemonic}/?uri={mapping2.url}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(mapping2.id))


class CollectionVersionMappingRetrieveViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        self.mapping = MappingFactory()
        self.reference = CollectionReference(
            expression=self.mapping.url, collection=self.collection, system=self.mapping.parent.uri, version='HEAD')
        self.reference.save()
        self.reference.mappings.add(self.mapping)

    def test_get_200(self):
        expansion = ExpansionFactory(collection_version=self.collection)
        expansion.mappings.add(self.mapping)
        self.collection.expansion_uri = expansion.uri
        self.collection.save()
        response = self.client.get(self.collection.url + f'mappings/{self.mapping.mnemonic}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], str(self.mapping.mnemonic))
        self.assertEqual(response.data['type'], 'Mapping')

    def test_get_404(self):
        response = self.client.get(self.collection.url + f'/mappings/{self.mapping.mnemonic}/')
        self.assertEqual(response.status_code, 404)

        expansion = ExpansionFactory(collection_version=self.collection)
        expansion.mappings.add(self.mapping)
        self.collection.expansion_uri = expansion.uri
        self.collection.save()

        response = self.client.get(self.collection.url + f'mappings/{self.mapping.mnemonic}/1234/')
        self.assertEqual(response.status_code, 404)


class CollectionVersionExpansionConceptRetrieveViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.concept = ConceptFactory()
        self.reference = CollectionReference(
            expression=self.concept.url, collection=self.collection, system=self.concept.parent.uri, version='HEAD')
        self.reference.save()
        self.expansion.concepts.add(self.concept)
        self.reference.concepts.add(self.concept)

    def test_get_200(self):
        response = self.client.get(self.expansion.url + f'concepts/{self.concept.mnemonic}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], str(self.concept.mnemonic))
        self.assertEqual(response.data['type'], 'Concept')

    def test_get_404(self):
        response = self.client.get(
            self.collection.url + f'expansions/e1/concepts/{self.concept.mnemonic}/')

        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            self.expansion.url + f'concepts/{self.concept.mnemonic}/1234/')

        self.assertEqual(response.status_code, 404)

    def test_get_409(self):
        concept2 = ConceptFactory(mnemonic=self.concept.mnemonic)
        self.expansion.concepts.add(concept2)
        self.reference.concepts.add(concept2)
        response = self.client.get(
            self.expansion.url + f'concepts/{self.concept.mnemonic}/')

        self.assertEqual(response.status_code, 409)

        response = self.client.get(
            self.expansion.url + f'concepts/{self.concept.mnemonic}/?uri={concept2.uri}'
        )

        self.assertEqual(response.status_code, 200)


class CollectionVersionExpansionConceptMappingsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory()
        self.source = OrganizationSourceFactory(organization=self.org)
        self.collection = OrganizationCollectionFactory(organization=self.org)
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.concept = ConceptFactory(parent=self.source)
        self.mapping = MappingFactory(from_concept=self.concept, parent=self.source)
        self.mapping2 = MappingFactory(from_concept=self.concept) # random owner/parent
        self.reference = CollectionReference(expression=self.concept.url, collection=self.collection)
        self.reference.save()
        self.expansion.concepts.add(self.concept)
        self.reference.concepts.add(self.concept)

    def test_get_200(self):
        response = self.client.get(
            self.expansion.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        self.expansion.mappings.add(self.mapping2)

        response = self.client.get(self.expansion.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['url'], self.mapping2.url)

        self.expansion.mappings.add(self.mapping)

        response = self.client.get(
            self.expansion.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data]),
            sorted([self.mapping.url, self.mapping2.url])
        )

    def test_get_404(self):
        response = self.client.get(
            self.collection.url + f'expansions/e1/concepts/{self.concept.mnemonic}/mappings/?brief=true')

        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            self.expansion.url + f'concepts/{self.concept.mnemonic}/1234/mappings/?brief=true')

        self.assertEqual(response.status_code, 404)

    def test_get_409(self):
        concept2 = ConceptFactory(mnemonic=self.concept.mnemonic)
        self.expansion.concepts.add(concept2)
        self.reference.concepts.add(concept2)
        response = self.client.get(
            self.expansion.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true')

        self.assertEqual(response.status_code, 409)

        response = self.client.get(
            self.expansion.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true&uri={concept2.uri}'
        )

        self.assertEqual(response.status_code, 200)


class CollectionVersionConceptMappingsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory()
        self.source = OrganizationSourceFactory(organization=self.org)
        self.collection = OrganizationCollectionFactory(organization=self.org)
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.concept = ConceptFactory(parent=self.source)
        self.mapping = MappingFactory(from_concept=self.concept, parent=self.source)
        self.mapping2 = MappingFactory(from_concept=self.concept)  # random owner/parent
        self.reference = CollectionReference(expression=self.concept.url, collection=self.collection)
        self.reference.save()
        self.expansion.concepts.add(self.concept)
        self.reference.concepts.add(self.concept)

    def test_get_200(self):
        response = self.client.get(
            self.collection.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true')

        self.assertEqual(response.status_code, 404)

        self.expansion.mappings.add(self.mapping2)

        response = self.client.get(
            self.collection.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true')

        self.assertEqual(response.status_code, 404)

        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()

        response = self.client.get(
            self.collection.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['url'], self.mapping2.url)

        self.expansion.mappings.add(self.mapping)

        response = self.client.get(
            self.collection.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data]),
            sorted([self.mapping.url, self.mapping2.url])
        )

    def test_get_404(self):
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
        response = self.client.get(
            self.collection.url + f'concepts/{self.concept.mnemonic}/1234/mappings/?brief=true')

        self.assertEqual(response.status_code, 404)

    def test_get_409(self):
        concept2 = ConceptFactory(mnemonic=self.concept.mnemonic)
        self.expansion.concepts.add(concept2)
        self.reference.concepts.add(concept2)
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
        response = self.client.get(
            self.collection.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true')

        self.assertEqual(response.status_code, 409)

        response = self.client.get(
            self.collection.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true&uri={concept2.uri}'
        )

        self.assertEqual(response.status_code, 200)


class CollectionVersionExpansionMappingsViewTest(OCLAPITestCase):
    def test_get(self):
        org = OrganizationFactory()
        source = OrganizationSourceFactory(organization=org)
        collection = OrganizationCollectionFactory(organization=org)
        expansion = ExpansionFactory(collection_version=collection)
        concept = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept, parent=source)
        reference = CollectionReference(expression=concept.url, collection=collection)
        reference.save()
        expansion.concepts.add(concept)
        reference.concepts.add(concept)
        expansion.mappings.add(mapping)

        response = self.client.get(collection.url + 'expansions/e1/mappings/')

        self.assertEqual(response.status_code, 404)

        response = self.client.get(expansion.url + 'mappings/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)


class CollectionVersionExpansionConceptsViewTest(OCLAPITestCase):
    def test_get(self):
        org = OrganizationFactory()
        source = OrganizationSourceFactory(organization=org)
        collection = OrganizationCollectionFactory(organization=org)
        expansion = ExpansionFactory(collection_version=collection)
        concept = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept, parent=source)
        reference = CollectionReference(expression=concept.url, collection=collection)
        reference.save()
        expansion.concepts.add(concept)
        reference.concepts.add(concept)
        expansion.mappings.add(mapping)

        response = self.client.get(collection.url + 'expansions/e1/concepts/')

        self.assertEqual(response.status_code, 404)

        response = self.client.get(expansion.url + 'concepts/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)


class CollectionReferenceViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        self.reference = CollectionReference(
            expression='/concepts/', collection=self.collection, reference_type='concepts')
        self.reference.save()

    def test_get_404(self):
        response = self.client.get(
            self.collection.parent.url + 'collections/foobar/references/' + str(self.reference.id) + '/')

        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            self.collection.url + 'references/123/')

        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            self.collection.url + 'v1/references/' + str(self.reference.id) + '/')

        self.assertEqual(response.status_code, 404)

    def test_get_200(self):
        response = self.client.get(self.reference.uri)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uri'], self.reference.uri)
        self.assertEqual(response.data['id'], self.reference.id)

        response = self.client.get(self.collection.url + 'HEAD/references/'  + str(self.reference.id) + '/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uri'], self.reference.uri)
        self.assertEqual(response.data['id'], self.reference.id)

    def test_delete_401(self):
        response = self.client.delete(self.reference.uri)

        self.assertEqual(response.status_code, 401)

    def test_delete_204(self):
        response = self.client.delete(
            self.reference.uri,
            HTTP_AUTHORIZATION='Token ' + self.collection.created_by.get_token(),
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.references.count(), 0)

    def test_delete_collection_version_reference_405(self):
        collection_v1 = OrganizationCollectionFactory(
            mnemonic=self.collection.mnemonic, version='v1', organization=self.collection.organization)
        reference = CollectionReference(
            expression='/concepts/', collection=collection_v1, reference_type='concepts')
        reference.save()

        response = self.client.delete(
            collection_v1.url + 'references/' + str(reference.id) + '/',
            HTTP_AUTHORIZATION='Token ' + self.collection.created_by.get_token(),
        )

        self.assertEqual(response.status_code, 405)
        self.assertEqual(collection_v1.references.count(), 1)


class CollectionReferenceConceptsViewTest(OCLAPITestCase):
    def test_get(self):
        response = self.client.get(
            '/orgs/foo/collections/bar/references/123/concepts/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

        org = OrganizationFactory()
        collection = OrganizationCollectionFactory(organization=org)
        source = OrganizationSourceFactory(organization=org)
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)

        response = self.client.get(
            collection.uri + 'references/123/concepts/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

        reference = CollectionReference(collection=collection, expression=source.uri + 'concepts/')
        reference.save()
        reference.concepts.set([concept1, concept2])

        response = self.client.get(
            reference.uri + 'concepts/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(sorted([data['url'] for data in response.data]), sorted([concept1.uri, concept2.uri]))


class CollectionReferenceMappingsViewTest(OCLAPITestCase):
    def test_get(self):
        response = self.client.get(
            '/orgs/foo/collections/bar/references/123/mappings/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

        org = OrganizationFactory()
        collection = OrganizationCollectionFactory(organization=org)
        source = OrganizationSourceFactory(organization=org)
        mapping1 = MappingFactory(parent=source)
        mapping2 = MappingFactory(parent=source)

        response = self.client.get(
            collection.uri + 'references/123/mappings/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

        reference = CollectionReference(collection=collection, expression=source.uri + 'mappings/')
        reference.save()
        reference.mappings.set([mapping1, mapping2])

        response = self.client.get(
            reference.uri + 'mappings/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(sorted([data['url'] for data in response.data]), sorted([mapping1.uri, mapping2.uri]))

        response = self.client.get(
            f"{collection.uri}HEAD/references/{reference.id}/mappings/",
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(sorted([data['url'] for data in response.data]), sorted([mapping1.uri, mapping2.uri]))


class CollectionVersionExpansionViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        self.expansion_default = ExpansionFactory(collection_version=self.collection)
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.collection.expansion_uri = self.expansion_default.uri
        self.collection.save()
        self.assertEqual(self.collection.expansions.count(), 2)

    def test_delete_404(self):
        response = self.client.delete(
            self.collection.url + 'expansions/e1/',
            HTTP_AUTHORIZATION='Token ' + self.collection.created_by.get_token(),
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_400(self):
        response = self.client.delete(
            self.expansion_default.url,
            HTTP_AUTHORIZATION='Token ' + self.collection.created_by.get_token(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'errors': ['Cannot delete default expansion']})

    def test_delete_204(self):
        response = self.client.delete(
            self.expansion.url,
            HTTP_AUTHORIZATION='Token ' + self.collection.created_by.get_token(),
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.collection.expansions.count(), 1)


class CollectionVersionExpansionsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        self.token = self.collection.created_by.get_token()

    def test_post(self):
        response = self.client.post(
            self.collection.url + 'HEAD/expansions/',
            {'mnemonic': 'e1'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)

        self.assertIsNone(self.collection.expansion_uri)

        response = self.client.post(
            self.collection.url + 'HEAD/expansions/',
            {'mnemonic': 'e1', 'parameters': {'activeOnly': False}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['mnemonic'], 'e1')
        self.assertIsNotNone(response.data['id'])
        self.assertIsNotNone(response.data['parameters'])

        self.collection.refresh_from_db()
        self.assertEqual(self.collection.expansions.count(), 1)
        self.assertIsNotNone(self.collection.expansion_uri)

    def test_get(self):
        response = self.client.get(
            self.collection.url + 'HEAD/expansions/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

        expansion = ExpansionFactory(mnemonic='e1', collection_version=self.collection)

        response = self.client.get(
            self.collection.url + 'HEAD/expansions/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], expansion.id)
        self.assertEqual(response.data[0]['mnemonic'], 'e1')

        response = self.client.get(
            self.collection.url + 'HEAD/expansions/?includeSummary=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], expansion.id)
        self.assertEqual(response.data[0]['mnemonic'], 'e1')
        self.assertEqual(response.data[0]['summary'], {'active_concepts': 0, 'active_mappings': 0})

        response = self.client.get(
            self.collection.url + 'HEAD/expansions/?verbose=true&includeSummary=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], expansion.id)
        self.assertEqual(response.data[0]['mnemonic'], 'e1')
        self.assertEqual(response.data[0]['parameters'], expansion.parameters)
        self.assertEqual(response.data[0]['summary'], {'active_concepts': 0, 'active_mappings': 0})
