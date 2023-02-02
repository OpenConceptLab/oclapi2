from core.collections.models import CollectionReference, Collection
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.tests import OCLAPITestCase
from core.concepts.documents import ConceptDocument
from core.concepts.tests.factories import ConceptFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.tests.factories import UserProfileFactory
from core.value_sets.serializers import ValueSetDetailSerializer


class ValueSetTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory()
        self.org_source = OrganizationSourceFactory(organization=self.org, canonical_url='http://some/url')
        self.org_source_v1 = OrganizationSourceFactory.build(
            version='v1', mnemonic=self.org_source.mnemonic, organization=self.org_source.parent)
        Source.persist_new_version(self.org_source_v1, self.org_source.created_by)

        self.concept_1 = ConceptFactory(parent=self.org_source)
        self.concept_2 = ConceptFactory(parent=self.org_source)
        self.org_source_v2 = OrganizationSourceFactory.build(
            version='v2', mnemonic=self.org_source.mnemonic, organization=self.org_source.parent)
        Source.persist_new_version(self.org_source_v2, self.org_source.created_by)

        self.user = UserProfileFactory()
        self.user_token = self.user.get_token()
        self.user_source = UserSourceFactory(user=self.user, public_access='None', canonical_url='http://some/url')
        self.user_source_v1 = UserSourceFactory.build(
            version='v1', mnemonic=self.user_source.mnemonic, user=self.user_source.parent)
        Source.persist_new_version(self.user_source_v1, self.user_source.created_by)

        self.collection = OrganizationCollectionFactory(
            organization=self.org, mnemonic='c1', canonical_url='http://c1.com', version='HEAD')
        self.collection_v1 = OrganizationCollectionFactory(
            mnemonic='c1', canonical_url='http://c1.com', version='v1', organization=self.collection.organization)
        expansion = ExpansionFactory(mnemonic='e1', collection_version=self.collection)
        self.collection.expansion_uri = expansion.uri
        self.collection.save()
        expansion_v2 = ExpansionFactory(mnemonic='e2', collection_version=self.collection_v1)
        self.collection_v1.expansion_uri = expansion_v2.uri
        self.collection_v1.save()

    def test_public_can_find_globally_without_compose(self):
        response = self.client.get('/fhir/ValueSet/?url=http://c1.com')

        self.assertEqual(len(response.data['entry']), 1)

        resource = response.data['entry'][0]['resource']

        self.assertEqual(
            resource['identifier'][0]['value'], f'/orgs/{self.org.mnemonic}/ValueSet/{self.collection.mnemonic}/')
        self.assertEqual(resource['compose'], None)

    def test_public_can_find_globally(self):
        self.collection.add_references([
            CollectionReference(
                expression=self.concept_1.uri, collection=self.collection, code=self.concept_1.mnemonic,
                system=self.concept_1.parent.uri, version='v2'),
            CollectionReference(
                expression=self.concept_2.uri, collection=self.collection, code=self.concept_2.mnemonic,
                system=self.concept_2.parent.uri, version='v2')
        ])
        self.collection_v1.seed_references()

        response = self.client.get('/fhir/ValueSet/?url=http://c1.com')

        self.assertEqual(len(response.data['entry']), 1)
        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'], f'/orgs/{self.org.mnemonic}/ValueSet/{self.collection.mnemonic}/')
        self.assertEqual(len(resource['compose']['include']), 1)
        self.assertEqual(resource['compose']['include'][0]['system'],
                         f'/orgs/{self.org.mnemonic}/ValueSet/{self.org_source_v2.mnemonic}/')
        self.assertEqual(resource['compose']['include'][0]['version'], self.org_source_v2.version)
        self.assertEqual(len(resource['compose']['include'][0]['concept']), 2)

    def test_public_can_view(self):
        self.collection.add_references([
            CollectionReference(
                expression=self.concept_1.uri, collection=self.collection, code=self.concept_1.mnemonic,
                system=self.concept_1.parent.uri, version='v2'),
            CollectionReference(
                expression=self.concept_2.uri, collection=self.collection, code=self.concept_2.mnemonic,
                system=self.concept_2.parent.uri, version='v2'),
        ])
        self.collection_v1.seed_references()

        response = self.client.get('/orgs/' + self.org.mnemonic + '/ValueSet/c1/')

        resource = response.data
        self.assertEqual(
            resource['identifier'][0]['value'], f'/orgs/{self.org.mnemonic}/ValueSet/{self.collection.mnemonic}/')
        self.assertEqual(len(resource['compose']['include']), 1)
        self.assertEqual(resource['compose']['include'][0]['system'],
                         f'/orgs/{self.org.mnemonic}/ValueSet/{self.org_source_v2.mnemonic}/')
        self.assertEqual(resource['compose']['include'][0]['version'], self.org_source_v2.version)
        self.assertEqual(len(resource['compose']['include'][0]['concept']), 2)

    def test_can_create_empty(self):
        response = self.client.post(
            f'/users/{self.user.mnemonic}/ValueSet/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'resourceType': 'ValueSet',
                'id': 'c2',
                'version': 'v1',
                'url': 'http://c2.com',
                'status': 'draft',
                'name': 'collection1',
                'description': 'This is a test collection'
            },
            format='json'
        )

        resource = response.data
        self.assertEqual(resource['version'], 'v1')
        self.assertEqual(resource['identifier'][0]['value'], f'/users/{self.user.username}/ValueSet/c2/')
        self.assertEqual(resource['compose'], None)

    def test_can_create_with_compose(self):
        response = self.client.post(
            f'/users/{self.user.mnemonic}/ValueSet/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'resourceType': 'ValueSet',
                'id': 'c2',
                'url': 'http://c2.com',
                'status': 'draft',
                'name': 'collection1',
                'description': 'This is a test collection',
                'compose': {
                    'include': [
                        {
                            'system': 'http://some/url',
                            'version': self.org_source_v2.version,
                            'concept': [
                                {
                                    'code': self.concept_1.mnemonic
                                }
                            ]
                        }
                    ]
                }
            },
            format='json'
        )

        resource = response.data
        self.assertEqual(resource['version'], '0.1')
        self.assertEqual(resource['identifier'][0]['value'], f'/users/{self.user.username}/ValueSet/c2/')
        self.assertEqual(len(resource['compose']['include']), 1)
        self.assertEqual(resource['compose']['include'][0]['system'], 'http://some/url')
        self.assertEqual(resource['compose']['include'][0]['version'], self.org_source_v2.version)
        self.assertEqual(len(resource['compose']['include'][0]['concept']), 1)

    def test_create_with_filter_and_system(self):
        ConceptDocument().update(self.org_source_v2.head.concepts_set.all())

        response = self.client.post(
            f'/users/{self.user.username}/ValueSet/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'resourceType': 'ValueSet',
                'id': 'c2',
                'url': 'http://c2.com',
                'status': 'draft',
                'name': 'collection1',
                'description': 'This is a test collection',
                'compose': {
                    'include': [
                        {
                            'system': 'http://some/url',
                            'version': self.org_source_v2.version,
                            'filter': [
                                {
                                    'property': 'q',
                                    'op': '=',
                                    'value': self.concept_1.mnemonic
                                }
                            ]
                        }
                    ]
                }
            },
            format='json'
        )

        resource = response.data
        self.assertEqual(resource['version'], '0.1')
        self.assertEqual(resource['identifier'][0]['value'], f'/users/{self.user.username}/ValueSet/c2/')
        self.assertEqual(len(resource['compose']['include']), 1)
        self.assertEqual(resource['compose']['include'][0]['system'], 'http://some/url')
        self.assertEqual(resource['compose']['include'][0]['version'], self.org_source_v2.version)
        self.assertIsNone(resource['compose']['include'][0].get('concept'))

    def test_create_with_filter_and_concept(self):
        ConceptDocument().update(self.org_source_v2.head.concepts_set.all())

        response = self.client.post(
            f'/users/{self.user.mnemonic}/ValueSet/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'resourceType': 'ValueSet',
                'id': 'c2',
                'url': 'http://c2.com',
                'status': 'draft',
                'name': 'collection1',
                'description': 'This is a test collection',
                'compose': {
                    'include': [
                        {
                            'system': 'http://some/url',
                            'version': self.org_source_v2.version,
                            'filter': [
                                {
                                    'property': 'q',
                                    'op': '=',
                                    'value': self.concept_2.mnemonic
                                }
                            ]
                        }
                    ]
                }
            },
            format='json'
        )

        resource = response.data
        self.assertEqual(resource['version'], '0.1')
        self.assertEqual(resource['identifier'][0]['value'], f'/users/{self.user.username}/ValueSet/c2/')
        self.assertEqual(len(resource['compose']['include']), 1)
        self.assertEqual(resource['compose']['include'][0]['system'], 'http://some/url')
        self.assertEqual(resource['compose']['include'][0]['version'], self.org_source_v2.version)
        self.assertEqual(len(resource['compose']['include'][0]['filter']), 1)
        self.assertEqual(resource['compose']['include'][0]['filter'][0]['property'], 'q')
        self.assertEqual(resource['compose']['include'][0]['filter'][0]['value'], self.concept_2.mnemonic)
        self.assertIsNone(resource['compose']['include'][0].get('concept'))

    def test_can_update_empty(self):
        response = self.client.put(
            f'/orgs/{self.org.mnemonic}/ValueSet/c1/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'resourceType': 'ValueSet',
                'id': 'c1',
                'version': 'v2',
                'url': 'http://c2.com',
                'status': 'draft',
                'name': 'collection1',
                'description': 'This is a test collection'
            },
            format='json'
        )

        resource = response.data
        self.assertEqual(resource['version'], 'v2')
        self.assertEqual(resource['identifier'][0]['value'], f'/orgs/{self.org.mnemonic}/ValueSet/c1/')
        self.assertEqual(resource['compose'], None)

    def test_update_with_compose(self):
        self.collection.add_references([
            CollectionReference(
                expression=self.concept_1.uri, collection=self.collection, code=self.concept_1.mnemonic,
                system=self.concept_1.parent.uri, version='v2'
            )
        ])
        self.collection_v1.seed_references()

        response = self.client.put(
            f'/orgs/{self.org.mnemonic}/ValueSet/c1/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'resourceType': 'ValueSet',
                'id': 'c1',
                'version': 'v2',
                'url': 'http://c2.com',
                'status': 'draft',
                'name': 'collection1',
                'description': 'This is a test collection',
                'compose': {
                    'include': [
                        {
                            'system': 'http://some/url',
                            'version': self.org_source_v2.version,
                            'concept': [
                                {
                                    'code': self.concept_2.mnemonic
                                }
                            ]
                        }
                    ]
                }
            },
            format='json'
        )

        resource = response.data
        self.assertEqual(resource['version'], 'v2')
        self.assertEqual(resource['identifier'][0]['value'], f'/orgs/{self.org.mnemonic}/ValueSet/c1/')
        self.assertEqual(len(resource['compose']['include']), 2)
        self.assertEqual(resource['compose']['include'][1]['system'], 'http://some/url')
        self.assertEqual(resource['compose']['include'][1]['version'], self.org_source_v2.version)
        self.assertEqual(len(resource['compose']['include'][1]['concept']), 1)
        self.assertEqual(resource['compose']['include'][1]['concept'][0]['code'], self.concept_2.mnemonic)

    def test_validate_code(self):
        self.collection.add_references([
            CollectionReference(
                expression=self.concept_1.uri, collection=self.collection, code=self.concept_1.mnemonic,
                system=self.concept_1.parent.uri, version='v2'
            ),
            CollectionReference(
                expression=self.concept_2.uri, collection=self.collection, code=self.concept_2.mnemonic,
                system=self.concept_2.parent.uri, version='v2'
            ),
        ])
        self.collection_v1.seed_references()

        response = self.client.get(
            f'/orgs/{self.org.mnemonic}/ValueSet/{self.collection.mnemonic}/$validate-code/'
            f'?system=http://some/url&systemVersion={self.org_source_v2.version}&code={self.concept_1.mnemonic}'
        )

        resource = response.data
        self.assertEqual(resource['parameter'][0]['name'], 'result')
        self.assertEqual(resource['parameter'][0]['valueBoolean'], True)

    def test_validate_code_negative(self):
        self.collection.add_references([
            CollectionReference(
                expression=self.concept_1.uri, collection=self.collection, code=self.concept_1.mnemonic,
                system=self.concept_1.parent.uri, version='v2'
            ),
            CollectionReference(
                expression=self.concept_2.uri, collection=self.collection, code=self.concept_2.mnemonic,
                system=self.concept_2.parent.uri, version='v2'
            ),
        ])
        self.collection_v1.seed_references()

        response = self.client.get(
            f'/orgs/{self.org.mnemonic}/ValueSet/{self.collection.mnemonic}/$validate-code/'
            f'?system=http://non/existing&systemVersion={self.org_source_v2.version}&code={self.concept_1.mnemonic}'
        )

        resource = response.data
        self.assertEqual(resource['parameter'][0]['name'], 'result')
        self.assertEqual(resource['parameter'][0]['valueBoolean'], False)

    def test_validate_code_globally(self):
        self.collection.add_references([
            CollectionReference(
                expression=self.concept_1.uri, collection=self.collection, code=self.concept_1.mnemonic,
                system=self.concept_1.parent.uri, version='v2'
            ),
            CollectionReference(
                expression=self.concept_2.uri, collection=self.collection, code=self.concept_2.mnemonic,
                system=self.concept_2.parent.uri, version='v2'
            ),
        ])
        self.collection_v1.seed_references()

        response = self.client.get(
            f'/fhir/ValueSet/$validate-code/'
            f'?url=http://c1.com&system=http://some/url&systemVersion={self.org_source_v2.version}'
            f'&code={self.concept_1.mnemonic}'
        )

        resource = response.data
        self.assertEqual(resource['parameter'][0]['name'], 'result')
        self.assertEqual(resource['parameter'][0]['valueBoolean'], True)

    def test_validate_code_globally_negative(self):
        self.collection.add_references([
            CollectionReference(
                expression=self.concept_1.uri, collection=self.collection, code=self.concept_1.mnemonic,
                system=self.concept_1.parent.uri, version='v2'
            ),
            CollectionReference(
                expression=self.concept_2.uri, collection=self.collection, code=self.concept_2.mnemonic,
                system=self.concept_2.parent.uri, version='v2'
            ),
        ])
        self.collection_v1.seed_references()

        response = self.client.get(
            f'/fhir/ValueSet/$validate-code/'
            f'?url=http://c1.com&system=http://some/url&systemVersion={self.org_source_v2.version}'
            f'&code=non_existing'
        )

        resource = response.data
        self.assertEqual(resource['parameter'][0]['name'], 'result')
        self.assertEqual(resource['parameter'][0]['valueBoolean'], False)

    def test_expand(self):
        self.client.post(
            f'/users/{self.user.mnemonic}/ValueSet/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'resourceType': 'ValueSet',
                'id': 'c2',
                'url': 'http://c2.com',
                'status': 'draft',
                'name': 'collection1',
                'description': 'This is a test collection',
                'compose': {
                    'include': [
                        {
                            'system': 'http://some/url',
                            'version': self.org_source_v2.version,
                            'concept': [
                                {
                                    'code': self.concept_1.mnemonic
                                }
                            ]
                        }
                    ]
                }
            },
            format='json'
        )

        ConceptDocument().update(self.concept_1.parent.concepts_set.all())

        response = self.client.post(
            '/users/' + self.user.mnemonic + '/ValueSet/c2/$expand/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'resourceType': 'Parameters',
                'parameter': [
                    {
                        'name': 'filter',
                        'valueString': self.concept_1.mnemonic
                    }
                ]
            },
            format='json'
        )

        resource = response.data

        self.assertEqual(resource['resourceType'], 'ValueSet')
        expansion = resource['expansion']
        self.assertIsNotNone(expansion['timestamp'])
        self.assertEqual(len(expansion['contains']), 1)
        self.assertEqual(expansion['contains'][0]['code'], self.concept_1.mnemonic)

    def test_unable_to_represent_as_fhir(self):
        instance = Collection(id='1', uri='/invalid/uri')
        serialized = ValueSetDetailSerializer(instance=instance).data
        self.assertDictEqual(serialized, {
            'resourceType': 'OperationOutcome',
            'issue': [{'severity': 'error', 'details': 'Failed to represent "/invalid/uri" as ValueSet'}]})
