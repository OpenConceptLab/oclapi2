from mock import ANY

from core.common.tests import OCLAPITestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, LocalizedTextFactory
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
                'locale': 'ab', 'locale_preferred': True, 'description': 'c1 desc', 'description_type': 'None'
            }],
            'external_id': '',
            'id': 'c1',
            'names': [{
                'locale': 'ab', 'locale_preferred': True, 'name': 'c1 name', 'name_type': 'Fully Specified'
            }]
        }

    def test_post_201(self):
        concepts_url = "/orgs/{}/sources/{}/concepts/".format(self.organization.mnemonic, self.source.mnemonic)

        response = self.client.post(
            concepts_url,
            self.concept_payload,
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
        self.assertEqual(response.data['display_locale'], 'ab')
        self.assertEqual(response.data['versions_url'], concept.uri + 'versions/')
        self.assertEqual(response.data['version'], str(concept.id))
        self.assertEqual(response.data['extras'], dict(foo='bar'))
        self.assertEqual(response.data['parent_id'], str(self.source.id))
        self.assertEqual(response.data['name'], 'c1')
        self.assertEqual(response.data['type'], 'Concept')
        self.assertEqual(response.data['version_url'], latest_version.uri)
        self.assertEqual(response.data['mappings'], [])

        response = self.client.post(
            concepts_url,
            self.concept_payload,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(mnemonic='Concept ID must be unique within a source.'))

    def test_post_400(self):
        concepts_url = "/orgs/{}/sources/{}/concepts/".format(self.organization.mnemonic, self.source.mnemonic)

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
        concepts_url = "/orgs/{}/sources/{}/concepts/{}/".format(
            self.organization.mnemonic, self.source.mnemonic, concept.mnemonic
        )

        response = self.client.put(
            concepts_url,
            {**self.concept_payload, 'datatype': 'None', 'update_comment': 'Updated datatype'},
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
        self.assertEqual(response.data['source'], self.source.mnemonic)
        self.assertEqual(response.data['owner'], self.organization.mnemonic)
        self.assertEqual(response.data['owner_type'], "Organization")
        self.assertEqual(response.data['owner_url'], self.organization.uri)
        self.assertEqual(response.data['display_name'], 'c1 name')
        self.assertEqual(response.data['display_locale'], 'ab')
        self.assertEqual(response.data['versions_url'], concept.uri + 'versions/')
        self.assertEqual(response.data['version'], str(version.id))
        self.assertEqual(response.data['extras'], dict(foo='bar'))
        self.assertEqual(response.data['parent_id'], str(self.source.id))
        self.assertEqual(response.data['type'], 'Concept')
        self.assertEqual(response.data['version_url'], version.uri)
        self.assertEqual(response.data['mappings'], [])
        self.assertTrue(concept.is_versioned_object)
        self.assertEqual(concept.datatype, "None")

    def test_put_400(self):
        concept = ConceptFactory(parent=self.source)
        concepts_url = "/orgs/{}/sources/{}/concepts/{}/".format(
            self.organization.mnemonic, self.source.mnemonic, concept.mnemonic
        )

        response = self.client.put(
            concepts_url,
            {**self.concept_payload, 'concept_class': '', 'update_comment': 'Updated concept_class'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data.keys()), ['concept_class'])

    def test_put_404(self):
        concepts_url = "/orgs/{}/sources/{}/concepts/foobar/".format(
            self.organization.mnemonic, self.source.mnemonic
        )

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
        concepts_url = "/orgs/{}/sources/{}/concepts/{}/".format(
            self.organization.mnemonic, self.source.mnemonic, concept.mnemonic
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
        concepts_url = "/orgs/{}/sources/{}/concepts/foobar/".format(
            self.organization.mnemonic, self.source.mnemonic
        )

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
        concepts_url = "/orgs/{}/sources/{}/concepts/{}/".format(
            self.organization.mnemonic, self.source.mnemonic, concept.mnemonic
        )

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
        extras_url = "/orgs/{}/sources/{}/concepts/{}/extras/".format(
            self.organization.mnemonic, self.source.mnemonic, concept.mnemonic
        )

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
            return "/orgs/{}/sources/{}/concepts/{}/extras/{}/".format(
                self.organization.mnemonic, self.source.mnemonic, concept.mnemonic, extra
            )

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
            return "/orgs/{}/sources/{}/concepts/{}/extras/{}/".format(
                self.organization.mnemonic, self.source.mnemonic, concept.mnemonic, extra
            )

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
            return "/orgs/{}/sources/{}/concepts/{}/extras/{}/".format(
                self.organization.mnemonic, self.source.mnemonic, concept.mnemonic, extra
            )

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
            return "/orgs/{}/sources/{}/concepts/{}/extras/{}/".format(
                self.organization.mnemonic, self.source.mnemonic, concept.mnemonic, extra
            )

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
            return "/orgs/{}/sources/{}/concepts/{}/extras/{}/".format(
                self.organization.mnemonic, self.source.mnemonic, concept.mnemonic, extra
            )

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
            "/orgs/{}/sources/{}/concepts/{}/names/".format(
                self.organization.mnemonic, self.source.mnemonic, concept.mnemonic
            ),
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
            "/orgs/{}/sources/{}/concepts/{}/names/".format(
                self.organization.mnemonic, self.source.mnemonic, concept.mnemonic
            ),
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
            "/orgs/{}/sources/{}/concepts/{}/names/".format(
                self.organization.mnemonic, self.source.mnemonic, concept.mnemonic
            ),
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
            "/orgs/{}/sources/{}/concepts/{}/names/{}/".format(
                self.organization.mnemonic, self.source.mnemonic, concept.mnemonic, name2.id
            ),
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
        self.assertEqual(latest_version.comment, 'Deleted {} in names.'.format(name2.name))

    def test_get_200(self):
        concept1 = ConceptFactory(parent=self.source, mnemonic='conceptA')
        concept2 = ConceptFactory(parent=self.source, mnemonic='conceptB')

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
            "/concepts/?limit=1",
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], concept2.mnemonic)
        self.assertEqual(response['num_found'], '2')
        self.assertEqual(response['num_returned'], '1')
        self.assertTrue('/concepts/?limit=1&page=2' in response['next'])
        self.assertFalse(response.has_header('previous'))

        response = self.client.get(
            "/concepts/?page=2&limit=1",
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], concept1.mnemonic)
        self.assertEqual(response['num_found'], '2')
        self.assertEqual(response['num_returned'], '1')
        self.assertTrue('/concepts/?page=1&limit=1' in response['previous'])
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
