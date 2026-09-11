"""Selection planning contracts and SQL-free index retrieval through the public schema."""

from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase
from elasticsearch import ConnectionError as ESConnectionError
from elasticsearch_dsl import Search
from elasticsearch_dsl.response import Response

from core.graphql.indexed import source_uri
from core.graphql.schema import schema
from core.graphql.selection import index_projection


class ProjectionTests(SimpleTestCase):
    """SimpleTestCase rejects SQL, including accidentally materialized ORM relationships."""

    def execute(self, query, variables=None, auth_status='none'):
        """Execute as an anonymous user without allowing database access."""
        return async_to_sync(schema.execute)(
            query, variable_values=variables,
            context_value=SimpleNamespace(user=AnonymousUser(), auth_status=auth_status),
        )

    @staticmethod
    def response(index, fields, total=None):
        """Use real Elasticsearch response wrappers to validate projection serialization."""
        hits = [{'_id': str(pos + 10), '_index': index, '_source': item} for pos, item in enumerate(fields)]
        return Response(Search(), {'hits': {'total': {'value': len(hits) if total is None else total}, 'hits': hits}})

    def test_source_metadata_has_no_sql_and_projects_selected_fields(self):
        """Minimal source reads use one ES request with owner, version and visibility filters."""
        response = self.response('sources', [{
            'name': 'CIEL', 'canonical_url': 'https://ciel.org', 'is_active': True,
            'version': 'HEAD', 'mnemonic': 'CIEL', 'owner': 'CIEL', 'owner_type': 'Organization',
        }])
        with patch('elasticsearch_dsl.Search.execute', autospec=True, return_value=response) as execute:
            result = self.execute('''{ source(org: "CIEL", source: "CIEL") {
                name canonicalUrl uri
            } }''')
        self.assertIsNone(result.errors)
        # The URI is rebuilt from ownership, mnemonic and version rather than read from the index.
        self.assertEqual(result.data['source']['uri'], '/orgs/CIEL/sources/CIEL/')
        body = execute.call_args.args[0].to_dict()
        self.assertEqual(set(body['_source']), {
            'name', 'canonical_url', 'is_active', 'version', 'mnemonic', 'owner', 'owner_type',
        })
        filters = str(body['query'])
        for expected in ('public_can_view', 'owner_type', 'Organization', 'ciel', 'HEAD'):
            self.assertIn(expected, filters)

    def test_rebuilt_source_uri_matches_the_stored_encoding(self):
        """The URI is not indexed, so its reconstruction must reproduce ORM percent-encoding."""
        def hit(owner, owner_type, mnemonic, version):
            return SimpleNamespace(owner=owner, owner_type=owner_type, mnemonic=mnemonic, version=version)

        self.assertEqual(source_uri(hit('CIEL', 'Organization', 'CIEL', 'HEAD')), '/orgs/CIEL/sources/CIEL/')
        self.assertEqual(source_uri(hit('jane', 'User', 'S1', 'HEAD')), '/users/jane/sources/S1/')
        # Reserved characters in a version label are double-encoded, exactly as calculate_uri stores them.
        self.assertEqual(
            source_uri(hit('OpenMRS-OCL-Squad', 'Organization', 'Bridge-5', 'WHO-ICD11@2026-01')),
            '/orgs/OpenMRS-OCL-Squad/sources/Bridge-5/WHO-ICD11%25402026-01/',
        )
        # An already-encoded label must not be encoded a second time.
        self.assertEqual(
            source_uri(hit('OCL', 'Organization', 'S1', 'v1%402026')),
            '/orgs/OCL/sources/S1/v1%25402026/',
        )

    def test_concept_fragments_aliases_and_directives_remain_sql_free(self):
        """Skipped heavy fields do not force ORM loading, including named fragments."""
        response = self.response('concepts', [{
            # The index stores the search-normalized pair; `display` is rebuilt from them.
            'id': '123', 'name': 'Hypertension_test', '_name': 'hypertension-test',
            'datatype': 'Numeric', 'concept_class': 'Diagnosis',
        }])
        query = '''query($heavy: Boolean!, $light: Boolean!) {
          found: concepts(query: "hypertension") { totalCount results {
            ...Light
            ... on ConceptType { externalId }
            names @include(if: $heavy) { name }
            mappings @skip(if: $light) { mapType }
          } }
        }
        fragment Light on ConceptType { code: conceptId label: display datatype { name } conceptClass }
        '''
        with patch('elasticsearch_dsl.Search.execute', autospec=True, return_value=response) as execute:
            result = self.execute(query, {'heavy': False, 'light': True})
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['found']['results'][0], {
            'code': '123', 'label': 'Hypertension-test', 'datatype': {'name': 'Numeric'},
            'conceptClass': 'Diagnosis', 'externalId': None,
        })
        body = execute.call_args.args[0].to_dict()
        self.assertEqual(set(body['_source']), {
            'id', 'name', '_name', 'datatype', 'concept_class', 'external_id',
        })
        self.assertNotIn('extras', body['_source'])
        self.assertIn('is_head', str(body['query']))

    def test_id_order_pagination_and_total(self):
        """Ordering follows exact mnemonic input, with duplicates removed before slicing."""
        response = self.response('concepts', [{'id': 'A'}, {'id': 'B'}, {'id': 'C'}])
        with patch('elasticsearch_dsl.Search.execute', return_value=response):
            result = self.execute('''{ concepts(conceptIds: ["C", "B", "C", "A"], page: 2, limit: 1) {
                totalCount hasNextPage results { conceptId }
            } }''')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['concepts'], {
            'totalCount': 3, 'hasNextPage': True, 'results': [{'conceptId': 'B'}],
        })

    def test_zero_hits_are_authoritative_for_index_projection(self):
        """An empty successful ES response does not trigger database search."""
        with patch('elasticsearch_dsl.Search.execute', return_value=self.response('concepts', [])):
            result = self.execute('{ concepts(query: "absent") { totalCount results { display } } }')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['concepts'], {'totalCount': 0, 'results': []})

    def test_two_aliases_plan_fields_independently(self):
        """Selections for one alias never get dropped or borrowed from another."""
        with patch('elasticsearch_dsl.Search.execute', autospec=True, return_value=self.response('concepts', [])) as es:
            result = self.execute('''{
              a: concepts(query: "a") { results { conceptId } }
              b: concepts(query: "b") { results { display } }
            }''')
        self.assertIsNone(result.errors)
        self.assertEqual([call.args[0].to_dict()['_source'] for call in es.call_args_list], [['id'], ['_name', 'name']])

    def test_invalid_auth_stops_all_queries(self):
        """Schema-level auth failure precedes both source and concept data access."""
        with patch('elasticsearch_dsl.Search.execute') as es:
            result = self.execute('{ source(org: "CIEL", source: "CIEL") { name } }', auth_status='invalid')
        self.assertEqual(result.errors[0].extensions['code'], 'AUTHENTICATION_FAILED')
        es.assert_not_called()

    def test_invalid_scope_and_pagination_stop_before_search(self):
        """Ambiguous ownership, incomplete pagination and excessive windows are rejected early."""
        cases = [
            'source(org: "O", owner: "U", source: "S") { name }',
            'source(source: "S") { name }',
            'concepts(query: "x", org: "O") { totalCount }',
            'concepts(query: "x", version: "v1") { totalCount }',
            'concepts(query: "x", page: 1) { totalCount }',
            'concepts(query: "x", limit: 1) { totalCount }',
            'concepts(query: "x", page: 0, limit: 1) { totalCount }',
            'concepts(query: "x", page: 2, limit: 10000) { totalCount }',
        ]
        for body in cases:
            with self.subTest(body=body), patch('elasticsearch_dsl.Search.execute') as es:
                result = self.execute('{' + body + '}')
                self.assertEqual(result.errors[0].extensions['code'], 'VALIDATION_ERROR')
                es.assert_not_called()

    def test_planner_rejects_relationships_and_details(self):
        """Only a fully covered payload can use the direct projection path."""
        self.assertIsNone(index_projection({'datatype.details.units'}, {'datatype.name': ('datatype',)}))
        self.assertIsNone(index_projection(None, {}))
        self.assertEqual(index_projection(set(), {}), [])

    def test_es_connection_failure_returns_fallback_signal(self):
        """Only expected transport failures request the permission-checked ORM fallback."""
        from core.graphql.indexed import indexed_concepts, indexed_source
        with patch('elasticsearch_dsl.Search.execute', side_effect=ESConnectionError('offline')):
            self.assertIsNone(indexed_concepts({'display'}, 'x', [], None, None, None, None, AnonymousUser()))
            self.assertIsNone(indexed_source('O', None, 'S', None, AnonymousUser(), {'name'}))

    def test_introspection_documents_queries_and_arguments(self):
        """GraphiQL exposes query, argument, source and summary descriptions."""
        sdl = schema.as_str()
        for fragment in ('canonicalUrl', 'externalSources', 'activeConcepts', 'SourceSummaryType',
                         'Owning organization mnemonic.', 'Source mnemonic, e.g. CIEL.',
                         'Free text to search concept identifiers'):
            self.assertIn(fragment, sdl)

    def test_typename_only_nested_selection_preserves_nullable_objects(self):
        """A datatype selected only for __typename still needs its indexed value."""
        response = self.response('concepts', [{'datatype': 'Numeric'}])
        with patch('elasticsearch_dsl.Search.execute', return_value=response):
            result = self.execute('{ concepts(query: "x") { results { datatype { __typename } } } }')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['concepts']['results'], [{'datatype': {'__typename': 'DatatypeType'}}])

    def test_count_only_does_not_fetch_result_documents(self):
        """Count-only queries request zero hits instead of loading an unused result page."""
        response = self.response('concepts', [], total=500)
        with patch('elasticsearch_dsl.Search.execute', autospec=True, return_value=response) as es:
            result = self.execute('{ concepts(query: "x") { totalCount } }')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['concepts']['totalCount'], 500)
        self.assertEqual(es.call_args.args[0].to_dict()['size'], 0)

    def test_shared_search_rule_preserves_rest_creator_access(self):
        """REST keeps creator visibility while GraphQL explicitly opts into owner/membership rules."""
        from core.common.search import get_document_public_visibility_criteria
        from elasticsearch_dsl import Q
        user = SimpleNamespace(is_authenticated=True, username='Creator')
        criterion = get_document_public_visibility_criteria(user, include_creator_private_access=True)
        expected = Q('term', public_can_view=True) | (
            Q('term', public_can_view=False) & Q('term', created_by='Creator')
        )
        self.assertEqual(criterion.to_dict(), expected.to_dict())
