import io
import json
import zipfile

import boto3
from moto import mock_aws

from celery_once import AlreadyQueued
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings, tag
from mock import patch, Mock, ANY
from mock.mock import PropertyMock
from rest_framework.exceptions import ErrorDetail

from core.collections.constants import REFERENCE_VERSION_NOT_HELD, REFERENCE_EXCLUDED_BY_PARAMETERS
from core.collections.models import CollectionReference, Collection, default_expansion_parameters
from core.collections.serializers import CollectionVersionExportSerializer, CollectionReferenceDetailSerializer
from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW
from core.common.tasks import export_collection, delete_s3_objects, add_references, \
    update_collection_active_concepts_count, update_collection_active_mappings_count
from core.common.tests import OCLAPITestCase
from core.integration_tests.tests_access_control import PrivateHeadVersionAccessMixin
from core.common.utils import get_latest_dir_in_path, drop_version, get_export_key_pattern
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.serializers import ConceptVersionExportSerializer, ConceptListSerializer
from core.concepts.tests.factories import ConceptFactory
from core.mappings.serializers import MappingListSerializer, MappingVersionExportSerializer
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.services.storages.cloud.aws import S3
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.tasks.models import Task, AsyncTask, QueueOnceCustomTask
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

    def test_get_200_with_latest_released_version(self):
        coll = OrganizationCollectionFactory(mnemonic='coll1')
        OrganizationCollectionFactory(
            mnemonic=coll.mnemonic, organization=coll.organization, version='v1', released=True)
        OrganizationCollectionFactory(
            mnemonic=coll.mnemonic, organization=coll.organization, version='v2', released=False)

        response = self.client.get(f'/orgs/{coll.parent.mnemonic}/collections/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertFalse('latest_released_version' in response.data[0])

        response = self.client.get(
            f'/orgs/{coll.parent.mnemonic}/collections/?includeLatestReleasedVersion=true', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], 'coll1')
        self.assertEqual(response.data[0]['latest_released_version'], 'v1')

        response = self.client.get('/collections/?includeLatestReleasedVersion=true', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [data['latest_released_version'] for data in response.data if data['id'] == 'coll1'],
            ['v1']
        )

    def test_get_200_with_latest_released_version_none_when_never_released(self):
        coll = OrganizationCollectionFactory(mnemonic='coll1')
        OrganizationCollectionFactory(
            mnemonic=coll.mnemonic, organization=coll.organization, version='v1', released=False)

        response = self.client.get(
            f'/orgs/{coll.parent.mnemonic}/collections/?includeLatestReleasedVersion=true', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertIsNone(response.data[0]['latest_released_version'])

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
        self.assertEqual(
            collection.expansion.get_member_counts(),
            {'active_concepts': 1, 'active_mappings': 0, 'retired_concepts': 0, 'retired_mappings': 0})
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

    def test_post_201_ignores_expansion_uri(self):
        org = OrganizationFactory(mnemonic='org')
        user = UserProfileFactory(organizations=[org], username='user')
        other_expansion = ExpansionFactory(collection_version=OrganizationCollectionFactory())

        for collection_id, expansion_uri, autoexpand_head in [
                ('coll1', 'https://other.example/x/', True), ('coll2', other_expansion.uri, False)]:
            with self.subTest(expansion_uri=expansion_uri):
                response = self.client.post(
                    '/orgs/org/collections/',
                    {
                        'id': collection_id,
                        'name': 'Collection',
                        'expansion_uri': expansion_uri,
                        'autoexpand_head': autoexpand_head
                    },
                    HTTP_AUTHORIZATION='Token ' + user.get_token(),
                    format='json'
                )

                self.assertEqual(response.status_code, 201)
                collection = org.collection_set.get(mnemonic=collection_id, version='HEAD')
                own_expansion = collection.expansions.first()
                self.assertEqual(collection.expansion_uri, own_expansion.uri if autoexpand_head else None)
                self.assertEqual(response.data['expansion_url'], collection.expansion_uri)

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

    def test_get_leaves_out_retired_unless_include_retired(self):
        org = OrganizationFactory(mnemonic='retired-list-org')
        OrganizationCollectionFactory(organization=org, mnemonic='active-collection')
        OrganizationCollectionFactory(organization=org, mnemonic='retired-collection', retired=True)
        user = UserProfileFactory(username='retired-list-user')
        UserCollectionFactory(user=user, mnemonic='active-collection')
        UserCollectionFactory(user=user, mnemonic='retired-collection', retired=True)

        for url in [org.collections_url, user.collections_url]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([coll['short_code'] for coll in response.data], ['active-collection'])

            response = self.client.get(url + '?includeRetired=true')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                sorted(coll['short_code'] for coll in response.data), ['active-collection', 'retired-collection'])

        response = self.client.get(
            '/user/collections/', HTTP_AUTHORIZATION='Token ' + user.get_token())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([coll['short_code'] for coll in response.data], ['active-collection'])


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

    def test_get_200_include_concepts_ordered_by_id_desc(self):
        concept1 = ConceptFactory()
        concept2 = ConceptFactory()
        coll = OrganizationCollectionFactory(mnemonic='coll1')
        expansion = ExpansionFactory(collection_version=coll)
        coll.expansion_uri = expansion.uri
        coll.save()
        expansion.concepts.add(concept1, concept2)

        response = self.client.get(coll.uri + '?includeConcepts=true', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [c['url'] for c in response.data['concepts']],
            [c.uri for c in sorted([concept1, concept2], key=lambda c: c.id, reverse=True)])

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

    @override_settings(LIST_MAX_LIMIT=1)
    def test_get_200_with_resources_limit_capped(self):
        coll = OrganizationCollectionFactory(mnemonic='coll1')
        expansion = ExpansionFactory(collection_version=coll)
        coll.expansion_uri = expansion.uri
        coll.save()
        expansion.concepts.add(ConceptFactory(), ConceptFactory())
        expansion.mappings.add(MappingFactory(), MappingFactory())

        response = self.client.get(
            coll.uri + '?includeConcepts=true&includeMappings=true&limit=1000', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['concepts']), 1)
        self.assertEqual(len(response.data['mappings']), 1)

        response = self.client.get(coll.uri + 'concepts/?limit=1000', format='json')
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response['num_found'], '2')

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
        owner = coll.organization.mnemonic
        self.assertEqual(delete_s3_objects_mock.apply_async.call_count, 4)
        for prefix in (
                f'orgs/{owner}/collections/coll1/HEAD.', f'orgs/{owner}/collections/coll1/v1.',
                f'orgs/{owner}/{owner}_coll1_vHEAD.', f'orgs/{owner}/{owner}_coll1_v1.'):
            delete_s3_objects_mock.apply_async.assert_any_call(
                (prefix, get_export_key_pattern(prefix)), queue='default', permanent=False)

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
        delete_collection_task_mock.apply_async.assert_called_once_with((coll.id, ), task_id=ANY, queue='default')

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


