from django.core.management import call_command
from rest_framework.test import APITestCase

from core.common.constants import OCL_ORG_ID, SUPER_ADMIN_USER_ID
from core.common.tests import PauseElasticSearchIndex
from core.orgs.models import Organization
from core.sources.models import Source
from core.sources.tests.factories import SourceFactory
from core.users.models import UserProfile


class SourceCreateUpdateDestroyViewTest(APITestCase, PauseElasticSearchIndex):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")

    def setUp(self):
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.source_payload = {
            'website': '', 'custom_validation_schema': 'None', 'name': 's2', 'default_locale': 'ab',
            'short_code': 's2', 'description': '', 'source_type': '', 'full_name': 'source 2', 'public_access': 'View',
            'external_id': '', 'id': 's2', 'supported_locales': 'af,am'
        }

    def tearDown(self):
        Source.objects.all().delete()
        Organization.objects.exclude(id=OCL_ORG_ID).all().delete()
        UserProfile.objects.exclude(id=SUPER_ADMIN_USER_ID).all().delete()

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
                'updated_by', 'extras', 'external_id', 'versions_url', 'version', 'concepts_url'
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

    def test_put_200(self):
        source = SourceFactory(organization=self.organization)
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
                'updated_by', 'extras', 'external_id', 'versions_url', 'version', 'concepts_url'
            ]
        )
        source = Source.objects.last()

        self.assertTrue(source.is_head)
        self.assertEqual(source.versions.count(), 1)
        self.assertEqual(response.data['full_name'], source.full_name)
        self.assertEqual(response.data['full_name'], 'Full name')

    def test_delete_400(self):
        source = SourceFactory(organization=self.organization)
        sources_url = "/orgs/{}/sources/{}/".format(
            self.organization.mnemonic, source.mnemonic
        )
        response = self.client.delete(
            sources_url,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'detail': ['Cannot delete only version.']})
        self.assertEqual(source.versions.count(), 1)

    def test_version_delete_204(self):
        source = SourceFactory(organization=self.organization)
        source_v1 = SourceFactory(mnemonic=source.mnemonic, organization=source.organization, version='v1')
        self.assertEqual(source.versions.count(), 2)

        sources_url = "/orgs/{}/sources/{}/{}/".format(
            self.organization.mnemonic, source.mnemonic, source_v1.version
        )
        response = self.client.delete(
            sources_url,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(source.versions.count(), 1)
        self.assertFalse(source.versions.filter(version='v1').exists())
