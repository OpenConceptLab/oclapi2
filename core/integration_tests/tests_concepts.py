from django.core.management import call_command
from rest_framework.test import APITestCase

from core.common.constants import OCL_ORG_ID, SUPER_ADMIN_USER_ID
from core.concepts.models import Concept, LocalizedText
from core.concepts.tests.factories import ConceptFactory, LocalizedTextFactory
from core.orgs.models import Organization
from core.sources.models import Source
from core.sources.tests.factories import SourceFactory
from core.users.models import UserProfile


class ConceptCreateUpdateDestroyViewTest(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")

    def setUp(self):
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.concepts_payload = {
            'datatype': 'Coded',
            'concept_class': 'Procedure',
            'extras': {'foo': 'bar'},
            'descriptions': [{
                'locale': 'ab', 'locale_preferred': True, 'description': 'c1 desc', 'description_type': 'None'
            }],
            'external_id': '',
            'id': 'c1',
            'names': [{
                'locale': 'ab', 'locale_preferred': True, 'name': 'c1 name', 'name_type': 'Fully Specified'
            }]
        }

    def tearDown(self):
        Concept.objects.all().delete()
        LocalizedText.objects.all().delete()
        Source.objects.all().delete()
        Organization.objects.exclude(id=OCL_ORG_ID).all().delete()
        UserProfile.objects.exclude(id=SUPER_ADMIN_USER_ID).all().delete()

    def test_post_201(self):
        source = SourceFactory(organization=self.organization)
        concepts_url = "/orgs/{}/sources/{}/concepts/".format(self.organization.mnemonic, source.mnemonic)

        response = self.client.post(
            concepts_url,
            self.concepts_payload,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertListEqual(
            list(response.data.keys()),
            ['uuid',
             'id',
             'external_id',
             'concept_class',
             'datatype',
             'url',
             'retired',
             'source',
             'owner',
             'owner_type',
             'owner_url',
             'display_name',
             'display_locale',
             'names',
             'descriptions',
             'created_on',
             'updated_on',
             'versions_url',
             'version',
             'extras',
             'parent_id',
             'name',
             'type',
             'update_comment',
             'version_url',
             'mappings']
        )

        concept = Concept.objects.last()

        self.assertTrue(concept.is_versioned_object)
        self.assertTrue(concept.is_latest_version)
        self.assertEqual(concept.versions.count(), 1)
        self.assertEqual(response.data['uuid'], str(concept.id))
        self.assertEqual(response.data['datatype'], 'Coded')
        self.assertEqual(response.data['concept_class'], 'Procedure')
        self.assertEqual(response.data['url'], concept.uri)
        self.assertFalse(response.data['retired'])
        self.assertEqual(response.data['source'], source.mnemonic)
        self.assertEqual(response.data['owner'], self.organization.mnemonic)
        self.assertEqual(response.data['owner_type'], "Organization")
        self.assertEqual(response.data['owner_url'], self.organization.uri)
        self.assertEqual(response.data['display_name'], 'c1 name')
        self.assertEqual(response.data['display_locale'], 'ab')
        self.assertEqual(response.data['versions_url'], concept.uri + 'versions/')
        self.assertEqual(response.data['version'], str(concept.id))
        self.assertEqual(response.data['extras'], dict(foo='bar'))
        self.assertEqual(response.data['parent_id'], str(source.id))
        self.assertEqual(response.data['name'], 'c1')
        self.assertEqual(response.data['type'], 'Concept')
        self.assertEqual(response.data['version_url'], concept.uri)
        self.assertEqual(response.data['mappings'], [])

    def test_post_400(self):
        source = SourceFactory(organization=self.organization)
        concepts_url = "/orgs/{}/sources/{}/concepts/".format(self.organization.mnemonic, source.mnemonic)

        response = self.client.post(
            concepts_url,
            {**self.concepts_payload.copy(), 'datatype': ''},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertListEqual(
            list(response.data.keys()),
            ['datatype']
        )

    def test_put_200(self):
        source = SourceFactory(organization=self.organization)
        concept = ConceptFactory(parent=source)
        concepts_url = "/orgs/{}/sources/{}/concepts/{}/".format(
            self.organization.mnemonic, source.mnemonic, concept.mnemonic
        )

        response = self.client.put(
            concepts_url,
            {**self.concepts_payload, 'datatype': 'None', 'update_comment': 'Updated datatype'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertListEqual(
            list(response.data.keys()),
            ['uuid',
             'id',
             'external_id',
             'concept_class',
             'datatype',
             'url',
             'retired',
             'source',
             'owner',
             'owner_type',
             'owner_url',
             'display_name',
             'display_locale',
             'names',
             'descriptions',
             'created_on',
             'updated_on',
             'versions_url',
             'version',
             'extras',
             'parent_id',
             'name',
             'type',
             'update_comment',
             'version_url',
             'mappings']
        )

        version = Concept.objects.last()
        concept.refresh_from_db()

        self.assertFalse(version.is_versioned_object)
        self.assertTrue(version.is_latest_version)
        self.assertEqual(version.versions.count(), 2)
        self.assertEqual(response.data['uuid'], str(version.id))
        self.assertEqual(response.data['datatype'], 'None')
        self.assertEqual(response.data['update_comment'], 'Updated datatype')
        self.assertEqual(response.data['concept_class'], 'Procedure')
        self.assertEqual(response.data['url'], version.uri)
        self.assertFalse(response.data['retired'])
        self.assertEqual(response.data['source'], source.mnemonic)
        self.assertEqual(response.data['owner'], self.organization.mnemonic)
        self.assertEqual(response.data['owner_type'], "Organization")
        self.assertEqual(response.data['owner_url'], self.organization.uri)
        self.assertEqual(response.data['display_name'], 'c1 name')
        self.assertEqual(response.data['display_locale'], 'ab')
        self.assertEqual(response.data['versions_url'], concept.uri + 'versions/')
        self.assertEqual(response.data['version'], str(version.id))
        self.assertEqual(response.data['extras'], dict(foo='bar'))
        self.assertEqual(response.data['parent_id'], str(source.id))
        self.assertEqual(response.data['type'], 'Concept')
        self.assertEqual(response.data['version_url'], version.uri)
        self.assertEqual(response.data['mappings'], [])
        self.assertTrue(concept.is_versioned_object)
        self.assertEqual(concept.datatype, "None")

    def test_put_400(self):
        source = SourceFactory(organization=self.organization)
        concept = ConceptFactory(parent=source)
        concepts_url = "/orgs/{}/sources/{}/concepts/{}/".format(
            self.organization.mnemonic, source.mnemonic, concept.mnemonic
        )

        response = self.client.put(
            concepts_url,
            {**self.concepts_payload, 'concept_class': '', 'update_comment': 'Updated concept_class'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data.keys()), ['concept_class'])

    def test_put_404(self):
        source = SourceFactory(organization=self.organization)
        concepts_url = "/orgs/{}/sources/{}/concepts/foobar/".format(
            self.organization.mnemonic, source.mnemonic
        )

        response = self.client.put(
            concepts_url,
            {**self.concepts_payload, 'concept_class': '', 'update_comment': 'Updated concept_class'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_204(self):
        source = SourceFactory(organization=self.organization)
        names = [LocalizedTextFactory()]
        concept = ConceptFactory(parent=source, names=names)
        concepts_url = "/orgs/{}/sources/{}/concepts/{}/".format(
            self.organization.mnemonic, source.mnemonic, concept.mnemonic
        )

        response = self.client.delete(
            concepts_url,
            {'update_comment': 'Deleting it'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)

        concept.refresh_from_db()

        self.assertEqual(concept.versions.count(), 2)
        latest_version = concept.versions.order_by('-created_at').first()
        self.assertTrue(latest_version.retired)
        self.assertTrue(concept.retired)
        self.assertTrue(latest_version.comment, 'Deleting it')

    def test_delete_404(self):
        source = SourceFactory(organization=self.organization)
        concepts_url = "/orgs/{}/sources/{}/concepts/foobar/".format(
            self.organization.mnemonic, source.mnemonic
        )

        response = self.client.delete(
            concepts_url,
            {'update_comment': 'Deleting it'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_400(self):
        source = SourceFactory(organization=self.organization)
        names = [LocalizedTextFactory()]
        concept = ConceptFactory(parent=source, names=names, retired=True)
        concepts_url = "/orgs/{}/sources/{}/concepts/{}/".format(
            self.organization.mnemonic, source.mnemonic, concept.mnemonic
        )

        response = self.client.delete(
            concepts_url + '?includeRetired=true',
            {'update_comment': 'Deleting it'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'__all__': 'Concept is already retired'})
