from mock.mock import patch, Mock

from django.test import tag
from core.collections.models import CollectionReference, Collection, Expansion
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory, UserCollectionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLAPITestCase
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.url_registry.tests_resolution import set_registry_toggle
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory
from core.value_sets.serializers import ValueSetDetailSerializer


@tag('es')
class ValueSetTest(OCLAPITestCase):
    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def setUp(self):
        set_registry_toggle(False)  # the operations resolve through core.fhir.resolve: no registry, as in production
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
            mnemonic='c1', canonical_url='http://c1.com', version='v1', organization=self.collection.organization,
            released=True)
        expansion = ExpansionFactory(mnemonic='e1', collection_version=self.collection)
        self.collection.expansion_uri = expansion.uri
        self.collection.save()
        expansion_v2 = ExpansionFactory(mnemonic='e2', collection_version=self.collection_v1)
        self.collection_v1.expansion_uri = expansion_v2.uri
        self.collection_v1.save()

    def test_public_can_find_globally_without_compose(self):
        response = self.client.get('/fhir/ValueSet/?url=http://c1.com')

        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(response.data['total'], 1)

        resource = response.data['entry'][0]['resource']

        self.assertEqual(
            resource['identifier'][0]['value'], f'/orgs/{self.org.mnemonic}/ValueSet/{self.collection.mnemonic}/')
        self.assertFalse('compose' in resource)

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
        self.assertNotIn('compose', response.data['entry'][0]['resource'])  # a search entry is a summary
        resource = self.client.get(response.data['entry'][0]['fullUrl']).data
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
        self.assertFalse('compose' in resource)

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

    def test_create_with_is_a_filter_and_system(self):
        ConceptDocument().update(self.org_source_v2.head.concepts_set.all())

        response = self.client.post(
            f'/users/{self.user.username}/ValueSet/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
               "resourceType": "ValueSet",
              "id": "v3-ActEncounterCode",
              "language": "en",
              "text": {
                "status": "extensions",
                "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"en\" lang=\"en\"><p>This value set includes codes based on the following rules:</p><ul><li>Include codes from <a href=\"CodeSystem-v3-ActCode.html\"><code>http://terminology.hl7.org/CodeSystem/v3-ActCode</code></a> where concept  is-a  <a href=\"CodeSystem-v3-ActCode.html#v3-ActCode-_ActEncounterCode\">_ActEncounterCode</a></li></ul><p>This value set excludes codes based on the following rules:</p><ul><li>Exclude these codes as defined in <a href=\"CodeSystem-v3-ActCode.html\"><code>http://terminology.hl7.org/CodeSystem/v3-ActCode</code></a><table class=\"none\"><tr><td style=\"white-space:nowrap\"><b>Code</b></td><td><b>Display</b></td><td><b>Definition</b></td></tr><tr><td><a href=\"CodeSystem-v3-ActCode.html#v3-ActCode-_ActEncounterCode\">_ActEncounterCode</a></td><td>ActEncounterCode</td><td>Domain provides codes that qualify the ActEncounterClass (ENC)</td></tr></table></li></ul></div>"  # pylint: disable=line-too-long
              },
              "url": "http://terminology.hl7.org/ValueSet/v3-ActEncounterCode",
              "identifier": [
                {
                  "system": "urn:ietf:rfc:3986",
                  "value": "urn:oid:2.16.840.1.113883.1.11.13955"
                }
              ],
              "version": "2.0.0",
              "name": "ActEncounterCode",
              "title": "ActEncounterCode",
              "status": "active",
              "date": "2014-03-26",
              "description": "Domain provides codes that qualify the ActEncounterClass (ENC)",
              "compose": {
                "include": [
                  {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "filter": [
                      {
                        "property": "concept",
                        "op": "is-a",
                        "value": "_ActEncounterCode"
                      }
                    ]
                  }
                ],
                "exclude": [
                  {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "concept": [
                      {
                        "code": "_ActEncounterCode"
                      }
                    ]
                  }
                ]
              }
            },
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        resource = response.data
        self.assertEqual(resource['version'], '2.0.0')
        self.assertEqual(
            resource['identifier'][0],
            {'system': 'urn:ietf:rfc:3986', 'value': 'urn:oid:2.16.840.1.113883.1.11.13955'}
        )
        self.assertEqual(
            resource['identifier'][1]['value'],
            f'/users/{self.user.username}/ValueSet/v3-ActEncounterCode/'
        )
        self.assertEqual(len(resource['compose']['include']), 1)
        self.assertEqual(
            resource['compose']['include'][0]['system'],
            'http://terminology.hl7.org/CodeSystem/v3-ActCode'
        )
        self.assertEqual(
            resource['compose']['include'][0]['filter'][0],
            {'property': 'concept', 'op': 'is-a', 'value': '_ActEncounterCode'}
        )
        self.assertIsNone(resource['compose']['include'][0].get('concept'))
        self.assertEqual(
            resource['compose']['exclude'],
            [{
                 'system': 'http://terminology.hl7.org/CodeSystem/v3-ActCode',
                 'concept': [{'code': '_ActEncounterCode'}]
             }]
        )

    def test_create_with_filter_and_system_and_no_filter(self):
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
        self.org.members.add(self.user)  # changing an org's value set needs membership
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
        self.assertFalse('compose' in resource)

    def test_update_with_compose(self):
        self.org.members.add(self.user)  # changing an org's value set needs membership
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

    def release_system(self):
        """FHIR shows code systems with a release: the org source's v2 is one."""
        Source.objects.filter(id=self.org_source_v2.id).update(released=True)

    def expand(self, collection, *concepts):
        """The concepts, as the org source's v2 holds them, in the collection version's default expansion."""
        collection.expansion.concepts.add(*Concept.objects.filter(
            sources=self.org_source_v2, mnemonic__in=[concept.mnemonic for concept in concepts]))

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
        self.release_system()
        self.expand(self.collection_v1, self.concept_1, self.concept_2)

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
        self.release_system()
        self.expand(self.collection_v1, self.concept_1, self.concept_2)

        response = self.client.get(
            f'/fhir/ValueSet/$validate-code/'
            f'?url=http://c1.com&system=http://some/url&systemVersion={self.org_source_v2.version}'
            f'&code={self.concept_1.mnemonic}'
        )

        resource = response.data
        self.assertEqual(resource['parameter'][0]['name'], 'result')
        self.assertEqual(resource['parameter'][0]['valueBoolean'], True)

    def test_validate_code_globally_via_post(self):
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
        self.release_system()
        self.expand(self.collection_v1, self.concept_1, self.concept_2)

        response = self.client.post(
            '/fhir/ValueSet/$validate-code/',
            data={
                'resourceType': 'Parameters',
                'parameter': [
                    {'name': 'url', 'valueUri': 'http://c1.com'},
                    {'name': 'system', 'valueUri': 'http://some/url'},
                    {'name': 'systemVersion', 'valueString': self.org_source_v2.version},
                    {'name': 'code', 'valueCode': self.concept_1.mnemonic}
                ]
            },
            format='json'
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

    def test_validate_code_private_value_set_only_for_members_and_staff(self):
        """A private value set: at its owner's base for members and staff; anyone else, and anyone at the global base
        (which shows public value sets only), is answered as for a value set that doesn't exist."""
        self.release_system()
        self.expand(self.collection_v1, self.concept_1)
        Collection.objects.filter(mnemonic='c1', organization=self.org).update(public_access=ACCESS_TYPE_NONE)
        member = UserProfileFactory()
        self.org.members.add(member)
        admin = UserProfile.objects.get(username='ocladmin')
        query = f'system=http://some/url&systemVersion={self.org_source_v2.version}&code={self.concept_1.mnemonic}'
        route = f'/orgs/{self.org.mnemonic}/ValueSet/{self.collection.mnemonic}/$validate-code/?{query}'
        by_url = f'/fhir/ValueSet/$validate-code/?url=http://c1.com&{query}'

        for headers, expected in [
                ({}, 404),
                ({'HTTP_AUTHORIZATION': 'Token ' + self.user_token}, 404),
                ({'HTTP_AUTHORIZATION': 'Token ' + member.get_token()}, True),
                ({'HTTP_AUTHORIZATION': 'Token ' + admin.get_token()}, True),
        ]:
            response = self.client.get(route, **headers)
            if expected is True:
                self.assertEqual(response.status_code, 200, headers)
                self.assertTrue(response.data['parameter'][0]['valueBoolean'], headers)
            else:
                self.assertEqual(response.status_code, expected, headers)
                self.assertEqual(response.data['issue'][0]['code'], 'not-found')
            response = self.client.get(by_url, **headers)
            self.assertEqual(response.status_code, 422, headers)
            self.assertEqual(response.data['issue'][0]['code'], 'not-found')

    def add_reference(self, collection, concept):
        if not collection.expansion_uri:
            collection.expansion_uri = ExpansionFactory(collection_version=collection).uri
            collection.save()
        collection.add_references([
            CollectionReference(
                expression=concept.uri, collection=collection, code=concept.mnemonic,
                system=concept.parent.uri, version='v2'
            ),
        ])

    def released(self, collection, concept):
        """A release of a collection, whose default expansion holds the concept."""
        owner = {'user': collection.user} if collection.user_id else {'organization': collection.organization}
        factory = UserCollectionFactory if collection.user_id else OrganizationCollectionFactory
        release = factory(mnemonic=collection.mnemonic, version='v1', released=True,
                          public_access=collection.public_access, **owner)
        self.add_reference(release, concept)
        self.expand(release, concept)
        return release

    def test_validate_code_reads_only_value_sets_the_requester_can_view(self):
        """
        Only the value set the request names is read (`url`, or the route's), never one other query params pick; one
        the requester can't view answers as one that doesn't exist. Without `url`, the global route checks the code
        system alone.
        """
        self.release_system()
        self.expand(self.collection_v1, self.concept_1)
        private_org = OrganizationFactory()
        member = UserProfileFactory()
        private_org.members.add(member)
        priv = self.released(OrganizationCollectionFactory(
            organization=private_org, mnemonic='priv', public_access=ACCESS_TYPE_NONE), self.concept_2)
        self.released(OrganizationCollectionFactory(organization=private_org, mnemonic='pub'), self.concept_1)
        owner = UserProfileFactory()
        upriv = self.released(
            UserCollectionFactory(user=owner, mnemonic='upriv', public_access=ACCESS_TYPE_NONE), self.concept_2)
        query = f'system=http://some/url&systemVersion={self.org_source_v2.version}&code='
        private_org_params = f'org={private_org.mnemonic}&collection=priv'

        for headers in [{}, {'HTTP_AUTHORIZATION': 'Token ' + self.user_token}]:
            for url in [
                    f'/orgs/{self.org.mnemonic}/ValueSet/c1/$validate-code/?{private_org_params}&{query}',
                    f'/orgs/{private_org.mnemonic}/ValueSet/pub/$validate-code/?collection=priv&{query}',
                    f'/fhir/ValueSet/{self.collection_v1.id}/$validate-code/?user={owner.username}&collection=upriv&'
                    f'{query}',
            ]:
                response = self.client.get(url + self.concept_2.mnemonic, **headers)
                self.assertEqual(response.status_code, 200, (url, headers))
                self.assertFalse(response.data['parameter'][0]['valueBoolean'], (url, headers))
            for url in [
                    f'/orgs/{private_org.mnemonic}/ValueSet/priv/$validate-code/?{query}',
                    f'/users/{owner.username}/ValueSet/upriv/$validate-code/?{query}',
                    f'/fhir/ValueSet/{priv.id}/$validate-code/?{query}',
                    f'/fhir/ValueSet/{upriv.id}/$validate-code/?user={owner.username}&{query}',
            ]:
                response = self.client.get(url + self.concept_2.mnemonic, **headers)
                self.assertEqual(response.status_code, 404, (url, headers))

            # Without `url`, the global route checks the code system alone; other params don't pick a value set
            for concept in [self.concept_1, self.concept_2]:
                response = self.client.get(
                    f'/fhir/ValueSet/$validate-code/?{private_org_params}&{query}{concept.mnemonic}', **headers)
                self.assertTrue(response.data['parameter'][0]['valueBoolean'], (concept.mnemonic, headers))

        for url, user in [
                (f'/orgs/{private_org.mnemonic}/ValueSet/priv/$validate-code/?{query}', member),
                (f'/users/{owner.username}/ValueSet/upriv/$validate-code/?{query}', owner),
        ]:
            response = self.client.get(url + self.concept_2.mnemonic, HTTP_AUTHORIZATION='Token ' + user.get_token())
            self.assertTrue(response.data['parameter'][0]['valueBoolean'], url)
        response = self.client.get(f'/fhir/ValueSet/{self.collection_v1.id}/$validate-code/?{query}'
                                   f'{self.concept_1.mnemonic}')
        self.assertTrue(response.data['parameter'][0]['valueBoolean'])

    def test_validate_code_with_display_for_code_not_in_value_set(self):
        self.add_reference(self.collection, self.concept_1)

        response = self.client.get(
            f'/orgs/{self.org.mnemonic}/ValueSet/c1/$validate-code/'
            f'?system=http://some/url&systemVersion={self.org_source_v2.version}&code=missing&display=x'
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['parameter'][0]['valueBoolean'])

    def test_expand(self):
        """$expand reads the released version's stored expansion, by GET or POST, and a POST never makes another."""
        self.client.post(
            f'/users/{self.user.mnemonic}/ValueSet/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'resourceType': 'ValueSet',
                'id': 'c2',
                'url': 'http://c2.com',
                'status': 'active',
                'version': '1',
                'name': 'collection1',
                'description': 'This is a test collection',
                'compose': {
                    'include': [
                        {
                            'system': 'http://some/url',
                            'version': self.org_source_v2.version,
                            'namespace': self.org.uri,
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
        expansions = list(Expansion.objects.values_list('id', flat=True))

        posted = self.client.post(
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
        response = self.client.get(
            f'/users/{self.user.mnemonic}/ValueSet/c2/$expand/', {'filter': self.concept_1.mnemonic},
            HTTP_AUTHORIZATION='Token ' + self.user_token)

        for resource in (posted.data, response.data):
            self.assertEqual(resource['resourceType'], 'ValueSet')
            expansion = resource['expansion']
            self.assertIsNotNone(expansion['timestamp'])
            self.assertIn('/users/' + self.user.mnemonic + '/collections/c2/1/expansions', expansion['identifier'])
            self.assertEqual(len(expansion['contains']), 1)
            self.assertEqual(expansion['contains'][0]['code'], self.concept_1.mnemonic)
        self.assertEqual(posted.content, response.content)
        self.assertEqual(list(Expansion.objects.values_list('id', flat=True)), expansions)

    def test_unable_to_represent_as_fhir(self):
        instance = Collection(id='1', uri='/invalid/uri')
        serialized = ValueSetDetailSerializer(instance=instance).data
        self.assertDictEqual(serialized, {
            'resourceType': 'OperationOutcome',
            'issue': [{'severity': 'error', 'details': 'Failed to represent "/invalid/uri" as ValueSet'}]})