@tag('es')
class CollectionReferencesFilterViewTest(OCLAPITestCase):
    def test_put_200_filter_with_in_op(self):
        user = UserProfileFactory()
        collection = UserCollectionFactory(user=user)
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        source = OrganizationSourceFactory()
        diagnosis = ConceptFactory(parent=source, concept_class='Diagnosis')
        procedure = ConceptFactory(parent=source, concept_class='Procedure')
        ConceptFactory(parent=source, concept_class='Misc')
        ConceptDocument().update(source.concepts_set.all())

        response = self.client.put(
            collection.uri + 'references/',
            {'data': [{'system': source.uri, 'filter': [
                {'property': 'concept_class', 'op': 'in', 'value': 'Diagnosis,Procedure'}]}]},
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['added'], True)
        self.assertEqual(
            set(collection.expansion.concepts.values_list('versioned_object_id', flat=True)),
            {diagnosis.id, procedure.id})


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
        self.assertNotIn('resolved_repo_versions', response.data[0])

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

    @override_settings(LIST_MAX_LIMIT=1)
    def test_get_200_limit_capped(self):
        CollectionReference(expression='/foo/bar', collection=self.collection).save()

        response = self.client.get(self.collection.uri + 'references/?limit=1000', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response['num_found'], '2')
        self.assertEqual(response['pages'], '2')

        response = self.client.get(self.collection.uri + 'references/?limit=1000&page=2', format='json')
        self.assertEqual(len(response.data), 1)

    def test_get_200_with_resolved_repo_versions(self):
        duplicate_reference = CollectionReference(
            expression=f'{self.concept.parent.uri}concepts/duplicate/',
            collection=self.collection,
            system=self.concept.parent.uri,
            version='HEAD'
        )
        duplicate_reference.save()

        with patch.object(Source, 'resolve_reference_expression', wraps=Source.resolve_reference_expression) as mock:
            response = self.client.get(
                self.collection.uri + 'references/?includeResolvedRepoVersions=true',
                format='json'
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(mock.call_count, 1)
        for reference in response.data:
            self.assertIn('resolved_repo_versions', reference)
            self.assertEqual(
                [version['version_url'] for version in reference['resolved_repo_versions']],
                [self.concept.parent.uri]
            )

    def test_get_200_with_resolved_repo_versions_from_expansion(self):
        source = self.concept.parent
        v1 = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1', released=True)
        OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v2', released=True)
        reference = CollectionReference(
            expression=f'{source.uri}concepts/{self.concept.mnemonic}/', collection=self.collection,
            system=source.uri, code=self.concept.mnemonic)
        reference.save()
        self.expansion.evaluated_source_versions.add(v1)

        response = self.client.get(
            self.collection.uri + 'references/?includeResolvedRepoVersions=true', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item['expression']: [version['version_url'] for version in item['resolved_repo_versions']]
             for item in response.data},
            {reference.expression: [v1.uri], self.reference.expression: [source.uri]}
        )

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
        self.assertEqual(self.collection.references.count(), 1)
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
        self.assertEqual(self.collection.references.count(), 0)
        self.assertEqual(self.collection.expansion.concepts.count(), 0)
        self.assertEqual(self.collection.expansion.mappings.count(), 0)

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
            {'task_id': ANY, 'queue': 'default'}
        )
        self.assertTrue(
            '-foobar~default' in add_references_mock.apply_async.call_args[1]['task_id'],
        )
        self.assertEqual(
            len(add_references_mock.apply_async.call_args[1]['task_id']), 36 + 1 + 7 + 1 + 6
        )

    def test_put_async_counts_what_the_references_added_once_they_commit(self):
        concept = ConceptFactory()
        retired_concept = ConceptFactory(retired=True)
        mapping = MappingFactory()
        published = []

        def record(task, *args, **kwargs):
            published.append((task, args[0] if args else kwargs.get('args')))

        def published_counts():
            return sorted((task.name, args) for task, args in published if task.name.endswith('_count'))

        # the tasks the request and then the worker queue are recorded, and run here as the worker would
        with override_settings(TEST_MODE=False), \
                patch.object(AsyncTask, 'apply_async', autospec=True, side_effect=record), \
                patch.object(QueueOnceCustomTask, 'apply_async', autospec=True, side_effect=record):
            response = self.client.put(
                self.collection.uri + 'references/?async=true',
                {'data': {'expressions': [
                    obj.get_latest_version().url for obj in [concept, retired_concept, mapping]]}},
                HTTP_AUTHORIZATION='Token ' + self.token,
                format='json'
            )
            self.assertEqual(response.status_code, 202)
            [(job, job_args)] = [(task, args) for task, args in published if task.name == add_references.name]
            with self.captureOnCommitCallbacks(execute=True):
                job(*job_args)
                counts_queued_before_commit = published_counts()

        self.assertEqual(counts_queued_before_commit, [])
        self.assertEqual(
            published_counts(),
            sorted([(update_collection_active_concepts_count.name, (self.collection.id,)),
                    (update_collection_active_mappings_count.name, (self.collection.id,))])
        )
        for task, args in published:
            if task.name.endswith('_count'):
                task.run(*args)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.expansion.concepts.count(), 3)
        self.assertEqual((self.collection.active_concepts, self.collection.active_mappings), (2, 1))

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
        self.assertEqual(self.collection.references.count(), 4)
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
        self.assertEqual(self.collection.references.count(), 5)
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
        self.assertEqual(self.collection.references.count(), 6)
        self.assertEqual(self.collection.expansion.concepts.count(), 3)
        self.assertEqual(self.collection.expansion.mappings.count(), 2)
        self.assertEqual(self.collection.active_concepts, 3)
        self.assertEqual(self.collection.active_mappings, 2)

    def test_put_200_reference_resolving_to_nothing_names_the_version_it_looked_in(self):
        source = self.concept.parent
        # unreleased, so the versionless concept2 reference still resolves to HEAD
        empty_version = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1', released=False)
        concept2 = ConceptFactory(parent=source)
        missing_expression = f'{empty_version.uri}concepts/{concept2.mnemonic}/'

        response = self.client.put(
            self.collection.uri + 'references/',
            {'data': {'expressions': [missing_expression, concept2.uri]}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.collection.references.filter(expression=missing_expression).exists())
        self.assertEqual(self.collection.expansion.concepts.count(), 2)
        self.assertCountEqual(
            response.data,
            [
                {
                    'added': True,
                    'resolved': False,
                    'expression': missing_expression,
                    'repo_versions': [empty_version.uri],
                    'message': f'The reference is saved in collection {self.collection.name}, but it resolved to '
                               f'no concepts or mappings in {empty_version.uri}'
                },
                {
                    'added': True,
                    'expression': concept2.uri,
                    'message': f'The concept {concept2.mnemonic} is successfully added to '
                               f'collection {self.collection.name}'
                }
            ]
        )

    @staticmethod
    def _source_with_concept_version_no_release_holds():
        """A concept version no released version holds; v2 holds its replacement"""
        source = OrganizationSourceFactory(organization=OrganizationFactory())
        concept = ConceptFactory(parent=source, mnemonic='c1')
        old = concept.get_latest_version()
        new = ConceptFactory(parent=source, mnemonic='c1', versioned_object=concept, version='new')
        old.sources.remove(source)
        v2 = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v2', released=True)
        new.sources.add(v2)
        return source, old, v2

    def _put_with_resolution_on(self, expression):
        with patch.object(CollectionReference, 'is_resource_version_repo_resolution_on', return_value=True):
            return self.client.put(
                self.collection.uri + 'references/',
                {'data': {'expressions': [expression]}},
                HTTP_AUTHORIZATION='Token ' + self.token,
                format='json'
            )

    def test_put_200_pinned_reference_no_version_holds_says_so_end_to_end(self):
        source, old, _ = self._source_with_concept_version_no_release_holds()

        response = self._put_with_resolution_on(old.uri)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [{
                'added': True,
                'resolved': False,
                'expression': old.uri,
                'repo_versions': [source.uri],
                'message': f'The reference is saved in collection {self.collection.name}, but no version of '
                           f'{source.uri} holds resource version {old.version}'
            }]
        )

    def test_put_200_pinned_reference_naming_a_version_that_does_not_hold_it_names_that_version(self):
        _, old, v2 = self._source_with_concept_version_no_release_holds()
        expression = f'{v2.uri}concepts/c1/{old.version}/'

        response = self._put_with_resolution_on(expression)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['resolved'], False)
        self.assertEqual(response.data[0]['repo_versions'], [v2.uri])
        self.assertEqual(
            response.data[0]['message'],
            f'The reference is saved in collection {self.collection.name}, but {v2.uri} does not hold '
            f'resource version {old.version}'
        )

    @patch('core.collections.views.add_references')
    def test_put_200_pinned_reference_no_version_holds_says_so(self, add_references_mock):
        add_references_mock.__name__ = 'add_references'
        source = self.concept.parent
        reference = CollectionReference(
            expression=f'{self.concept.uri}v9/', collection=self.collection, system=source.uri,
            code=self.concept.mnemonic, resource_version='v9')
        reference.save()
        add_references_mock.return_value = ([reference.id], {}, [[reference.id, REFERENCE_VERSION_NOT_HELD]])

        response = self.client.put(
            self.collection.uri + 'references/',
            {'data': {'expressions': [reference.expression]}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [{
                'added': True,
                'resolved': False,
                'expression': reference.expression,
                'repo_versions': [source.uri],
                'message': f'The reference is saved in collection {self.collection.name}, but no version of '
                           f'{source.uri} holds resource version v9'
            }]
        )

    @patch('core.collections.views.add_references')
    def test_put_400_filter_with_a_property_or_op_it_cant_apply(self, add_references_mock):
        source = self.concept.parent
        expression = f'{source.uri}concepts/?conceptclass=Diagnosis'
        for data, expected in (
                ({'expressions': [expression]}, {expression: {'filter': [
                    "Filter property 'conceptclass' is not supported."]}}),
                ([{'system': source.uri, 'filter': [{'property': 'concept_class', 'op': 'is-a', 'value': 'D'}]}],
                 {f'{source.uri}concepts/?concept_class=D': {'filter': [
                     "Filter op 'is-a' is not supported for property 'concept_class'. Use one of: =, in."]}})):
            with self.subTest(data=data):
                response = self.client.put(
                    self.collection.uri + 'references/',
                    {'data': data},
                    HTTP_AUTHORIZATION='Token ' + self.token,
                    format='json'
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), expected)
        add_references_mock.assert_not_called()
        self.assertEqual(self.collection.references.count(), 1)

    @patch('core.collections.views.add_references')
    def test_put_search_url_with_paging_parameters_isnt_refused(self, add_references_mock):
        add_references_mock.__name__ = 'add_references'
        add_references_mock.return_value = ([], {}, [])

        response = self.client.put(
            self.collection.uri + 'references/',
            {'data': {'expressions': [f'{self.concept.parent.uri}concepts/?q=fever&page=2&limit=25&sortAsc=name']}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        add_references_mock.assert_called_once()

    def test_put_200_concept_expression_with_a_query_string_its_filter_never_applies_to(self):
        response = self.client.put(
            self.collection.uri + 'references/',
            {'data': {'expressions': [f'{self.concept.uri}?includeMappings=true']}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)

    def test_put_200_reference_with_a_valueset_that_has_no_expansion_says_so_on_autoexpand_head(self):
        collection = UserCollectionFactory(user=self.user)
        expansion = ExpansionFactory(collection_version=collection, mnemonic='autoexpand-HEAD')
        collection.expansion_uri = expansion.uri
        collection.save()
        self.assertTrue(expansion.is_auto_generated)
        unexpanded = OrganizationCollectionFactory()

        response = self.client.put(
            collection.uri + 'references/',
            {'data': [
                {'system': self.concept.parent.uri, 'valueset': [unexpanded.uri], 'code': self.concept.mnemonic}]},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['resolved'], False)
        self.assertEqual(
            response.data[0]['message'],
            f'The reference is saved in collection {collection.name}, but it adds nothing: '
            f'value set {unexpanded.uri} has no default expansion')
        self.assertEqual(collection.expansion.concepts.count(), 0)

    def test_put_200_reference_with_a_valueset_that_has_no_expansion_says_so(self):
        source = self.concept.parent
        concept = ConceptFactory(parent=source)
        unexpanded = OrganizationCollectionFactory()

        response = self.client.put(
            self.collection.uri + 'references/',
            {'data': [{'system': source.uri, 'valueset': [unexpanded.uri], 'code': concept.mnemonic}]},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['added'], True)
        self.assertEqual(response.data[0]['resolved'], False)
        self.assertEqual(response.data[0]['repo_versions'], [unexpanded.uri])
        self.assertEqual(
            response.data[0]['message'],
            f'The reference is saved in collection {self.collection.name}, but it adds nothing: '
            f'value set {unexpanded.uri} has no default expansion')
        self.assertEqual(self.collection.expansion.concepts.count(), 1)

    @patch('core.collections.views.add_references')
    def test_put_200_reference_the_expansion_parameters_exclude_says_so(self, add_references_mock):
        add_references_mock.__name__ = 'add_references'
        source = self.concept.parent
        reference = CollectionReference(
            expression=self.concept.uri, collection=self.collection, system=source.uri, code=self.concept.mnemonic)
        reference.save()
        add_references_mock.return_value = (
            [reference.id], {}, [[reference.id, REFERENCE_EXCLUDED_BY_PARAMETERS]])

        response = self.client.put(
            self.collection.uri + 'references/',
            {'data': {'concepts': [self.concept.uri]}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['resolved'], False)
        self.assertEqual(response.data[0]['repo_versions'], [source.uri])
        self.assertEqual(
            response.data[0]['message'],
            f"The reference is saved in collection {self.collection.name}, but the expansion's parameters "
            f"exclude everything it resolved to in {source.uri}"
        )

    @patch('core.collections.views.add_references')
    def test_put_200_with_a_result_from_an_older_worker(self, add_references_mock):
        add_references_mock.__name__ = 'add_references'
        concept2 = ConceptFactory()
        reference = CollectionReference(
            expression=concept2.uri, collection=self.collection, system=concept2.parent.uri, code=concept2.mnemonic)
        reference.save()
        add_references_mock.return_value = ([reference.id], {})

        response = self.client.put(
            self.collection.uri + 'references/',
            {'data': {'concepts': [concept2.uri]}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [{
                'added': True,
                'expression': concept2.uri,
                'message': f'The concept {concept2.mnemonic} is successfully added to '
                           f'collection {self.collection.name}'
            }]
        )

    @staticmethod
    def _private_source_with_empty_release():
        """A source only its org can view, whose latest release holds none of its concepts"""
        source = OrganizationSourceFactory(organization=OrganizationFactory(), public_access=ACCESS_TYPE_NONE)
        concept = ConceptFactory(parent=source, mnemonic='c1')
        release = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        return source, concept, release

    def _put_expressions(self, expressions):
        return self.client.put(
            self.collection.uri + 'references/',
            {'data': {'expressions': expressions}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

    def _resolved_to_nothing_item(self, expression, repo_version):
        return {
            'added': True,
            'resolved': False,
            'expression': expression,
            'repo_versions': [repo_version],
            'message': f'The reference is saved in collection {self.collection.name}, but it resolved to '
                       f'no concepts or mappings in {repo_version}'
        }

    def test_put_200_reference_to_a_repository_the_requester_cannot_view_answers_like_a_missing_one(self):
        source, concept, _ = self._private_source_with_empty_release()
        missing_expression = '/orgs/nobody/sources/nothing/concepts/c1/'

        response = self._put_expressions([concept.uri, missing_expression])

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            list(response.data),
            [
                self._resolved_to_nothing_item(concept.uri, source.uri),
                self._resolved_to_nothing_item(missing_expression, '/orgs/nobody/sources/nothing/')
            ]
        )

    def test_put_200_reference_to_a_private_repository_names_the_version_to_its_members(self):
        source, concept, release = self._private_source_with_empty_release()
        source.organization.members.add(self.user)

        response = self._put_expressions([concept.uri])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.data), [self._resolved_to_nothing_item(concept.uri, release.uri)])

    def test_put_200_reference_to_a_private_repository_names_the_version_to_staff(self):
        _, concept, release = self._private_source_with_empty_release()
        self.user.is_staff = True
        self.user.save()

        response = self._put_expressions([concept.uri])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.data), [self._resolved_to_nothing_item(concept.uri, release.uri)])

    def test_put_200_pinned_reference_naming_a_version_the_requester_cannot_view_answers_like_a_missing_one(self):
        source, old, v2 = self._source_with_concept_version_no_release_holds()
        Source.objects.filter(mnemonic=source.mnemonic, organization=source.organization).update(
            public_access=ACCESS_TYPE_NONE)
        expression = f'{v2.uri}concepts/c1/{old.version}/'
        missing_source_uri = '/orgs/nobody/sources/nothing/'
        missing_expression = f'{missing_source_uri}v2/concepts/c1/{old.version}/'

        with patch.object(CollectionReference, 'is_resource_version_repo_resolution_on', return_value=True):
            response = self._put_expressions([expression, missing_expression])

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            list(response.data),
            [
                {
                    'added': True,
                    'resolved': False,
                    'expression': _expression,
                    'repo_versions': [source_uri],
                    'message': f'The reference is saved in collection {self.collection.name}, but no version of '
                               f'{source_uri} holds resource version {old.version}'
                } for _expression, source_uri in ((expression, source.uri), (missing_expression, missing_source_uri))
            ]
        )

    def test_put_expression_with_cascade_to_concepts(self):
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source1, mnemonic='concept1')
        concept2 = ConceptFactory(parent=source1)
        concept3 = ConceptFactory(parent=source2)
        concept4 = ConceptFactory(parent=source2)

        MappingFactory(
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
        MappingFactory(
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
        self.assertEqual(len(response.data), 1)
        self.assertTrue(all(data['added'] for data in response.data))
        self.assertEqual(
            sorted([data['expression'] for data in response.data]),
            sorted([concept1.get_latest_version().uri])
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
        self.assertEqual(len(response.data), 1)
        self.assertTrue(all(data['added'] for data in response.data))
        self.assertEqual(
            sorted([data['expression'] for data in response.data]),
            sorted([concept4.get_latest_version().uri])
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

    def test_put_with_updated_resolve_reference_rules(self):  # pylint: disable=too-many-statements
        collection = UserCollectionFactory(mnemonic='coll1', user=self.user)
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()

        source1 = OrganizationSourceFactory(mnemonic='source1')
        source1_v1 = OrganizationSourceFactory(
            mnemonic='source1', organization=source1.organization, version='v1', released=True)
        source1_v2 = OrganizationSourceFactory(
            mnemonic='source1', organization=source1.organization, version='v2', released=True)

        concept1 = ConceptFactory(
            parent=source1, mnemonic='concept1', datatype='N/A', names=1, names__name="concept_1_name")
        user = concept1.created_by
        concept1_v1 = concept1.get_latest_version()
        concept1_v1.is_latest_version = True
        concept1_v1.version = 'v1'
        concept1_v1.save()
        Concept.create_new_version_for(
            concept1.clone(),
            {
                'datatype': 'N/A',
                'comment': 'Changed datatype to Numeric',
                'version': 'v2',
                'names': [{
                    'locale': 'en',
                    'name': 'English',
                    'locale_preferred': True
                }]
            },
            user
        )
        concept1_v2 = concept1.get_latest_version()
        concept1_v2.is_latest_version = True
        concept1_v2.version = 'v2'
        concept1_v2.save()
        Concept.create_new_version_for(
            concept1.clone(),
            {
                'datatype': 'String',
                'comment': 'Changed datatype to String',
                'version': 'v3',
                'names': [{
                    'locale': 'en',
                    'name': 'English',
                    'locale_preferred': True
                }]
            },
            user
        )
        concept1_v3 = concept1.get_latest_version()
        concept1_v3.is_latest_version = True
        concept1_v3.version = 'v3'
        concept1_v3.save()
        Concept.create_new_version_for(
            concept1.clone(),
            {
                'datatype': 'JSON',
                'comment': 'Changed datatype to JSON',
                'version': 'v4',
                'names': [{
                    'locale': 'en',
                    'name': 'English',
                    'locale_preferred': True
                }]
            },
            user
        )
        concept1_v4 = concept1.get_latest_version()
        concept1_v4.is_latest_version = True
        concept1_v4.version = 'v4'
        concept1_v4.save()

        self.assertEqual(Concept.objects.filter(mnemonic='concept1').count(), 5)

        source1.concepts.add(concept1_v2)
        source1_v1.concepts.add(concept1_v2)
        source1_v1.concepts.add(concept1_v3)
        source1_v2.concepts.add(concept1_v3)
        source1_v2.concepts.add(concept1_v4)

        response = self.client.put(
            collection.uri + 'references/',
            {
                'data': {'concepts': [source1_v1.uri + 'concepts/' + concept1_v2.mnemonic + '/']}
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
                    'expression': source1_v1.uri + 'concepts/' + concept1_v2.mnemonic + '/',
                    'message': 'The reference is successfully added to collection '
                }
            ]
        )
        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.concepts.first().uri, concept1_v3.uri)
        self.assertEqual(len(expansion.unresolved_repo_versions), 0)
        self.assertEqual(expansion.evaluated_source_versions.count(), 0)
        self.assertEqual(expansion.explicit_source_versions.count(), 1)
        self.assertEqual(expansion.explicit_source_versions.first().uri, source1_v1.uri)

        response = self.client.put(
            collection.uri + 'references/',
            {
                'data': {'concepts': [source1_v2.uri + 'concepts/' + concept1_v2.mnemonic + '/']}
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
                    'expression': source1_v2.uri + 'concepts/' + concept1_v2.mnemonic + '/',
                    'message': 'The reference is successfully added to collection '
                }
            ]
        )
        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(
            sorted(expansion.concepts.values_list('uri', flat=True)), sorted([concept1_v4.uri]))
        self.assertEqual(len(expansion.unresolved_repo_versions), 0)
        self.assertEqual(expansion.evaluated_source_versions.count(), 0)
        self.assertEqual(expansion.explicit_source_versions.count(), 2)
        self.assertEqual(
            sorted(expansion.explicit_source_versions.values_list('uri', flat=True)),
            sorted([source1_v2.uri, source1_v1.uri])
        )

        expansion.concepts.clear()
        expansion.explicit_source_versions.clear()
        CollectionReference.objects.filter(collection=collection).delete()

        response = self.client.put(
            collection.uri + 'references/',
            {
                'data': {
                    'concepts': [concept1_v3.uri]
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
                    'expression': concept1_v3.uri,
                    'message': 'Added the concept version to the collection.'
                               ' Future updates will not be added automatically.'
                }
            ]
        )
        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(
            sorted(expansion.concepts.values_list('uri', flat=True)), sorted([concept1_v3.uri]))
        self.assertEqual(len(expansion.unresolved_repo_versions), 0)
        self.assertEqual(expansion.evaluated_source_versions.count(), 0)
        self.assertEqual(expansion.explicit_source_versions.count(), 1)
        self.assertEqual(
            sorted(expansion.explicit_source_versions.values_list('uri', flat=True)),
            sorted([source1_v2.uri])
        )

        expansion.concepts.clear()
        expansion.explicit_source_versions.clear()
        CollectionReference.objects.filter(collection=collection).delete()

        response = self.client.put(
            collection.uri + 'references/',
            {
                'data': {
                    'concepts': [concept1.uri]
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
                    'expression': concept1.uri,
                    'message': f'The concept concept1 is successfully added to collection {collection.name}'
                }
            ]
        )
        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(
            sorted(expansion.concepts.values_list('uri', flat=True)), sorted([concept1_v4.uri]))
        self.assertEqual(len(expansion.unresolved_repo_versions), 0)
        self.assertEqual(expansion.evaluated_source_versions.count(), 1)
        self.assertEqual(expansion.explicit_source_versions.count(), 0)
        self.assertEqual(
            sorted(expansion.evaluated_source_versions.values_list('uri', flat=True)),
            sorted([source1_v2.uri])
        )


class CollectionReferencesPreviewTest(OCLAPITestCase):
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

    def test_post_200_specific_expression(self):  # pylint: disable=too-many-statements
        response = self.client.post(
            self.collection.uri + 'references/preview/',
            {'data': {'concepts': [self.concept.uri]}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {
                    'reference': self.concept.uri,
                    'concepts': [ConceptListSerializer(self.concept).data],
                    'mappings': [],
                    'concepts_count': 1,
                    'mappings_count': 0,
                    'exclude': False
                }
            ]
        )

        concept2 = ConceptFactory()
        response = self.client.post(
            self.collection.uri + 'references/preview/',
            {'data': {'concepts': [concept2.uri]}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {
                    'reference': concept2.uri,
                    'concepts': [ConceptListSerializer(concept2).data],
                    'mappings': [],
                    'concepts_count': 1,
                    'mappings_count': 0,
                    'exclude': False
                }
            ]
        )

        mapping = MappingFactory(from_concept=concept2, to_concept=self.concept, parent=self.concept.parent)

        response = self.client.post(
            self.collection.uri + 'references/preview/',
            {'data': {'mappings': [mapping.uri]}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {
                    'reference': mapping.uri,
                    'concepts': [],
                    'mappings': [MappingListSerializer(mapping).data],
                    'concepts_count': 0,
                    'mappings_count': 1,
                    'exclude': False
                }
            ]
        )

        concept3 = ConceptFactory()
        latest_version = concept3.get_latest_version()
        mapping = MappingFactory(from_concept=concept3, parent=concept3.parent)

        response = self.client.post(
            self.collection.uri + 'references/preview/?cascade=sourcemappings',
            {'data': {'concepts': [latest_version.uri]}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            sorted(response.data, key=lambda ref: ref['reference']),
            [
                {
                    'reference': latest_version.uri,
                    'concepts': [ConceptListSerializer(latest_version).data],
                    'mappings': [MappingListSerializer(mapping).data],
                    'concepts_count': 1,
                    'mappings_count': 1,
                    'exclude': False
                }
            ]
        )

        concept4 = ConceptFactory()
        latest_version = concept4.get_latest_version()
        mapping2 = MappingFactory(from_concept=concept4, parent=concept4.parent)

        response = self.client.post(
            self.collection.uri + 'references/preview/',
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
        self.assertEqual(
            response.data,
            [
                {
                    'reference': latest_version.uri,
                    'concepts': [ConceptListSerializer(latest_version).data],
                    'mappings': [MappingListSerializer(mapping2).data],
                    'concepts_count': 1,
                    'mappings_count': 1,
                    'exclude': False
                }
            ]
        )

        response = self.client.post(
            self.collection.uri + 'references/preview/',
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
        self.assertEqual(
            response.data,
            [
                {
                    'reference': latest_version.uri,
                    'concepts': [ConceptListSerializer(latest_version).data],
                    'mappings': [MappingListSerializer(mapping2).data],
                    'concepts_count': 1,
                    'mappings_count': 1,
                    'exclude': True
                }
            ]
        )

    def test_post_expression_with_cascade_to_concepts(self):
        def normalized(refs):  # concept order within a reference is not guaranteed
            return [{**ref, 'concepts': sorted(ref['concepts'], key=lambda c: c['uuid'])}
                    for ref in sorted(refs, key=lambda ref: ref['reference'])]

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

        response = self.client.post(
            self.collection.uri + 'references/preview/?cascade=sourceToConcepts',
            {
                'data': {
                    'concepts': [concept1.get_latest_version().uri]
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            normalized(response.data),
            normalized([
                {
                    'reference': concept1.get_latest_version().uri,
                    'concepts': ConceptListSerializer([concept1.get_latest_version(), concept2], many=True).data,
                    'concepts_count': 2,
                    'mappings': [MappingListSerializer(mapping1).data],
                    'mappings_count': 1,
                    'exclude': False
                }
            ])
        )

        response = self.client.post(
            self.collection.uri + 'references/preview/?cascade=sourceToConcepts',
            {
                'data': {
                    'concepts': [concept4.get_latest_version().uri]
                }
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            normalized(response.data),
            normalized([
                {
                    'reference': concept4.get_latest_version().uri,
                    'concepts': ConceptListSerializer([concept3, concept4.get_latest_version()], many=True).data,
                    'concepts_count': 2,
                    'mappings': [MappingListSerializer(mapping4).data],
                    'mappings_count': 1,
                    'exclude': False
                }
            ])
        )

        random_concept = ConceptFactory()

        response = self.client.post(
            self.collection.uri + 'references/preview/?transformReferences=extensional',
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
                 'reference': random_concept.parent.uri,
                 'concepts': [],
                 'mappings': [],
                 'concepts_count': 0,
                 'mappings_count': 0,
                 'exclude': False
             }]
        )

        expr = random_concept.parent.uri + 'v1/concepts/excludeWildcard=true&excludeFuzzy=true&includeSearchMETA=true'
        response = self.client.post(
            self.collection.uri + 'references/preview/?cascade=sourcemappings',
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
                'reference': expr,
                'concepts': [],
                'mappings': [],
                'concepts_count': 0,
                'mappings_count': 0,
                'exclude': False
            }]
        )

    def test_post_expression_transform_to_latest_version(self):
        concept2 = ConceptFactory()
        concept2_latest_version = concept2.get_latest_version()
        concept3 = ConceptFactory()
        concept3_latest_version = concept3.get_latest_version()

        self.assertNotEqual(concept2.uri, concept2_latest_version.uri)
        self.assertNotEqual(concept3.uri, concept3_latest_version.uri)

        response = self.client.post(
            self.collection.uri + 'references/preview/?transformReferences=resourceVersions',
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
            [{
                'reference': concept2.get_latest_version().uri,
                'concepts': [ConceptListSerializer(concept2.get_latest_version()).data],
                'mappings': [],
                'concepts_count': 1,
                'mappings_count': 0,
                'exclude': False
            }]
        )

    def test_post_expression_transform_to_extensional(self):
        concept2 = ConceptFactory()
        concept2_latest_version = concept2.get_latest_version()
        concept3 = ConceptFactory()
        concept3_latest_version = concept3.get_latest_version()

        self.assertNotEqual(concept2.uri, concept2_latest_version.uri)
        self.assertNotEqual(concept3.uri, concept3_latest_version.uri)

        response = self.client.post(
            self.collection.uri + 'references/preview/?transformReferences=extensional',
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
            [{
                'reference': concept2.uri,
                'concepts': [ConceptListSerializer(concept2).data],
                'mappings': [],
                'concepts_count': 1,
                'mappings_count': 0,
                'exclude': False
            }]
        )

    def test_post_expression_cascade_and_transform_to_resource_versions_generate_multiple_references(self):
        source = OrganizationSourceFactory()
        concept2 = ConceptFactory(parent=source, mnemonic='concept2')
        concept2_latest_version = concept2.get_latest_version()
        concept3 = ConceptFactory(parent=source, mnemonic='concept3')
        concept3_latest_version = concept3.get_latest_version()
        mapping = MappingFactory(from_concept=concept2, to_concept=concept3, parent=source)
        mapping_latest_version = mapping.get_latest_version()

        self.assertNotEqual(concept2.uri, concept2_latest_version.uri)
        self.assertNotEqual(concept3.uri, concept3_latest_version.uri)
        self.assertNotEqual(mapping.uri, mapping_latest_version.uri)
        response = self.client.post(
            self.collection.uri + 'references/preview/?transformReferences=resourceVersions',
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
        results = sorted(response.data, key=lambda ref: ref['reference'])
        self.assertEqual(
            results[0],
            {
                'reference': concept2_latest_version.uri,
                'concepts': [ConceptListSerializer(concept2_latest_version).data],
                'mappings': [],
                'concepts_count': 1,
                'mappings_count': 0,
                'exclude': False
            }
        )
        self.assertEqual(
            results[1],
            {
                'reference': concept3_latest_version.uri,
                'concepts': [ConceptListSerializer(concept3_latest_version).data],
                'mappings': [],
                'concepts_count': 1,
                'mappings_count': 0,
                'exclude': False
            }
        )
        self.assertEqual(
            results[2],
            {
                'reference': mapping_latest_version.uri,
                'concepts': [],
                'mappings': [MappingListSerializer(mapping_latest_version).data],
                'concepts_count': 0,
                'mappings_count': 1,
                'exclude': False
            }
        )

        response = self.client.post(
            self.collection.uri + 'references/preview/',
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
        self.assertEqual(
            response.data,
            [{
                'reference': concept3.uri,
                'concepts': [ConceptListSerializer(concept3).data],
                'mappings': [],
                'concepts_count': 1,
                'mappings_count': 0,
                'exclude': True
            }]
        )

    def test_post_expression_cascade_and_transform_to_extensional_generate_multiple_references(self):
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
        response = self.client.post(
            self.collection.uri + 'references/preview/?transformReferences=extensional',
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
        # keyed by reference: sorting the URIs as strings depends on the factory sequence (concept1000 < concept999)
        results = {ref['reference']: ref for ref in response.data}
        self.assertEqual(
            results[concept2.uri],
            {
                'reference': concept2.uri,
                'concepts': [ConceptListSerializer(concept2).data],
                'mappings': [],
                'concepts_count': 1,
                'mappings_count': 0,
                'exclude': False
            }
        )
        self.assertEqual(
            results[concept3.uri],
            {
                'reference': concept3.uri,
                'concepts': [ConceptListSerializer(concept3).data],
                'mappings': [],
                'concepts_count': 1,
                'mappings_count': 0,
                'exclude': False
            }
        )
        self.assertEqual(
            results[mapping.uri],
            {
                'reference': mapping.uri,
                'concepts': [],
                'mappings': [MappingListSerializer(mapping).data],
                'concepts_count': 0,
                'mappings_count': 1,
                'exclude': False
            }
        )

    def test_post_bad_expressions(self):
        expression = {
           "data": {
                "url": [
                    "http://worldhealthorganization.github.io/ddcc/ValueSet/DDCC-QR-Format-ValueSet"
                ]
            }
        }
        response = self.client.post(
            self.collection.uri + 'references/preview/',
            expression,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

        response = self.client.post(
            self.collection.uri + 'references/preview/',
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

    def test_put_expansion_url(self):
        own_expansion = ExpansionFactory(collection_version=self.collection_v1)
        other_expansion = ExpansionFactory(collection_version=self.collection)

        for expansion_url, status_code in [
                ('https://other.example/x/', 400), (other_expansion.uri, 400), (own_expansion.uri, 200)]:
            with self.subTest(expansion_url=expansion_url):
                response = self.client.put(
                    self.collection_v1.uri,
                    {'expansion_url': expansion_url},
                    HTTP_AUTHORIZATION='Token ' + self.token,
                    format='json'
                )

                self.assertEqual(response.status_code, status_code)

        self.collection_v1.refresh_from_db()
        self.assertEqual(self.collection_v1.expansion_uri, own_expansion.uri)

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
        owner = self.collection.parent.mnemonic
        self.assertEqual(delete_s3_objects_mock.apply_async.call_count, 2)
        for v1_prefix in (f'users/{owner}/collections/coll/v1.', f'users/{owner}/{owner}_coll_v1.'):
            delete_s3_objects_mock.apply_async.assert_any_call(
                (v1_prefix, get_export_key_pattern(v1_prefix)), queue='default', permanent=False)


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


COLLECTION_EXPORT_KEY_PATTERN = r'users/username/collections/coll/v1\.(\d{4})-?(\d{2})-?(\d{2})_?(\d{6})\.zip'


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
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        s3_exists_mock.assert_any_call(f"users/username/collections/coll/HEAD.{self.HEAD_updated_at}.zip")

    @patch('core.services.storages.cloud.aws.S3.has_path')
    def test_get_204_for_version(self, s3_has_path_mock):
        s3_has_path_mock.return_value = False

        response = self.client.get(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        s3_has_path_mock.assert_any_call(
            "users/username/collections/coll/v1.", key_pattern=COLLECTION_EXPORT_KEY_PATTERN)

    @patch('core.services.storages.cloud.aws.S3.url_for')
    @patch('core.services.storages.cloud.aws.S3.get_last_key_from_path')
    @patch('core.services.storages.cloud.aws.S3.has_path')
    def test_get_302_version(self, s3_has_path_mock, s3_get_last_key_from_path_mock, s3_url_for_mock):
        s3_has_path_mock.return_value = True
        s3_url_for_mock.return_value = 'https://signed.example/coll-v1.zip'
        s3_get_last_key_from_path_mock.return_value = f'users/username/collections/coll/v1.{self.v1_updated_at}.zip'

        response = self.client.get(
            self.collection_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://signed.example/coll-v1.zip')
        s3_has_path_mock.assert_any_call(
            "users/username/collections/coll/v1.", key_pattern=COLLECTION_EXPORT_KEY_PATTERN)
        s3_get_last_key_from_path_mock.assert_called_once_with(
            "users/username/collections/coll/v1.", key_pattern=COLLECTION_EXPORT_KEY_PATTERN)
        s3_url_for_mock.assert_called_once_with(
            f'users/username/collections/coll/v1.{self.v1_updated_at}.zip',
            filename=f'users_username_collections_coll_v1_{self.v1_updated_at}.zip'
        )

    @patch('core.services.storages.cloud.aws.S3.url_for')
    @patch('core.services.storages.cloud.aws.S3.exists')
    def test_get_302_head(self, s3_exists_mock, s3_url_for_mock):
        s3_exists_mock.return_value = True
        s3_url_for_mock.return_value = 'https://signed.example/coll-head.zip'

        response = self.client.get(
            self.collection.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://signed.example/coll-head.zip')
        s3_exists_mock.assert_any_call(f"users/username/collections/coll/HEAD.{self.HEAD_updated_at}.zip")
        s3_url_for_mock.assert_called_once_with(
            f"users/username/collections/coll/HEAD.{self.HEAD_updated_at}.zip",
            filename=f"users_username_collections_coll_HEAD_{self.HEAD_updated_at}.zip"
        )

    @patch('core.services.storages.cloud.aws.S3.url_for')
    @patch('core.services.storages.cloud.aws.S3.exists')
    def test_get_500_head_when_signed_url_generation_fails(self, s3_exists_mock, s3_url_for_mock):
        s3_exists_mock.return_value = True
        s3_url_for_mock.return_value = None

        response = self.client.get(
            self.collection.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data, {'detail': 'Export exists but could not generate a download URL.'})
        s3_exists_mock.assert_any_call(f"users/username/collections/coll/HEAD.{self.HEAD_updated_at}.zip")
        s3_url_for_mock.assert_called_once_with(
            f"users/username/collections/coll/HEAD.{self.HEAD_updated_at}.zip",
            filename=f"users_username_collections_coll_HEAD_{self.HEAD_updated_at}.zip"
        )

    def test_get_405(self):
        random_user = UserProfileFactory()
        response = self.client.get(
            f'/users/{self.collection.parent.mnemonic}/collections/{self.collection.mnemonic}/{"HEAD"}/export/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    def test_post_405(self):
        random_user = UserProfileFactory()
        response = self.client.post(
            f'/users/{self.collection.parent.mnemonic}/collections/{self.collection.mnemonic}/{"HEAD"}/export/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    @patch('core.services.storages.cloud.aws.S3.exists')
    def test_post_303_head(self, s3_exists_mock):
        s3_exists_mock.return_value = True
        response = self.client.post(
            self.collection.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['URL'], self.collection.uri + 'export/')
        s3_exists_mock.assert_any_call(f"users/username/collections/coll/HEAD.{self.HEAD_updated_at}.zip")

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
        s3_has_path_mock.assert_any_call(
            "users/username/collections/coll/v1.", key_pattern=COLLECTION_EXPORT_KEY_PATTERN)

    @patch('core.collections.views.export_collection')
    @patch('core.services.storages.cloud.aws.S3.exists')
    def test_post_202_head(self, s3_exists_mock, export_collection_mock):
        s3_exists_mock.return_value = False
        export_collection_mock.__name__ = 'export_collection'
        response = self.client.post(
            self.collection.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        s3_exists_mock.assert_any_call(f"users/username/collections/coll/HEAD.{self.HEAD_updated_at}.zip")
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
        s3_has_path_mock.assert_any_call(
            "users/username/collections/coll/v1.", key_pattern=COLLECTION_EXPORT_KEY_PATTERN)
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
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        s3_exists_mock.assert_any_call(f"users/username/collections/coll/HEAD.{self.HEAD_updated_at}.zip")
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
        s3_has_path_mock.assert_any_call(
            "users/username/collections/coll/v1.", key_pattern=COLLECTION_EXPORT_KEY_PATTERN)
        export_collection_mock.apply_async.assert_called_once_with(
            (self.collection_v1.id,), task_id=ANY, queue='default')
        self.assertEqual(
            Task.objects.filter(created_by=self.user, state='PENDING', name='export_collection').count(), 0)

    def test_delete_405_head(self):
        random_user = UserProfileFactory()
        response = self.client.delete(
            self.collection.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    @patch('core.collections.models.Collection.version_export_path', new_callable=PropertyMock)
    @patch('core.collections.models.Collection.has_export')
    @patch('core.services.storages.cloud.aws.S3.delete_objects')
    def test_delete_204_head(self, s3_delete_objects_mock, has_export_mock, export_path_mock):
        has_export_mock.return_value = True
        export_path_mock.return_value = 'head/export/path'
        response = self.client.delete(
            self.collection.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        s3_delete_objects_mock.assert_called()


def signed_url_for(_, key, filename=None):  # pylint: disable=unused-argument
    return f'https://signed.example/{key}'


@mock_aws
class CollectionVersionExportSiblingVersionsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        boto3.resource('s3', region_name='us-east-1').create_bucket(Bucket='oclapi2-dev')
        self.owner = UserProfileFactory(username='owner')
        self.owner_token = self.owner.get_token()
        self.head = UserCollectionFactory(user=self.owner, mnemonic='coll')
        self.v1_8 = UserCollectionFactory(user=self.owner, mnemonic='coll', version='1.8')
        self.v1_8_24 = UserCollectionFactory(user=self.owner, mnemonic='coll', version='1.8.24')
        self.v1_8_24_1 = UserCollectionFactory(user=self.owner, mnemonic='coll', version='1.8.24.1')

    def upload(self, version, timestamp):
        key = version.get_version_export_path(suffix=f'{timestamp}.zip')
        S3().upload(key, io.BytesIO(b'content'))
        return key

    def get_export(self, version):
        return self.client.get(version.uri + 'export/', HTTP_AUTHORIZATION='Token ' + self.owner_token, format='json')

    @patch('core.services.storages.cloud.aws.S3.url_for', signed_url_for)
    def test_get_serves_own_export_not_sibling(self):
        own_key = self.upload(self.v1_8_24, '2026-10-01_090000')
        self.upload(self.v1_8_24_1, '2026-10-02_090000')

        response = self.get_export(self.v1_8_24)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'https://signed.example/{own_key}')

    @patch('core.services.storages.cloud.aws.S3.url_for', signed_url_for)
    def test_get_204_when_only_siblings_have_exports(self):
        self.upload(self.v1_8_24, '2026-10-01_090000')
        self.upload(self.v1_8_24_1, '2026-10-02_090000')

        self.assertEqual(self.get_export(self.v1_8).status_code, 204)

    @patch('core.common.models.delete_s3_objects')
    def test_delete_version_leaves_sibling_exports(self, delete_s3_objects_mock):
        delete_s3_objects_mock.apply_async.side_effect = lambda args, **kwargs: delete_s3_objects(*args)
        self.upload(self.v1_8, '2026-10-01_090000')
        key_1_8_24 = self.upload(self.v1_8_24, '2026-10-01_090000')
        key_1_8_24_1 = self.upload(self.v1_8_24_1, '2026-10-02_090000')

        response = self.client.delete(
            self.v1_8.uri, HTTP_AUTHORIZATION='Token ' + self.owner_token, format='json')

        self.assertEqual(response.status_code, 204)
        remaining = sorted(
            obj.key for obj in boto3.resource('s3', region_name='us-east-1').Bucket('oclapi2-dev').objects.all())
        self.assertEqual(remaining, sorted([key_1_8_24, key_1_8_24_1]))


class CollectionVersionExternalExportViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.admin_token = self.admin.get_token()
        self.user = UserProfileFactory(username='username')
        self.token = self.user.get_token()
        self.collection_v1 = UserCollectionFactory(version='v1', mnemonic='coll', user=self.user)

    def test_get_404_unknown_key(self):
        response = self.client.get(
            self.collection_v1.uri + 'export/openmrs23-sql/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.services.storages.cloud.aws.S3.remove')
    @patch('core.services.storages.cloud.aws.S3.upload')
    def test_post_201_create_then_get_302_and_delete_204(self, s3_upload_mock, s3_remove_mock):
        uploaded_file = SimpleUploadedFile('openmrs23.sql.zip', b'content', content_type='application/zip')

        response = self.client.post(
            self.collection_v1.uri + 'export/openmrs23-sql/',
            {'file': uploaded_file},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['key'], 'openmrs23-sql')
        self.assertEqual(response.data['url'], self.collection_v1.uri + 'export/openmrs23-sql/')
        s3_upload_mock.assert_called_once()

        from core.repos.models import RepoExternalExport
        instance = RepoExternalExport.objects.get(key='openmrs23-sql')

        with patch('core.services.storages.cloud.aws.S3.url_for') as s3_url_for_mock:
            s3_url_for_mock.return_value = 'https://signed.example/openmrs23.sql.zip'
            response = self.client.get(
                self.collection_v1.uri + 'export/openmrs23-sql/',
                HTTP_AUTHORIZATION='Token ' + self.token,
                format='json'
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://signed.example/openmrs23.sql.zip')

        response = self.client.delete(
            self.collection_v1.uri + 'export/openmrs23-sql/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        s3_remove_mock.assert_called_once_with(instance.file_path)
        self.assertFalse(RepoExternalExport.objects.filter(key='openmrs23-sql').exists())

    def test_post_400_no_file(self):
        response = self.client.post(
            self.collection_v1.uri + 'export/openmrs23-sql/',
            {},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 400)

    def test_post_403_non_admin(self):
        random_user = UserProfileFactory()
        uploaded_file = SimpleUploadedFile('openmrs23.sql.zip', b'content', content_type='application/zip')

        response = self.client.post(
            self.collection_v1.uri + 'export/openmrs23-sql/',
            {'file': uploaded_file},
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
        )

        self.assertEqual(response.status_code, 403)

    @patch('core.services.storages.cloud.aws.S3.upload')
    def test_export_serializer_includes_external_exports(self, s3_upload_mock):  # pylint: disable=unused-argument
        uploaded_file = SimpleUploadedFile('openmrs23.sql.zip', b'content', content_type='application/zip')
        self.client.post(
            self.collection_v1.uri + 'export/openmrs23-sql/',
            {'file': uploaded_file},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.collection_v1.refresh_from_db()
        external_exports = CollectionVersionExportSerializer(self.collection_v1).data['external_exports']

        self.assertEqual(len(external_exports), 1)
        self.assertEqual(external_exports[0]['key'], 'openmrs23-sql')
        self.assertEqual(external_exports[0]['url'], self.collection_v1.uri + 'export/openmrs23-sql/')

    def test_delete_404_unknown_key(self):
        response = self.client.delete(
            self.collection_v1.uri + 'export/openmrs23-sql/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.services.storages.cloud.aws.S3.remove')
    @patch('core.services.storages.cloud.aws.S3.upload')
    def test_delete_403_non_admin(self, s3_upload_mock, s3_remove_mock):  # pylint: disable=unused-argument
        uploaded_file = SimpleUploadedFile('openmrs23.sql.zip', b'content', content_type='application/zip')
        self.client.post(
            self.collection_v1.uri + 'export/openmrs23-sql/',
            {'file': uploaded_file},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        random_user = UserProfileFactory()
        response = self.client.delete(
            self.collection_v1.uri + 'export/openmrs23-sql/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)
        s3_remove_mock.assert_not_called()


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

    def test_post_201_released_as_string(self):
        response = self.client.post(
            self.collection.uri + 'versions/',
            {
                'id': 'v1',
                'description': 'version1',
                'released': 'true'
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.collection.versions.count(), 2)
        self.assertIs(self.collection.versions.get(version='v1').released, True)

    def test_post_400_released_invalid_string(self):
        response = self.client.post(
            self.collection.uri + 'versions/',
            {
                'id': 'v1',
                'description': 'version1',
                'released': 'yeah'
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data, {'released': [ErrorDetail(string='Must be a valid boolean.', code='invalid')]})
        self.assertEqual(self.collection.versions.count(), 1)

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

    def post_version(self, version, **payload):
        return self.client.post(
            self.collection.uri + 'versions/', {'id': version, 'description': 'version', **payload},
            HTTP_AUTHORIZATION='Token ' + self.token, format='json')

    def test_post_201_expansion_choice(self):
        seed_from_head = Collection.seed_from_head_expansion
        cases = [
            (True, None, None, 'lock', True), (True, None, True, 'lock', True), (True, None, False, None, False),
            (True, 'lock', None, 'lock', True), (True, 'latest', None, 'latest', True),
            (True, 'none', None, None, False), (False, None, None, None, True),
            (False, None, True, None, True), (False, None, False, None, False),
            (False, 'lock', None, None, True), (False, 'latest', None, None, True),
            (False, 'none', None, None, False),
        ]
        for index, (is_locked, choice, autoexpand, built, expands) in enumerate(cases):
            with self.subTest(is_locked=is_locked, choice=choice, autoexpand=autoexpand), \
                    patch.object(CollectionReference, 'is_expansion_locked_repo_version_on', return_value=is_locked), \
                    patch.object(
                        Collection, 'seed_from_head_expansion', autospec=True, side_effect=seed_from_head
                    ) as seed_mock:
                payload = {'expansion': choice} if choice else {}
                if autoexpand is not None:
                    payload['autoexpand'] = autoexpand
                response = self.post_version(f'v{index}', **payload)

                self.assertEqual(response.status_code, 201)
                version = self.collection.versions.get(version=f'v{index}')
                if built:
                    seed_mock.assert_called_once_with(version, lock=built == 'lock')
                else:
                    seed_mock.assert_not_called()
                self.assertEqual(version.autoexpand, expands)
                self.assertEqual(version.references.count(), 1)
                if expands:
                    self.assertEqual(version.expansions.count(), 1)
                    self.assertEqual(version.expansion.concepts.count(), 1)
                else:
                    self.assertEqual(version.expansions.count(), 0)
                    self.assertIsNone(version.expansion_uri)

    def test_post_201_defaults_to_latest_without_head_expansion(self):
        Collection.objects.filter(id=self.collection.id).update(expansion_uri=None)

        with patch.object(Collection, 'seed_from_head_expansion', autospec=True, return_value=True) as seed_mock:
            response = self.post_version('v1')

        self.assertEqual(response.status_code, 201)
        seed_mock.assert_called_once_with(self.collection.versions.get(version='v1'), lock=False)

    def test_post_400_expansion_choice_invalid(self):
        response = self.client.post(
            self.collection.uri + 'versions/', {'id': 'v1', 'description': 'version1', 'expansion': 'rebuild'},
            HTTP_AUTHORIZATION='Token ' + self.token, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('expansion', response.data)
        self.assertEqual(self.collection.versions.count(), 1)

    def test_post_201_uses_head_name_when_older_version_exists(self):
        # Regression for the same class of bug fixed on SourceVersionListView.create(): resolving
        # "head_object" via get_queryset().first() (ordered by -created_at, no version filter) would
        # return the most recently created *version* row rather than HEAD whenever an older version
        # already exists (HEAD is always created first, so it always has the oldest created_at).
        # CollectionVersionListView already resolves via get_queryset().first().head, which should
        # correctly fall back to the true HEAD object regardless of row ordering.
        UserCollectionFactory(
            mnemonic=self.collection.mnemonic, user=self.user, version='v0', name='stale-version-name'
        )
        self.collection.name = 'updated-head-name'
        self.collection.save()

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
        self.assertEqual(response.data['name'], 'updated-head-name')

        new_version = self.collection.versions.get(version='v1')
        self.assertEqual(new_version.name, 'updated-head-name')

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

    def test_post_201_ignores_expansion_uri(self):
        other_expansion = ExpansionFactory(collection_version=UserCollectionFactory())

        for version, expansion_uri, autoexpand in [
                ('v1', 'https://other.example/x/', True), ('v2', other_expansion.uri, False)]:
            with self.subTest(expansion_uri=expansion_uri):
                response = self.client.post(
                    self.collection.uri + 'versions/',
                    {'id': version, 'expansion_uri': expansion_uri, 'autoexpand': autoexpand},
                    HTTP_AUTHORIZATION='Token ' + self.token,
                    format='json'
                )

                self.assertEqual(response.status_code, 201)
                new_version = self.collection.versions.get(version=version)
                own_expansion = new_version.expansions.first()
                self.assertEqual(new_version.expansion_uri, own_expansion.uri if autoexpand else None)


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
        expected_mappings = MappingVersionExportSerializer([mapping.get_latest_version()], many=True).data

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
        uuids = [int(item['uuid']) for item in response.data]
        self.assertEqual(uuids, sorted(uuids, reverse=True))
        self.assertEqual(response['num_found'], '3')

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
        uuids = [int(item['uuid']) for item in response.data]
        self.assertEqual(uuids, sorted(uuids, reverse=True))
        self.assertEqual(response['num_found'], '3')

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

    def test_retired_mappings_only_with_include_retired(self):
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
        retired = MappingFactory(from_concept=self.concept, parent=self.source, retired=True)
        self.expansion.mappings.add(self.mapping, retired)
        url = self.collection.url + f'concepts/{self.concept.mnemonic}/mappings/?brief=true'

        for query, expected in (('', [self.mapping]), ('&includeRetired=false', [self.mapping]),
                                ('&includeRetired=true', [self.mapping, retired])):
            response = self.client.get(url + query)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(sorted(data['url'] for data in response.data), sorted(m.url for m in expected), query)

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
            self.collection.url + 'references/' + str(self.reference.id + 1) + '/')  # not one of its references

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


def valueset_release(valueset, version):
    """A release of the value set whose expansion holds one concept"""
    release = OrganizationCollectionFactory(
        organization=valueset.organization, mnemonic=valueset.mnemonic, version=version, released=True)
    expansion = ExpansionFactory(collection_version=release)
    release.expansion_uri = expansion.uri
    release.save()
    concept = ConceptFactory().get_latest_version()
    expansion.concepts.add(concept)
    return release, concept


def lock_valueset(collection, expansion, release):
    """The expansion locks the value set at the release, and the collection references the value set"""
    expansion.evaluated_collection_versions.add(release)
    valueset = drop_version(release.uri)
    reference = CollectionReference(
        expression=valueset, collection=collection, valueset=[valueset], reference_type='concepts')
    reference.clean()
    reference.save()


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

    def test_get_summary_counts_retired_members_apart_from_active_ones(self):
        source = OrganizationSourceFactory()
        active = ConceptFactory(parent=source).get_latest_version()
        retired = ConceptFactory(parent=source, retired=True).get_latest_version()
        inactive = ConceptFactory(parent=source).get_latest_version()
        Concept.objects.filter(id=inactive.id).update(is_active=False)
        self.expansion.concepts.add(active, retired, inactive)
        self.expansion.mappings.add(
            MappingFactory(parent=source).get_latest_version(),
            MappingFactory(parent=source, retired=True).get_latest_version())

        response = self.client.get(self.expansion.url + '?includeSummary=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['summary'],
            {'active_concepts': 1, 'active_mappings': 1, 'retired_concepts': 1, 'retired_mappings': 1})


    def test_post_re_evaluate_keeps_resource_version_held_by_older_release(self):
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, mnemonic='c1')
        old = concept.get_latest_version()
        ConceptFactory(parent=source, mnemonic='c1', versioned_object=concept, version='new')
        old.sources.remove(source)
        v1 = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1', released=True)
        OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v2', released=True)
        old.sources.add(v1)
        reference = CollectionReference(
            expression=old.uri, collection=self.collection, system=source.uri, code='c1',
            resource_version=old.version)
        reference.evaluate()
        reference.save()
        self.expansion_default.concepts.add(old)

        response = self.client.post(
            self.expansion_default.url + 're-evaluate/',
            HTTP_AUTHORIZATION='Token ' + self.collection.created_by.get_token(),
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(list(self.expansion_default.concepts.all()), [old])
        self.assertEqual(list(self.expansion_default.explicit_source_versions.all()), [v1])

    @patch('core.common.tasks.export_collection')
    def test_post_re_evaluate_keeps_valueset_lock_of_a_version_built_with_lock(self, _):
        valueset = OrganizationCollectionFactory()
        locked, locked_concept = valueset_release(valueset, 'v1')
        lock_valueset(self.collection, self.expansion_default, locked)
        token = self.collection.created_by.get_token()
        response = self.client.post(
            self.collection.url + 'versions/', {'id': 'r1', 'description': 'r1', 'expansion': 'lock'},
            HTTP_AUTHORIZATION='Token ' + token, format='json')
        self.assertEqual(response.status_code, 201)
        version = self.collection.versions.get(version='r1')
        self.assertEqual(list(version.expansion.concepts.all()), [locked_concept])
        valueset_release(valueset, 'v2')

        response = self.client.post(version.expansion.url + 're-evaluate/', HTTP_AUTHORIZATION='Token ' + token)

        self.assertEqual(response.status_code, 204)
        expansion = version.expansion
        self.assertEqual(list(expansion.concepts.all()), [locked_concept])
        self.assertEqual(list(expansion.evaluated_collection_versions.all()), [locked])

    @patch('core.common.tasks.export_collection')
    def test_get_shows_how_version_create_built_the_expansion(self, _):
        token = self.collection.created_by.get_token()
        for version, choice in (('r1', 'lock'), ('r2', 'latest')):
            response = self.client.post(
                self.collection.url + 'versions/', {'id': version, 'description': version, 'expansion': choice},
                HTTP_AUTHORIZATION='Token ' + token, format='json')
            self.assertEqual(response.status_code, 201)

        for version, choice in (('r1', 'lock'), ('r2', 'latest')):
            expansion = self.collection.versions.get(version=version).expansion
            response = self.client.get(expansion.url, HTTP_AUTHORIZATION='Token ' + token)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['built_with'], choice)
            response = self.client.get(
                expansion.collection_version.uri + 'expansions/', HTTP_AUTHORIZATION='Token ' + token)
            self.assertEqual([item['built_with'] for item in response.data], [choice])

        # an expansion made on its own records no choice, and a client can't set one: it changes what a rebuild keeps
        response = self.client.get(self.expansion.url, HTTP_AUTHORIZATION='Token ' + token)
        self.assertIsNone(response.data['built_with'])
        response = self.client.post(
            self.collection.url + 'HEAD/expansions/',
            {'mnemonic': 'e-built-with', 'parameters': {'activeOnly': False}, 'built_with': 'lock'},
            HTTP_AUTHORIZATION='Token ' + token, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data['built_with'])
        self.assertIsNone(self.collection.expansions.get(mnemonic='e-built-with').built_with)


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

    def test_post_resolves_valuesets_under_its_own_lock_not_the_default_expansions(self):
        valueset = OrganizationCollectionFactory()
        default_lock, _ = valueset_release(valueset, 'v1')
        _, latest_concept = valueset_release(valueset, 'v2')
        default = ExpansionFactory(collection_version=self.collection, mnemonic='autoexpand-HEAD')
        self.collection.expansion_uri = default.uri
        self.collection.save()
        lock_valueset(self.collection, default, default_lock)

        response = self.client.post(
            self.collection.url + 'HEAD/expansions/', {'mnemonic': 'e1', 'parameters': {'activeOnly': False}},
            HTTP_AUTHORIZATION='Token ' + self.token, format='json')

        self.assertEqual(response.status_code, 201)
        manual = self.collection.expansions.get(mnemonic='e1')
        self.assertEqual(list(manual.concepts.all()), [latest_concept])
        self.assertEqual(list(default.evaluated_collection_versions.all()), [default_lock])

    def test_post_unsupported_parameter_400(self):
        response = self.client.post(
            self.collection.url + 'HEAD/expansions/',
            {'mnemonic': 'e1', 'parameters': {'activeOnly': True, 'systemVersion': 'https://a.org|v1', 'foo': 1}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(sorted(response.data['parameters']), ['foo', 'systemVersion'])
        self.assertIn('Not a supported expansion parameter.', response.data['parameters']['foo'][0])
        self.assertEqual(self.collection.expansions.count(), 0)

    def test_post_parameters_that_arent_an_object_400(self):
        for parameters in (['activeOnly'], 'activeOnly', 1):
            with self.subTest(parameters=parameters):
                response = self.client.post(
                    self.collection.url + 'HEAD/expansions/',
                    {'mnemonic': 'e1', 'parameters': parameters},
                    HTTP_AUTHORIZATION='Token ' + self.token,
                    format='json'
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {'parameters': ['Must be an object of expansion parameters.']})
        self.assertEqual(self.collection.expansions.count(), 0)

    def test_post_with_every_default_parameter_201(self):
        response = self.client.post(
            self.collection.url + 'HEAD/expansions/',
            {'mnemonic': 'e1', 'parameters': {**default_expansion_parameters(), 'activeOnly': True}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.collection.expansions.get(mnemonic='e1').parameters['activeOnly'], True)

    def test_post_drops_spaces_from_parameter_keys(self):
        source = OrganizationSourceFactory()
        v1 = OrganizationSourceFactory(organization=source.organization, mnemonic=source.mnemonic, version='v1')

        response = self.client.post(
            self.collection.url + 'HEAD/expansions/',
            {'mnemonic': 'e1', 'parameters': {'system - version': v1.uri, 'active Only': True}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            self.collection.expansions.get(mnemonic='e1').parameters, {'system-version': v1.uri, 'activeOnly': True})

    def test_post_system_version_with_two_versions_of_one_source_400(self):
        source = OrganizationSourceFactory()
        v1 = OrganizationSourceFactory(organization=source.organization, mnemonic=source.mnemonic, version='v1')
        v2 = OrganizationSourceFactory(organization=source.organization, mnemonic=source.mnemonic, version='v2')

        response = self.client.post(
            self.collection.url + 'HEAD/expansions/',
            {'mnemonic': 'e1', 'parameters': {'system-version': f'{v1.uri},{v2.uri}'}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {'parameters': {'system-version': [
                f'Names more than one version of {source.uri}: {v1.uri}, {v2.uri}. '
                'Use only one version of a source in system-version.'
            ]}})
        self.assertEqual(self.collection.expansions.count(), 0)

        for value in ([v1.uri], [], {}, 0, False):
            response = self.client.post(
                self.collection.url + 'HEAD/expansions/',
                {'mnemonic': 'e1', 'parameters': {'system-version': value}},
                HTTP_AUTHORIZATION='Token ' + self.token,
                format='json'
            )

            self.assertEqual(response.status_code, 400, value)
            self.assertEqual(self.collection.expansions.count(), 0)

        response = self.client.post(
            self.collection.url + 'HEAD/expansions/',
            {'mnemonic': 'e1', 'parameters': {'system-version': f'{v1.uri},{v1.uri}'}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.collection.expansions.count(), 1)

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
        self.assertEqual(
            response.data[0]['summary'],
            {'active_concepts': 0, 'active_mappings': 0, 'retired_concepts': 0, 'retired_mappings': 0})

        response = self.client.get(
            self.collection.url + 'HEAD/expansions/?verbose=true&includeSummary=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], expansion.id)
        self.assertEqual(response.data[0]['mnemonic'], 'e1')
        self.assertEqual(response.data[0]['parameters'], expansion.parameters)
        self.assertEqual(
            response.data[0]['summary'],
            {'active_concepts': 0, 'active_mappings': 0, 'retired_concepts': 0, 'retired_mappings': 0})


class CollectionExpansionsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        self.token = self.collection.created_by.get_token()

    def test_get(self):
        collection_v1 = OrganizationCollectionFactory(
            mnemonic=self.collection.mnemonic, organization=self.collection.organization, version='v1'
        )

        response = self.client.get(
            self.collection.url + 'expansions/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

        ExpansionFactory(mnemonic='e1-head', collection_version=self.collection)
        ExpansionFactory(mnemonic='e1-v1', collection_version=collection_v1)
        ExpansionFactory(mnemonic='e2-head', collection_version=self.collection)

        response = self.client.get(
            self.collection.url + 'expansions/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)
        self.assertEqual([expansion['mnemonic'] for expansion in response.data], ['e2-head', 'e1-v1', 'e1-head'])


class CollectionVersionExpansionProcessingViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.token = self.collection.created_by.get_token()

    def test_get_200(self):
        response = self.client.get(
            self.expansion.url + 'processing/',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'False')

        self.expansion.is_processing = True
        self.expansion.save(update_fields=['is_processing'])

        response = self.client.get(
            self.expansion.url + 'processing/',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'True')

    def test_post_200(self):
        self.expansion.is_processing = True
        self.expansion.save(update_fields=['is_processing'])

        response = self.client.post(
            self.expansion.url + 'processing/',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 200)
        self.expansion.refresh_from_db()
        self.assertFalse(self.expansion.is_processing)

        response = self.client.post(
            self.expansion.url + 'processing/',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 200)
        self.expansion.refresh_from_db()
        self.assertFalse(self.expansion.is_processing)


class CollectionVersionExpansionResolvedRepoUpdatesViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
        self.token = self.collection.created_by.get_token()

    def test_get_200_empty(self):
        # No explicit repo versions linked — response should be an empty dict
        response = self.client.get(
            self.expansion.url + 'resolved-repo-updates/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {})

    def test_get_200_with_updates(self):
        # HEAD source must exist for resolve_reference_expression to find the latest released version
        source_head = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(
            mnemonic=source_head.mnemonic, organization=source_head.organization, version='v1', released=True)
        source_v2 = OrganizationSourceFactory(
            mnemonic=source_head.mnemonic, organization=source_head.organization, version='v2', released=True)
        self.expansion.evaluated_source_versions.add(source_v1)

        response = self.client.get(
            self.expansion.url + 'resolved-repo-updates/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertIn(source_v1.url, response.data)
        self.assertEqual(response.data[source_v1.url], source_v2.url)

    def test_get_401_unauthenticated_private_collection(self):
        # Private collection — unauthenticated request should be denied (403 via custom permission)
        private_collection = OrganizationCollectionFactory(public_access=ACCESS_TYPE_NONE)
        expansion = ExpansionFactory(collection_version=private_collection)

        response = self.client.get(
            expansion.url + 'resolved-repo-updates/',
            format='json'
        )

        self.assertEqual(response.status_code, 401)

    def test_get_403_unauthorized_private_collection(self):
        # Private collection — user not in the owning org should be denied
        private_collection = OrganizationCollectionFactory(public_access=ACCESS_TYPE_NONE)
        expansion = ExpansionFactory(collection_version=private_collection)
        other_user = UserProfileFactory()

        response = self.client.get(
            expansion.url + 'resolved-repo-updates/',
            HTTP_AUTHORIZATION='Token ' + other_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)

    def test_delete_400_default_expansion(self):
        response = self.client.delete(
            self.expansion.url + 'resolved-repo-updates/', HTTP_AUTHORIZATION='Token ' + self.token)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'errors': ['Cannot delete default expansion']})
        self.expansion.refresh_from_db()
        self.assertTrue(self.expansion.is_active)

    def test_delete_204_other_expansion(self):
        expansion = ExpansionFactory(collection_version=self.collection)

        response = self.client.delete(
            expansion.url + 'resolved-repo-updates/', HTTP_AUTHORIZATION='Token ' + self.token)

        self.assertEqual(response.status_code, 204)
        expansion.refresh_from_db()
        self.assertFalse(expansion.is_active)


class CollectionVersionsComparisonViewTest(OCLAPITestCase):
    def test_post_200(self):  # pylint: disable=too-many-locals
        collection = OrganizationCollectionFactory()
        collection_v1 = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1')
        collection_v2 = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v2')
        expansion_v1 = ExpansionFactory(collection_version=collection_v1)
        expansion_v2 = ExpansionFactory(collection_version=collection_v2)
        collection_v1.expansion_uri = expansion_v1.uri
        collection_v1.save()
        collection_v2.expansion_uri = expansion_v2.uri
        collection_v2.save()

        concept1 = ConceptFactory(mnemonic='concept1')
        concept2 = ConceptFactory(mnemonic='concept2')
        concept2_v2 = ConceptFactory(
            parent=concept2.parent, mnemonic=concept2.mnemonic, version='v2', concept_class='Foobar')
        concept3 = ConceptFactory(mnemonic='concept3')

        expansion_v1.concepts.add(concept1, concept2, concept3)
        expansion_v2.concepts.add(concept1, concept2_v2)

        for concept in Concept.objects.all():
            concept.set_checksums()

        token = collection.created_by.get_token()
        response = self.client.post(
            '/collections/$compare/?inline=true',
            {
                'version1': collection_v1.uri,
                'version2': collection_v2.uri,
                'verbosity': 2,
            },
            HTTP_AUTHORIZATION=f'Token {token}',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['meta']['version1']['uri'], collection_v1.uri)
        self.assertEqual(response.data['meta']['version2']['uri'], collection_v2.uri)
        self.assertEqual(response.data['meta']['version1']['concepts'], 3)
        self.assertEqual(response.data['meta']['version2']['concepts'], 2)
        self.assertEqual(response.data['concepts']['removed'], {'total': 1, 'mnemonic': ['concept3']})
        self.assertEqual(response.data['concepts']['changed_major'], {'total': 1, 'mnemonic': ['concept2']})
        self.assertEqual(response.data['concepts']['same_major'], 1)

    def test_post_400_missing_default_expansion(self):
        collection = OrganizationCollectionFactory()
        collection_v1 = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1')
        collection_v2 = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v2')
        # v1 has a default expansion set; v2 does not
        expansion_v1 = ExpansionFactory(collection_version=collection_v1)
        collection_v1.expansion_uri = expansion_v1.uri
        collection_v1.save()

        token = collection.created_by.get_token()
        response = self.client.post(
            '/collections/$compare/?inline=true',
            {
                'version1': collection_v1.uri,
                'version2': collection_v2.uri,
            },
            HTTP_AUTHORIZATION=f'Token {token}',
            format='json'
        )

        self.assertEqual(response.status_code, 400)


class CollectionVersionsChangelogViewTest(OCLAPITestCase):
    def test_post_200(self):
        collection = OrganizationCollectionFactory()
        collection_v1 = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1')
        collection_v2 = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v2')
        expansion_v1 = ExpansionFactory(collection_version=collection_v1)
        expansion_v2 = ExpansionFactory(collection_version=collection_v2)
        collection_v1.expansion_uri = expansion_v1.uri
        collection_v1.save()
        collection_v2.expansion_uri = expansion_v2.uri
        collection_v2.save()

        concept1 = ConceptFactory(mnemonic='concept1')
        concept2 = ConceptFactory(mnemonic='concept2')

        expansion_v1.concepts.add(concept1)
        expansion_v2.concepts.add(concept1, concept2)

        for concept in Concept.objects.all():
            concept.set_checksums()

        token = collection.created_by.get_token()
        response = self.client.post(
            '/collections/$changelog/?inline=true',
            {
                'version1': collection_v1.uri,
                'version2': collection_v2.uri,
                'verbosity': 1,
            },
            HTTP_AUTHORIZATION=f'Token {token}',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['meta']['version1']['uri'], collection_v1.uri)
        self.assertEqual(response.data['meta']['version2']['uri'], collection_v2.uri)
        self.assertEqual(list(response.data['concepts']['new'].keys()), [concept2.mnemonic])

    def test_post_200_output_markdown(self):
        collection = OrganizationCollectionFactory()
        collection_v1 = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1')
        collection_v2 = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v2')
        expansion_v1 = ExpansionFactory(collection_version=collection_v1)
        expansion_v2 = ExpansionFactory(collection_version=collection_v2)
        collection_v1.expansion_uri = expansion_v1.uri
        collection_v1.save()
        collection_v2.expansion_uri = expansion_v2.uri
        collection_v2.save()

        concept1 = ConceptFactory(mnemonic='concept1')
        expansion_v2.concepts.add(concept1)
        concept1.set_checksums()

        token = collection.created_by.get_token()
        response = self.client.post(
            '/collections/$changelog/?inline=true&output=markdown',
            {
                'version1': collection_v1.uri,
                'version2': collection_v2.uri,
            },
            HTTP_AUTHORIZATION=f'Token {token}',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('markdown', response.data)


class ExpansionsComparisonViewTest(OCLAPITestCase):
    def test_post_200(self):
        collection = OrganizationCollectionFactory()
        expansion1 = ExpansionFactory(collection_version=collection)
        expansion2 = ExpansionFactory(collection_version=collection)

        concept1 = ConceptFactory(mnemonic='concept1')
        concept2 = ConceptFactory(mnemonic='concept2')

        expansion1.concepts.add(concept1)
        expansion2.concepts.add(concept1, concept2)

        for concept in Concept.objects.all():
            concept.set_checksums()

        token = collection.created_by.get_token()
        response = self.client.post(
            '/collections/expansions/$compare/?inline=true',
            {
                'expansion1': expansion1.uri,
                'expansion2': expansion2.uri,
                'verbosity': 2,
            },
            HTTP_AUTHORIZATION=f'Token {token}',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['meta']['version1']['uri'], expansion1.uri)
        self.assertEqual(response.data['meta']['version2']['uri'], expansion2.uri)
        self.assertEqual(response.data['concepts']['new'], {'total': 1, 'mnemonic': ['concept2']})


class CollectionReferencesPreviewPermissionTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.owner = UserProfileFactory()
        self.collection = UserCollectionFactory(user=self.owner, public_access='View')
        self.payload = {'data': {'expressions': ['/orgs/OCL/sources/NoSuchSource/concepts/']}}

    def test_viewer_cannot_preview(self):
        response = self.client.post(
            self.collection.uri + 'references/preview/', self.payload,
            HTTP_AUTHORIZATION='Token ' + UserProfileFactory().get_token(), format='json')
        self.assertEqual(response.status_code, 403)

    def test_owner_can_preview(self):
        response = self.client.post(
            self.collection.uri + 'references/preview/', self.payload,
            HTTP_AUTHORIZATION='Token ' + self.owner.get_token(), format='json')
        self.assertEqual(response.status_code, 200)


class CollectionVersionPrivateHeadAccessTest(PrivateHeadVersionAccessMixin, OCLAPITestCase):
    model = Collection
    org_repo_factory = OrganizationCollectionFactory
    user_repo_factory = UserCollectionFactory

    def add_concept(self, head, version):
        concept = ConceptFactory(parent=OrganizationSourceFactory())
        expansion = ExpansionFactory(collection_version=version)
        expansion.concepts.add(concept)
        version.expansion_uri = expansion.uri
        version.save()
        return concept

    def test_expansion_content_counts_are_hidden_when_head_is_private(self):
        head, version = self.create_org_version()
        self.add_concept(head, version)
        expansion = version.expansions.first()

        urls = [f'{expansion.uri}{suffix}{query}' for suffix in ('concepts/', 'mappings/') for query in ('', '?q=c')]
        for url in urls:
            self.assertEqual(
                self.client.head(url, HTTP_AUTHORIZATION=f'Token {self.outsider.get_token()}').status_code, 403, url)
            self.assertEqual(self.client.head(url).status_code, 401, url)

    def test_expansion_content_counts_of_public_collection_stay_visible(self):
        head = self.org_repo_factory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        version = self.org_repo_factory(
            mnemonic=head.mnemonic, organization=self.org, version='v1', public_access=ACCESS_TYPE_VIEW)
        self.add_concept(head, version)
        expansion = version.expansions.first()

        for suffix in ('concepts/', 'mappings/'):
            response = self.client.head(
                expansion.uri + suffix, HTTP_AUTHORIZATION=f'Token {self.outsider.get_token()}')
            self.assertEqual(response.status_code, 200, suffix)
