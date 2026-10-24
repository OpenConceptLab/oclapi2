from mock.mock import patch, Mock
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

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
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
            version='v1', mnemonic=self.user_source.mnemonic, user=self.user_source.parent, released=True)
        Source.persist_new_version(self.user_source_v1, self.user_source.created_by)

        self.org_source_B = OrganizationSourceFactory(organization=self.org, canonical_url='/some/url/B',
                                                       full_name='source name B')
        self.concept_B_1 = ConceptFactory(parent=self.org_source_B, mnemonic='concept_B_1',
                                          names=[ConceptNameFactory.build(name="concept_B_1_name")])
        self.concept_B_2 = ConceptFactory(parent=self.org_source_B, mnemonic='concept_B_2')
        self.org_source_B_v1 = OrganizationSourceFactory.build(
            version='v1', mnemonic=self.org_source_B.mnemonic, organization=self.org_source_B.parent, released=True)
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
            version='v2', mnemonic=self.org_source.mnemonic, organization=self.org_source.parent, released=True)
        Source.persist_new_version(self.org_source_v2, self.org_source.created_by)

        for mapping in self.org_source_v2.get_mappings_queryset().all():
            mapping.populate_fields_from_relations({})
            mapping.save()

        self.client = APIClient()

    def test_public_can_view(self):
        response = self.client.get('/fhir/ConceptMap/?url=/some/url')

        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(response.data['total'], 1)

        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/ConceptMap/' + self.org_source.mnemonic + '/'
        )
        self.assertEqual(resource['version'], 'v2')
        self.assertNotIn('group', resource)  # a search entry is a summary; the read has the groups

        resource = self.client.get(response.data['entry'][0]['fullUrl']).data
        self.assertEqual(len(resource['group']), 1)
        self.assertEqual(resource['group'], [{'source': self.org_source_B_v1.canonical_url,
                                     'target': self.org_source.canonical_url,
                                     'element': [
                                         {'code': 'concept_B_1',
                                          'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]},
                                         {'code': 'concept_B_2',  # one element per code, each of its targets
                                          'target': [{'code': 'concept_2', 'equivalence': 'equivalent'},
                                                     {'code': 'concept_1', 'equivalence': 'equivalent'}]}]}])

    def test_private_can_view(self):
        # the global base shows public repositories only, whoever asks; the owner base shows the owner its own
        response = self.client.get('/fhir/ConceptMap/?url=/some/url', HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(response.data['total'], 1)

        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/ConceptMap/' + self.org_source.mnemonic + '/'
        )
        self.assertEqual(resource['version'], 'v2')

        response = self.client.get(
            f'/users/{self.user.mnemonic}/ConceptMap/?url=/some/url', HTTP_AUTHORIZATION='Token ' + self.user_token)
        resource2 = response.data['entry'][0]['resource']
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
        response = self.client.get('/fhir/ConceptMap/?title:exact=source name')  # title alone is 'starts with'

        self.assertEqual(len(response.data['entry']), 1)
        resource = response.data['entry'][0]['resource']
        self.assertEqual(
            resource['identifier'][0]['value'],
            '/orgs/' + self.org.mnemonic + '/ConceptMap/' + self.org_source.mnemonic + '/'
        )
        self.assertEqual(resource['version'], 'v2')

    def test_private_can_list(self):
        response = self.client.get('/fhir/ConceptMap/', HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(len(response.data['entry']), 2)  # public only at the global base
        response = self.client.get(
            f'/users/{self.user.mnemonic}/ConceptMap/', HTTP_AUTHORIZATION='Token ' + self.user_token)
        self.assertEqual(len(response.data['entry']), 1)

    def test_get_concept_map(self):
        response = self.client.get(f'/orgs/{self.org.mnemonic}/ConceptMap/{self.org_source.mnemonic}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.org_source.mnemonic)
        self.assertEqual(response.data['version'], 'v2')

    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
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
        self.assertFalse('group' in response.data)

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

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
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

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
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
                                'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]},
                               {'code': 'concept_B_2',
                                'target': [{'code': 'concept_2', 'equivalence': 'equivalent'}]},
                               {'code': 'concept_B_2',
                                'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]}]}]
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

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
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
                                'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]},
                               {'code': 'concept_B_2',
                                'target': [{'code': 'concept_2', 'equivalence': 'equivalent'}]},
                               {'code': 'concept_B_2',
                                'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]}]}]
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

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
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

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_put_concept_map_with_modified_mappings(self):
        response = self.client.put(
            f'/users/{self.user.mnemonic}/ConceptMap/test2/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data=self.newConceptMap(self.user.mnemonic, 'test2', '1.0',
                                    [{'source': self.org_source_B_v1.url,
                                      'target': self.org_source.url,
                                      'element': [
                                           {'code': 'concept_B_1',
                                            'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]},
                                           {'code': 'concept_B_2',
                                            'target': [{'code': 'concept_2', 'equivalence': 'equivalent'}]},
                                           {'code': 'concept_B_2',
                                            'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]}]}])
            ,
            format='json'
        )
        self.assertEqual(response.data['id'], 'test2')
        self.assertEqual(response.data['version'], '1.0')
        self.assertEqual(response.data['status'], 'draft')

        response = self.client.put(
            f'/users/{self.user.mnemonic}/ConceptMap/test2/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data=self.newConceptMap(self.user.mnemonic, 'test2', '1.1',
                                    [{'source': self.org_source_B_v1.url,
                                      'target': self.org_source.url,
                                      'element': [
                                       {'code': 'concept_B_1',
                                        'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]},
                                       {'code': 'concept_B_2',
                                        'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]}]}]
                                    ),
            format='json'
        )

        # check if HEAD persisted
        sources = Source.objects.filter(mnemonic='test2', version='HEAD', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, False)
        self.assertEqual(source.name, 'test2')
        mappings = source.get_mappings_queryset().filter(retired=False).order_by('id')
        self.assertEqual(len(mappings), 2)
        mapping = mappings[0]
        self.assertEqual(mapping.is_head, True)
        self.assertEqual(mapping.from_concept_code, 'concept_B_1')
        self.assertEqual(mapping.to_concept_code, 'concept_1')
        mapping = mappings[1]
        self.assertEqual(mapping.is_head, True)
        self.assertEqual(mapping.from_concept_code, 'concept_B_2')
        self.assertEqual(mapping.to_concept_code, 'concept_1')
        # check if version persisted
        sources = Source.objects.filter(mnemonic='test2', version='1.1', user=self.user)
        self.assertEqual(len(sources), 1)
        source = sources.first()
        self.assertEqual(source.canonical_url, 'http://localhost/url')
        self.assertEqual(source.retired, False)
        self.assertEqual(source.name, 'test2')
        mappings = source.get_mappings_queryset().filter(retired=False).order_by('id')
        self.assertEqual(len(mappings), 2)
        mapping = mappings[0]
        self.assertEqual(mapping.is_head, False)
        self.assertEqual(mapping.from_concept_code, 'concept_B_1')
        self.assertEqual(mapping.to_concept_code, 'concept_1')
        mapping = mappings[1]
        self.assertEqual(mapping.is_head, False)
        self.assertEqual(mapping.from_concept_code, 'concept_B_2')
        self.assertEqual(mapping.to_concept_code, 'concept_1')


    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_put_concept_map_overwriting_version(self):
        response = self.client.put(
            f'/users/{self.user.mnemonic}/ConceptMap/test2/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data=self.newConceptMap(self.user.mnemonic, 'test2', '1.0',
                                    [{'source': self.org_source_B_v1.url,
                                      'target': self.org_source.url,
                                      'element': [
                                          {'code': 'concept_B_1',
                                           'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]},
                                          {'code': 'concept_B_2',
                                           'target': [{'code': 'concept_2', 'equivalence': 'equivalent'}]},
                                          {'code': 'concept_B_2',
                                           'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]}]}])
            ,
            format='json'
        )
        self.assertEqual(response.data['id'], 'test2')
        self.assertEqual(response.data['version'], '1.0')
        self.assertEqual(response.data['status'], 'draft')

        response = self.client.put(
            f'/users/{self.user.mnemonic}/ConceptMap/test2/',
            HTTP_AUTHORIZATION='Token ' + self.user_token,
            data=self.newConceptMap(self.user.mnemonic, 'test2', '1.0',
                                    [{'source': self.org_source_B_v1.url,
                                      'target': self.org_source.url,
                                      'element': [
                                          {'code': 'concept_B_1',
                                           'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]},
                                          {'code': 'concept_B_2',
                                           'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]}]}]
                                    ),
            format='json'
        )

        # check if bad request
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['resourceType'], 'OperationOutcome')
        self.assertEqual(
            [(issue['code'], issue['expression'], issue['details']['text']) for issue in response.data['issue']],
            [('invalid', ['version'], 'Version 1.0 already exists for CodeSystem test2.')])

    def translate(self, base, token=True, **params):
        """$translate at a base, as the user (or anonymously), after the user's PUT of their ConceptMap, released."""
        if not Source.objects.filter(mnemonic=self.user_source.mnemonic, user=self.user, version='1.0').exists():
            self.putConceptMap(status='active')
        headers = {'HTTP_AUTHORIZATION': 'Token ' + self.user_token} if token else {}
        return self.client.get(f'{base}/ConceptMap/$translate', params, **headers)

    def assertTranslation(self, response, matches):  # pylint: disable=invalid-name
        """result, a message only when there is no match, and each match: (code, system, equivalence)."""
        self.assertEqual(response.status_code, 200, response.data)
        parameters = response.data['parameter']
        self.assertEqual(parameters[0], {'name': 'result', 'valueBoolean': bool(matches)})
        self.assertEqual([item['name'] for item in parameters[1:]], ['match'] * len(matches) or ['message'])
        found = [item['part'] for item in parameters[1:] if item['name'] == 'match']
        self.assertEqual(
            [(part[1]['valueCoding']['code'], part[1]['valueCoding']['system'], part[0]['valueCode'],
              part[2]['valueUri']) for part in found],
            [(*match, 'http://localhost/url|1.0') for match in matches])

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_translate_positive(self):
        """The ConceptMap the user PUT, read back by $translate: the mapping its group wrote."""
        self.assertTranslation(
            self.translate(f'/users/{self.user.mnemonic}', url='http://localhost/url',
                           system=self.org_source_B_v1.canonical_url, code='concept_B_1'),
            [('concept_1', '/some/url', 'equivalent')])

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_public_translate_negative(self):
        """The user's ConceptMap is private: anonymous, it is not found, and no code system has the mapping."""
        response = self.translate(f'/users/{self.user.mnemonic}', token=False, url='http://localhost/url',
                                  system=self.org_source_B_v1.canonical_url, code='concept_B_1')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data['issue'][0]['expression'], ['url'])
        self.assertTranslation(self.translate(f'/users/{self.user.mnemonic}', token=False,
                                              system=self.org_source_B_v1.canonical_url, code='concept_B_1'), [])

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_translate_negative(self):
        self.assertTranslation(
            self.translate(f'/users/{self.user.mnemonic}', url='http://localhost/url',
                           system=self.org_source_B_v1.canonical_url, code='concept_1'), [])

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_translate_with_url(self):
        """The global base shows public repositories only: the user's private ConceptMap isn't found there."""
        response = self.translate('/fhir', url='http://localhost/url', system=self.org_source_B_v1.canonical_url,
                                  code='concept_B_1')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data['issue'][0]['code'], 'not-found')

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_translate_with_url_negative(self):
        response = self.translate(f'/users/{self.user.mnemonic}', url='http://unknown-concept-map/url',
                                  system=self.org_source_B_v1.canonical_url, code='concept_B_1')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data['issue'][0]['expression'], ['url'])

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_translate_with_target(self):
        self.assertTranslation(
            self.translate(f'/users/{self.user.mnemonic}', url='http://localhost/url',
                           system=self.org_source_B_v1.canonical_url, code='concept_B_1',
                           targetsystem=self.org_source.canonical_url),
            [('concept_1', '/some/url', 'equivalent')])

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_translate_with_target_negative(self):
        self.assertTranslation(
            self.translate(f'/users/{self.user.mnemonic}', url='http://localhost/url',
                           system=self.org_source_B_v1.canonical_url, code='concept_B_1',
                           targetsystem=self.org_source_B_v1.canonical_url), [])

    def newConceptMap(self, owner, identifier, version, group):
        if not owner:
            owner = self.user.mnemonic
        return {
            'url': 'http://localhost/url',
            'title': 'test',
            'language': 'en',
            'identifier': [{
                'value': f'/users/{owner}/ConceptMap/{identifier}/',
                'type': {
                    'coding': [{
                        'code': 'ACSN',
                        'system': 'http://hl7.org/fhir/v2/0203'
                    }]
                }
            }],
            'version': version,
            'name': identifier,
            'id': identifier,
            'status': 'draft',
            'group': group

        }

    def putConceptMap(self, status='draft'):
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
                'status': status,
                'group': [{'source': self.org_source_B_v1.canonical_url,
                           'target': self.org_source.canonical_url,
                           'element': [
                               {'code': 'concept_B_1',
                                'target': [{'code': 'concept_1', 'equivalence': 'equivalent'}]}]
                           }]

            },
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        return response

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_an_exported_concept_map_imports_with_the_map_types_it_had(self):
        """A ConceptMap read, then written back: each map type with an R4 equivalence is stored as it was."""
        map_types = ['SAME-AS', 'NARROWER-THAN', 'BROADER-THAN', 'NOT-SAME-AS']
        auth = {'HTTP_AUTHORIZATION': 'Token ' + self.user_token}
        group = [{'source': self.org_source_B_v1.canonical_url, 'target': self.org_source.canonical_url,
                  'element': [{'code': 'concept_B_1', 'target': [
                      {'code': f'concept_{index}', 'equivalence': equivalence}
                      for index, equivalence in enumerate(['equivalent', 'wider', 'narrower', 'disjoint'])]}]}]
        for identifier in ('exported', 'imported'):
            response = self.client.post(f'/users/{self.user.mnemonic}/ConceptMap/', data={
                **self.newConceptMap(None, identifier, '1.0', group), 'status': 'active'}, format='json', **auth)
            self.assertEqual(response.status_code, 201, response.data)
            version = Source.objects.get(mnemonic=identifier, version='1.0', user=self.user)
            self.assertEqual(
                list(version.get_mappings_queryset().order_by('id').values_list('map_type', flat=True)), map_types)
            group = self.client.get(f'/users/{self.user.mnemonic}/ConceptMap/{identifier}/', **auth).data['group']

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_group_equivalence_is_the_map_type_relationship(self):
        """
        A group target's equivalence is its map type's relationship in R4 ($translate's table), and a written
        equivalence is stored as the map type it is, so it reads back the same; one with no map type (relatedto,
        inexact) is kept as it is.
        One element per code, whatever the number of its targets.
        """
        written = ['equivalent', 'wider', 'narrower', 'disjoint', 'relatedto', 'equal', 'subsumes', 'specializes',
                   'inexact', 'unmatched']
        stored = ['SAME-AS', 'NARROWER-THAN', 'BROADER-THAN', 'NOT-SAME-AS', 'relatedto', 'SAME-AS', 'NARROWER-THAN',
                  'BROADER-THAN', 'inexact', 'NOT-SAME-AS']
        read = ['equivalent', 'wider', 'narrower', 'disjoint', 'relatedto', 'equivalent', 'wider', 'narrower',
                'relatedto', 'disjoint']
        group = [{'source': self.org_source_B_v1.canonical_url, 'target': self.org_source.canonical_url,
                  'element': [{'code': 'concept_B_1', 'target': [
                      {'code': f'concept_{index}', 'equivalence': equivalence}
                      for index, equivalence in enumerate(written)]}]}]
        response = self.client.post(
            f'/users/{self.user.mnemonic}/ConceptMap/', HTTP_AUTHORIZATION='Token ' + self.user_token,
            data={**self.newConceptMap(None, 'equivalences', '1.0', group), 'status': 'active'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        version = Source.objects.get(mnemonic='equivalences', version='1.0', user=self.user)
        self.assertEqual(list(version.get_mappings_queryset().order_by('id').values_list('map_type', flat=True)),
                         stored)

        resource = self.client.get(f'/users/{self.user.mnemonic}/ConceptMap/equivalences/',
                                   HTTP_AUTHORIZATION='Token ' + self.user_token).data
        [element] = resource['group'][0]['element']
        self.assertEqual(element['code'], 'concept_B_1')
        self.assertEqual([target['equivalence'] for target in element['target']], read)

    def test_unable_to_represent_as_fhir(self):
        instance = Source(id='1', uri='/invalid/uri')
        serialized = ConceptMapDetailSerializer(instance=instance).data
        self.assertDictEqual(serialized, {
            'resourceType': 'OperationOutcome',
            'issue': [{'severity': 'error', 'details': 'Failed to represent "/invalid/uri" as ConceptMap'}]})
