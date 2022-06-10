import json

from rest_framework.test import APIClient

from core.common.tests import OCLTestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, LocalizedTextFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.tests.factories import UserProfileFactory


class CodeSystemTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory()

        self.org_source = OrganizationSourceFactory(organization=self.org, canonical_url='/some/url',
                                                    full_name='source name')
        self.org_source_v1 = OrganizationSourceFactory.build(
            version='v1', mnemonic=self.org_source.mnemonic, organization=self.org_source.parent)
        Source.persist_new_version(self.org_source_v1, self.org_source.created_by)
        self.concept_1 = ConceptFactory(parent=self.org_source, names=[LocalizedTextFactory(name="concept_1_name")])
        self.concept_2 = ConceptFactory(parent=self.org_source)
        self.org_source_v2 = OrganizationSourceFactory.build(
            version='v2', mnemonic=self.org_source.mnemonic, organization=self.org_source.parent)
        Source.persist_new_version(self.org_source_v2, self.org_source.created_by)

        self.user = UserProfileFactory()
        self.user_token = self.user.get_token()
        self.user_source = UserSourceFactory(user=self.user, public_access='None', canonical_url='/some/url')
        self.user_source_v1 = UserSourceFactory.build(
            version='v1', mnemonic=self.user_source.mnemonic, user=self.user_source.parent)
        Source.persist_new_version(self.user_source_v1, self.user_source.created_by)

        self.client = APIClient()

    def test_public_can_view(self):
        response = self.client.get('/fhir/CodeSystem/?url=/some/url')

        self.assertEqual(len(response.data['entry']), 1)

        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/CodeSystem/' + self.org_source.mnemonic + '/'
        )
        self.assertEqual(resource['version'], 'v2')
        self.assertEqual(len(resource['concept']), 2)
        self.assertEqual(resource['concept'][0]['code'], self.concept_1.mnemonic)
        self.assertEqual(resource['concept'][1]['code'], self.concept_2.mnemonic)

    def test_private_can_view(self):
        response = self.client.get('/fhir/CodeSystem/?url=/some/url', HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(len(response.data['entry']), 2)
        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/users/' + self.user.mnemonic + '/CodeSystem/' + self.user_source.mnemonic + '/'
        )
        self.assertEqual(resource['version'], 'v1')
        resource_2 = response.data['entry'][1]['resource']
        self.assertEqual(
            resource_2['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/CodeSystem/' + self.org_source.mnemonic + '/'
        )
        self.assertEqual(resource_2['version'], 'v2')

    def test_public_can_list(self):
        response = self.client.get('/fhir/CodeSystem/')

        self.assertEqual(len(response.data['entry']), 1)
        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/CodeSystem/' + self.org_source.mnemonic + '/'
        )
        self.assertEqual(resource['version'], 'v2')

    def test_find_by_title(self):
        response = self.client.get('/fhir/CodeSystem/?title=source name')

        self.assertEqual(len(response.data['entry']), 1)
        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/CodeSystem/' + self.org_source.mnemonic + '/'
        )
        self.assertEqual(resource['version'], 'v2')

    def test_private_can_list(self):
        response = self.client.get('/fhir/CodeSystem/', HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(len(response.data['entry']), 2)
        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/users/' + self.user.mnemonic + '/CodeSystem/' + self.user_source.mnemonic + '/'
        )
        self.assertEqual(resource['version'], 'v1')
        resource_2 = response.data['entry'][1]['resource']
        self.assertEqual(
            resource_2['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/CodeSystem/' + self.org_source.mnemonic + '/'
        )
        self.assertEqual(resource_2['version'], 'v2')

    def test_get_code_system(self):
        response = self.client.get(f'/orgs/{self.org.mnemonic}/CodeSystem/{self.org_source.mnemonic}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.org_source.mnemonic)
        self.assertEqual(response.data['version'], 'v2')

    def test_validate_code_for_code_system(self):
        response = self.client.get(f'/fhir/CodeSystem/$validate-code'
                                   f'?url={self.org_source.canonical_url}&code={self.concept_1.mnemonic}')

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(json.dumps(response.data), json.dumps(
            {'resourceType': 'Parameters', 'parameter': [{'name': 'result', 'valueBoolean': True}]}))

    def test_validate_code_for_code_system_negative(self):
        response = self.client.get(f'/fhir/CodeSystem/$validate-code'
                                   f'?url={self.org_source.canonical_url}&code=non_existing_code')

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(json.dumps(response.data), json.dumps(
            {'resourceType': 'Parameters', 'parameter': [
                {'name': 'result', 'valueBoolean': False},
                {'name': 'message', 'valueString': 'The code is incorrect.'}
            ]}))

    def test_validate_code_with_display_for_code_system(self):
        response = self.client.get(f'/fhir/CodeSystem/$validate-code'
                                   f'?url={self.org_source.canonical_url}'
                                   f'&code={self.concept_1.mnemonic}'
                                   f'&display={self.concept_1.display_name}')

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(json.dumps(response.data), json.dumps(
            {'resourceType': 'Parameters', 'parameter': [{'name': 'result', 'valueBoolean': True}]}))

    def test_validate_code_with_display_for_code_system_negative(self):
        response = self.client.get(f'/fhir/CodeSystem/$validate-code'
                                   f'?url={self.org_source.canonical_url}'
                                   f'&code={self.concept_1.mnemonic}'
                                   f'&display=wrong_display')

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(json.dumps(response.data), json.dumps(
            {'resourceType': 'Parameters', 'parameter': [
                {'name': 'result', 'valueBoolean': False},
                {'name': 'message', 'valueString': 'The code is incorrect.'}
                ]}))

    def test_lookup_for_code_system(self):
        response = self.client.get(f'/fhir/CodeSystem/$lookup'
                                   f'?system={self.org_source.canonical_url}'
                                   f'&code={self.concept_1.mnemonic}')

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(json.dumps(response.data), json.dumps(
            {'resourceType': 'Parameters', 'parameter': [
                {'name': 'name', 'valueString': self.org_source.mnemonic},
                {'name': 'version', 'valueString': self.org_source_v2.version},
                {'name': 'display', 'valueString': self.concept_1.display_name}]}))

    def test_post_code_system_without_concepts(self):
        response = self.client.post(
            f'/users/{self.user.mnemonic}/CodeSystem/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'url': 'http://localhost/url',
                'title': 'test',
                'language': 'en',
                'identifier': [{
                                   'value': f'/users/{self.user.mnemonic}/CodeSystem/test',
                                   'type': {
                                       'coding': [{
                                                      'code': 'ACSN',
                                                      'system': 'http://hl7.org/fhir/v2/0203'
                                                  }]
                                   }
                               }],
                'version': '1.0',
                'name': 'test',
                'id': 'test',
                'status': 'retired',
                'content': 'fragment'
            },
            format='json'
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['id'], 'test')
        self.assertEqual(response.data['version'], '1.0')

        # check if HEAD persisted
        sources = Source.objects.filter(mnemonic='test', version='HEAD', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, True)
        self.assertEqual(source.name, 'test')
        # check if version persisted
        sources = Source.objects.filter(mnemonic='test', version='1.0', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, True)
        self.assertEqual(source.name, 'test')

    def test_put_code_system_without_concepts(self):
        response = self.client.put(
            f'/users/{self.user.mnemonic}/CodeSystem/{self.user_source.mnemonic}/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'url': 'http://localhost/url',
                'title': 'test',
                'language': 'en',
                'identifier': [{
                                   'value': f'/users/{self.user.mnemonic}/CodeSystem/{self.user_source.mnemonic}/',
                                   'type': {
                                       'coding': [{
                                                      'code': 'ACSN',
                                                      'system': 'http://hl7.org/fhir/v2/0203'
                                                  }]
                                   }
                               }],
                'version': '1.0',
                'name': 'test',
                'id': self.user_source.mnemonic,
                'status': 'draft',
                'content': 'fragment'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.user_source.mnemonic)
        self.assertEqual(response.data['version'], '1.0')
        self.assertEqual(response.data['status'], 'draft')

        # check if HEAD persisted
        sources = Source.objects.filter(mnemonic=self.user_source.mnemonic, version='HEAD', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, False)
        self.assertEqual(source.name, 'test')
        # check if version persisted
        sources = Source.objects.filter(mnemonic=self.user_source.mnemonic, version='1.0', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, False)
        self.assertEqual(source.name, 'test')

    def test_post_code_system_with_concepts(self):
        response = self.client.post(
            f'/users/{self.user.mnemonic}/CodeSystem/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'url': 'http://localhost/url',
                'title': 'test',
                'language': 'en',
                'identifier': [{
                                   'value': f'/users/{self.user.mnemonic}/CodeSystem/test',
                                   'type': {
                                       'coding': [{
                                                      'code': 'ACSN',
                                                      'system': 'http://hl7.org/fhir/v2/0203'
                                                  }]
                                   }
                               }],
                'version': '1.0',
                'name': 'test',
                'id': 'test',
                'status': 'retired',
                'content': 'fragment',
                'concept': [{
                                'code': 'test',
                                'display': 'Test',
                                'property': [{
                                                 'code': 'conceptclass',
                                                 'value': 'Locale'
                                             }, {
                                                 'code': 'datatype',
                                                 'value': 'N/A'
                                             }],
                                'designation': [{
                                                    'value': 'Testing',
                                                    'language': 'en',
                                                    'use': {
                                                        'code': 'fully qualified'
                                                    }
                                                }]
                            }]
            },
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['id'], 'test')
        self.assertEqual(response.data['version'], '1.0')

        # check if HEAD persisted
        sources = Source.objects.filter(mnemonic='test', version='HEAD', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, True)
        self.assertEqual(source.name, 'test')
        self.assertEqual(len(source.get_concepts_queryset().all()), 1)
        concept = source.get_concepts_queryset().first()
        self.assertEqual(concept.is_head, True)
        self.assertEqual(concept.mnemonic, 'test')
        self.assertEqual(concept.display_name, 'Test')
        self.assertEqual(len(concept.names.all()), 2)
        # check if version persisted
        sources = Source.objects.filter(mnemonic='test', version='1.0', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, True)
        self.assertEqual(source.name, 'test')
        self.assertEqual(len(source.concepts.all()), 1)
        concept = source.concepts.first()
        self.assertEqual(concept.mnemonic, 'test')
        self.assertEqual(concept.display_name, 'Test')
        self.assertEqual(concept.is_head, False)
        self.assertEqual(len(concept.names.all()), 2)

    def test_put_code_system_with_all_new_concepts(self):
        response = self.client.put(
            f'/users/{self.user.mnemonic}/CodeSystem/{self.user_source.mnemonic}/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'url': 'http://localhost/url',
                'title': 'test',
                'language': 'en',
                'identifier': [{
                                   'value': f'/users/{self.user.mnemonic}/CodeSystem/{self.user_source.mnemonic}/',
                                   'type': {
                                       'coding': [{
                                                      'code': 'ACSN',
                                                      'system': 'http://hl7.org/fhir/v2/0203'
                                                  }]
                                   }
                               }],
                'version': '1.0',
                'name': 'test',
                'id': self.user_source.mnemonic,
                'status': 'draft',
                'content': 'fragment',
                'concept': [{
                                'code': 'test',
                                'display': 'Test',
                                'property': [{
                                                 'code': 'conceptclass',
                                                 'value': 'Locale'
                                             }, {
                                                 'code': 'datatype',
                                                 'value': 'N/A'
                                             }]
                            }]
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.user_source.mnemonic)
        self.assertEqual(response.data['version'], '1.0')
        self.assertEqual(response.data['status'], 'draft')

        # check if HEAD persisted
        sources = Source.objects.filter(mnemonic=self.user_source.mnemonic, version='HEAD', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, False)
        self.assertEqual(source.name, 'test')
        self.assertEqual(len(source.get_concepts_queryset().all()), 1)
        concept = source.get_concepts_queryset().first()
        self.assertEqual(concept.is_head, True)
        self.assertEqual(concept.mnemonic, 'test')
        self.assertEqual(concept.display_name, 'Test')
        self.assertEqual(len(concept.names.all()), 1)
        # check if version persisted
        sources = Source.objects.filter(mnemonic=self.user_source.mnemonic, version='1.0', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, False)
        self.assertEqual(source.name, 'test')
        self.assertEqual(len(source.concepts.all()), 1)
        concept = source.concepts.first()
        self.assertEqual(concept.mnemonic, 'test')
        self.assertEqual(concept.display_name, 'Test')
        self.assertEqual(concept.is_head, False)
        self.assertEqual(len(concept.names.all()), 1)

    def test_put_code_system_with_existing_concepts(self):
        response = self.client.put(
            f'/users/{self.user.mnemonic}/CodeSystem/{self.user_source.mnemonic}/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'url': 'http://localhost/url',
                'title': 'test',
                'language': 'en',
                'identifier': [{
                    'value': f'/users/{self.user.mnemonic}/CodeSystem/{self.user_source.mnemonic}/',
                    'type': {
                        'coding': [{
                            'code': 'ACSN',
                            'system': 'http://hl7.org/fhir/v2/0203'
                        }]
                    }
                }],
                'version': '1.0',
                'name': 'test',
                'id': self.user_source.mnemonic,
                'status': 'draft',
                'content': 'fragment',
                'concept': [{
                    'code': 'test',
                    'display': 'Test',
                    'property': [
                        {
                            'code': 'conceptclass',
                            'value': 'Locale'
                        },
                        {
                            'code': 'datatype',
                            'value': 'N/A'
                        }]
                }]
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)

        response = self.client.put(
            f'/users/{self.user.mnemonic}/CodeSystem/{self.user_source.mnemonic}/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'url': 'http://localhost/url',
                'title': 'test',
                'language': 'en',
                'identifier': [{
                                   'value': f'/users/{self.user.mnemonic}/CodeSystem/{self.user_source.mnemonic}/',
                                   'type': {
                                       'coding': [{
                                                      'code': 'ACSN',
                                                      'system': 'http://hl7.org/fhir/v2/0203'
                                                  }]
                                   }
                               }],
                'version': '2.0',
                'name': 'test',
                'id': self.user_source.mnemonic,
                'status': 'draft',
                'content': 'fragment',
                'concept': [{
                                'code': 'test',
                                'display': 'Test',
                                'property': [{
                                                 'code': 'conceptclass',
                                                 'value': 'Locale'
                                             }, {
                                                 'code': 'datatype',
                                                 'value': 'N/A'
                                             }]
                            }, {
                                'code': 'test2',
                                'display': 'Test2',
                                'property': [{
                                                 'code': 'conceptclass',
                                                 'value': 'Locale'
                                             }, {
                                                 'code': 'datatype',
                                                 'value': 'N/A'
                                             }]
                            }]
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.user_source.mnemonic)
        self.assertEqual(response.data['version'], '2.0')

        # check if HEAD persisted
        sources = Source.objects.filter(mnemonic=self.user_source.mnemonic, version='HEAD', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, False)
        self.assertEqual(source.name, 'test')
        self.assertEqual(len(source.get_concepts_queryset().all()), 2)
        concepts = source.get_concepts_queryset().order_by('mnemonic').all()
        self.assertEqual(concepts[0].is_head, True)
        self.assertEqual(concepts[0].mnemonic, 'test')
        self.assertEqual(concepts[0].display_name, 'Test')
        self.assertEqual(len(concepts[0].names.all()), 1)
        self.assertEqual(concepts[1].is_head, True)
        self.assertEqual(concepts[1].mnemonic, 'test2')
        self.assertEqual(concepts[1].display_name, 'Test2')
        self.assertEqual(len(concepts[1].names.all()), 1)
        # check if version persisted
        sources = Source.objects.filter(mnemonic=self.user_source.mnemonic, version='2.0', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, False)
        self.assertEqual(source.name, 'test')
        concepts = Concept.objects.filter(sources__id=source.id).order_by('mnemonic').all()
        self.assertEqual(len(concepts), 2)
        self.assertEqual(concepts[0].mnemonic, 'test')
        self.assertEqual(concepts[0].display_name, 'Test')
        self.assertEqual(concepts[0].is_head, False)
        self.assertEqual(len(concepts[0].names.all()), 1)
        self.assertEqual(concepts[1].mnemonic, 'test2')
        self.assertEqual(concepts[1].display_name, 'Test2')
        self.assertEqual(concepts[1].is_head, False)
        self.assertEqual(len(concepts[1].names.all()), 1)
