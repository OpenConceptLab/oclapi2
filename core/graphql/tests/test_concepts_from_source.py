import json
import os
from unittest import mock

from django.test import TestCase, override_settings

from core.common.constants import HEAD
from core.concepts.tests.factories import (
    ConceptDescriptionFactory,
    ConceptFactory,
    ConceptNameFactory,
)
from core.graphql.queries import format_datetime_for_api, serialize_concepts
from core.graphql.tests.conftest import (
    auth_header_for_token,
    bootstrap_super_user,
    create_user_with_token,
)
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.tests.factories import UserProfileFactory


@override_settings(
    SESSION_ENGINE='django.contrib.sessions.backends.signed_cookies',
    MESSAGE_STORAGE='django.contrib.messages.storage.cookie.CookieStorage',
    STRAWBERRY_ASYNC=False,
    MIDDLEWARE=[
        mw for mw in __import__('django.conf').conf.settings.MIDDLEWARE
        if 'SessionMiddleware' not in mw
        and 'AuthenticationMiddleware' not in mw
        and 'MessageMiddleware' not in mw
        and 'TokenAuthMiddleWare' not in mw  # type: ignore
    ],
)
class ConceptsFromSourceQueryTests(TestCase):
    maxDiff = None

    def setUp(self):
        self._old_async_flag = os.environ.get('DJANGO_ALLOW_ASYNC_UNSAFE')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
        self.super_user = bootstrap_super_user()
        self.audit_user, self.audit_token = create_user_with_token(
            username='graphql-audit',
            super_user=self.super_user,
        )
        self.client.force_login(self.audit_user)
        self.auth_header = auth_header_for_token(self.audit_token)
        self.organization = OrganizationFactory(
            mnemonic='CIEL',
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        self.source = OrganizationSourceFactory(
            organization=self.organization,
            mnemonic='CIEL',
            name='CIEL',
            version=HEAD,
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        self.concept1 = ConceptFactory(
            parent=self.source,
            mnemonic='12345',
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        ConceptNameFactory(concept=self.concept1, name='Hypertension', locale='en', locale_preferred=True)
        ConceptDescriptionFactory(concept=self.concept1, name='Hypertension description', locale='en',
                                  locale_preferred=True)
        self.concept2 = ConceptFactory(
            parent=self.source,
            mnemonic='67890',
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        ConceptNameFactory(concept=self.concept2, name='Diabetes', locale='en', locale_preferred=True)
        self.mapping = MappingFactory(
            parent=self.source,
            from_concept=self.concept1,
            to_concept=self.concept2,
            map_type='Same As',
            comment='primary link',
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )

        self.release_version = OrganizationSourceFactory(
            organization=self.organization,
            mnemonic=self.source.mnemonic,
            name=self.source.name,
            version='2024.01',
            released=True,
            is_latest_version=True,
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        self.concept1.sources.add(self.release_version)
        self.concept2.sources.add(self.release_version)
        self.mapping.sources.add(self.release_version)
        self.concept1.extras = {**(self.concept1.extras or {}), 'is_set': False}
        self.concept1.save(update_fields=['extras'])

    def _execute(self, query: str, variables: dict):
        response = self.client.post(
            '/graphql/',
            data=json.dumps({'query': query, 'variables': variables}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth_header,
        )
        payload = response.json()
        if 'errors' in payload:
            self.fail(payload['errors'])
        return response.status_code, payload['data']

    def tearDown(self):
        if self._old_async_flag is None:
            os.environ.pop('DJANGO_ALLOW_ASYNC_UNSAFE', None)
        else:
            os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = self._old_async_flag
        super().tearDown()

    def test_fetch_concepts_by_ids_with_pagination(self):
        query = """
        query ConceptsByIds($org: String, $source: String, $conceptIds: [String!], $page: Int, $limit: Int) {
          concepts(org: $org, source: $source, conceptIds: $conceptIds, page: $page, limit: $limit) {
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
        payload = data['concepts']
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

    def test_concepts_include_metadata_fields(self):
        query = """
        query ConceptsByIds($org: String, $source: String, $conceptIds: [String!]) {
          concepts(org: $org, source: $source, conceptIds: $conceptIds) {
            results {
              conceptId
              description
              conceptClass
              datatype { name }
              metadata {
                isSet
                isRetired
                createdBy
                createdAt
                updatedBy
                updatedAt
              }
            }
          }
        }
        """
        status, data = self._execute(query, {
            'org': self.organization.mnemonic,
            'source': self.source.mnemonic,
            'conceptIds': [self.concept1.mnemonic],
        })

        self.assertEqual(status, 200)
        concept_payload = data['concepts']['results'][0]
        self.assertEqual(concept_payload['description'], 'Hypertension description')
        self.assertEqual(concept_payload['conceptClass'], self.concept1.concept_class)
        self.assertEqual(concept_payload['datatype']['name'], self.concept1.datatype)
        metadata = concept_payload['metadata']
        self.assertFalse(metadata['isSet'])
        self.assertFalse(metadata['isRetired'])
        self.assertEqual(metadata['createdBy'], self.audit_user.username)
        self.assertEqual(metadata['updatedBy'], self.audit_user.username)
        self.assertEqual(metadata['createdAt'], format_datetime_for_api(self.concept1.created_at))
        self.assertEqual(metadata['updatedAt'], format_datetime_for_api(self.concept1.updated_at))

    def test_numeric_datatype_details_from_graphql(self):
        numeric_concept = ConceptFactory(
            parent=self.source,
            mnemonic='num-001',
            datatype='Numeric',
            created_by=self.audit_user,
            updated_by=self.audit_user,
            extras={
                'units': 'mg/dL',
                'low_absolute': 0,
                'hi_absolute': 10,
                'low_normal': 3,
                'hi_normal': 7,
                'low_critical': 1.5,
                'hi_critical': 8.5,
            },
        )
        query = """
        query ($org: String!, $source: String!, $conceptIds: [String!]) {
          concepts(org: $org, source: $source, conceptIds: $conceptIds) {
            results {
              conceptId
              datatype {
                name
                details {
                  __typename
                  ... on NumericDatatypeDetails {
                    units
                    lowAbsolute
                    highAbsolute
                    lowNormal
                    highNormal
                    lowCritical
                    highCritical
                  }
                }
              }
            }
          }
        }
        """
        status, data = self._execute(query, {
            'org': self.organization.mnemonic,
            'source': self.source.mnemonic,
            'conceptIds': [numeric_concept.mnemonic],
        })

        self.assertEqual(status, 200)
        result = data['concepts']['results'][0]
        self.assertEqual(result['datatype']['name'], 'Numeric')
        details = result['datatype']['details']
        self.assertIsNotNone(details)
        self.assertEqual(details['__typename'], 'NumericDatatypeDetails')
        self.assertEqual(details['units'], 'mg/dL')
        self.assertEqual(details['lowAbsolute'], 0)
        self.assertEqual(details['highAbsolute'], 10)
        self.assertEqual(details['lowNormal'], 3)
        self.assertEqual(details['highNormal'], 7)
        self.assertEqual(details['lowCritical'], 1.5)
        self.assertEqual(details['highCritical'], 8.5)

    def test_coded_datatype_details_from_graphql(self):
        coded_concept = ConceptFactory(
            parent=self.source,
            mnemonic='coded-1',
            datatype='Coded',
            created_by=self.audit_user,
            updated_by=self.audit_user,
            extras={'allow_multiple': True},
        )
        query = """
        query ($org: String!, $source: String!, $conceptIds: [String!]) {
          concepts(org: $org, source: $source, conceptIds: $conceptIds) {
            results {
              conceptId
              datatype {
                name
                details {
                  __typename
                  ... on CodedDatatypeDetails {
                    allowMultiple
                  }
                }
              }
            }
          }
        }
        """
        status, data = self._execute(query, {
            'org': self.organization.mnemonic,
            'source': self.source.mnemonic,
            'conceptIds': [coded_concept.mnemonic],
        })

        self.assertEqual(status, 200)
        result = data['concepts']['results'][0]
        self.assertEqual(result['datatype']['name'], 'Coded')
        details = result['datatype']['details']
        self.assertEqual(details['__typename'], 'CodedDatatypeDetails')
        self.assertTrue(details['allowMultiple'])

    def test_text_datatype_details_from_graphql(self):
        text_concept = ConceptFactory(
            parent=self.source,
            mnemonic='text-1',
            datatype='Text',
            created_by=self.audit_user,
            updated_by=self.audit_user,
            extras={'text_format': 'paragraph'},
        )
        query = """
        query ($org: String!, $source: String!, $conceptIds: [String!]) {
          concepts(org: $org, source: $source, conceptIds: $conceptIds) {
            results {
              conceptId
              datatype {
                name
                details {
                  __typename
                  ... on TextDatatypeDetails {
                    textFormat
                  }
                }
              }
            }
          }
        }
        """
        status, data = self._execute(query, {
            'org': self.organization.mnemonic,
            'source': self.source.mnemonic,
            'conceptIds': [text_concept.mnemonic],
        })

        self.assertEqual(status, 200)
        result = data['concepts']['results'][0]
        self.assertEqual(result['datatype']['name'], 'Text')
        details = result['datatype']['details']
        self.assertEqual(details['__typename'], 'TextDatatypeDetails')
        self.assertEqual(details['textFormat'], 'paragraph')

    @mock.patch('core.graphql.queries.concept_ids_from_es')
    def test_fetch_concepts_by_query_uses_es_ordering(self, mock_es):
        mock_es.return_value = ([self.concept2.id, self.concept1.id], 2)
        query = """
        query ConceptsByQuery($org: String, $source: String, $text: String!) {
          concepts(org: $org, source: $source, query: $text) {
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
        payload = data['concepts']
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
        query ConceptsByQuery($org: String, $source: String, $text: String!) {
          concepts(org: $org, source: $source, query: $text) {
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
        payload = data['concepts']
        self.assertEqual(payload['totalCount'], 1)
        self.assertEqual(payload['results'][0]['conceptId'], self.concept1.mnemonic)

    @mock.patch('core.graphql.queries.concept_ids_from_es')
    def test_fetch_concepts_by_query_recovers_when_es_returns_zero_hits(self, mock_es):
        mock_es.return_value = ([], 0)
        query = """
        query ConceptsByQuery($org: String, $source: String, $text: String!) {
          concepts(org: $org, source: $source, query: $text) {
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
        payload = data['concepts']
        self.assertEqual(payload['totalCount'], 1)
        self.assertEqual(payload['results'][0]['conceptId'], self.concept2.mnemonic)

    def test_fetch_concepts_for_specific_version(self):
        query = """
        query ConceptsByIds($org: String, $source: String, $conceptIds: [String!], $version: String) {
          concepts(org: $org, source: $source, conceptIds: $conceptIds, version: $version) {
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
        payload = data['concepts']
        self.assertEqual(payload['versionResolved'], self.release_version.version)
        self.assertEqual(payload['results'][0]['conceptId'], self.concept1.mnemonic)

    def test_fetch_concepts_global_search(self):
        query = """
        query GlobalConcepts($query: String!) {
          concepts(query: $query) {
            org
            source
            versionResolved
            totalCount
            results { conceptId }
          }
        }
        """
        status, data = self._execute(query, {'query': 'hyper'})
        self.assertEqual(status, 200)
        payload = data['concepts']
        self.assertIsNone(payload['org'])
        self.assertIsNone(payload['source'])
        self.assertEqual(payload['versionResolved'], '')
        self.assertGreaterEqual(payload['totalCount'], 1)
        self.assertIn(
            self.concept1.mnemonic,
            [item['conceptId'] for item in payload['results']],
        )

    def test_concept_names_include_preferred_locale(self):
        query = """
        query ConceptsByIds($org: String, $source: String, $conceptIds: [String!]) {
          concepts(org: $org, source: $source, conceptIds: $conceptIds) {
            results {
              conceptId
              names { name locale type preferred }
            }
          }
        }
        """
        status, data = self._execute(query, {
            'org': self.organization.mnemonic,
            'source': self.source.mnemonic,
            'conceptIds': [self.concept1.mnemonic],
        })

        self.assertEqual(status, 200)
        names = data['concepts']['results'][0]['names']
        self.assertIn({
            'name': 'Hypertension',
            'locale': 'en',
            'type': 'FULLY_SPECIFIED',
            'preferred': True,
        }, names)

    def test_concept_names_include_non_preferred_locale(self):
        ConceptNameFactory(
            concept=self.concept1,
            name='Hypertension French',
            locale='fr',
            type='SYNONYM',
            locale_preferred=False,
        )
        query = """
        query ConceptsByIds($org: String, $source: String, $conceptIds: [String!]) {
          concepts(org: $org, source: $source, conceptIds: $conceptIds) {
            results {
              conceptId
              names { name locale type preferred }
            }
          }
        }
        """
        status, data = self._execute(query, {
            'org': self.organization.mnemonic,
            'source': self.source.mnemonic,
            'conceptIds': [self.concept1.mnemonic],
        })

        self.assertEqual(status, 200)
        names = data['concepts']['results'][0]['names']
        self.assertIn({
            'name': 'Hypertension French',
            'locale': 'fr',
            'type': 'SYNONYM',
            'preferred': False,
        }, names)

    def test_serialize_concepts_includes_metadata_for_retired_concepts(self):
        retired = ConceptFactory(
            parent=self.source,
            mnemonic='retired-1',
            concept_class='Procedure',
            datatype='Text',
            retired=True,
            extras={'is_set': True, 'text_format': 'markdown'},
            created_by=self.audit_user,
            updated_by=self.audit_user,
        )
        retired_user = UserProfileFactory(
            username='retired-moderator',
            created_by=self.super_user,
            updated_by=self.super_user,
        )
        retired.created_by = retired_user
        retired.updated_by = retired_user
        retired.save(update_fields=['created_by', 'updated_by'])
        retired.refresh_from_db()
        ConceptDescriptionFactory(concept=retired, name='Retired concept description', locale='en',
                                  locale_preferred=True)

        serialized = serialize_concepts([retired])[0]

        self.assertEqual(serialized.description, 'Retired concept description')
        self.assertEqual(serialized.concept_class, 'Procedure')
        self.assertEqual(serialized.datatype.name, 'Text')
        self.assertTrue(serialized.metadata.is_set)
        self.assertTrue(serialized.metadata.is_retired)
        self.assertIsNotNone(serialized.datatype.details)
        self.assertEqual(serialized.datatype.details.text_format, 'markdown')
        self.assertEqual(serialized.metadata.created_by, retired_user.username)
        self.assertEqual(serialized.metadata.updated_by, retired_user.username)
        self.assertEqual(serialized.metadata.created_at, format_datetime_for_api(retired.created_at))
        self.assertEqual(serialized.metadata.updated_at, format_datetime_for_api(retired.updated_at))
