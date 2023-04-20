from rest_framework.exceptions import ErrorDetail

from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.mappings.constants import SAME_AS
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import UserSourceFactory, OrganizationSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class MappingListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get('/mappings/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        mapping = MappingFactory()
        response = self.client.get('/mappings/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        response = self.client.get(mapping.parent.mappings_url, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()

        response = self.client.get(collection.mappings_url, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        collection.add_expressions(dict(expressions=[mapping.uri]), collection.created_by)

        response = self.client.get(collection.mappings_url, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_post_400(self):
        source = UserSourceFactory(user=self.user)

        response = self.client.post(
            source.mappings_url,
            dict(foo='bar'),
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            dict(
                map_type=[ErrorDetail(string='This field is required.', code='required')],
            )
        )

    def test_post_201(self):
        source = UserSourceFactory(user=self.user)
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)

        response = self.client.post(
            source.mappings_url,
            dict(map_type='same as', from_concept_url=concept2.uri, to_concept_url=concept1.uri),
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['map_type'], 'same as')
        self.assertEqual(response.data['from_concept_code'], concept2.mnemonic)
        self.assertEqual(response.data['to_concept_code'], concept1.mnemonic)
        mapping = source.mappings.first()
        self.assertEqual(mapping.from_concept.id, concept2.id)
        self.assertEqual(mapping.from_source.id, concept2.parent.id)
        self.assertEqual(mapping.to_concept.id, concept1.id)
        self.assertEqual(mapping.to_source.id, concept1.parent.id)
        self.assertEqual(mapping.from_source_url, concept2.parent.uri)
        self.assertEqual(mapping.to_source_url, concept1.parent.uri)
        self.assertEqual(mapping.from_source_version, None)
        self.assertEqual(mapping.to_source_version, None)
        self.assertEqual(mapping.from_concept_name, None)
        self.assertEqual(mapping.to_concept_name, None)

        response = self.client.post(
            source.mappings_url,
            dict(map_type='same as', from_concept_url=concept2.uri, to_concept_url=concept1.uri),
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data, {"__all__": ["Parent, map_type, from_concept, to_source, to_concept_code must be unique."]}
        )

        response = self.client.post(
            source.mappings_url,
            dict(map_type='same as', from_concept_url=concept2.uri, to_concept_url=concept2.uri),
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['map_type'], 'same as')
        self.assertEqual(response.data['from_concept_code'], concept2.mnemonic)
        self.assertEqual(response.data['to_concept_code'], concept2.mnemonic)

    def test_post_to_concept_does_not_exists_201(self):
        source = UserSourceFactory(user=self.user)
        concept = ConceptFactory(parent=source)

        response = self.client.post(
            source.mappings_url,
            {
                "map_type": "Same As",
                "from_concept_url": concept.get_latest_version().uri,
                "to_concept_url": "/orgs/WHO/sources/ICPC-2/concepts/A73/"
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['from_source_url'], source.uri)
        self.assertEqual(response.data['from_source_version'], None)
        self.assertEqual(response.data['from_concept_code'], concept.mnemonic)
        self.assertEqual(response.data['from_concept_name'], None)
        self.assertEqual(response.data['to_source_url'], '/orgs/WHO/sources/ICPC-2/')
        self.assertEqual(response.data['to_source_version'], None)
        self.assertEqual(response.data['to_concept_code'], 'A73')
        self.assertEqual(response.data['to_concept_name'], None)

    def test_post_everything_exists_201(self):
        source1 = UserSourceFactory(user=self.user)
        source2 = UserSourceFactory(user=self.user)
        concept1 = ConceptFactory(parent=source1)
        concept2 = ConceptFactory(parent=source2)

        response = self.client.post(
            source1.mappings_url,
            {
                "map_type": "Same As",
                "from_concept_url": concept1.get_latest_version().uri,
                "to_concept_url": concept2.get_latest_version().uri,
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['from_source_url'], source1.uri)
        self.assertEqual(response.data['from_source_version'], None)
        self.assertEqual(response.data['from_concept_code'], concept1.mnemonic)
        self.assertEqual(response.data['from_concept_name'], None)
        self.assertEqual(response.data['to_source_url'], source2.uri)
        self.assertEqual(response.data['to_source_version'], None)
        self.assertEqual(response.data['to_concept_code'], concept2.mnemonic)
        self.assertEqual(response.data['to_concept_name'], None)
        mapping = source1.mappings.first()
        self.assertEqual(mapping.from_concept, concept1.get_latest_version())
        self.assertEqual(mapping.to_concept, concept2.get_latest_version())
        self.assertEqual(mapping.to_source, source2)
        self.assertEqual(mapping.from_source, source1)

    def test_post_to_concept_does_not_exists_with_resolved_payload_201(self):
        source = UserSourceFactory(user=self.user)
        concept = ConceptFactory(parent=source)

        response = self.client.post(
            source.mappings_url,
            {
                "map_type": "Same As",
                "from_concept_url": concept.get_latest_version().uri,
                "to_source_url": "/orgs/WHO/sources/ICPC-2/",
                "to_concept_code": "A73",
                "to_concept_name": "Malaria"
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['from_source_url'], source.uri)
        self.assertEqual(response.data['from_source_version'], None)
        self.assertEqual(response.data['from_concept_code'], concept.mnemonic)
        self.assertEqual(response.data['from_concept_name'], None)
        self.assertEqual(response.data['to_source_url'], '/orgs/WHO/sources/ICPC-2/')
        self.assertEqual(response.data['to_source_version'], None)
        self.assertEqual(response.data['to_concept_code'], 'A73')
        self.assertEqual(response.data['to_concept_name'], 'Malaria')

    def test_post_with_source_versions_201(self):
        source1 = UserSourceFactory(user=self.user, mnemonic='source1')
        source1_v1 = UserSourceFactory(
            user=source1.parent, mnemonic=source1.mnemonic, version='v1.1'
        )
        concept1 = ConceptFactory(parent=source1, sources=[source1_v1])

        source2 = UserSourceFactory(user=self.user, mnemonic='source2')
        source2_v1 = UserSourceFactory(
            user=source2.parent, mnemonic=source1.mnemonic, version='v2.1'
        )
        concept2 = ConceptFactory(parent=source2, sources=[source2_v1])

        response = self.client.post(
            source1.mappings_url,
            {
                "map_type": "Same As",
                "from_concept_url": concept1.get_latest_version().uri,
                "from_source_version": "v1.1",
                "to_concept_url": concept2.get_latest_version().uri,
                "to_source_version": "v2.1"
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['from_source_url'], source1.uri)
        self.assertEqual(response.data['from_source_version'], 'v1.1')
        self.assertEqual(response.data['from_concept_code'], concept1.mnemonic)
        self.assertEqual(response.data['from_concept_name'], None)
        self.assertEqual(response.data['to_source_url'], source2.uri)
        self.assertEqual(response.data['to_source_version'], 'v2.1')
        self.assertEqual(response.data['to_concept_code'], concept2.mnemonic)
        self.assertEqual(response.data['to_concept_name'], None)
        mapping = source1.mappings.first()
        self.assertEqual(mapping.from_concept, concept1.get_latest_version())
        self.assertEqual(mapping.to_concept, concept2.get_latest_version())
        self.assertEqual(mapping.to_source, source2)
        self.assertEqual(mapping.from_source, source1)

    def test_post_with_source_versions_without_to_concept_exists_201(self):
        source = UserSourceFactory(user=self.user, mnemonic='source')
        source_v1 = UserSourceFactory(
            user=source.parent, mnemonic=source.mnemonic, version='v1.1'
        )
        concept = ConceptFactory(parent=source, sources=[source_v1])

        response = self.client.post(
            source.mappings_url,
            {
                "map_type": "Same As",
                "from_concept_url": concept.get_latest_version().uri,
                "from_source_version": "v1.1",
                "to_source_url": "/orgs/WHO/sources/ICPC-2/",
                "to_source_version": "v11",
                "to_concept_code": "A73",
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['from_source_url'], source.uri)
        self.assertEqual(response.data['from_source_version'], 'v1.1')
        self.assertEqual(response.data['from_concept_code'], concept.mnemonic)
        self.assertEqual(response.data['from_concept_name'], None)
        self.assertEqual(response.data['to_source_url'], "/orgs/WHO/sources/ICPC-2/")
        self.assertEqual(response.data['to_source_version'], 'v11')
        self.assertEqual(response.data['to_concept_code'], 'A73')
        self.assertEqual(response.data['to_concept_name'], None)
        mapping = source.mappings.first()
        self.assertEqual(mapping.from_concept, concept.get_latest_version())
        self.assertIsNone(mapping.to_concept_id)
        self.assertIsNone(mapping.to_source_id)

        concept2 = ConceptFactory(parent=source, sources=[source_v1], names=[ConceptNameFactory.build()])
        self.assertIsNotNone(concept2.display_name)

        response = self.client.post(
            source.mappings_url,
            {
                "map_type": "Same As",
                "from_concept_url": concept2.get_latest_version().uri,
                "from_source_version": "v1.1",
                "to_source_url": "/orgs/WHO/sources/ICPC-2/v11/",
                "to_concept_code": "A73",
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['from_source_url'], source.uri)
        self.assertEqual(response.data['from_source_version'], 'v1.1')
        self.assertEqual(response.data['from_concept_code'], concept2.mnemonic)
        self.assertEqual(response.data['from_concept_name'], None)
        self.assertEqual(response.data['to_source_url'], "/orgs/WHO/sources/ICPC-2/")
        self.assertEqual(response.data['to_source_version'], 'v11')
        self.assertEqual(response.data['to_concept_code'], 'A73')
        self.assertEqual(response.data['to_concept_name'], None)
        mapping = source.mappings.last()
        self.assertEqual(mapping.from_concept, concept2.get_latest_version())
        self.assertIsNone(mapping.to_concept_id)
        self.assertIsNone(mapping.to_source_id)

        response = self.client.post(
            source.mappings_url,
            {
                "map_type": "Same As",
                "from_concept_url": concept2.get_latest_version().uri,
                "from_source_version": "v1.1",
                "to_source_url": "/orgs/WHO/sources/ICPC-2/v11/",
                "to_concept_code": "A73",
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data, {'__all__': ['Parent, map_type, from_concept, to_source, to_concept_code must be unique.']}
        )

    def test_post_without_to_and_from_concept_url_201(self):
        source = UserSourceFactory(user=self.user, mnemonic='source')
        response = self.client.post(
            source.mappings_url,
            {
                "map_type": "Same As",
                "from_source_url": source.uri,
                "from_source_version": "2.46",
                "from_concept_code": "32700-7",
                "from_concept_name": "Microscopic observation [Identifier] in Blood by Malaria smear",
                "to_source_url": "/orgs/WHO/sources/ICPC-2/",
                "to_source_version": "v11",
                "to_concept_code": "A73",
                "to_concept_name": "Malaria"
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['from_source_url'], source.uri)
        self.assertEqual(response.data['from_source_version'], '2.46')
        self.assertEqual(response.data['from_concept_code'], '32700-7')
        self.assertEqual(
            response.data['from_concept_name'], 'Microscopic observation [Identifier] in Blood by Malaria smear'
        )
        self.assertEqual(response.data['to_source_url'], "/orgs/WHO/sources/ICPC-2/")
        self.assertEqual(response.data['to_source_version'], 'v11')
        self.assertEqual(response.data['to_concept_code'], 'A73')
        self.assertEqual(response.data['to_concept_name'], 'Malaria')
        mapping = source.mappings.first()
        self.assertIsNone(mapping.from_concept_id)
        self.assertIsNone(mapping.to_concept_id)
        self.assertIsNone(mapping.to_source_id)

        org = OrganizationFactory(mnemonic='WHO')
        source = OrganizationSourceFactory(organization=org, mnemonic='ICPC-2')
        source.update_mappings()

        mapping.refresh_from_db()
        self.assertEqual(mapping.to_source_url, source.uri)
        self.assertEqual(mapping.to_source, source)
        self.assertIsNone(mapping.to_concept)

        concept = ConceptFactory(
            parent=source, mnemonic='A73', names=[ConceptNameFactory.build(name='Malaria Updated')])
        concept.update_mappings()
        mapping.refresh_from_db()
        self.assertEqual(mapping.to_concept_code, 'A73')
        self.assertEqual(mapping.to_concept_name, 'Malaria')
        self.assertEqual(mapping.to_source, source)
        self.assertEqual(mapping.to_source, concept.parent)
        self.assertEqual(mapping.to_concept, concept)

        response = self.client.post(
            source.mappings_url,
            {
                "map_type": "Same As",
                "from_concept_code": "foo",
                "from_concept_name": "foo",
                "to_concept_code": "bar",
                "to_concept_name": "bar"
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['from_concept_code'], 'foo')
        self.assertEqual(response.data['from_concept_name'], 'foo')
        self.assertEqual(response.data['to_concept_code'], 'bar')
        self.assertEqual(response.data['to_concept_name'], 'bar')
        self.assertIsNone(response.data['to_source_name'])

    def test_post_using_canonical_url_201(self):  # pylint: disable=too-many-statements
        source = UserSourceFactory(user=self.user, mnemonic='source')
        response = self.client.post(
            source.mappings_url,
            {
                "map_type": "Same As",
                "from_source_url": "http://loinc.org",
                "from_source_version": "2.46",
                "from_concept_code": "32700-7",
                "from_concept_name": "Microscopic observation [Identifier] in Blood by Malaria smear",
                "to_source_url": "http://who.int/ICPC-2",
                "to_source_version": "v11",
                "to_concept_code": "A73",
                "to_concept_name": "Malaria"
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['from_source_url'], 'http://loinc.org')
        self.assertEqual(response.data['from_source_version'], '2.46')
        self.assertEqual(response.data['from_concept_code'], '32700-7')
        self.assertEqual(
            response.data['from_concept_name'], 'Microscopic observation [Identifier] in Blood by Malaria smear'
        )
        self.assertEqual(response.data['to_source_url'], "http://who.int/ICPC-2")
        self.assertEqual(response.data['to_source_version'], 'v11')
        self.assertEqual(response.data['to_concept_code'], 'A73')
        self.assertEqual(response.data['to_concept_name'], 'Malaria')
        mapping = source.mappings.first()
        self.assertIsNone(mapping.from_concept_id)
        self.assertIsNone(mapping.to_concept_id)
        self.assertIsNone(mapping.to_source_id)

        to_source = OrganizationSourceFactory(canonical_url="http://who.int/ICPC-2")
        to_source.update_mappings()
        mapping.refresh_from_db()

        self.assertEqual(mapping.to_source_url, to_source.canonical_url)
        self.assertEqual(mapping.to_source, to_source)
        self.assertEqual(mapping.to_source_version, 'v11')

        concept = ConceptFactory(parent=to_source, mnemonic='A73', names=[ConceptNameFactory.build(name='foobar')])
        concept.update_mappings()

        mapping.refresh_from_db()
        self.assertEqual(mapping.to_concept, concept)
        self.assertTrue(mapping.to_concept_code == concept.mnemonic == 'A73')
        self.assertEqual(mapping.to_concept_name, 'Malaria')
        self.assertNotEqual(concept.display_name, 'Malaria')
        self.assertEqual(mapping.from_concept, None)
        self.assertEqual(mapping.from_source, None)

        from_source = OrganizationSourceFactory(canonical_url="http://loinc.org")
        from_source.update_mappings()
        mapping.refresh_from_db()

        self.assertTrue(mapping.from_source_url == from_source.canonical_url == "http://loinc.org")
        self.assertEqual(mapping.from_source_version, "2.46")
        self.assertEqual(mapping.from_source, from_source)

        concept = ConceptFactory(
            parent=from_source, mnemonic='32700-7', names=[ConceptNameFactory.build(name='foobar')])
        concept.update_mappings()

        mapping.refresh_from_db()
        self.assertEqual(mapping.from_concept, concept)
        self.assertTrue(mapping.from_concept_code == concept.mnemonic == '32700-7')
        self.assertEqual(mapping.from_concept_name, 'Microscopic observation [Identifier] in Blood by Malaria smear')
        self.assertNotEqual(concept.display_name, 'Microscopic observation [Identifier] in Blood by Malaria smear')

        # retrievals with lookups
        response = self.client.get(
            mapping.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], mapping.uri)
        self.assertTrue('from_concept' not in response.data)
        self.assertTrue('to_concept' not in response.data)
        self.assertTrue('from_source' not in response.data)
        self.assertTrue('to_source' not in response.data)

        response = self.client.get(
            mapping.uri + '?lookupConcepts=false',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], mapping.uri)
        self.assertTrue('from_concept' not in response.data)
        self.assertTrue('to_concept' not in response.data)
        self.assertTrue('from_source' not in response.data)
        self.assertTrue('to_source' not in response.data)

        response = self.client.get(
            mapping.uri + '?lookupConcepts=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], mapping.uri)
        self.assertIsNotNone(response.data['from_concept'])
        self.assertIsNotNone(response.data['to_concept'])
        self.assertTrue('from_source' not in response.data)
        self.assertTrue('to_source' not in response.data)

        response = self.client.get(
            mapping.uri + '?lookupFromConcept=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], mapping.uri)
        self.assertIsNotNone(response.data['from_concept'])
        self.assertTrue('to_concept' not in response.data)
        self.assertTrue('from_source' not in response.data)
        self.assertTrue('to_source' not in response.data)

        response = self.client.get(
            mapping.uri + '?lookupToConcept=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], mapping.uri)
        self.assertIsNotNone(response.data['to_concept'])
        self.assertTrue('from_concept' not in response.data)
        self.assertTrue('from_source' not in response.data)
        self.assertTrue('to_source' not in response.data)

        response = self.client.get(
            mapping.uri + '?lookupSources=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], mapping.uri)
        self.assertTrue('from_concept' not in response.data)
        self.assertTrue('to_concept' not in response.data)
        self.assertIsNotNone(response.data['from_source'])
        self.assertIsNotNone(response.data['to_source'])

        response = self.client.get(
            mapping.uri + '?lookupFromSource=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], mapping.uri)
        self.assertTrue('from_concept' not in response.data)
        self.assertTrue('to_concept' not in response.data)
        self.assertIsNotNone(response.data['from_source'])
        self.assertTrue('to_source' not in response.data)

        response = self.client.get(
            mapping.uri + '?lookupToSource=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], mapping.uri)
        self.assertTrue('from_concept' not in response.data)
        self.assertTrue('to_concept' not in response.data)
        self.assertIsNotNone(response.data['to_source'])
        self.assertTrue('from_source' not in response.data)

        response = self.client.get(
            mapping.uri + '?lookupFromSource=true&lookupToSource=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], mapping.uri)
        self.assertTrue('from_concept' not in response.data)
        self.assertTrue('to_concept' not in response.data)
        self.assertIsNotNone(response.data['from_source'])
        self.assertIsNotNone(response.data['to_source'])


class MappingRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.source = UserSourceFactory(user=self.user)
        self.mapping = MappingFactory(parent=self.source)

    def test_get_200(self):
        response = self.client.get(self.mapping.uri, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.mapping.id))

    def test_get_404(self):
        response = self.client.get(
            self.source.mappings_url + '123/', format='json'
        )
        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        self.assertEqual(self.mapping.versions.count(), 1)
        self.assertEqual(self.mapping.get_latest_version().map_type, SAME_AS)

        response = self.client.put(
            self.mapping.uri,
            dict(map_type='narrower than'),
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.mapping.get_latest_version().id))
        self.assertEqual(response.data['map_type'], 'narrower than')
        self.assertEqual(self.mapping.versions.count(), 2)
        self.assertEqual(self.mapping.get_latest_version().map_type, 'narrower than')

        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.map_type, 'narrower than')

    def test_put_200_sort_weight(self):
        self.assertEqual(self.mapping.versions.count(), 1)
        self.assertEqual(self.mapping.get_latest_version().sort_weight, None)

        response = self.client.put(
            self.mapping.uri,
            dict(sort_weight=1.67983),
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.mapping.get_latest_version().id))
        self.assertEqual(response.data['sort_weight'], 1.67983)
        self.assertEqual(self.mapping.versions.count(), 2)
        self.assertEqual(self.mapping.get_latest_version().sort_weight, 1.67983)

        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.sort_weight, 1.67983)

    def test_put_400(self):
        self.assertEqual(self.mapping.versions.count(), 1)
        self.assertEqual(self.mapping.get_latest_version().map_type, SAME_AS)

        response = self.client.put(
            self.mapping.uri,
            dict(map_type=''),
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data, dict(map_type=[ErrorDetail(string='This field may not be blank.', code='blank')])
        )
        self.assertEqual(self.mapping.versions.count(), 1)

    def test_delete_204(self):
        self.assertEqual(self.mapping.versions.count(), 1)
        self.assertFalse(self.mapping.get_latest_version().retired)

        response = self.client.delete(
            self.mapping.uri,
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.mapping.versions.count(), 2)
        self.assertFalse(self.mapping.versions.first().retired)

        latest_version = self.mapping.get_latest_version()
        self.assertTrue(latest_version.retired)
        self.assertEqual(latest_version.comment, 'Mapping was retired')

        self.mapping.refresh_from_db()
        self.assertTrue(self.mapping.retired)

    def test_hard_delete_403(self):
        response = self.client.delete(
            self.mapping.uri + '?hardDelete=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 403)

    def test_hard_delete_204(self):
        token = UserProfileFactory(is_superuser=True, is_staff=True).get_token()
        response = self.client.delete(
            self.mapping.uri + '?hardDelete=true',
            HTTP_AUTHORIZATION='Token ' + token,
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Mapping.objects.filter(id=self.mapping.id).exists())


class MappingVersionsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.source = UserSourceFactory(user=self.user)
        self.mapping = MappingFactory(parent=self.source)

    def test_get_200(self):
        latest_version = self.mapping.get_latest_version()

        response = self.client.get(self.mapping.url + 'versions/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertTrue(response.data[0]['is_latest_version'])
        self.assertEqual(response.data[0]['version_url'], latest_version.uri)
        self.assertEqual(response.data[0]['versioned_object_id'], self.mapping.id)


class MappingVersionRetrieveViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.source = UserSourceFactory(user=self.user)
        self.mapping = MappingFactory(parent=self.source)

    def test_get_200(self):
        latest_version = self.mapping.get_latest_version()

        response = self.client.get(self.mapping.url + f'{latest_version.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['is_latest_version'], True)
        self.assertEqual(response.data['version_url'], latest_version.uri)
        self.assertEqual(response.data['versioned_object_id'], self.mapping.id)

    def test_get_404(self):
        response = self.client.get(self.mapping.url + 'unknown/')

        self.assertEqual(response.status_code, 404)

    def test_soft_delete_204(self):
        admin_token = UserProfile.objects.get(username='ocladmin').get_token()
        mapping_v1 = MappingFactory(
            parent=self.source, version='v1', mnemonic=self.mapping.mnemonic, to_concept=self.mapping.to_concept,
            from_concept=self.mapping.from_concept, map_type=self.mapping.map_type
        )

        response = self.client.delete(
            self.mapping.url + f'{mapping_v1.version}/',
            HTTP_AUTHORIZATION=f'Token {admin_token}',
        )

        self.assertEqual(response.status_code, 204)
        self.assertTrue(Mapping.objects.filter(id=mapping_v1.id).exists())
        mapping_v1.refresh_from_db()
        self.assertFalse(mapping_v1.is_active)

    def test_hard_delete_204(self):
        admin_token = UserProfile.objects.get(username='ocladmin').get_token()
        mapping_v1 = MappingFactory(
            parent=self.source, version='v1', mnemonic=self.mapping.mnemonic, to_concept=self.mapping.to_concept,
            from_concept=self.mapping.from_concept, map_type=self.mapping.map_type
        )

        response = self.client.delete(
            f'{self.mapping.url}{mapping_v1.version}/?hardDelete=true',
            HTTP_AUTHORIZATION=f'Token {admin_token}',
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Mapping.objects.filter(id=mapping_v1.id).exists())


class MappingExtrasViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.extras = dict(foo='bar', tao='ching')
        self.concept = MappingFactory(extras=self.extras)
        self.user = UserProfileFactory(organizations=[self.concept.parent.organization])
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(self.concept.uri + 'extras/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.extras)


class MappingExtraRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.extras = dict(foo='bar', tao='ching')
        self.mapping = MappingFactory(extras=self.extras)
        self.user = UserProfileFactory(organizations=[self.mapping.parent.organization])
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(self.mapping.uri + 'extras/foo/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='bar'))

    def test_get_404(self):
        response = self.client.get(self.mapping.uri + 'extras/bar/', format='json')

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        self.assertEqual(self.mapping.versions.count(), 1)
        self.assertEqual(self.mapping.get_latest_version().extras, self.extras)
        self.assertEqual(self.mapping.extras, self.extras)

        response = self.client.put(
            self.mapping.uri + 'extras/foo/',
            dict(foo='foobar'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='foobar'))
        self.assertEqual(self.mapping.versions.count(), 2)
        self.assertEqual(self.mapping.get_latest_version().extras, dict(foo='foobar', tao='ching'))
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.extras, dict(foo='foobar', tao='ching'))

    def test_put_400(self):
        self.assertEqual(self.mapping.versions.count(), 1)
        self.assertEqual(self.mapping.get_latest_version().extras, self.extras)
        self.assertEqual(self.mapping.extras, self.extras)

        response = self.client.put(
            self.mapping.uri + 'extras/foo/',
            dict(tao='foobar'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, ['Must specify foo param in body.'])
        self.assertEqual(self.mapping.versions.count(), 1)
        self.assertEqual(self.mapping.get_latest_version().extras, self.extras)
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.extras, self.extras)

    def test_delete_204(self):
        self.assertEqual(self.mapping.versions.count(), 1)
        self.assertEqual(self.mapping.get_latest_version().extras, self.extras)
        self.assertEqual(self.mapping.extras, self.extras)

        response = self.client.delete(
            self.mapping.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.mapping.versions.count(), 2)
        self.assertEqual(self.mapping.get_latest_version().extras, dict(tao='ching'))
        self.assertEqual(self.mapping.versions.first().extras, dict(foo='bar', tao='ching'))
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.extras, dict(tao='ching'))


class MappingReactivateViewTest(OCLAPITestCase):
    def test_put(self):
        mapping = MappingFactory(retired=True)
        self.assertTrue(mapping.retired)
        self.assertTrue(mapping.get_latest_version().retired)
        token = mapping.created_by.get_token()

        response = self.client.put(
            mapping.url + 'reactivate/',
            HTTP_AUTHORIZATION='Token ' + token,
        )

        self.assertEqual(response.status_code, 204)
        mapping.refresh_from_db()
        self.assertFalse(mapping.retired)
        self.assertFalse(mapping.get_latest_version().retired)
        self.assertTrue(mapping.get_latest_version().prev_version.retired)

        response = self.client.put(
            mapping.url + 'reactivate/',
            HTTP_AUTHORIZATION='Token ' + token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'__all__': 'Mapping is already not retired'})


class MappingCollectionMembershipViewTest(OCLAPITestCase):
    def test_get_200(self):
        parent = OrganizationSourceFactory()
        mapping1 = MappingFactory(parent=parent)
        mapping2 = MappingFactory()  # random owner/parent
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
        expansion1.mappings.add(mapping1)
        expansion2.mappings.add(mapping1)
        expansion3.mappings.add(mapping1)
        expansion1.mappings.add(mapping2)
        expansion2.mappings.add(mapping2)
        expansion3.mappings.add(mapping2)

        response = self.client.get(mapping1.url + 'collection-versions/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data]),
            sorted([collection2.url, collection1.url])
        )

        response = self.client.get(mapping2.url + 'collection-versions/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)
