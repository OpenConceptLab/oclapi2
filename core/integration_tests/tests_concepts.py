from unittest.mock import patch

from django.conf import settings
from mock import ANY

from core.bundles.models import Bundle
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import OPENMRS_VALIDATION_SCHEMA
from core.common.tasks import rebuild_indexes
from core.common.tests import OCLAPITestCase
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory, ConceptDescriptionFactory
from core.mappings.tests.factories import MappingFactory
from core.orgs.models import Organization
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class ConceptCreateUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.source = OrganizationSourceFactory(organization=self.organization)
        self.concept_payload = {
            'datatype': 'Coded',
            'concept_class': 'Procedure',
            'extras': {'foo': 'bar'},
            'descriptions': [{
                'locale': 'en', 'locale_preferred': True, 'description': 'c1 desc', 'description_type': 'None'
            }],
            'external_id': '',
            'id': 'c1',
            'names': [{
                'locale': 'en', 'locale_preferred': True, 'name': 'c1 name', 'name_type': 'Fully Specified'
            }]
        }

    def test_get_200(self):
        response = self.client.get(
            self.source.concepts_url,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        ConceptFactory(parent=self.source)

        response = self.client.get(
            self.source.concepts_url,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_post_201(self):
        concepts_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/"

        response = self.client.post(
            concepts_url,
            self.concept_payload,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertListEqual(
            sorted(list(response.data.keys())),
            sorted([
                'uuid',
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
                'type',
                'update_comment',
                'version_url',
                'updated_by',
                'created_by',
                'parent_concept_urls',
                'public_can_view',
                'checksums',
                'versioned_object_id',
            ])
        )

        concept = Concept.objects.first()
        latest_version = Concept.objects.last()

        self.assertFalse(latest_version.is_versioned_object)
        self.assertTrue(latest_version.is_latest_version)

        self.assertTrue(concept.is_versioned_object)
        self.assertFalse(concept.is_latest_version)

        self.assertEqual(concept.versions.count(), 1)
        self.assertEqual(response.data['uuid'], str(concept.id))
        self.assertEqual(response.data['datatype'], 'Coded')
        self.assertEqual(response.data['concept_class'], 'Procedure')
        self.assertEqual(response.data['url'], concept.uri)
        self.assertFalse(response.data['retired'])
        self.assertEqual(response.data['source'], self.source.mnemonic)
        self.assertEqual(response.data['owner'], self.organization.mnemonic)
        self.assertEqual(response.data['owner_type'], "Organization")
        self.assertEqual(response.data['owner_url'], self.organization.uri)
        self.assertEqual(response.data['display_name'], 'c1 name')
        self.assertEqual(response.data['display_locale'], 'en')
        self.assertEqual(response.data['versions_url'], concept.uri + 'versions/')
        self.assertEqual(response.data['version'], str(concept.id))
        self.assertEqual(response.data['extras'], {'foo': 'bar'})
        self.assertEqual(response.data['type'], 'Concept')
        self.assertEqual(response.data['version_url'], latest_version.uri)

        response = self.client.post(
            concepts_url,
            self.concept_payload,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'__all__': ['Concept ID must be unique within a source.']})

    def test_post_400(self):
        concepts_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/"

        response = self.client.post(
            concepts_url,
            {**self.concept_payload.copy(), 'datatype': ''},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertListEqual(
            list(response.data.keys()),
            ['datatype']
        )

    def test_put_200(self):
        concept = ConceptFactory(parent=self.source)
        self.assertEqual(concept.versions.count(), 1)
        concepts_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/{concept.mnemonic}/"

        response = self.client.put(
            concepts_url,
            {**self.concept_payload, 'datatype': 'None', 'update_comment': 'Updated datatype'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertListEqual(
            sorted(list(response.data.keys())),
            sorted(['uuid',
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
                    'type',
                    'update_comment',
                    'version_url',
                    'updated_by',
                    'created_by',
                    'parent_concept_urls',
                    'public_can_view',
                    'checksums',
                    'versioned_object_id'])
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
        self.assertEqual(response.data['url'], concept.uri)
        self.assertEqual(response.data['url'], version.versioned_object.uri)
        self.assertEqual(response.data['version_url'], version.uri)
        self.assertFalse(response.data['retired'])
        self.assertEqual(response.data['source'], self.source.mnemonic)
        self.assertEqual(response.data['owner'], self.organization.mnemonic)
        self.assertEqual(response.data['owner_type'], "Organization")
        self.assertEqual(response.data['owner_url'], self.organization.uri)
        self.assertEqual(response.data['display_name'], 'c1 name')
        self.assertEqual(response.data['display_locale'], 'en')
        self.assertEqual(response.data['versions_url'], concept.uri + 'versions/')
        self.assertEqual(response.data['version'], str(version.id))
        self.assertEqual(response.data['extras'], {'foo': 'bar'})
        self.assertEqual(response.data['type'], 'Concept')
        self.assertEqual(response.data['version_url'], version.uri)
        self.assertTrue(concept.is_versioned_object)
        self.assertEqual(concept.datatype, "None")

    def test_put_200_openmrs_schema(self):  # pylint: disable=too-many-statements
        self.create_lookup_concept_classes()
        source = OrganizationSourceFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        name = ConceptNameFactory.build(locale='fr')
        concept = ConceptFactory(parent=source, names=[name])
        self.assertEqual(concept.versions.count(), 1)
        response = self.client.put(
            concept.uri,
            {**self.concept_payload, 'datatype': 'None', 'update_comment': 'Updated datatype'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertListEqual(
            sorted(list(response.data.keys())),
            sorted(['uuid',
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
                    'type',
                    'update_comment',
                    'version_url',
                    'updated_by',
                    'created_by',
                    'parent_concept_urls',
                    'public_can_view',
                    'checksums',
                    'versioned_object_id'])
        )

        names = response.data['names']

        version = Concept.objects.last()
        concept.refresh_from_db()

        self.assertFalse(version.is_versioned_object)
        self.assertTrue(version.is_latest_version)
        self.assertEqual(version.versions.count(), 2)
        self.assertEqual(response.data['uuid'], str(version.id))
        self.assertEqual(response.data['datatype'], 'None')
        self.assertEqual(response.data['update_comment'], 'Updated datatype')
        self.assertEqual(response.data['concept_class'], 'Procedure')
        self.assertEqual(response.data['url'], concept.uri)
        self.assertEqual(response.data['url'], version.versioned_object.uri)
        self.assertEqual(response.data['version_url'], version.uri)
        self.assertFalse(response.data['retired'])
        self.assertEqual(response.data['source'], source.mnemonic)
        self.assertEqual(response.data['owner'], source.organization.mnemonic)
        self.assertEqual(response.data['owner_type'], "Organization")
        self.assertEqual(response.data['owner_url'], source.organization.uri)
        self.assertEqual(response.data['display_name'], 'c1 name')
        self.assertEqual(response.data['display_locale'], 'en')
        self.assertEqual(response.data['versions_url'], concept.uri + 'versions/')
        self.assertEqual(response.data['version'], str(version.id))
        self.assertEqual(response.data['extras'], {'foo': 'bar'})
        self.assertEqual(response.data['type'], 'Concept')
        self.assertEqual(response.data['version_url'], version.uri)
        self.assertTrue(concept.is_versioned_object)
        self.assertEqual(concept.datatype, "None")

        # same names in update
        names[0]['uuid'] = str(name.id)
        [name.pop('type', None) for name in names]  # pylint: disable=expression-not-assigned
        response = self.client.put(
            concept.uri,
            {**self.concept_payload, 'names': names, 'datatype': 'Numeric', 'update_comment': 'Updated datatype'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)

        concept.refresh_from_db()
        self.assertEqual(concept.datatype, "Numeric")
        self.assertEqual(concept.names.count(), 1)

        latest_version = concept.get_latest_version()
        prev_version = latest_version.prev_version
        self.assertEqual(latest_version.names.count(), 1)
        self.assertEqual(prev_version.names.count(), 1)
        self.assertEqual(prev_version.names.first().name, latest_version.names.first().name)
        self.assertEqual(prev_version.names.first().locale, latest_version.names.first().locale)

    def test_put_400(self):
        concept = ConceptFactory(parent=self.source)
        concepts_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/{concept.mnemonic}/"

        response = self.client.put(
            concepts_url,
            {**self.concept_payload, 'concept_class': '', 'update_comment': 'Updated concept_class'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data.keys()), ['concept_class'])

    def test_put_404(self):
        concepts_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/foobar/"

        response = self.client.put(
            concepts_url,
            {**self.concept_payload, 'concept_class': '', 'update_comment': 'Updated concept_class'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_204(self):
        names = [ConceptNameFactory.build()]
        concept = ConceptFactory(parent=self.source, names=names)
        concepts_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/{concept.mnemonic}/"

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

    def test_db_hard_delete_204(self):
        names = [ConceptNameFactory.build()]
        concept = ConceptFactory(parent=self.source, names=names)
        concepts_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/{concept.mnemonic}/"

        response = self.client.delete(
            concepts_url + '?db=true&hardDelete=true',
            {'update_comment': 'Deleting it'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Concept.objects.filter(id=concept.id).exists())
        self.assertFalse(Concept.objects.filter(mnemonic=concept.mnemonic).exists())

    def test_hard_delete_204(self):
        names = [ConceptNameFactory.build()]
        concept = ConceptFactory(parent=self.source, names=names)
        concepts_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/{concept.mnemonic}/"

        response = self.client.delete(
            concepts_url + '?hardDelete=true',
            {'update_comment': 'Deleting it'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Concept.objects.filter(id=concept.id).exists())
        self.assertFalse(Concept.objects.filter(mnemonic=concept.mnemonic).exists())

    @patch('core.concepts.views.delete_concept')
    def test_async_hard_delete_204(self, delete_conceot_task_mock):
        names = [ConceptNameFactory.build()]
        concept = ConceptFactory(parent=self.source, names=names)
        concepts_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/{concept.mnemonic}/"

        response = self.client.delete(
            concepts_url + '?async=true&hardDelete=true',
            {'update_comment': 'Deleting it'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        delete_conceot_task_mock.delay.assert_called_once_with(concept.id)

    def test_delete_404(self):
        concepts_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/foobar/"

        response = self.client.delete(
            concepts_url,
            {'update_comment': 'Deleting it'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_400(self):
        names = [ConceptNameFactory.build()]
        concept = ConceptFactory(parent=self.source, names=names, retired=True)
        concepts_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/{concept.mnemonic}/"

        response = self.client.delete(
            concepts_url + '?includeRetired=true',
            {'update_comment': 'Deleting it'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'__all__': 'Concept is already retired'})

    def test_extras_get_200(self):
        names = [ConceptNameFactory.build()]
        concept = ConceptFactory(parent=self.source, names=names, extras={'foo': 'bar'})
        extras_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}" \
            f"/concepts/{concept.mnemonic}/extras/"

        response = self.client.get(
            extras_url,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'foo': 'bar'})

    def test_extra_get_200(self):
        names = [ConceptNameFactory.build()]
        concept = ConceptFactory(parent=self.source, names=names, extras={'foo': 'bar', 'tao': 'ching'})

        def extra_url(extra):
            return f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}" \
                f"/concepts/{concept.mnemonic}/extras/{extra}/"

        response = self.client.get(
            extra_url('tao'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'tao': 'ching'})

        response = self.client.get(
            extra_url('foo'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'foo': 'bar'})

        response = self.client.get(
            extra_url('bar'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, {'detail': 'Not found.'})

    def test_extra_put_200(self):
        names = [ConceptNameFactory.build()]
        concept = ConceptFactory(parent=self.source, names=names, extras={'foo': 'bar', 'tao': 'ching'})

        def extra_url(extra):
            return f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}" \
                f"/concepts/{concept.mnemonic}/extras/{extra}/"

        response = self.client.put(
            extra_url('tao'),
            {'tao': 'te-ching'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)

        concept.refresh_from_db()
        self.assertTrue(concept.extras['tao'] == response.data['tao'] == 'te-ching')
        self.assertEqual(concept.versions.count(), 2)

        latest_version = concept.versions.order_by('-created_at').first()
        self.assertEqual(latest_version.extras, {'foo': 'bar', 'tao': 'te-ching'})
        self.assertEqual(latest_version.comment, 'Updated extras: tao=te-ching.')

    def test_extra_put_400(self):
        names = [ConceptNameFactory.build()]
        concept = ConceptFactory(parent=self.source, names=names, extras={'foo': 'bar', 'tao': 'ching'})

        def extra_url(extra):
            return f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}" \
                f"/concepts/{concept.mnemonic}/extras/{extra}/"

        response = self.client.put(
            extra_url('tao'),
            {'tao': None},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, ['Must specify tao param in body.'])
        concept.refresh_from_db()
        self.assertEqual(concept.extras, {'foo': 'bar', 'tao': 'ching'})

    def test_extra_delete_204(self):
        names = [ConceptNameFactory.build()]
        concept = ConceptFactory(parent=self.source, names=names, extras={'foo': 'bar', 'tao': 'ching'})
        self.assertEqual(concept.versions.count(), 1)

        def extra_url(extra):
            return f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}" \
                f"/concepts/{concept.mnemonic}/extras/{extra}/"

        response = self.client.delete(
            extra_url('tao'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)

        concept.refresh_from_db()
        self.assertFalse('tao' in concept.extras)
        self.assertEqual(concept.versions.count(), 2)

        latest_version = concept.get_latest_version()
        self.assertEqual(latest_version.extras, {'foo': 'bar'})
        self.assertEqual(latest_version.comment, 'Deleted extra tao.')

    def test_extra_delete_404(self):
        names = [ConceptNameFactory.build()]
        concept = ConceptFactory(parent=self.source, names=names, extras={'foo': 'bar', 'tao': 'ching'})

        def extra_url(extra):
            return f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}" \
                f"/concepts/{concept.mnemonic}/extras/{extra}/"

        response = self.client.delete(
            extra_url('bar'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_names_get_200(self):
        name = ConceptNameFactory.build()
        concept = ConceptFactory(parent=self.source, names=[name])

        response = self.client.get(
            f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/{concept.mnemonic}/names/",
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            dict(response.data[0]),
            {
                "uuid": str(name.id),
                "external_id": None,
                "type": 'ConceptName',
                "locale": name.locale,
                "locale_preferred": False,
                "name": name.name,
                "name_type": "FULLY_SPECIFIED"
            }
        )

    def test_names_post_201(self):
        name = ConceptNameFactory.build()
        concept = ConceptFactory(parent=self.source, names=[name])

        response = self.client.post(
            f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/{concept.mnemonic}/names/",
            {
                "type": 'ConceptName',
                "locale": 'en',
                "locale_preferred": False,
                "name": 'foo',
                "name_type": "Fully Specified"
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.data,
            {
                "uuid": ANY,
                "external_id": None,
                "type": 'ConceptName',
                "locale": 'en',
                "locale_preferred": False,
                "name": 'foo',
                "name_type": "Fully Specified"
            }
        )
        self.assertEqual(concept.names.count(), 2)

    def test_names_post_400(self):
        name = ConceptNameFactory.build()
        concept = ConceptFactory(parent=self.source, names=[name])

        response = self.client.post(
            f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}/concepts/{concept.mnemonic}/names/",
            {
                "type": 'ConceptName',
                "name": name.name,
                "name_type": "Fully Specified"
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data.keys()), ['locale'])

    def test_name_delete_204(self):
        name1 = ConceptNameFactory.build()
        name2 = ConceptNameFactory.build()
        concept = ConceptFactory(parent=self.source, names=[name1, name2])
        response = self.client.delete(
            f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}"
            f"/concepts/{concept.mnemonic}/names/{name2.id}/",
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(concept.versions.count(), 2)
        self.assertEqual(concept.names.count(), 1)
        self.assertEqual(concept.names.first().name, name1.name)

        latest_version = concept.get_latest_version()
        self.assertEqual(latest_version.names.count(), 1)
        self.assertEqual(latest_version.names.first().name, name1.name)
        self.assertEqual(latest_version.comment, f'Deleted {name2.name} in names.')

    def test_get_200_with_response_modes(self):
        ConceptFactory(parent=self.source, mnemonic='conceptA')
        response = self.client.get(
            "/concepts/",
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(response.data[0].keys()),
            sorted(['uuid', 'id', 'external_id', 'concept_class', 'datatype', 'url', 'retired', 'source',
                    'owner', 'owner_type', 'owner_url', 'display_name', 'display_locale', 'version', 'update_comment',
                    'locale', 'version_created_by', 'version_created_on', 'is_latest_version',
                    'versions_url', 'version_url', 'type', 'versioned_object_id'])
        )

        response = self.client.get(
            "/concepts/?verbose=true",
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(response.data[0].keys()),
            sorted(['uuid', 'id', 'external_id', 'concept_class', 'datatype', 'url', 'retired', 'source',
                    'owner', 'owner_type', 'owner_url', 'display_name', 'display_locale', 'names', 'descriptions',
                    'created_on', 'updated_on', 'versions_url', 'version', 'extras', 'type',
                    'update_comment', 'version_url', 'updated_by', 'created_by',
                    'public_can_view', 'versioned_object_id', 'checksums'])
        )

        response = self.client.get(
            "/concepts/?brief=true",
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(response.data[0].keys()),
            sorted(['uuid', 'id', 'url', 'version_url', 'type', 'retired'])
        )

    def test_get_200_with_mappings(self):
        concept1 = ConceptFactory(parent=self.source, mnemonic='conceptA')
        concept2 = ConceptFactory(parent=self.source, mnemonic='conceptB')
        mapping = MappingFactory(
            parent=self.source, from_concept=concept2.get_latest_version(), to_concept=concept1.get_latest_version()
        )

        response = self.client.get(
            "/concepts/",
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            [response.data[0]['id'], response.data[1]['id']],
            [concept2.mnemonic, concept1.mnemonic]
        )
        self.assertEqual(response['num_found'], '2')
        self.assertEqual(response['num_returned'], '2')
        self.assertFalse(response.has_header('previous'))
        self.assertFalse(response.has_header('next'))

        response = self.client.get(
            "/concepts/?limit=1&verbose=true&includeMappings=true",
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], concept2.mnemonic)
        self.assertEqual(len(response.data[0]['mappings']), 1)
        self.assertEqual(response.data[0]['mappings'][0]['uuid'], str(mapping.id))
        self.assertEqual(response['num_found'], '2')
        self.assertEqual(response['num_returned'], '1')
        self.assertTrue('/concepts/?limit=1&verbose=true&includeMappings=true&page=2' in response['next'])
        self.assertFalse(response.has_header('previous'))

        response = self.client.get(
            "/concepts/?page=2&limit=1&verbose=true&includeInverseMappings=true",
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], concept1.mnemonic)
        self.assertEqual(response['num_found'], '2')
        self.assertEqual(response['num_returned'], '1')
        self.assertEqual(len(response.data[0]['mappings']), 1)
        self.assertEqual(response.data[0]['mappings'][0]['uuid'], str(mapping.id))
        self.assertTrue('/concepts/?page=1&limit=1&verbose=true&includeInverseMappings=true' in response['previous'])
        self.assertFalse(response.has_header('next'))


class ConceptVersionRetrieveViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.source = UserSourceFactory(user=self.user)
        self.concept = ConceptFactory(parent=self.source)

    def test_get_200(self):
        latest_version = self.concept.get_latest_version()

        response = self.client.get(self.concept.url + f'{latest_version.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['is_latest_version'], True)
        self.assertEqual(response.data['version_url'], latest_version.uri)
        self.assertEqual(response.data['versioned_object_id'], self.concept.id)

    def test_get_404(self):
        response = self.client.get(self.concept.url + 'unknown/')

        self.assertEqual(response.status_code, 404)

    def test_soft_delete_204(self):
        admin_token = UserProfile.objects.get(username='ocladmin').get_token()
        concept_v1 = ConceptFactory(
            parent=self.source, version='v1', mnemonic=self.concept.mnemonic
        )

        response = self.client.delete(
            self.concept.url + f'{concept_v1.version}/',
            HTTP_AUTHORIZATION=f'Token {admin_token}',
        )

        self.assertEqual(response.status_code, 204)
        self.assertTrue(Concept.objects.filter(id=concept_v1.id).exists())
        concept_v1.refresh_from_db()
        self.assertFalse(concept_v1.is_active)

    def test_hard_delete_204(self):
        admin_token = UserProfile.objects.get(username='ocladmin').get_token()
        concept_v1 = ConceptFactory(
            parent=self.source, version='v1', mnemonic=self.concept.mnemonic
        )

        response = self.client.delete(
            f'{self.concept.url}{concept_v1.version}/?hardDelete=true',
            HTTP_AUTHORIZATION=f'Token {admin_token}',
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Concept.objects.filter(id=concept_v1.id).exists())


class ConceptExtrasViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.extras = {'foo': 'bar', 'tao': 'ching'}
        self.concept = ConceptFactory(extras=self.extras)
        self.user = UserProfileFactory(organizations=[self.concept.parent.organization])
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(self.concept.uri + 'extras/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.extras)


class ConceptExtraRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.extras = {'foo': 'bar', 'tao': 'ching'}
        self.concept = ConceptFactory(extras=self.extras, names=[ConceptNameFactory.build()])
        self.user = UserProfileFactory(organizations=[self.concept.parent.organization])
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(self.concept.uri + 'extras/foo/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'foo': 'bar'})

    def test_get_404(self):
        response = self.client.get(self.concept.uri + 'extras/bar/', format='json')

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        self.assertEqual(self.concept.versions.count(), 1)
        self.assertEqual(self.concept.get_latest_version().extras, self.extras)
        self.assertEqual(self.concept.extras, self.extras)

        response = self.client.put(
            self.concept.uri + 'extras/foo/',
            {'foo': 'foobar'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'foo': 'foobar'})
        self.assertEqual(self.concept.versions.count(), 2)
        self.assertEqual(self.concept.get_latest_version().extras, {'foo': 'foobar', 'tao': 'ching'})
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.extras, {'foo': 'foobar', 'tao': 'ching'})

    def test_put_400(self):
        self.assertEqual(self.concept.versions.count(), 1)
        self.assertEqual(self.concept.get_latest_version().extras, self.extras)
        self.assertEqual(self.concept.extras, self.extras)

        response = self.client.put(
            self.concept.uri + 'extras/foo/',
            {'tao': 'foobar'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, ['Must specify foo param in body.'])
        self.assertEqual(self.concept.versions.count(), 1)
        self.assertEqual(self.concept.get_latest_version().extras, self.extras)
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.extras, self.extras)

    def test_delete_204(self):
        self.assertEqual(self.concept.versions.count(), 1)
        self.assertEqual(self.concept.get_latest_version().extras, self.extras)
        self.assertEqual(self.concept.extras, self.extras)

        response = self.client.delete(
            self.concept.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.concept.versions.count(), 2)
        self.assertEqual(self.concept.get_latest_version().extras, {'tao': 'ching'})
        self.assertEqual(self.concept.versions.first().extras, {'foo': 'bar', 'tao': 'ching'})
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.extras, {'tao': 'ching'})


class ConceptVersionsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.concept = ConceptFactory(names=[ConceptNameFactory.build()])
        self.user = UserProfileFactory(organizations=[self.concept.parent.organization])
        self.token = self.user.get_token()

    def test_get_200(self):
        self.assertEqual(self.concept.versions.count(), 1)

        response = self.client.get(self.concept.versions_url)

        self.assertEqual(response.status_code, 200)
        versions = response.data
        self.assertEqual(len(versions), 1)
        version = versions[0]
        latest_version = self.concept.get_latest_version()
        self.assertEqual(version['uuid'], str(latest_version.id))
        self.assertEqual(version['id'], self.concept.mnemonic)
        self.assertEqual(version['url'], self.concept.uri)
        self.assertEqual(version['version_url'], latest_version.uri)
        self.assertTrue(version['is_latest_version'])
        self.assertIsNone(version['previous_version_url'])

        response = self.client.put(
            self.concept.uri,
            {'names': [{
                'locale': 'ab', 'locale_preferred': True, 'name': 'c1 name', 'name_type': 'Fully Specified'
            }], 'datatype': 'foobar', 'update_comment': 'Updated datatype'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.concept.versions.count(), 2)

        response = self.client.get(self.concept.versions_url)

        self.assertEqual(response.status_code, 200)
        versions = response.data
        self.assertEqual(len(versions), 2)

        prev_latest_version = [v for v in versions if v['uuid'] == version['uuid']][0]
        new_latest_version = [v for v in versions if v['uuid'] != version['uuid']][0]
        latest_version = self.concept.get_latest_version()

        self.assertEqual(new_latest_version['version_url'], latest_version.uri)
        self.assertEqual(str(latest_version.id), str(new_latest_version['uuid']))
        self.assertEqual(prev_latest_version['uuid'], version['uuid'])
        self.assertEqual(new_latest_version['previous_version_url'], prev_latest_version['version_url'])
        self.assertEqual(new_latest_version['previous_version_url'], version['version_url'])
        self.assertIsNone(prev_latest_version['previous_version_url'])
        self.assertFalse(prev_latest_version['is_latest_version'])
        self.assertTrue(new_latest_version['is_latest_version'])
        self.assertEqual(new_latest_version['datatype'], 'foobar')
        self.assertEqual(prev_latest_version['datatype'], 'None')


class ConceptMappingsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.concept = ConceptFactory(names=[ConceptNameFactory.build()])

    def test_get_200_for_concept(self):
        mappings_url = self.concept.uri + 'mappings/'
        response = self.client.get(mappings_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        direct_mapping = MappingFactory(parent=self.concept.parent, from_concept=self.concept)
        indirect_mapping = MappingFactory(parent=self.concept.parent, to_concept=self.concept)

        response = self.client.get(mappings_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['uuid'], str(direct_mapping.id))

        response = self.client.get(mappings_url + '?includeInverseMappings=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([mapping['uuid'] for mapping in response.data]),
            sorted([str(direct_mapping.id), str(indirect_mapping.id)])
        )

    def test_get_200_for_concept_version(self):
        concept_latest_version = self.concept.get_latest_version()

        mappings_url = concept_latest_version.uri + 'mappings/'
        response = self.client.get(mappings_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        direct_mapping = MappingFactory(parent=self.concept.parent, from_concept=concept_latest_version)
        indirect_mapping = MappingFactory(parent=self.concept.parent, to_concept=concept_latest_version)

        response = self.client.get(mappings_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['uuid'], str(direct_mapping.id))

        response = self.client.get(mappings_url + '?includeInverseMappings=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([mapping['uuid'] for mapping in response.data]),
            sorted([str(direct_mapping.id), str(indirect_mapping.id)])
        )


class ConceptCascadeViewTest(OCLAPITestCase):
    def test_get_200_for_source_version(self):  # pylint: disable=too-many-statements
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source1)
        concept2 = ConceptFactory(parent=source1)
        concept3 = ConceptFactory(parent=source2)
        mapping1 = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source1, map_type='map_type1')
        mapping2 = MappingFactory(from_concept=concept2, to_concept=concept1, parent=source1, map_type='map_type1')
        mapping3 = MappingFactory(from_concept=concept2, to_concept=concept3, parent=source1, map_type='map_type1')
        mapping4 = MappingFactory(from_concept=concept1, to_concept=concept3, parent=source1, map_type='map_type2')
        mapping6 = MappingFactory(from_concept=concept3, to_concept=concept1, parent=source2, map_type='map_type2')

        response = self.client.get(concept1.uri + '$cascade/?method=sourceMappings&cascadeLevels=1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 3)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                mapping1.uri,
                mapping4.uri,
            ])
        )

        response = self.client.get(concept1.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 4)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept2.uri,
                mapping1.uri,
                mapping4.uri,
            ])
        )

        response = self.client.get(concept1.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=*')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 6)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept2.uri,
                mapping1.uri,
                mapping2.uri,
                mapping3.uri,
                mapping4.uri,
            ])
        )

        response = self.client.get(
            concept1.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=1&returnMapTypes=false')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept2.uri,
            ])
        )

        response = self.client.get(
            concept1.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=1&'
                           'cascadeMappings=false&cascadeHierarchy=false')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
            ])
        )

        response = self.client.get(
            concept1.uri +
            '$cascade/?method=sourceToConcepts&mapTypes=map_type1&cascadeLevels=1&returnMapTypes=map_type1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 3)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept2.uri,
                mapping1.uri,
            ])
        )

        response = self.client.get(
            concept1.uri +
            '$cascade/?method=sourceToConcepts&excludeMapTypes=map_type1&cascadeLevels=1&returnMapTypes=map_type2')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                mapping4.uri,
            ])
        )

        response = self.client.get(concept2.uri + '$cascade/?method=sourceMappings&cascadeLevels=1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 3)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept2.uri,
                mapping2.uri,
                mapping3.uri,
            ])
        )

        response = self.client.get(concept2.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 4)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept2.uri,
                concept1.uri,
                mapping2.uri,
                mapping3.uri,
            ])
        )

        response = self.client.get(concept3.uri + '$cascade/?method=sourceMappings&cascadeLevels=1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
                mapping6.uri,
            ])
        )

        response = self.client.get(concept3.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
                mapping6.uri,
            ])
        )

        response = self.client.get(
            concept3.uri + '$cascade/?method=sourceToConcepts&mapTypes=foobar&cascadeLevels=1&returnMapTypes=foobar')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
            ])
        )

        # bundle response
        response = self.client.get(concept3.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resourceType'], 'Bundle')
        self.assertEqual(response.data['total'], 2)
        self.assertEqual(len(response.data['entry']), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
                mapping6.uri,
            ])
        )

        response = self.client.get(
            concept3.uri + '$cascade/?method=sourceToConcepts&mapTypes=foobar&cascadeLevels=1&returnMapTypes=foobar')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resourceType'], 'Bundle')
        self.assertEqual(response.data['total'], 1)
        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
            ])
        )

        # hierarchy response
        response = self.client.get(concept1.uri + '$cascade/?view=hierarchy&returnMapTypes=false')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resourceType'], 'Bundle')

        entry = response.data['entry']
        self.assertEqual(
            list(entry.keys()),
            ['id', 'type', 'url', 'version_url', 'terminal', 'entries', 'display_name', 'retired']
        )
        self.assertEqual(entry['id'], concept1.mnemonic)
        self.assertEqual(entry['type'], 'Concept')
        self.assertEqual(len(entry['entries']), 1)
        self.assertEqual(entry['entries'][0]['url'], concept2.url)

        # reverse ($cascade up)
        response = self.client.get(concept3.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=*&reverse=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
            ])
        )

        # reverse ($cascade up)
        response = self.client.get(concept2.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=*&reverse=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 4)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept2.uri,
                mapping1.uri,
                mapping2.uri,
            ])
        )

        # reverse ($cascade up)
        response = self.client.get(concept1.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=*&reverse=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 4)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept2.uri,
                mapping1.uri,
                mapping2.uri,
            ])
        )

        # reverse hierarchy response
        response = self.client.get(concept2.uri + '$cascade/?view=hierarchy&reverse=true&returnMapTypes=false')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resourceType'], 'Bundle')

        entry = response.data['entry']
        self.assertEqual(
            list(entry.keys()),
            ['id', 'type', 'url', 'version_url', 'terminal', 'entries', 'display_name', 'retired']
        )
        self.assertEqual(entry['id'], concept2.mnemonic)
        self.assertEqual(entry['type'], 'Concept')
        self.assertEqual(len(entry['entries']), 1)
        self.assertEqual(entry['entries'][0]['url'], concept1.url)

        # cascade 0
        response = self.client.get(concept3.uri + '$cascade/?cascadeLevels=0')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resourceType'], 'Bundle')
        self.assertEqual(response.data['total'], 1)
        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
            ])
        )

        # $cascade 0 - reverse ($cascade up)
        response = self.client.get(concept1.uri + '$cascade/?cascadeLevels=0&reverse=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
            ])
        )

        # $cascade 0 - hierarchy response
        response = self.client.get(concept1.uri + '$cascade/?view=hierarchy&cascadeLevels=0')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resourceType'], 'Bundle')

        entry = response.data['entry']
        self.assertEqual(
            list(entry.keys()),
            ['id', 'type', 'url', 'version_url', 'terminal', 'entries', 'display_name', 'retired']
        )
        self.assertEqual(entry['id'], concept1.mnemonic)
        self.assertEqual(entry['type'], 'Concept')
        self.assertEqual(len(entry['entries']), 0)

        # $cascade 0 - reverse hierarchy response
        response = self.client.get(concept2.uri + '$cascade/?view=hierarchy&reverse=true&cascadeLevels=0')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resourceType'], 'Bundle')

        entry = response.data['entry']
        self.assertEqual(
            list(entry.keys()),
            ['id', 'type', 'url', 'version_url', 'terminal', 'entries', 'display_name', 'retired']
        )
        self.assertEqual(entry['id'], concept2.mnemonic)
        self.assertEqual(entry['type'], 'Concept')
        self.assertEqual(len(entry['entries']), 0)

        # $cascade all forward with omitIfExistsIn
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        expansion.concepts.add(concept2.get_latest_version())
        expansion.concepts.add(concept3)
        expansion.mappings.add(mapping2)
        expansion.mappings.add(mapping6)

        response = self.client.get(
            concept1.uri + '$cascade/?omitIfExistsIn=' + collection.uri
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 3)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                mapping1.uri,
                mapping4.uri,
            ])
        )

        response = self.client.get(
            concept1.uri + '$cascade/?view=hierarchy&omitIfExistsIn=' + collection.uri
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['entry']['url'], concept1.uri)
        self.assertEqual(len(response.data['entry']['entries']), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']['entries']]),
            sorted([
                mapping1.uri,
                mapping4.uri,
            ])
        )

    def test_get_200_for_collection_version(self):  # pylint: disable=too-many-locals,too-many-statements
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source1)
        concept2 = ConceptFactory(parent=source1)
        concept3 = ConceptFactory(parent=source2)
        mapping1 = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source1, map_type='map_type1')
        mapping2 = MappingFactory(from_concept=concept2, to_concept=concept1, parent=source1, map_type='map_type1')
        mapping3 = MappingFactory(from_concept=concept2, to_concept=concept3, parent=source1, map_type='map_type1')
        mapping4 = MappingFactory(from_concept=concept1, to_concept=concept3, parent=source1, map_type='map_type2')
        mapping5 = MappingFactory(from_concept=concept3, to_concept=concept1, parent=source2, map_type='map_type2')

        collection1 = OrganizationCollectionFactory()
        expansion1 = ExpansionFactory(collection_version=collection1)
        collection2 = OrganizationCollectionFactory()
        expansion2 = ExpansionFactory(collection_version=collection2)
        expansion1.concepts.set([concept1, concept3])
        expansion1.mappings.set([mapping1, mapping4, mapping5])
        expansion2.concepts.set([concept1, concept2, concept3])
        expansion2.mappings.set([mapping1, mapping2, mapping3, mapping4, mapping5])

        response = self.client.get(
            collection1.uri + 'HEAD/concepts/' + concept1.mnemonic + '/$cascade/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([concept1.uri])
        )

        collection1.expansion_uri = expansion1.uri
        collection1.save()

        response = self.client.get(
            collection1.uri + 'HEAD/concepts/' + concept1.mnemonic + '/$cascade/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 5)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept3.uri,
                mapping1.uri,
                mapping4.uri,
                mapping5.uri
            ])
        )

        response = self.client.get(
            collection1.uri + 'HEAD/concepts/' + concept1.mnemonic + '/$cascade/?cascadeLevels=1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 4)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept3.uri,
                mapping1.uri,
                mapping4.uri,
            ])
        )

        response = self.client.get(
            collection1.uri + 'HEAD/concepts/' + concept2.mnemonic + '/$cascade/')

        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            collection1.uri + 'HEAD/concepts/' + concept3.mnemonic + '/$cascade/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 5)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
                concept1.uri,
                mapping1.uri,
                mapping4.uri,
                mapping5.uri
            ])
        )

        response = self.client.get(
            collection1.uri + 'HEAD/concepts/' + concept3.mnemonic +
            '/$cascade/?mapTypes=map_type1&returnMapTypes=map_type1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
            ])
        )

        response = self.client.get(
            collection1.uri + 'HEAD/concepts/' + concept3.mnemonic + '/$cascade/?cascadeLevels=1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 3)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
                concept1.uri,
                mapping5.uri
            ])
        )

        collection2.expansion_uri = expansion2.uri
        collection2.save()

        response = self.client.get(
            collection2.uri + 'HEAD/concepts/' + concept1.mnemonic + '/$cascade/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 8)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept2.uri,
                concept3.uri,
                mapping1.uri,
                mapping2.uri,
                mapping3.uri,
                mapping4.uri,
                mapping5.uri,
            ])
        )

        response = self.client.get(
            collection2.uri +
            'HEAD/concepts/' +
            concept2.mnemonic +
            '/$cascade/?excludeMapTypes=map_type2&returnMapTypes=map_type1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 6)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept2.uri,
                concept3.uri,
                mapping1.uri,
                mapping2.uri,
                mapping3.uri,
            ])
        )

        response = self.client.get(
            collection2.uri + 'HEAD/concepts/' + concept2.mnemonic + '/$cascade/?reverse=true&cascadeLevels=1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 3)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept2.uri,
                mapping1.uri,
            ])
        )

        response = self.client.get(
            collection2.uri + 'HEAD/concepts/' + concept2.mnemonic + '/$cascade/?reverse=true&cascadeLevels=2')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 6)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                concept2.uri,
                concept3.uri,
                mapping1.uri,
                mapping2.uri,
                mapping5.uri,
            ])
        )


class ConceptListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory(mnemonic='MySource')
        self.source_v1 = OrganizationSourceFactory(version='v1', mnemonic='MySource', organization=self.source.parent)
        self.concept1 = ConceptFactory(
            mnemonic='MyConcept1', parent=self.source, concept_class='classA', extras={'foo': 'bar'}
        )
        self.concept2 = ConceptFactory(
            mnemonic='MyConcept2', parent=self.source, concept_class='classB', extras={'bar': 'foo'}
        )
        self.source_v1.concepts.add(self.concept2)
        ConceptDocument().update(self.source.concepts.all())  # needed for parallel test execution
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.random_user = UserProfileFactory()

    def test_search(self):  # pylint: disable=too-many-statements
        response = self.client.get('/concepts/?q=MyConcept2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], 'MyConcept2')
        self.assertEqual(response.data[0]['uuid'], str(self.concept2.get_latest_version().id))
        self.assertEqual(response.data[0]['versioned_object_id'], self.concept2.id)

        response = self.client.get('/concepts/?q=MyConcept1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], 'MyConcept1')

        response = self.client.get('/concepts/?q=MyConcept1&exact_match=on')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], 'MyConcept1')

        response = self.client.get('/concepts/?q=MyConcept&conceptClass=classA')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], 'MyConcept1')

        response = self.client.get('/concepts/?q=MyConcept1&conceptClass=classB')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        response = self.client.get('/concepts/?conceptClass=classA')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], 'MyConcept1')

        response = self.client.get('/concepts/?extras.foo=bar')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], 'MyConcept1')

        response = self.client.get('/concepts/?extras.exists=bar')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], 'MyConcept2')

        response = self.client.get(
            self.source.concepts_url + '?q=MyConcept&extras.exact.foo=bar&includeSearchMeta=true')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], 'MyConcept1')
        self.assertEqual(
            response.data[0]['search_meta']['search_highlight'],
            {'extras.foo': ['<em>bar</em>'], 'id': ['<em>MyConcept1</em>']}
        )

        response = self.client.get(
            self.source.uri + 'v1/concepts/?q=MyConcept&sortAsc=last_update',
            HTTP_AUTHORIZATION='Token ' + self.random_user.get_token(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], 'MyConcept2')

        response = self.client.get(
            self.source.concepts_url + '?q=MyConcept&searchStatsOnly=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {'name': 'high', 'threshold': ANY, 'confidence': ANY, 'total': ANY},
                {'name': 'medium', 'threshold': ANY, 'confidence': ANY, 'total': 0},
                {'name': 'low', 'threshold': 0.01, 'confidence': '<50.0%', 'total': 0}
            ]
        )
        self.assertTrue(response.data[0]['total'] >= 2)

        response = self.client.get(
            self.source.concepts_url + '?q=MyConcept',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_facets(self):
        if settings.ENV == 'ci':
            rebuild_indexes(['concepts'])
        ConceptDocument().update(self.source.concepts_set.all())

        response = self.client.get(
            '/concepts/?facetsOnly=true'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.data.keys()), ['facets'])

        class_a_facet = [x for x in response.data['facets']['fields']['conceptClass'] if x[0] == 'classa'][0]
        self.assertEqual(class_a_facet[0], 'classa')
        self.assertTrue(class_a_facet[1] >= 1)
        self.assertFalse(class_a_facet[2])

        class_b_facet = [x for x in response.data['facets']['fields']['conceptClass'] if x[0] == 'classb'][0]
        self.assertEqual(class_b_facet[0], 'classb')
        self.assertTrue(class_b_facet[1] >= 1)
        self.assertFalse(class_b_facet[2])


class ConceptNameRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.name = ConceptNameFactory.build(locale='fr', name='froobar')
        self.concept = ConceptFactory(names=[self.name])
        self.name = self.concept.names.first()
        self.token = self.concept.created_by.get_token()
        self.url = f'{self.concept.url}names/{self.name.id}/'

    def test_get_404(self):
        response = self.client.get(
            '/orgs/foo/sources/source/concepts/1234/names/1234/',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 404)
        response = self.client.get(
            self.concept.url + 'names/1234/',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 404)

    def test_get_200(self):
        self.assertEqual(self.concept.versions.count(), 1)

        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION='Token ' + self.token,

        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.name.id))
        self.assertEqual(response.data['type'], 'ConceptName')
        self.assertEqual(response.data['name'], 'froobar')
        self.assertEqual(response.data['locale'], 'fr')

    def test_put_200(self):
        self.assertEqual(self.concept.versions.count(), 1)

        response = self.client.put(
            self.url,
            {'name': 'brar'},
            HTTP_AUTHORIZATION='Token ' + self.token,

        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.concept.versions.count(), 2)
        self.assertEqual(self.concept.get_latest_version().names.first().name, 'brar')
        self.assertEqual(self.concept.get_latest_version().prev_version.names.first().name, 'froobar')
        self.assertEqual(self.concept.names.first().name, 'brar')


