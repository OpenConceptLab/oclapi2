import json
import zipfile

from celery_once import AlreadyQueued
from django.db import transaction
from mock import patch, Mock, ANY
from rest_framework.exceptions import ErrorDetail

from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.tasks import export_source
from core.common.tests import OCLAPITestCase
from core.common.utils import get_latest_dir_in_path
from core.concepts.serializers import ConceptVersionDetailSerializer
from core.concepts.tests.factories import ConceptFactory
from core.mappings.serializers import MappingDetailSerializer
from core.mappings.tests.factories import MappingFactory
from core.orgs.models import Organization
from core.sources.models import Source
from core.sources.serializers import SourceDetailSerializer
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
            'external_id': '', 'id': 's2', 'supported_locales': 'af,am'
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

    def test_post_201(self):
        sources_url = "/orgs/{}/sources/".format(self.organization.mnemonic)

        response = self.client.post(
            sources_url,
            self.source_payload,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertListEqual(
            list(response.data.keys()),
            [
                'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'source_type',
                'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
                'url', 'owner', 'owner_type', 'owner_url', 'versions', 'created_on', 'updated_on', 'created_by',
                'updated_by', 'extras', 'external_id', 'versions_url', 'version', 'concepts_url', 'mappings_url',
                'active_concepts', 'active_mappings',
            ]
        )
        source = Source.objects.last()

        self.assertEqual(response.data['uuid'], str(source.id))
        self.assertEqual(response.data['short_code'], source.mnemonic)
        self.assertEqual(response.data['full_name'], source.full_name)
        self.assertEqual(response.data['owner_url'], source.parent.uri)
        self.assertEqual(response.data['url'], source.uri)

    def test_post_400(self):
        sources_url = "/orgs/{}/sources/".format(self.organization.mnemonic)

        response = self.client.post(
            sources_url,
            {**self.source_payload, 'name': None},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data.keys()), ['name'])


