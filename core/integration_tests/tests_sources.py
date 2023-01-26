import json
import zipfile

from celery_once import AlreadyQueued
from django.db import transaction
from mock import patch, Mock, ANY, PropertyMock
from rest_framework.exceptions import ErrorDetail

from core.bundles.models import Bundle
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.tasks import export_source
from core.common.tests import OCLAPITestCase
from core.common.utils import get_latest_dir_in_path
from core.concepts.serializers import ConceptVersionExportSerializer
from core.concepts.tests.factories import ConceptFactory
from core.mappings.serializers import MappingDetailSerializer
from core.mappings.tests.factories import MappingFactory
from core.orgs.models import Organization
from core.sources.models import Source
from core.sources.serializers import SourceDetailSerializer, SourceVersionExportSerializer
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class SourceListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.source_payload = {
            'website': '', 'custom_validation_schema': 'None', 'name': 's2', 'default_locale': 'ab',
            'short_code': 's2', 'description': '', 'source_type': '', 'full_name': 'source 2', 'public_access': 'View',
            'external_id': '', 'id': 's2', 'supported_locales': 'af,am', 'canonical_url': 'https://foo.com/foo/bar/'
        }

    def test_get_200(self):
        response = self.client.get(
            self.organization.sources_url,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        source = OrganizationSourceFactory(organization=self.organization)

        response = self.client.get(
            self.organization.sources_url + '?verbose=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['short_code'], source.mnemonic)
        self.assertEqual(response.data[0]['owner'], self.organization.mnemonic)
        self.assertEqual(response.data[0]['owner_type'], 'Organization')
        self.assertEqual(response.data[0]['owner_url'], self.organization.uri)
        self.assertEqual(response.data[0]['type'], 'Source')
        for attr in ['active_concepts', 'active_mappings', 'versions', 'summary']:
            self.assertFalse(attr in response.data[0])

        response = self.client.get(
            self.organization.sources_url + '?verbose=true&includeSummary=true',
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['short_code'], source.mnemonic)
        self.assertEqual(response.data[0]['owner'], self.organization.mnemonic)
        self.assertEqual(response.data[0]['owner_type'], 'Organization')
        self.assertEqual(response.data[0]['owner_url'], self.organization.uri)
        self.assertTrue('summary' in response.data[0])
        for attr in ['active_concepts', 'active_mappings', 'versions']:
            self.assertTrue(attr in response.data[0]['summary'])

        response = self.client.get(
            self.organization.sources_url + '?brief=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0], dict(id=source.mnemonic, url=source.uri))

    def test_get_200_zip(self):
        response = self.client.get(
            self.organization.sources_url,
            HTTP_COMPRESS='true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        content = json.loads(zipfile.ZipFile(response.rendered_content.filelike).read('export.json').decode('utf-8'))
        self.assertEqual(content, [])

        source = OrganizationSourceFactory(organization=self.organization)

        response = self.client.get(
            self.organization.sources_url + '?verbose=true',
            HTTP_COMPRESS='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        content = json.loads(zipfile.ZipFile(response.rendered_content.filelike).read('export.json').decode('utf-8'))
        self.assertEqual(content, SourceDetailSerializer([source], many=True).data)

    def test_post_201(self):
        sources_url = f"/orgs/{self.organization.mnemonic}/sources/"

        response = self.client.post(
            sources_url,
            self.source_payload,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertListEqual(
            sorted(list(response.data.keys())),
            sorted([
                'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'source_type',
                'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
                'url', 'owner', 'owner_type', 'owner_url', 'created_on', 'updated_on', 'created_by',
                'updated_by', 'extras', 'external_id', 'versions_url', 'version', 'concepts_url', 'mappings_url',
                'canonical_url', 'identifier', 'publisher', 'contact', 'meta',
                'jurisdiction', 'purpose', 'copyright', 'content_type', 'revision_date', 'logo_url', 'text',
                'experimental', 'case_sensitive', 'collection_reference', 'hierarchy_meaning', 'compositional',
                'version_needed', 'hierarchy_root_url', 'autoid_concept_mnemonic', 'autoid_mapping_mnemonic',
                'autoid_concept_external_id', 'autoid_mapping_external_id',
                'autoid_concept_mnemonic_start_from', 'autoid_concept_external_id_start_from',
                'autoid_mapping_mnemonic_start_from', 'autoid_mapping_external_id_start_from',
            ])
        )
        source = Source.objects.last()

        self.assertEqual(response.data['uuid'], str(source.id))
        self.assertEqual(response.data['short_code'], source.mnemonic)
        self.assertEqual(response.data['full_name'], source.full_name)
        self.assertEqual(response.data['owner_url'], source.parent.uri)
        self.assertEqual(response.data['url'], source.uri)
        self.assertEqual(response.data['canonical_url'], source.canonical_url)
        self.assertEqual(response.data['default_locale'], 'ab')
        self.assertEqual(response.data['supported_locales'], ['ab', 'af', 'am'])
        self.assertEqual(source.default_locale, 'ab')
        self.assertEqual(source.canonical_url, 'https://foo.com/foo/bar/')
        self.assertEqual(source.supported_locales, ['af', 'am'])
        self.assertIsNone(source.active_mappings)
        self.assertIsNone(source.active_concepts)

    def test_post_400(self):
        sources_url = f"/orgs/{self.organization.mnemonic}/sources/"

        response = self.client.post(
            sources_url,
            {**self.source_payload, 'name': None},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data.keys()), ['name'])


class SourceRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()

    def test_get(self):
        response = self.client.get(
            self.organization.sources_url + 'source1/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 404)

        source = OrganizationSourceFactory(
            organization=self.organization, default_locale='en', supported_locales=['fr'])
        response = self.client.get(
            source.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(source.id))
        self.assertEqual(response.data['short_code'], source.mnemonic)
        self.assertEqual(response.data['default_locale'], 'en')
        self.assertEqual(response.data['supported_locales'], ['en', 'fr'])

        source2 = OrganizationSourceFactory(
            organization=self.organization, default_locale='en', supported_locales=['fr', 'en'])
        response = self.client.get(
            source2.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(source2.id))
        self.assertEqual(response.data['short_code'], source2.mnemonic)
        self.assertEqual(response.data['default_locale'], 'en')
        self.assertEqual(response.data['supported_locales'], ['en', 'fr'])

        source3 = OrganizationSourceFactory(
            organization=self.organization, default_locale='en', supported_locales=None)
        response = self.client.get(
            source3.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(source3.id))
        self.assertEqual(response.data['short_code'], source3.mnemonic)
        self.assertEqual(response.data['default_locale'], 'en')
        self.assertEqual(response.data['supported_locales'], ['en'])

    def test_put_200(self):
        source = OrganizationSourceFactory(organization=self.organization)
        self.assertTrue(source.is_head)
        self.assertEqual(source.versions.count(), 1)
        self.assertEqual(source.default_locale, 'en')
        self.assertEqual(source.supported_locales, ['fr'])

        sources_url = f"/orgs/{self.organization.mnemonic}/sources/{source.mnemonic}/"

        response = self.client.put(
            sources_url,
            {'full_name': 'Full name', 'supported_locales': ['fr']},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertListEqual(
            sorted(list(response.data.keys())),
            sorted([
                'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'source_type',
                'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
                'url', 'owner', 'owner_type', 'owner_url', 'created_on', 'updated_on', 'created_by',
                'updated_by', 'extras', 'external_id', 'versions_url', 'version', 'concepts_url', 'mappings_url',
                'canonical_url', 'identifier', 'publisher', 'contact', 'meta',
                'jurisdiction', 'purpose', 'copyright', 'content_type', 'revision_date', 'logo_url', 'text',
                'experimental', 'case_sensitive', 'collection_reference', 'hierarchy_meaning', 'compositional',
                'version_needed', 'hierarchy_root_url', 'autoid_concept_mnemonic', 'autoid_mapping_mnemonic',
                'autoid_concept_external_id', 'autoid_mapping_external_id',
                'autoid_concept_mnemonic_start_from', 'autoid_concept_external_id_start_from',
                'autoid_mapping_mnemonic_start_from', 'autoid_mapping_external_id_start_from',
            ])
        )
        source = Source.objects.last()

        self.assertTrue(source.is_head)
        self.assertEqual(source.versions.count(), 1)
        self.assertEqual(response.data['full_name'], source.full_name)
        self.assertEqual(response.data['full_name'], 'Full name')
        self.assertEqual(response.data['default_locale'], 'en')
        self.assertEqual(response.data['supported_locales'], ['en', 'fr'])
        self.assertEqual(source.default_locale, 'en')
        self.assertEqual(source.supported_locales, ['fr'])

    def test_put_hierarchy_root(self):
        source = OrganizationSourceFactory(organization=self.organization)
        self.assertTrue(source.is_head)
        self.assertEqual(source.versions.count(), 1)
        concept = ConceptFactory(parent=source)

        sources_url = f"/orgs/{self.organization.mnemonic}/sources/{source.mnemonic}/"

        response = self.client.put(
            sources_url,
            dict(hierarchy_root_url=concept.uri),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['hierarchy_root_url'], concept.uri)
        source.refresh_from_db()
        self.assertEqual(source.hierarchy_root_id, concept.id)

        concept2 = ConceptFactory(parent=source)

        sources_url = f"/orgs/{self.organization.mnemonic}/sources/{source.mnemonic}/"

        response = self.client.put(
            sources_url,
            dict(hierarchy_root_url=concept2.uri),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['hierarchy_root_url'], concept2.uri)
        source.refresh_from_db()
        self.assertEqual(source.hierarchy_root_id, concept2.id)

        unknown_concept = ConceptFactory()

        sources_url = f"/orgs/{self.organization.mnemonic}/sources/{source.mnemonic}/"

        response = self.client.put(
            sources_url,
            dict(hierarchy_root_url=unknown_concept.uri),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'hierarchy_root': ['Hierarchy Root must belong to the same Source.']})

        source.refresh_from_db()
        self.assertEqual(source.hierarchy_root_id, concept2.id)

    @patch('core.sources.views.delete_source')
    def test_delete_202(self, delete_source_task_mock):  # async delete
        delete_source_task_mock.delay = Mock(return_value=Mock(id='task-id'))
        source = OrganizationSourceFactory(mnemonic='source', organization=self.organization)
        response = self.client.delete(
            source.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, dict(task='task-id'))
        delete_source_task_mock.delay.assert_called_once_with(source.id)

    @patch('core.common.models.delete_s3_objects')
    def test_delete_204(self, delete_s3_objects_mock):  # sync delete
        source = OrganizationSourceFactory(mnemonic='source', organization=self.organization)
        response = self.client.delete(
            source.uri + '?inline=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Source.objects.filter(id=source.id).exists())
        self.assertFalse(Source.objects.filter(mnemonic='source').exists())
        delete_s3_objects_mock.delay.assert_called_once_with(f'{self.organization.mnemonic}/source_HEAD.')


class SourceVersionListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.source = OrganizationSourceFactory(organization=self.organization)

    def test_get_200(self):
        response = self.client.get(
            f'/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/versions/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['version'], 'HEAD')

        response = self.client.get(
            f'/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/versions/'
            f'?verbose=true&includeSummary=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['version'], 'HEAD')
        self.assertEqual(response.data[0]['concepts_url'], self.source.concepts_url)

    def test_post_201(self):
        response = self.client.post(
            f'/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/versions/',
            dict(id='v1', description='Version 1'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['uuid'])
        self.assertEqual(response.data['version'], 'v1')
        self.assertEqual(self.source.versions.count(), 2)

    def test_post_409(self):
        OrganizationSourceFactory(version='v1', organization=self.organization, mnemonic=self.source.mnemonic)
        with transaction.atomic():
            response = self.client.post(
                f'/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/versions/',
                dict(id='v1', description='Version 1'),
                HTTP_AUTHORIZATION='Token ' + self.token,
                format='json'
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['detail'], "Source version 'v1' already exist.")

    @patch('core.sources.views.export_source')
    def test_post_400(self, export_source_mock):
        response = self.client.post(
            f'/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/versions/',
            dict(id=None, description='Version 1'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'version': [ErrorDetail(string='This field may not be null.', code='null')]})
        export_source_mock.delay.assert_not_called()


class SourceLatestVersionRetrieveUpdateViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.source = OrganizationSourceFactory(organization=self.organization)
        self.latest_version = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, is_latest_version=True, organization=self.organization, version='v1',
            released=True
        )

    def test_get_200(self):
        response = self.client.get(
            f'/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/latest/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], 'v1')
        self.assertEqual(response.data['uuid'], str(self.latest_version.id))
        self.assertEqual(response.data['short_code'], self.source.mnemonic)
        self.assertEqual(response.data['type'], 'Source Version')

        response = self.client.get(
            f'/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/latest/summary/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], 'v1')
        self.assertEqual(response.data['uuid'], str(self.latest_version.id))
        self.assertEqual(response.data['active_concepts'], None)
        self.assertEqual(response.data['active_mappings'], None)

    def test_put_200(self):
        self.assertIsNone(self.latest_version.external_id)

        external_id = '123'
        response = self.client.put(
            f'/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/latest/',
            dict(external_id=external_id),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], 'v1')
        self.assertEqual(response.data['uuid'], str(self.latest_version.id))
        self.assertEqual(response.data['short_code'], self.source.mnemonic)
        self.assertEqual(response.data['external_id'], external_id)

        self.latest_version.refresh_from_db()
        self.assertEqual(self.latest_version.external_id, external_id)

    def test_put_400(self):
        response = self.client.put(
            f'/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/latest/',
            dict(id=None),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'id': [ErrorDetail(string='This field may not be null.', code='null')]})


class SourceExtrasViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.extras = dict(foo='bar', tao='ching')
        self.source = OrganizationSourceFactory(organization=self.organization, extras=self.extras)

    def test_get_200(self):
        response = self.client.get(self.source.uri + 'extras/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.extras)


class SourceVersionExtrasViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.extras = dict(foo='bar', tao='ching')
        self.source = OrganizationSourceFactory(organization=self.organization, extras=self.extras)
        self.source_v1 = OrganizationSourceFactory(
            organization=self.organization, extras=self.extras, mnemonic=self.source.mnemonic, version='v1')

    def test_get_200(self):
        response = self.client.get(self.source_v1.uri + 'extras/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.extras)


class SourceVersionRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.source = OrganizationSourceFactory(organization=self.organization)
        self.source_v1 = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.organization, version='v1',
        )

    def test_get_200(self):
        response = self.client.get(
            self.source_v1.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['version'], 'v1')

    def test_put_200(self):
        self.assertEqual(self.source.extras, {})
        self.assertEqual(self.source_v1.extras, {})

        extras = dict(foo='bar')
        response = self.client.put(
            self.source_v1.uri,
            dict(extras=extras),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['extras'], extras)
        self.source_v1.refresh_from_db()
        self.assertEqual(self.source_v1.extras, extras)
        self.assertEqual(self.source.extras, {})

    def test_put_400(self):
        response = self.client.put(
            self.source_v1.uri,
            dict(id=None),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'id': [ErrorDetail(string='This field may not be null.', code='null')]})

    @patch('core.common.models.delete_s3_objects')
    def test_version_delete_204(self, delete_s3_objects_mock):
        self.assertEqual(self.source.versions.count(), 2)

        response = self.client.delete(
            self.source_v1.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.source.versions.count(), 1)
        self.assertFalse(self.source.versions.filter(version='v1').exists())
        delete_s3_objects_mock.delay.assert_called_once_with(
            f'{self.source.parent.mnemonic}/{self.source.mnemonic}_v1.')

    @patch('core.common.models.delete_s3_objects')
    def test_version_delete_204_referenced_in_private_collection(self, delete_s3_objects_mock):
        concept = ConceptFactory(parent=self.source_v1)

        collection = OrganizationCollectionFactory(public_access='None', autoexpand_head=False)
        collection.add_expressions(dict(expressions=[concept.uri]), collection.created_by)
        self.assertEqual(collection.expansions.count(), 0)
        self.assertEqual(collection.references.count(), 1)

        response = self.client.delete(
            self.source_v1.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.source.versions.count(), 1)
        self.assertFalse(self.source.versions.filter(version='v1').exists())
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.source.versions.count(), 1)
        self.assertFalse(self.source.versions.filter(version='v1').exists())
        delete_s3_objects_mock.delay.assert_called_once_with(
            f'{self.source.parent.mnemonic}/{self.source.mnemonic}_v1.')

        source_v2 = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.organization, version='v2',
        )
        self.assertEqual(self.source.versions.count(), 2)

        concept2 = ConceptFactory(parent=self.source)
        concept2_latest_version = concept2.get_latest_version()
        concept2_latest_version.sources.add(source_v2)

        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.autoexpand_head = True
        collection.save()
        collection.add_expressions(dict(expressions=[concept2.uri]), collection.created_by)
        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.references.count(), 2)

        response = self.client.delete(
            source_v2.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.source.versions.count(), 1)
        self.assertFalse(self.source.versions.filter(version='v2').exists())


class SourceExtraRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.extras = dict(foo='bar', tao='ching')
        self.source = OrganizationSourceFactory(organization=self.organization, extras=self.extras)

    def test_get_200(self):
        response = self.client.get(
            self.source.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='bar'))

    def test_get_404(self):
        response = self.client.get(
            self.source.uri + 'extras/bar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        response = self.client.put(
            self.source.uri + 'extras/foo/',
            dict(foo='foobar'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='foobar'))
        self.source.refresh_from_db()
        self.assertEqual(self.source.extras, dict(foo='foobar', tao='ching'))

    def test_put_400(self):
        response = self.client.put(
            self.source.uri + 'extras/foo/',
            dict(tao='te-ching'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, ['Must specify foo param in body.'])

    def test_delete(self):
        response = self.client.delete(
            self.source.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.source.refresh_from_db()
        self.assertEqual(self.source.extras, dict(tao='ching'))

        response = self.client.delete(
            self.source.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)
        self.source.refresh_from_db()
        self.assertEqual(self.source.extras, dict(tao='ching'))


class SourceVersionExportViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory(username='username')
        self.token = self.user.get_token()
        self.source = UserSourceFactory(mnemonic='source1', user=self.user)
        self.source_v1 = UserSourceFactory(version='v1', mnemonic='source1', user=self.user)
        self.v1_updated_at = self.source_v1.updated_at.strftime('%Y%m%d%H%M%S')

    def test_get_404(self):
        response = self.client.get(
            '/users/foo/sources/source1/v2/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.common.services.S3.exists')
    def test_get_204(self, s3_exists_mock):
        s3_exists_mock.return_value = False

        response = self.client.get(
            self.source_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        s3_exists_mock.assert_called_once_with(f"username/source1_v1.{self.v1_updated_at}.zip")

    @patch('core.common.services.S3.url_for')
    @patch('core.common.services.S3.exists')
    def test_get_303(self, s3_exists_mock, s3_url_for_mock):
        s3_url = f'https://s3/username/source1_v1.{self.v1_updated_at}.zip'
        s3_url_for_mock.return_value = s3_url
        s3_exists_mock.return_value = True

        response = self.client.get(
            self.source_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['Location'], s3_url)
        self.assertEqual(response['Last-Updated'], self.source_v1.last_child_update.isoformat())
        self.assertEqual(response['Last-Updated-Timezone'], 'America/New_York')
        s3_exists_mock.assert_called_once_with(f"username/source1_v1.{self.v1_updated_at}.zip")
        s3_url_for_mock.assert_called_once_with(f"username/source1_v1.{self.v1_updated_at}.zip")

    @patch('core.common.services.S3.url_for')
    @patch('core.common.services.S3.exists')
    def test_get_200(self, s3_exists_mock, s3_url_for_mock):
        s3_url = f'https://s3/username/source1_v1.{self.v1_updated_at}.zip'
        s3_url_for_mock.return_value = s3_url
        s3_exists_mock.return_value = True

        response = self.client.get(
            self.source_v1.uri + 'export/?noRedirect=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(url=s3_url))
        s3_exists_mock.assert_called_once_with(f"username/source1_v1.{self.v1_updated_at}.zip")
        s3_url_for_mock.assert_called_once_with(f"username/source1_v1.{self.v1_updated_at}.zip")

    @patch('core.sources.models.Source.is_exporting', new_callable=PropertyMock)
    @patch('core.common.services.S3.exists')
    def test_get_208(self, s3_exists_mock, is_exporting_mock):
        s3_exists_mock.return_value = False
        is_exporting_mock.return_value = True

        response = self.client.get(
            self.source_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 208)
        s3_exists_mock.assert_called_once_with(f"username/source1_v1.{self.v1_updated_at}.zip")

    def test_get_405(self):
        response = self.client.get(
            f'/users/{self.source.parent.mnemonic}/sources/{self.source.mnemonic}/{"HEAD"}/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    def test_post_405(self):
        response = self.client.post(
            f'/users/{self.source.parent.mnemonic}/sources/{self.source.mnemonic}/{"HEAD"}/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    @patch('core.common.services.S3.exists')
    def test_post_303(self, s3_exists_mock):
        s3_exists_mock.return_value = True
        response = self.client.post(
            self.source_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['URL'], self.source_v1.uri + 'export/')
        s3_exists_mock.assert_called_once_with(f"username/source1_v1.{self.v1_updated_at}.zip")

    @patch('core.sources.views.export_source')
    @patch('core.common.services.S3.exists')
    def test_post_202(self, s3_exists_mock, export_source_mock):
        s3_exists_mock.return_value = False
        response = self.client.post(
            self.source_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        s3_exists_mock.assert_called_once_with(f"username/source1_v1.{self.v1_updated_at}.zip")
        export_source_mock.delay.assert_called_once_with(self.source_v1.id)

    @patch('core.sources.views.export_source')
    @patch('core.common.services.S3.exists')
    def test_post_409(self, s3_exists_mock, export_source_mock):
        s3_exists_mock.return_value = False
        export_source_mock.delay.side_effect = AlreadyQueued('already-queued')
        response = self.client.post(
            self.source_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        s3_exists_mock.assert_called_once_with(f"username/source1_v1.{self.v1_updated_at}.zip")
        export_source_mock.delay.assert_called_once_with(self.source_v1.id)

    def test_delete_405(self):
        random_user = UserProfileFactory()
        response = self.client.delete(
            self.source.uri + 'HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    def test_delete_403(self):
        random_user = UserProfileFactory()
        response = self.client.delete(
            self.source_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)

    @patch('core.sources.models.Source.has_export')
    def test_delete_404_no_export(self, has_export_mock):
        has_export_mock.return_value = False
        response = self.client.delete(
            self.source_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.sources.models.Source.export_path', new_callable=PropertyMock)
    @patch('core.sources.models.Source.has_export')
    @patch('core.common.services.S3.remove')
    def test_delete_204(self, s3_remove_mock, has_export_mock, export_path_mock):
        has_export_mock.return_value = True
        export_path_mock.return_value = 'v1/export/path'
        response = self.client.delete(
            self.source_v1.uri + 'export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        s3_remove_mock.assert_called_once_with('v1/export/path')


class ExportSourceTaskTest(OCLAPITestCase):
    @patch('core.common.utils.get_export_service')
    def test_export_source(self, export_service_mock):  # pylint: disable=too-many-locals
        s3_mock = Mock()
        export_service_mock.return_value = s3_mock
        s3_mock.url_for = Mock(return_value='https://s3-url')
        s3_mock.upload_file = Mock()
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept2, to_concept=concept1, parent=source)

        source_v1 = OrganizationSourceFactory(mnemonic=source.mnemonic, organization=source.organization, version='v1')
        concept1.sources.add(source_v1)
        concept2.sources.add(source_v1)
        mapping.sources.add(source_v1)

        export_source(source_v1.id)  # pylint: disable=no-value-for-parameter

        latest_temp_dir = get_latest_dir_in_path('/tmp/')
        zipped_file = zipfile.ZipFile(latest_temp_dir + '/export.zip')
        exported_data = json.loads(zipped_file.read('export.json').decode('utf-8'))

        self.assertEqual(
            exported_data, {**SourceVersionExportSerializer(source_v1).data, 'concepts': ANY, 'mappings': ANY}
        )

        exported_concepts = exported_data['concepts']
        expected_concepts = ConceptVersionExportSerializer([concept2, concept1], many=True).data

        self.assertEqual(len(exported_concepts), 2)
        self.assertIn(expected_concepts[0], exported_concepts)
        self.assertIn(expected_concepts[1], exported_concepts)

        exported_mappings = exported_data['mappings']
        expected_mappings = MappingDetailSerializer([mapping], many=True).data

        self.assertEqual(len(exported_mappings), 1)
        self.assertEqual(expected_mappings, exported_mappings)

        s3_upload_key = source_v1.export_path
        s3_mock.upload_file.assert_called_once_with(
            key=s3_upload_key, file_path=latest_temp_dir + '/export.zip', binary=True,
            metadata={'ContentType': 'application/zip'}, headers={'content-type': 'application/zip'}
        )
        s3_mock.url_for.assert_called_once_with(s3_upload_key)

        import shutil
        shutil.rmtree(latest_temp_dir)


class SourceLogoViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory(username='username')
        self.token = self.user.get_token()
        self.source = UserSourceFactory(mnemonic='source1', user=self.user)

    @patch('core.common.services.S3.upload_base64')
    def test_post_200(self, upload_base64_mock):
        upload_base64_mock.return_value = 'users/username/sources/source1/logo.png'
        self.assertIsNone(self.source.logo_url)
        self.assertIsNone(self.source.logo_path)

        response = self.client.post(
            self.source.uri + 'logo/',
            dict(base64='base64-data'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        expected_logo_url = 'http://oclapi2-dev.s3.amazonaws.com/users/username/sources/source1/logo.png'
        self.assertEqual(response.data['logo_url'].replace('https://', 'http://'), expected_logo_url)
        self.source.refresh_from_db()
        self.assertEqual(self.source.logo_url.replace('https://', 'http://'), expected_logo_url)
        self.assertEqual(self.source.logo_path, 'users/username/sources/source1/logo.png')
        upload_base64_mock.assert_called_once_with(
            'base64-data', 'users/username/sources/source1/logo.png', False, True
        )


class SourceVersionSummaryViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory()
        self.concept1 = ConceptFactory(parent=self.source)
        self.concept2 = ConceptFactory(parent=self.source)
        self.mapping = MappingFactory(from_concept=self.concept1, to_concept=self.concept2, parent=self.source)

    def test_get_200(self):
        self.source.active_concepts = 2
        self.source.active_mappings = 1
        self.source.save()

        response = self.client.get(self.source.url + 'HEAD/summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.source.id))
        self.assertEqual(response.data['id'], 'HEAD')
        self.assertEqual(response.data['active_concepts'], 2)
        self.assertEqual(response.data['active_mappings'], 1)

    def test_put_200(self):
        self.source.refresh_from_db()
        self.assertEqual(self.source.active_mappings, None)
        self.assertEqual(self.source.active_concepts, None)

        admin_token = UserProfileFactory(is_superuser=True, is_staff=True).get_token()

        response = self.client.put(
            self.source.url + 'HEAD/summary/',
            HTTP_AUTHORIZATION=f'Token {admin_token}'
        )

        self.assertEqual(response.status_code, 202)
        self.source.refresh_from_db()
        self.assertEqual(self.source.active_mappings, 1)
        self.assertEqual(self.source.active_concepts, 2)


class SourceSummaryViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory()
        self.concept1 = ConceptFactory(parent=self.source)
        self.concept2 = ConceptFactory(parent=self.source)
        self.mapping = MappingFactory(from_concept=self.concept1, to_concept=self.concept2, parent=self.source)

    def test_get_200(self):
        self.source.active_concepts = 2
        self.source.active_mappings = 1
        self.source.save()

        response = self.client.get(self.source.url + 'summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.source.id))
        self.assertEqual(response.data['id'], self.source.mnemonic)
        self.assertEqual(response.data['active_concepts'], 2)
        self.assertEqual(response.data['active_mappings'], 1)

    def test_put_200(self):
        self.source.refresh_from_db()
        self.assertEqual(self.source.active_mappings, None)
        self.assertEqual(self.source.active_concepts, None)

        admin_token = UserProfileFactory(is_superuser=True, is_staff=True).get_token()

        response = self.client.put(
            self.source.url + 'summary/',
            HTTP_AUTHORIZATION=f'Token {admin_token}'
        )

        self.assertEqual(response.status_code, 202)
        self.source.refresh_from_db()
        self.assertEqual(self.source.active_mappings, 1)
        self.assertEqual(self.source.active_concepts, 2)


class SourceHierarchyViewTest(OCLAPITestCase):
    @patch('core.sources.models.Source.hierarchy')
    def test_get_200(self, hierarchy_mock):
        source = OrganizationSourceFactory()
        hierarchy_mock.return_value = 'hierarchy-response'

        response = self.client.get(source.url + 'hierarchy/?limit=1000&offset=100')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'hierarchy-response')
        hierarchy_mock.assert_called_once_with(offset=100, limit=1000)


class SourceMappingsIndexViewTest(OCLAPITestCase):
    @patch('core.sources.views.index_source_mappings')
    def test_post_202(self, index_source_mappings_task_mock):
        index_source_mappings_task_mock.delay = Mock(return_value=Mock(state='PENDING', task_id='task-id-123'))
        source = OrganizationSourceFactory(id=100)
        user = UserProfileFactory(is_superuser=True, is_staff=True, username='soop')

        response = self.client.post(
            source.url + 'mappings/indexes/',
            HTTP_AUTHORIZATION=f'Token {user.get_token()}'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data, {'state': 'PENDING', 'username': 'soop', 'task': 'task-id-123', 'queue': 'default'})
        index_source_mappings_task_mock.delay.assert_called_once_with(100)


class SourceConceptsIndexViewTest(OCLAPITestCase):
    @patch('core.sources.views.index_source_concepts')
    def test_post_202(self, index_source_concepts_task_mock):
        index_source_concepts_task_mock.delay = Mock(return_value=Mock(state='PENDING', task_id='task-id-123'))
        source = OrganizationSourceFactory(id=100)
        user = UserProfileFactory(is_superuser=True, is_staff=True, username='soop')

        response = self.client.post(
            source.url + 'concepts/indexes/',
            HTTP_AUTHORIZATION=f'Token {user.get_token()}'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data, {'state': 'PENDING', 'username': 'soop', 'task': 'task-id-123', 'queue': 'default'})
        index_source_concepts_task_mock.delay.assert_called_once_with(100)


class SourceVersionProcessingViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory()
        self.token = self.source.created_by.get_token()

    @patch('core.common.models.AsyncResult.failed')
    @patch('core.common.models.AsyncResult.successful')
    def test_get_200(self, async_result_success_mock, async_result_failure_mock):
        async_result_success_mock.return_value = False
        async_result_failure_mock.return_value = False

        response = self.client.get(
            self.source.uri + 'HEAD/processing/',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'False')

        self.source.add_processing("Task123")

        response = self.client.get(
            self.source.uri + 'HEAD/processing/',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'True')

        response = self.client.get(
            self.source.uri + 'HEAD/processing/?debug=true',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(is_processing=True, process_ids=['Task123']))

    @patch('core.common.models.AsyncResult.failed')
    @patch('core.common.models.AsyncResult.successful')
    def test_post_200(self, async_result_success_mock, async_result_failure_mock):
        async_result_success_mock.return_value = False
        async_result_failure_mock.return_value = False

        self.source.add_processing("Task123")
        self.assertTrue(self.source.is_processing)

        response = self.client.post(
            self.source.uri + 'HEAD/processing/',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 200)
        self.source.refresh_from_db()
        self.assertFalse(self.source.is_processing)

        response = self.client.post(
            self.source.uri + 'HEAD/processing/',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 200)
        self.source.refresh_from_db()
        self.assertFalse(self.source.is_processing)


class SourceMappedSourcesListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory()
        self.token = self.source.created_by.get_token()

    def test_get_404(self):
        response = self.client.get(
            '/orgs/my/sources/empty/mapped-sources/',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.sources.views.Source.get_mapped_sources')
    def test_get_200(self, get_mapped_sources_mock):
        get_mapped_sources_mock.return_value = Source.objects.none()

        response = self.client.get(
            self.source.url + 'mapped-sources/',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)
        get_mapped_sources_mock.assert_called_once()

    @patch('core.sources.views.Source.get_mapped_sources')
    def test_get_200_with_data(self, get_mapped_sources_mock):
        source2 = OrganizationSourceFactory(mnemonic='source2')
        get_mapped_sources_mock.return_value = Source.objects.filter(id=source2.id)

        response = self.client.get(
            self.source.url + 'mapped-sources/',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['url'], source2.url)
        get_mapped_sources_mock.assert_called_once()

    def test_post_405(self):
        response = self.client.post(
            self.source.url + 'mapped-sources/',
            dict(default_locale='en'),
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )

        self.assertEqual(response.status_code, 405)


class SourceConceptsCloneViewTest(OCLAPITestCase):
    def setUp(self):
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.concept = ConceptFactory()
        self.clone_to_source = OrganizationSourceFactory()

    def test_post_bad_request(self):
        response = self.client.post(
            self.clone_to_source.uri + 'concepts/$clone/',
            {'foo': 'bar'},
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )

        self.assertEqual(response.status_code, 400)

    @patch('core.bundles.models.Bundle.clone')
    def test_post_success(self, bundle_clone_mock):
        parameters = {'mapTypes': 'Q-AND-A,CONCEPT-SET'}
        bundle_clone_mock.return_value = Bundle(
            root=self.concept, repo_version=self.concept.parent, params=parameters, verbose=False
        )

        response = self.client.post(
            self.clone_to_source.uri + 'concepts/$clone/',
            {'expressions': [self.concept.uri, '/orgs/MyOrg/sources/MySource/concepts/123/'], 'parameters': parameters},
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                self.concept.uri: {
                    'status': 200,
                    'bundle': {
                        'resourceType': 'Bundle',
                        'type': 'searchset',
                        'meta': ANY,
                        'total': None,
                        'entry': [],
                        'requested_url': None,
                        'repo_version_url': self.concept.parent.uri + 'HEAD/'
                    }
                },
                '/orgs/MyOrg/sources/MySource/concepts/123/': {
                    'status': 404,
                    'errors': ['Concept to clone with expression /orgs/MyOrg/sources/MySource/concepts/123/ not found.']
                }
            }
        )
        bundle_clone_mock.assert_called_once_with(
            self.concept, self.concept.parent, self.clone_to_source, self.user, ANY, False,
            **parameters
        )