class ConceptReactivateViewTest(OCLAPITestCase):
    def test_put(self):
        name = ConceptNameFactory.build()
        concept = ConceptFactory(retired=True, names=[name])
        self.assertTrue(concept.retired)
        self.assertTrue(concept.get_latest_version().retired)
        token = concept.created_by.get_token()

        response = self.client.put(
            concept.url + 'reactivate/',
            HTTP_AUTHORIZATION='Token ' + token,
        )

        self.assertEqual(response.status_code, 204)
        concept.refresh_from_db()
        self.assertFalse(concept.retired)
        self.assertFalse(concept.get_latest_version().retired)
        self.assertTrue(concept.get_latest_version().prev_version.retired)

        response = self.client.put(
            concept.url + 'reactivate/',
            HTTP_AUTHORIZATION='Token ' + token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'__all__': 'Concept is already not retired'})


class ConceptParentsViewTest(OCLAPITestCase):
    def test_get_200(self):
        parent_concept1 = ConceptFactory()
        parent_concept2 = ConceptFactory()
        child_concept = ConceptFactory()
        child_concept.parent_concepts.set([parent_concept1, parent_concept2])

        response = self.client.get(child_concept.url + 'parents/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data]),
            [parent_concept1.uri, parent_concept2.uri]
        )

        response = self.client.get(parent_concept1.url + 'parents/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)


class ConceptChildrenViewTest(OCLAPITestCase):
    def test_get_200(self):
        parent_concept = ConceptFactory()
        child_concept1 = ConceptFactory()
        child_concept2 = ConceptFactory()
        child_concept1.parent_concepts.set([parent_concept])
        child_concept2.parent_concepts.set([parent_concept])

        response = self.client.get(parent_concept.url + 'children/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data]),
            [child_concept1.uri, child_concept2.uri]
        )

        response = self.client.get(child_concept1.url + 'children/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)


class ConceptCollectionMembershipViewTest(OCLAPITestCase):
    def test_get_200(self):
        parent = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=parent)
        concept2 = ConceptFactory()  # random owner/parent
        collection1 = OrganizationCollectionFactory(organization=parent.organization)
        expansion1 = ExpansionFactory(collection_version=collection1)
        collection1.expansion_uri = expansion1.uri
        collection1.save()
        collection2 = OrganizationCollectionFactory(organization=parent.organization)
        expansion2 = ExpansionFactory(collection_version=collection2)
        collection2.expansion_uri = expansion2.uri
        collection2.save()
        collection3 = OrganizationCollectionFactory()  # random owner/parent
        expansion3 = ExpansionFactory(collection_version=collection3)
        collection3.expansion_uri = expansion3.uri
        collection3.save()
        expansion1.concepts.add(concept1)
        expansion2.concepts.add(concept1)
        expansion3.concepts.add(concept1)
        expansion1.concepts.add(concept2)
        expansion2.concepts.add(concept2)
        expansion3.concepts.add(concept2)

        response = self.client.get(concept1.url + 'collection-versions/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data]),
            sorted([collection2.url, collection1.url])
        )

        response = self.client.get(concept2.url + 'collection-versions/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)


class ConceptSummaryViewTest(OCLAPITestCase):
    def test_get_200(self):
        parent_concept = ConceptFactory(
            names=[ConceptNameFactory.build(), ConceptNameFactory.build()])
        child_concept = ConceptFactory(
            names=[ConceptNameFactory.build(), ConceptNameFactory.build()],
            descriptions=[ConceptDescriptionFactory.build()]
        )
        child_concept.parent_concepts.add(parent_concept)

        response = self.client.get(parent_concept.url + 'summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(parent_concept.id))
        self.assertEqual(response.data['id'], parent_concept.mnemonic)
        self.assertEqual(response.data['descriptions'], 0)
        self.assertEqual(response.data['names'], 2)
        self.assertEqual(response.data['versions'], 1)
        self.assertEqual(response.data['children'], 1)
        self.assertEqual(response.data['parents'], 0)

        response = self.client.get(child_concept.url + 'summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(child_concept.id))
        self.assertEqual(response.data['id'], child_concept.mnemonic)
        self.assertEqual(response.data['descriptions'], 1)
        self.assertEqual(response.data['names'], 2)
        self.assertEqual(response.data['versions'], 1)
        self.assertEqual(response.data['children'], 0)
        self.assertEqual(response.data['parents'], 1)


class ConceptCloneViewTest(OCLAPITestCase):
    def setUp(self):
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.concept = ConceptFactory()
        self.clone_to_source = OrganizationSourceFactory()

    def test_post_bad_requests(self):
        response = self.client.post(
            self.concept.uri + '$clone/',
            {'foo': 'bar'},
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            self.concept.uri + '$clone/',
            {'source_uri': 'foobar'},
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )
        self.assertEqual(response.status_code, 404)

        self.clone_to_source.public_access = 'None'
        self.clone_to_source.save()

        response = self.client.post(
            self.concept.uri + '$clone/',
            {'source_uri': self.clone_to_source.uri},
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )
        self.assertEqual(response.status_code, 403)

    @patch('core.concepts.views.Bundle.clone')
    def test_post_success(self, bundle_clone_mock):
        parameters = {'mapTypes': 'Q-AND-A,CONCEPT-SET'}
        bundle_clone_mock.return_value = Bundle(
            root=self.concept, repo_version=self.concept.parent, params=parameters, verbose=False
        )

        response = self.client.post(
            self.concept.uri + '$clone/',
            {'source_uri': self.clone_to_source.uri, 'parameters': parameters},
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                'resourceType': 'Bundle',
                'type': 'searchset',
                'meta': ANY,
                'total': None,
                'entry': [],
                'requested_url': None,
                'repo_version_url': self.concept.parent.uri + 'HEAD/'
            }
        )
        bundle_clone_mock.assert_called_once_with(
            self.concept, self.concept.parent, self.clone_to_source, self.user, ANY, False,
            **parameters
        )
