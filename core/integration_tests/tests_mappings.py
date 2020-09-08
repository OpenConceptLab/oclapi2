from rest_framework.exceptions import ErrorDetail

from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.constants import SAME_AS
from core.mappings.tests.factories import MappingFactory
from core.sources.tests.factories import UserSourceFactory
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

        response = self.client.get(collection.mappings_url, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        collection.add_references(expressions=[mapping.uri])

        response = self.client.get(collection.mappings_url, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_post_405(self):
        response = self.client.post(
            '/mappings/',
            dict(foo='bar'),
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 405)

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
                from_concept_url=[ErrorDetail(string='This field is required.', code='required')],
                to_concept_url=[ErrorDetail(string='This field is required.', code='required')]
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

        response = self.client.post(
            source.mappings_url,
            dict(map_type='same as', from_concept_url=concept2.uri, to_concept_url=concept1.uri),
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"__all__": ["Parent, map_type, from_concept, to_concept must be unique."]})

        response = self.client.post(
            source.mappings_url,
            dict(map_type='same as', from_concept_url=concept2.uri, to_concept_url=concept2.uri),
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'__all__': ['Cannot map concept to itself.']})


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
        self.assertEqual(response.data['uuid'], str(self.mapping.get_latest_version().id))

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

        response = self.client.get(self.mapping.url + '{}/'.format(latest_version.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['is_latest_version'], True)
        self.assertEqual(response.data['version_url'], latest_version.uri)
        self.assertEqual(response.data['versioned_object_id'], self.mapping.id)

    def test_get_404(self):
        response = self.client.get(self.mapping.url + '123/')

        self.assertEqual(response.status_code, 404)
