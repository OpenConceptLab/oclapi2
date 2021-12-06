import unittest

from mock import ANY

from core.common.constants import CUSTOM_VALIDATION_SCHEMA_OPENMRS
from core.common.tests import OCLAPITestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, LocalizedTextFactory
from core.mappings.tests.factories import MappingFactory
from core.orgs.models import Organization
from core.sources.tests.factories import OrganizationSourceFactory
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
                'name',
                'type',
                'update_comment',
                'version_url',
                'updated_by',
                'created_by',
                'parent_concept_urls',
                'public_can_view',
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
        self.assertEqual(response.data['extras'], dict(foo='bar'))
        self.assertEqual(response.data['name'], 'c1')
        self.assertEqual(response.data['type'], 'Concept')
        self.assertEqual(response.data['version_url'], latest_version.uri)

        response = self.client.post(
            concepts_url,
            self.concept_payload,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(__all__=['Concept ID must be unique within a source.']))

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
                    'name',
                    'type',
                    'update_comment',
                    'version_url',
                    'updated_by',
                    'created_by',
                    'parent_concept_urls',
                    'public_can_view'])
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
        self.assertEqual(response.data['extras'], dict(foo='bar'))
        self.assertEqual(response.data['type'], 'Concept')
        self.assertEqual(response.data['version_url'], version.uri)
        self.assertTrue(concept.is_versioned_object)
        self.assertEqual(concept.datatype, "None")

    @unittest.skip('Flaky test, needs fixing')
    def test_put_200_openmrs_schema(self):  # pylint: disable=too-many-statements
        self.create_lookup_concept_classes()
        source = OrganizationSourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        name = LocalizedTextFactory(locale='fr')
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
                    'name',
                    'type',
                    'update_comment',
                    'version_url',
                    'updated_by',
                    'created_by',
                    'parent_concept_urls',
                    'public_can_view'])
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
        self.assertEqual(response.data['extras'], dict(foo='bar'))
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
        names = [LocalizedTextFactory()]
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
        names = [LocalizedTextFactory()]
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
        names = [LocalizedTextFactory()]
        concept = ConceptFactory(parent=self.source, names=names, extras=dict(foo='bar'))
        extras_url = f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}" \
            f"/concepts/{concept.mnemonic}/extras/"

        response = self.client.get(
            extras_url,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='bar'))

    def test_extra_get_200(self):
        names = [LocalizedTextFactory()]
        concept = ConceptFactory(parent=self.source, names=names, extras=dict(foo='bar', tao='ching'))

        def extra_url(extra):
            return f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}" \
                f"/concepts/{concept.mnemonic}/extras/{extra}/"

        response = self.client.get(
            extra_url('tao'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(tao='ching'))

        response = self.client.get(
            extra_url('foo'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='bar'))

        response = self.client.get(
            extra_url('bar'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, dict(detail='Not found.'))

    def test_extra_put_200(self):
        names = [LocalizedTextFactory()]
        concept = ConceptFactory(parent=self.source, names=names, extras=dict(foo='bar', tao='ching'))

        def extra_url(extra):
            return f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}" \
                f"/concepts/{concept.mnemonic}/extras/{extra}/"

        response = self.client.put(
            extra_url('tao'),
            dict(tao='te-ching'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)

        concept.refresh_from_db()
        self.assertTrue(concept.extras['tao'] == response.data['tao'] == 'te-ching')
        self.assertEqual(concept.versions.count(), 2)

        latest_version = concept.versions.order_by('-created_at').first()
        self.assertEqual(latest_version.extras, dict(foo='bar', tao='te-ching'))
        self.assertEqual(latest_version.comment, 'Updated extras: tao=te-ching.')

    def test_extra_put_400(self):
        names = [LocalizedTextFactory()]
        concept = ConceptFactory(parent=self.source, names=names, extras=dict(foo='bar', tao='ching'))

        def extra_url(extra):
            return f"/orgs/{self.organization.mnemonic}/sources/{self.source.mnemonic}" \
                f"/concepts/{concept.mnemonic}/extras/{extra}/"

        response = self.client.put(
            extra_url('tao'),
            dict(tao=None),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, ['Must specify tao param in body.'])
        concept.refresh_from_db()
        self.assertEqual(concept.extras, dict(foo='bar', tao='ching'))

    def test_extra_delete_204(self):
        names = [LocalizedTextFactory()]
        concept = ConceptFactory(parent=self.source, names=names, extras=dict(foo='bar', tao='ching'))
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
        self.assertEqual(latest_version.extras, dict(foo='bar'))
        self.assertEqual(latest_version.comment, 'Deleted extra tao.')

    def test_extra_delete_404(self):
        names = [LocalizedTextFactory()]
        concept = ConceptFactory(parent=self.source, names=names, extras=dict(foo='bar', tao='ching'))

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
        name = LocalizedTextFactory()
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
        name = LocalizedTextFactory()
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
        name = LocalizedTextFactory()
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
        name1 = LocalizedTextFactory()
        name2 = LocalizedTextFactory()
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
                    'versions_url', 'version_url', 'type'])
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
                    'created_on', 'updated_on', 'versions_url', 'version', 'extras', 'name', 'type',
                    'update_comment', 'version_url', 'updated_by', 'created_by',
                    'public_can_view'])
        )

        response = self.client.get(
            "/concepts/?brief=true",
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(response.data[0].keys()),
            sorted(['uuid', 'id', 'url', 'version_url', 'type'])
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


class ConceptExtrasViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.extras = dict(foo='bar', tao='ching')
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
        self.extras = dict(foo='bar', tao='ching')
        self.concept = ConceptFactory(extras=self.extras, names=[LocalizedTextFactory()])
        self.user = UserProfileFactory(organizations=[self.concept.parent.organization])
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(self.concept.uri + 'extras/foo/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='bar'))

    def test_get_404(self):
        response = self.client.get(self.concept.uri + 'extras/bar/', format='json')

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        self.assertEqual(self.concept.versions.count(), 1)
        self.assertEqual(self.concept.get_latest_version().extras, self.extras)
        self.assertEqual(self.concept.extras, self.extras)

        response = self.client.put(
            self.concept.uri + 'extras/foo/',
            dict(foo='foobar'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='foobar'))
        self.assertEqual(self.concept.versions.count(), 2)
        self.assertEqual(self.concept.get_latest_version().extras, dict(foo='foobar', tao='ching'))
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.extras, dict(foo='foobar', tao='ching'))

    def test_put_400(self):
        self.assertEqual(self.concept.versions.count(), 1)
        self.assertEqual(self.concept.get_latest_version().extras, self.extras)
        self.assertEqual(self.concept.extras, self.extras)

        response = self.client.put(
            self.concept.uri + 'extras/foo/',
            dict(tao='foobar'),
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
        self.assertEqual(self.concept.get_latest_version().extras, dict(tao='ching'))
        self.assertEqual(self.concept.versions.first().extras, dict(foo='bar', tao='ching'))
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.extras, dict(tao='ching'))


class ConceptVersionsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.concept = ConceptFactory(names=[LocalizedTextFactory()])
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
        self.concept = ConceptFactory(names=[LocalizedTextFactory()])

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
    def test_get_200(self):  # pylint: disable=too-many-statements
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

        response = self.client.get(concept1.uri + '$cascade/?method=sourceMappings&cascadeLevels=0')

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

        response = self.client.get(concept1.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=0')

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
            concept1.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=0&includeMappings=false')

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
            concept1.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=0&'
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
            concept1.uri + '$cascade/?method=sourceToConcepts&mapTypes=map_type1&cascadeLevels=0')

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
            concept1.uri + '$cascade/?method=sourceToConcepts&excludeMapTypes=map_type1&cascadeLevels=0')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept1.uri,
                mapping4.uri,
            ])
        )

        response = self.client.get(concept2.uri + '$cascade/?method=sourceMappings&cascadeLevels=0')

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

        response = self.client.get(concept2.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=0')

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

        response = self.client.get(concept3.uri + '$cascade/?method=sourceMappings&cascadeLevels=0')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
                mapping6.uri,
            ])
        )

        response = self.client.get(concept3.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=0')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
                mapping6.uri,
            ])
        )

        response = self.client.get(concept3.uri + '$cascade/?method=sourceToConcepts&mapTypes=foobar&cascadeLevels=0')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
            ])
        )

        # bundle response
        response = self.client.get(concept3.uri + '$cascade/?method=sourceToConcepts&cascadeLevels=0')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['type'], 'Bundle')
        self.assertEqual(response.data['total'], 2)
        self.assertEqual(len(response.data['entry']), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
                mapping6.uri,
            ])
        )

        response = self.client.get(concept3.uri + '$cascade/?method=sourceToConcepts&mapTypes=foobar&cascadeLevels=0')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['type'], 'Bundle')
        self.assertEqual(response.data['total'], 1)
        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(
            sorted([data['url'] for data in response.data['entry']]),
            sorted([
                concept3.uri,
            ])
        )