class SourceCreateUpdateDestroyViewTest(OCLAPITestCase):
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

        source = OrganizationSourceFactory(organization=self.organization)
        response = self.client.get(
            source.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(source.id))
        self.assertEqual(response.data['short_code'], source.mnemonic)

    def test_put_200(self):
        source = OrganizationSourceFactory(organization=self.organization)
        self.assertTrue(source.is_head)
        self.assertEqual(source.versions.count(), 1)

        sources_url = "/orgs/{}/sources/{}/".format(self.organization.mnemonic, source.mnemonic)

        response = self.client.put(
            sources_url,
            {'full_name': 'Full name'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertListEqual(
            list(response.data.keys()),
            [
                'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'source_type',
                'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
                'url', 'owner', 'owner_type', 'owner_url', 'versions', 'created_on', 'updated_on', 'created_by',
                'updated_by', 'extras', 'external_id', 'versions_url', 'version', 'concepts_url', 'mappings_url',
                'active_concepts', 'active_mappings',
            ]
        )
        source = Source.objects.last()

        self.assertTrue(source.is_head)
        self.assertEqual(source.versions.count(), 1)
        self.assertEqual(response.data['full_name'], source.full_name)
        self.assertEqual(response.data['full_name'], 'Full name')

    def test_delete_204(self):
        source = OrganizationSourceFactory(organization=self.organization)
        response = self.client.delete(
            source.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Source.objects.filter(id=source.id).exists())


class SourceVersionListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.source = OrganizationSourceFactory(organization=self.organization)

    def test_get_200(self):
        response = self.client.get(
            '/orgs/{}/sources/{}/versions/'.format(self.organization.mnemonic, self.source.mnemonic),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['version'], 'HEAD')

        response = self.client.get(
            '/orgs/{}/sources/{}/versions/?verbose=true'.format(self.organization.mnemonic, self.source.mnemonic),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['version'], 'HEAD')
        self.assertEqual(response.data[0]['concepts_url'], self.source.concepts_url)

    @patch('core.sources.views.export_source')
    def test_post_201(self, export_source_mock):
        response = self.client.post(
            '/orgs/{}/sources/{}/versions/'.format(self.organization.mnemonic, self.source.mnemonic),
            dict(id='v1', description='Version 1'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['uuid'])
        self.assertEqual(response.data['version'], 'v1')
        self.assertEqual(self.source.versions.count(), 2)
        export_source_mock.delay.assert_called_once_with(response.data['uuid'])

    def test_post_409(self):
        OrganizationSourceFactory(version='v1', organization=self.organization, mnemonic=self.source.mnemonic)
        with transaction.atomic():
            response = self.client.post(
                '/orgs/{}/sources/{}/versions/'.format(self.organization.mnemonic, self.source.mnemonic),
                dict(id='v1', description='Version 1'),
                HTTP_AUTHORIZATION='Token ' + self.token,
                format='json'
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['detail'], "Source version 'v1' already exist.")

    @patch('core.sources.views.export_source')
    def test_post_400(self, export_source_mock):
        response = self.client.post(
            '/orgs/{}/sources/{}/versions/'.format(self.organization.mnemonic, self.source.mnemonic),
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
            '/orgs/{}/sources/{}/latest/'.format(self.organization.mnemonic, self.source.mnemonic),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], 'v1')
        self.assertEqual(response.data['uuid'], str(self.latest_version.id))
        self.assertEqual(response.data['short_code'], self.source.mnemonic)

    def test_put_200(self):
        self.assertIsNone(self.latest_version.external_id)

        external_id = '123'
        response = self.client.put(
            '/orgs/{}/sources/{}/latest/'.format(self.organization.mnemonic, self.source.mnemonic),
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
            '/orgs/{}/sources/{}/latest/'.format(self.organization.mnemonic, self.source.mnemonic),
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
        self.assertEqual(self.source.extras, dict())
        self.assertEqual(self.source_v1.extras, dict())

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
        self.assertEqual(self.source.extras, dict())

    def test_put_400(self):
        response = self.client.put(
            self.source_v1.uri,
            dict(id=None),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'id': [ErrorDetail(string='This field may not be null.', code='null')]})

    @patch('core.common.services.S3.delete_objects', Mock())
    def test_version_delete_204(self):
        self.assertEqual(self.source.versions.count(), 2)

        response = self.client.delete(
            self.source_v1.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.source.versions.count(), 1)
        self.assertFalse(self.source.versions.filter(version='v1').exists())

    @patch('core.common.services.S3.delete_objects', Mock())
    def test_version_delete_400(self):  # sources content referred in a private collection
        concept = ConceptFactory(parent=self.source_v1)

        collection = OrganizationCollectionFactory(public_access='None')
        collection.add_references([concept.uri])
        self.assertEqual(collection.concepts.count(), 1)
        self.assertEqual(collection.concepts.first(), concept.get_latest_version())

        response = self.client.delete(
            self.source_v1.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'detail': ['Could not delete. Some of the content is referred privately.']})
        self.assertEqual(self.source.versions.count(), 2)
        self.assertTrue(self.source.versions.filter(version='v1').exists())


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

    def test_get_404(self):
        response = self.client.get(
            '/sources/source1/v2/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.common.services.S3.url_for')
    def test_get_204(self, s3_url_for_mock):
        Source.objects.filter(id=self.source_v1.id).update(last_child_update='2020-01-01 10:00:00')

        s3_url_for_mock.return_value = None

        response = self.client.get(
            '/sources/source1/v1/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        s3_url_for_mock.assert_called_once_with("username/source1_v1.20200101100000.zip")

    @patch('core.common.services.S3.url_for')
    def test_get_303(self, s3_url_for_mock):
        Source.objects.filter(id=self.source_v1.id).update(last_child_update='2020-01-01 10:00:00')

        s3_url = 'https://s3/username/source1_v1.20200101100000.zip'
        s3_url_for_mock.return_value = s3_url

        response = self.client.get(
            '/sources/source1/v1/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['Location'], s3_url)
        self.assertEqual(response['Last-Updated'], '2020-01-01T10:00:00+00:00')
        self.assertEqual(response['Last-Updated-Timezone'], 'America/New_York')
        s3_url_for_mock.assert_called_once_with("username/source1_v1.20200101100000.zip")

    def test_get_405(self):
        response = self.client.get(
            '/sources/source1/HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    def test_post_405(self):
        response = self.client.post(
            '/sources/source1/HEAD/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    @patch('core.common.services.S3.url_for')
    def test_post_303(self, s3_url_for_mock):
        Source.objects.filter(id=self.source_v1.id).update(last_child_update='2020-01-01 10:00:00')
        s3_url = 'https://s3/username/source1_v1.20200101100000.zip'
        s3_url_for_mock.return_value = s3_url
        response = self.client.post(
            '/sources/source1/v1/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['URL'], self.source_v1.uri + 'export/')
        s3_url_for_mock.assert_called_once_with("username/source1_v1.20200101100000.zip")

    @patch('core.sources.views.export_source')
    @patch('core.common.services.S3.url_for')
    def test_post_202(self, s3_url_for_mock, export_source_mock):
        Source.objects.filter(id=self.source_v1.id).update(last_child_update='2020-01-01 10:00:00')

        s3_url_for_mock.return_value = None
        export_source_mock.delay = Mock()
        response = self.client.post(
            '/sources/source1/v1/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        s3_url_for_mock.assert_called_once_with("username/source1_v1.20200101100000.zip")
        export_source_mock.delay.assert_called_once_with(self.source_v1.id)

    @patch('core.sources.views.export_source')
    @patch('core.common.services.S3.url_for')
    def test_post_409(self, s3_url_for_mock, export_source_mock):
        Source.objects.filter(id=self.source_v1.id).update(last_child_update='2020-01-01 10:00:00')

        s3_url_for_mock.return_value = None
        export_source_mock.delay.side_effect = AlreadyQueued('already-queued')
        response = self.client.post(
            '/sources/source1/v1/export/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        s3_url_for_mock.assert_called_once_with("username/source1_v1.20200101100000.zip")
        export_source_mock.delay.assert_called_once_with(self.source_v1.id)


class ExportSourceTaskTest(OCLAPITestCase):
    @patch('core.common.utils.S3')
    def test_export_source(self, s3_mock):  # pylint: disable=too-many-locals
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

        self.assertEqual(exported_data, {**SourceDetailSerializer(source_v1).data, 'concepts': ANY, 'mappings': ANY})

        exported_concepts = exported_data['concepts']
        expected_concepts = ConceptVersionDetailSerializer([concept2, concept1], many=True).data

        self.assertEqual(len(exported_concepts), 2)
        self.assertIn(expected_concepts[0], exported_concepts)
        self.assertIn(expected_concepts[1], exported_concepts)

        exported_mappings = exported_data['mappings']
        expected_mappings = MappingDetailSerializer([mapping], many=True).data

        self.assertEqual(len(exported_mappings), 1)
        self.assertEqual(expected_mappings, exported_mappings)

        s3_upload_key = source_v1.export_path
        s3_mock.upload_file.assert_called_once_with(
            key=s3_upload_key, file_path=latest_temp_dir + '/export.zip', binary=True
        )
        s3_mock.url_for.assert_called_once_with(s3_upload_key)

        import shutil
        shutil.rmtree(latest_temp_dir)
