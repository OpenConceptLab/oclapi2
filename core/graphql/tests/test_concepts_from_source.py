import json
from unittest import mock

from django.test import TestCase

from core.common.constants import HEAD
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory


class ConceptsFromSourceQueryTests(TestCase):
    maxDiff = None

    def setUp(self):
        self.organization = OrganizationFactory(mnemonic='CIEL')
        self.source = OrganizationSourceFactory(
            organization=self.organization,
            mnemonic='CIEL',
            name='CIEL',
            version=HEAD,
        )
        self.concept1 = ConceptFactory(parent=self.source, mnemonic='12345')
        ConceptNameFactory(concept=self.concept1, name='Hypertension', locale='en', locale_preferred=True)
        self.concept2 = ConceptFactory(parent=self.source, mnemonic='67890')
        ConceptNameFactory(concept=self.concept2, name='Diabetes', locale='en', locale_preferred=True)
        self.mapping = MappingFactory(
            parent=self.source,
            from_concept=self.concept1,
            to_concept=self.concept2,
            map_type='Same As',
            comment='primary link'
        )

        self.release_version = OrganizationSourceFactory(
            organization=self.organization,
            mnemonic=self.source.mnemonic,
            name=self.source.name,
            version='2024.01',
            released=True,
            is_latest_version=True,
        )
        self.concept1.sources.add(self.release_version)
        self.concept2.sources.add(self.release_version)
        self.mapping.sources.add(self.release_version)

    def _execute(self, query: str, variables: dict):
        response = self.client.post(
            '/graphql/',
            data=json.dumps({'query': query, 'variables': variables}),
            content_type='application/json'
        )
        payload = response.json()
        if 'errors' in payload:
            self.fail(payload['errors'])
        return response.status_code, payload['data']

    def test_fetch_concepts_by_ids_with_pagination(self):
        query = """
        query ConceptsByIds($org: String!, $source: String!, $conceptIds: [String!], $page: Int, $limit: Int) {
          conceptsFromSource(org: $org, source: $source, conceptIds: $conceptIds, page: $page, limit: $limit) {
            org
            source
            versionResolved
            page
            limit
            totalCount
            hasNextPage
             results {
               conceptId
               display
               mappings { mapType toSource { url name } toCode comment }
             }
          }
        }
        """
        status, data = self._execute(query, {
            'org': self.organization.mnemonic,
            'source': self.source.mnemonic,
            'conceptIds': [self.concept1.mnemonic, self.concept2.mnemonic],
            'page': 1,
            'limit': 1,
        })

        self.assertEqual(status, 200)
        payload = data['conceptsFromSource']
        self.assertEqual(payload['org'], self.organization.mnemonic)
        self.assertEqual(payload['source'], self.source.mnemonic)
        self.assertEqual(payload['versionResolved'], HEAD)
        self.assertEqual(payload['totalCount'], 2)
        self.assertTrue(payload['hasNextPage'])
        self.assertEqual(payload['page'], 1)
        self.assertEqual(payload['limit'], 1)
        self.assertEqual(len(payload['results']), 1)
        self.assertEqual(payload['results'][0]['conceptId'], self.concept1.mnemonic)
        self.assertEqual(payload['results'][0]['mappings'][0]['toCode'], self.concept2.mnemonic)

    @mock.patch('core.graphql.queries.concept_ids_from_es')
    def test_fetch_concepts_by_query_uses_es_ordering(self, mock_es):
        mock_es.return_value = ([self.concept2.id, self.concept1.id], 2)
        query = """
        query ConceptsByQuery($org: String!, $source: String!, $text: String!) {
          conceptsFromSource(org: $org, source: $source, query: $text) {
            versionResolved
            page
            limit
            totalCount
            hasNextPage
            results { conceptId }
          }
        }
        """
        status, data = self._execute(query, {
            'org': self.organization.mnemonic,
            'source': self.source.mnemonic,
            'text': 'concept'
        })

        self.assertEqual(status, 200)
        payload = data['conceptsFromSource']
        self.assertEqual(payload['versionResolved'], HEAD)
        self.assertIsNone(payload['page'])
        self.assertIsNone(payload['limit'])
        self.assertFalse(payload['hasNextPage'])
        self.assertEqual(payload['totalCount'], 2)
        self.assertEqual([item['conceptId'] for item in payload['results']],
                         [self.concept2.mnemonic, self.concept1.mnemonic])

    @mock.patch('core.graphql.queries.concept_ids_from_es', return_value=None)
    def test_fetch_concepts_by_query_falls_back_to_db(self, _mock_es):
        query = """
        query ConceptsByQuery($org: String!, $source: String!, $text: String!) {
          conceptsFromSource(org: $org, source: $source, query: $text) {
            totalCount
            results { conceptId }
          }
        }
        """
        status, data = self._execute(query, {
            'org': self.organization.mnemonic,
            'source': self.source.mnemonic,
            'text': 'hyper'
        })

        self.assertEqual(status, 200)
        payload = data['conceptsFromSource']
        self.assertEqual(payload['totalCount'], 1)
        self.assertEqual(payload['results'][0]['conceptId'], self.concept1.mnemonic)

    @mock.patch('core.graphql.queries.concept_ids_from_es')
    def test_fetch_concepts_by_query_recovers_when_es_returns_zero_hits(self, mock_es):
        mock_es.return_value = ([], 0)
        query = """
        query ConceptsByQuery($org: String!, $source: String!, $text: String!) {
          conceptsFromSource(org: $org, source: $source, query: $text) {
            totalCount
            results { conceptId }
          }
        }
        """
        status, data = self._execute(query, {
            'org': self.organization.mnemonic,
            'source': self.source.mnemonic,
            'text': 'diabetes'
        })

        self.assertEqual(status, 200)
        payload = data['conceptsFromSource']
        self.assertEqual(payload['totalCount'], 1)
        self.assertEqual(payload['results'][0]['conceptId'], self.concept2.mnemonic)

    def test_fetch_concepts_for_specific_version(self):
        query = """
        query ConceptsByIds($org: String!, $source: String!, $conceptIds: [String!], $version: String) {
          conceptsFromSource(org: $org, source: $source, conceptIds: $conceptIds, version: $version) {
            versionResolved
            results { conceptId }
          }
        }
        """
        status, data = self._execute(query, {
            'org': self.organization.mnemonic,
            'source': self.source.mnemonic,
            'conceptIds': [self.concept1.mnemonic],
            'version': self.release_version.version,
        })

        self.assertEqual(status, 200)
        payload = data['conceptsFromSource']
        self.assertEqual(payload['versionResolved'], self.release_version.version)
        self.assertEqual(payload['results'][0]['conceptId'], self.concept1.mnemonic)
