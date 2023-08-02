from collections import OrderedDict

from rest_framework.test import APIClient

from core.common.tests import OCLTestCase
from core.concept_maps.serializers import ConceptMapDetailSerializer
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.tests.factories import UserProfileFactory


class ConceptMapTest(OCLTestCase):
    maxDiff = None

    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory()

        self.org_source = OrganizationSourceFactory(organization=self.org, canonical_url='/some/url',
                                                    full_name='source name')
        self.org_source_v1 = OrganizationSourceFactory.build(
            version='v1', mnemonic=self.org_source.mnemonic, organization=self.org_source.parent)
        Source.persist_new_version(self.org_source_v1, self.org_source.created_by)
        self.concept_1 = ConceptFactory(parent=self.org_source, mnemonic='concept_1',
                                        names=[ConceptNameFactory.build(name="concept_1_name")])
        self.concept_2 = ConceptFactory(parent=self.org_source, mnemonic='concept_2')

        self.user = UserProfileFactory()
        self.user_token = self.user.get_token()
        self.user_source = UserSourceFactory(user=self.user, public_access='None', canonical_url='/some/url')
        self.user_source_v1 = UserSourceFactory.build(
            version='v1', mnemonic=self.user_source.mnemonic, user=self.user_source.parent)
        Source.persist_new_version(self.user_source_v1, self.user_source.created_by)

        self.org_source_B = OrganizationSourceFactory(organization=self.org, canonical_url='/some/url/B',
                                                       full_name='source name B')
        self.concept_B_1 = ConceptFactory(parent=self.org_source_B, mnemonic='concept_B_1',
                                          names=[ConceptNameFactory.build(name="concept_B_1_name")])
        self.concept_B_2 = ConceptFactory(parent=self.org_source_B, mnemonic='concept_B_2')
        self.org_source_B_v1 = OrganizationSourceFactory.build(
            version='v1', mnemonic=self.org_source_B.mnemonic, organization=self.org_source_B.parent)
        Source.persist_new_version(self.org_source_B_v1, self.org_source_B.created_by)

        self.mapping_1 = MappingFactory(parent=self.org_source, to_concept=self.concept_1,
                                        from_concept=self.concept_B_1)
        self.mapping_1.populate_fields_from_relations({})
        self.mapping_1.save()
        self.mapping_2 = MappingFactory(parent=self.org_source, to_concept=self.concept_2,
                                        from_concept=self.concept_B_2)
        self.mapping_2.populate_fields_from_relations({})
        self.mapping_2.save()
        self.mapping_3 = MappingFactory(parent=self.org_source, to_concept=self.concept_1,
                                        from_concept=self.concept_B_2)
        self.mapping_3.populate_fields_from_relations({})
        self.mapping_3.save()

        self.org_source_v2 = OrganizationSourceFactory.build(
            version='v2', mnemonic=self.org_source.mnemonic, organization=self.org_source.parent)
        Source.persist_new_version(self.org_source_v2, self.org_source.created_by)

        for mapping in self.org_source_v2.get_mappings_queryset().all():
            mapping.populate_fields_from_relations({})
            mapping.save()

        self.client = APIClient()

    def test_public_can_view(self):
        response = self.client.get('/fhir/ConceptMap/?url=/some/url')

        self.assertEqual(len(response.data['entry']), 1)

        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/ConceptMap/' + self.org_source.mnemonic + '/'
        )
        self.assertEqual(resource['version'], 'v2')
        self.assertEqual(len(resource['group']), 1)
        self.assertEqual(resource['group'], [{'source': self.org_source_B_v1.canonical_url,
                                     'target': self.org_source.canonical_url,
                                     'element': [
                                         {'code': 'concept_B_1',
                                          'target': [{'code': 'concept_1', 'relationship': 'equivalent'}]},
                                         {'code': 'concept_B_2',
                                          'target': [{'code': 'concept_2', 'relationship': 'equivalent'}]},
                                         {'code': 'concept_B_2',
                                          'target': [{'code': 'concept_1', 'relationship': 'equivalent'}]}]}])

    def test_private_can_view(self):
        response = self.client.get('/fhir/ConceptMap/?url=/some/url', HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(len(response.data['entry']), 2)
        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/ConceptMap/' + self.org_source.mnemonic + '/'
        )

        self.assertEqual(resource['version'], 'v2')
        resource2 = response.data['entry'][1]['resource']
        self.assertEqual(
            resource2['identifier'][0]['value'],
            '/users/' + self.user.mnemonic + '/ConceptMap/' + self.user_source.mnemonic + '/'
        )
        self.assertEqual(resource2['version'], 'v1')

    def test_public_can_list(self):
        response = self.client.get('/fhir/ConceptMap/')

        self.assertEqual(len(response.data['entry']), 2)
        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/ConceptMap/' + self.org_source.mnemonic + '/'
        )
        self.assertEqual(resource['version'], 'v2')

        resource2 = response.data['entry'][1]['resource']
        self.assertEqual(
            resource2['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/ConceptMap/' + self.org_source_B.mnemonic + '/'
        )
        self.assertEqual(resource2['version'], 'v1')

    def test_find_by_title(self):
        response = self.client.get('/fhir/ConceptMap/?title=source name')

        self.assertEqual(len(response.data['entry']), 1)
        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/ConceptMap/' + self.org_source.mnemonic + '/'
        )
        self.assertEqual(resource['version'], 'v2')

    def test_private_can_list(self):
        response = self.client.get('/fhir/ConceptMap/', HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(len(response.data['entry']), 3)

    def test_get_concept_map(self):
        response = self.client.get(f'/orgs/{self.org.mnemonic}/ConceptMap/{self.org_source.mnemonic}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.org_source.mnemonic)
        self.assertEqual(response.data['version'], 'v2')

    def test_post_concept_map_without_mappings(self):
        response = self.client.post(
            f'/users/{self.user.mnemonic}/ConceptMap/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'url': 'http://localhost/url',
                'title': 'test',
                'language': 'en',
                'identifier': [{
                                   'value': f'/users/{self.user.mnemonic}/ConceptMap/test',
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
                'status': 'active'
            },
            format='json'
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['id'], 'test')
        self.assertEqual(response.data['version'], '1.0')
        self.assertEqual(response.data['group'], [])

        # check if HEAD persisted
        sources = Source.objects.filter(mnemonic='test', version='HEAD', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.name, 'test')
        # check if version persisted
        sources = Source.objects.filter(mnemonic='test', version='1.0', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.name, 'test')

    def test_put_concept_map_without_mappings(self):
        response = self.client.put(
            f'/users/{self.user.mnemonic}/ConceptMap/{self.user_source.mnemonic}/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'url': 'http://localhost/url',
                'title': 'test',
                'language': 'en',
                'identifier': [{
                                   'value': f'/users/{self.user.mnemonic}/ConceptMap/{self.user_source.mnemonic}/',
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
                'status': 'draft'
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

    def test_post_concept_map_with_mappings(self):
        response = self.client.post(
            f'/users/{self.user.mnemonic}/ConceptMap/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'url': 'http://localhost/url',
                'title': 'test',
                'language': 'en',
                'identifier': [{
                                   'value': f'/users/{self.user.mnemonic}/ConceptMap/test',
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
                'group': [{'source': self.org_source_B_v1.canonical_url,
                           'target': self.org_source.canonical_url,
                           'element': [
                               {'code': 'concept_B_1',
                                'target': [{'code': 'concept_1', 'relationship': 'equivalent'}]},
                               {'code': 'concept_B_2',
                                'target': [{'code': 'concept_2', 'relationship': 'equivalent'}]},
                               {'code': 'concept_B_2',
                                'target': [{'code': 'concept_1', 'relationship': 'equivalent'}]}]}]
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
        self.assertEqual(len(source.get_mappings_queryset().all()), 3)
        # check if version persisted
        sources = Source.objects.filter(mnemonic='test', version='1.0', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, True)
        self.assertEqual(source.name, 'test')
        self.assertEqual(len(source.get_mappings_queryset().all()), 3)

    def test_post_concept_map_with_mappings_without_canonicals(self):
        response = self.client.post(
            f'/users/{self.user.mnemonic}/ConceptMap/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'url': 'http://localhost/url',
                'title': 'test',
                'language': 'en',
                'identifier': [{
                    'value': f'/users/{self.user.mnemonic}/ConceptMap/test',
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
                'group': [{'source': self.org_source_B_v1.url,
                           'target': self.org_source.url,
                           'element': [
                               {'code': 'concept_B_1',
                                'target': [{'code': 'concept_1', 'relationship': 'equivalent'}]},
                               {'code': 'concept_B_2',
                                'target': [{'code': 'concept_2', 'relationship': 'equivalent'}]},
                               {'code': 'concept_B_2',
                                'target': [{'code': 'concept_1', 'relationship': 'equivalent'}]}]}]
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
        self.assertEqual(len(source.get_mappings_queryset().all()), 3)
        # check if version persisted
        sources = Source.objects.filter(mnemonic='test', version='1.0', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, True)
        self.assertEqual(source.name, 'test')
        self.assertEqual(len(source.get_mappings_queryset().all()), 3)

    def test_put_concept_map_with_all_new_concepts(self):
        response = self.putConceptMap()
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
        self.assertEqual(len(source.get_mappings_queryset().all()), 1)
        mapping = source.get_mappings_queryset().first()
        self.assertEqual(mapping.is_head, True)
        self.assertEqual(mapping.from_source_url, self.org_source_B_v1.canonical_url)
        self.assertEqual(mapping.to_source_url,  self.org_source.canonical_url)
        self.assertEqual(mapping.from_concept_code, 'concept_B_1')
        self.assertEqual(mapping.to_concept_code, 'concept_1')
        # check if version persisted
        sources = Source.objects.filter(mnemonic=self.user_source.mnemonic, version='1.0', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, False)
        self.assertEqual(source.name, 'test')
        self.assertEqual(len(source.get_mappings_queryset().all()), 1)
        mapping = source.get_mappings_queryset().first()
        self.assertEqual(mapping.is_head, False)
        self.assertEqual(mapping.from_source_url, self.org_source_B_v1.canonical_url)
        self.assertEqual(mapping.to_source_url,  self.org_source.canonical_url)
        self.assertEqual(mapping.from_concept_code, 'concept_B_1')
        self.assertEqual(mapping.to_concept_code, 'concept_1')

    def test_translate_positive(self):
        self.putConceptMap()

        response = self.client.get(
            f'/users/{self.user.mnemonic}/ConceptMap/$translate?'
            f'system={self.org_source_B_v1.canonical_url}&code=concept_B_1',
            HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(
            response.data,
            {
                'resourceType': 'Parameters',
                'parameter': [
                    OrderedDict([
                        ('name', 'result'),
                        ('valueBoolean', True)
                    ]),
                    OrderedDict([
                        ('name', 'match'),
                        (
                            'part',
                            [
                                OrderedDict([
                                    ('name', 'equivalence'),
                                    ('valueCode', 'equivalent')
                                ]),
                                OrderedDict([
                                    ('name', 'concept'),
                                    ('valueCoding', OrderedDict([('system', '/some/url'), ('code', 'concept_1')]))
                                ])
                            ]
                        )
                    ]),
                    OrderedDict([
                        ('name', 'match'),
                        (
                            'part',
                            [
                                OrderedDict([
                                    ('name', 'equivalence'),
                                    ('valueCode', 'equivalent')
                                ]),
                                OrderedDict([
                                    ('name', 'concept'),
                                    ('valueCoding', OrderedDict([('system', '/some/url'), ('code', 'concept_1')]))
                                ])
                            ]
                        )
                    ])
                ]
            })

    def test_public_translate_negative(self):
        self.putConceptMap()

        response = self.client.get(
            f'/users/{self.user.mnemonic}/ConceptMap/$translate?'
            f'system={self.org_source_B_v1.canonical_url}&code=concept_1')

        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(response.data, {
            'resourceType': 'Parameters',
            'parameter': [OrderedDict(
                [('name', 'result'), ('valueBoolean', False)])]})

    def putConceptMap(self):
        response = self.client.put(
            f'/users/{self.user.mnemonic}/ConceptMap/{self.user_source.mnemonic}/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={
                'url': 'http://localhost/url',
                'title': 'test',
                'language': 'en',
                'identifier': [{
                    'value': f'/users/{self.user.mnemonic}/ConceptMap/{self.user_source.mnemonic}/',
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
                'group': [{'source': self.org_source_B_v1.canonical_url,
                           'target': self.org_source.canonical_url,
                           'element': [
                               {'code': 'concept_B_1',
                                'target': [{'code': 'concept_1', 'relationship': 'equivalent'}]}]
                           }]

            },
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        return response

    def test_translate_negative(self):
        self.putConceptMap()

        response = self.client.get(
            f'/users/{self.user.mnemonic}/ConceptMap/$translate?'
            f'system={self.org_source_B_v1.canonical_url}&code=concept_1',
            HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(response.data, {
            'resourceType': 'Parameters',
            'parameter': [OrderedDict(
                [('name', 'result'), ('valueBoolean', False)]
            )]})

    def test_translate_with_target(self):
        self.putConceptMap()

        response = self.client.get(
            f'/users/{self.user.mnemonic}/ConceptMap/$translate?'
            f'system={self.org_source_B_v1.canonical_url}&code=concept_B_1'
            f'&targetsystem={self.org_source.canonical_url}', HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(
            response.data,
            {
                'resourceType': 'Parameters',
                'parameter': [
                    OrderedDict([
                        ('name', 'result'),
                        ('valueBoolean', True)
                    ]),
                    OrderedDict([
                        ('name', 'match'),
                        (
                            'part',
                            [
                                OrderedDict([
                                    ('name', 'equivalence'),
                                    ('valueCode', 'equivalent')
                                ]),
                                OrderedDict([
                                    ('name', 'concept'),
                                    ('valueCoding', OrderedDict([('system', '/some/url'), ('code', 'concept_1')]))
                                ])
                            ]
                        )
                    ]),
                    OrderedDict([
                        ('name', 'match'),
                        (
                            'part',
                            [
                                OrderedDict([
                                    ('name', 'equivalence'),
                                    ('valueCode', 'equivalent')
                                ]),
                                OrderedDict([
                                    ('name', 'concept'),
                                    ('valueCoding', OrderedDict([('system', '/some/url'), ('code', 'concept_1')]))
                                ])
                            ]
                        )
                    ])
                ]
            }
        )

    def test_translate_with_target_negative(self):
        self.putConceptMap()

        response = self.client.get(
            f'/users/{self.user.mnemonic}/ConceptMap/$translate?'
            f'system={self.org_source_B_v1.canonical_url}&code=concept_B_1'
            f'&targetsystem={self.org_source_B_v1.canonical_url}',
            HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(response.data, {
            'resourceType': 'Parameters',
            'parameter': [OrderedDict(
                [('name', 'result'), ('valueBoolean', False)])]})

    def test_unable_to_represent_as_fhir(self):
        instance = Source(id='1', uri='/invalid/uri')
        serialized = ConceptMapDetailSerializer(instance=instance).data
        self.assertDictEqual(serialized, {
            'resourceType': 'OperationOutcome',
            'issue': [{'severity': 'error', 'details': 'Failed to represent "/invalid/uri" as ConceptMap'}]})
