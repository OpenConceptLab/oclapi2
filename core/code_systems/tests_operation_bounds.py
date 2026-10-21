"""
FHIR operations read only the concepts and mappings their input names (OpenConceptLab/ocl_online#451).

A $validate-code or $translate without a code, or without the system the code is in, is a 400 OperationOutcome,
never a read of every concept or mapping. A POSTed Parameters resource is read with the value types R4 gives each
input, so a code sent as valueString is still the code.
"""
import json
import re
from itertools import product
from urllib.parse import urlencode

from django.db import connection
from django.test.utils import CaptureQueriesContext
from mock.mock import patch, Mock
from rest_framework.response import Response
from rest_framework.test import APIClient

from core.code_systems.serializers import ValidateCodeParametersSerializer
from core.collections.models import CollectionReference
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.mixins import ListWithHeadersMixin
from core.common.tests import OCLTestCase
from core.common.views import BaseAPIView
from core.concepts.tests.factories import ConceptFactory
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory

SYSTEM = 'http://example.org/fhir/CodeSystem/bounds'
VALUE_SET = 'http://example.org/fhir/ValueSet/bounds'
DISPLAY = 'Malaria'
WRONG_DISPLAY = 'Not the display of c1'
VALUE_TYPES = {'url': 'valueUri', 'system': 'valueUri', 'code': 'valueCode', 'display': 'valueString',
               'systemVersion': 'valueString'}
# A statement that reads concept or mapping rows: that table is its own, not a joined one
ROW_READ = re.compile(r'^SELECT .+? FROM "(concepts|mappings)"(?: |$)')
# A literal equality, or a literal IN list, on a column that names rows: a code, an id, or the concept whose
# parents or children are read. A LIMIT alone doesn't count: `.first()` of every concept is still every concept.
NAMED_ROWS = re.compile(
    r'"((concepts|mappings)"\."(mnemonic|from_concept_code|id|versioned_object_id)'
    r'|concepts_hierarchicalconcepts"\."(child_id|parent_id))" (= (\'|\d)|IN \((?!SELECT))')
# The list views' own query parameters and headers, each of which would take a list shortcut (a search, a sort,
# facets, search stats or a CSV export) past an operation's checks if the operation inherited it
LIST_SHORTCUTS = [
    ({'q': 'c1'}, {}),
    ({'sortAsc': 'id'}, {}),
    ({'sortDesc': 'id'}, {}),
    ({'facetsOnly': 'true'}, {}),
    ({'searchStatsOnly': 'true', 'q': 'c1'}, {}),
    ({'csv': 'true'}, {}),
    ({}, {'HTTP_INCLUDEFACETS': 'true'}),
    ({'q': 'c1'}, {'HTTP_INCLUDESEARCHSTATS': 'true'}),
]


def row_reads(queries):
    return [query['sql'] for query in queries if ROW_READ.match(query['sql'])]


def unbounded_reads(queries):
    """The statements that read concept or mapping rows without naming them by code or id."""
    return [sql for sql in row_reads(queries) if not NAMED_ROWS.search(sql)]


class FhirOperationBoundsTest(OCLTestCase):
    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory(mnemonic='BoundsOrg')
        self.source = OrganizationSourceFactory(organization=self.org, mnemonic='bounds', canonical_url=SYSTEM)
        self.concept = ConceptFactory(parent=self.source, mnemonic='c1', names=1, names__name=DISPLAY)
        other = ConceptFactory(parent=self.source, mnemonic='c2')
        MappingFactory(parent=self.source, from_concept=self.concept, to_concept=other, from_source=self.source,
                       to_source=self.source, from_concept_code='c1', to_concept_code='c2')
        self.source_v1 = OrganizationSourceFactory.build(
            version='v1', mnemonic='bounds', organization=self.org, canonical_url=SYSTEM, released=True)
        Source.persist_new_version(self.source_v1, self.source.created_by)
        self.collection = OrganizationCollectionFactory(
            organization=self.org, mnemonic='boundsvs', canonical_url=VALUE_SET, version='HEAD')
        self.collection_v1 = OrganizationCollectionFactory(
            organization=self.org, mnemonic='boundsvs', canonical_url=VALUE_SET, version='v1', released=True,
            is_latest_version=True)
        for collection, name in [(self.collection, 'e1'), (self.collection_v1, 'e2')]:
            collection.expansion_uri = ExpansionFactory(mnemonic=name, collection_version=collection).uri
            collection.save()
        self.collection.add_references([CollectionReference(
            expression=self.concept.uri, collection=self.collection, code='c1', system=self.source.uri, version='v1')])
        self.collection_v1.seed_references()
        self.client = APIClient()

    def request(self, path, method, headers=None, **inputs):
        """(response, the SQL it ran) for a GET with the inputs as a query, or a POST of them as Parameters. A POST
        as 'POST valueString' sends every input as valueString, as some clients do."""
        inputs = {name: value for name, value in inputs.items() if value is not None}
        with CaptureQueriesContext(connection) as context:
            if method == 'GET':
                response = self.client.get(f'{path}?{urlencode(inputs)}' if inputs else path, **(headers or {}))
            else:
                parameters = [{'name': name, ('valueString' if 'valueString' in method else VALUE_TYPES[name]): value}
                              for name, value in inputs.items()]
                response = self.client.post(path, json.dumps({'resourceType': 'Parameters', 'parameter': parameters}),
                                            content_type='application/json')
        return response, context.captured_queries

    def assert_required(self, response, name, msg):
        self.assertEqual(response.status_code, 400, msg)
        outcome = json.loads(response.content)
        self.assertEqual(outcome['resourceType'], 'OperationOutcome', msg)
        self.assertEqual(outcome['issue'][0]['code'], 'invalid', msg)
        self.assertEqual(outcome['issue'][0]['expression'], [name], msg)

    def assert_result(self, response, expected, msg):
        self.assertEqual(response.status_code, 200, msg)
        self.assertEqual(response.data['parameter'][0], {'name': 'result', 'valueBoolean': expected}, msg)

    def test_code_system_validate_code_reads_only_the_named_code(self):
        cases = product(('GET', 'POST', 'POST valueString'), (None, SYSTEM), (None, 'c1'),
                        (None, DISPLAY, WRONG_DISPLAY))
        for method, url, code, display in cases:
            msg = (method, url, code, display)
            with self.subTest(msg):
                response, queries = self.request(
                    '/fhir/CodeSystem/$validate-code', method, url=url, code=code, display=display)

                self.assertEqual(unbounded_reads(queries), [], msg)
                if not code:
                    self.assert_required(response, 'code', msg)
                elif not url:
                    self.assert_required(response, 'url', msg)
                else:
                    self.assert_result(response, display != WRONG_DISPLAY, msg)
                    self.assertTrue([sql for sql in row_reads(queries) if '"concepts"."mnemonic" = \'c1\'' in sql], msg)

    def test_code_system_validate_code_with_a_misnamed_code_is_a_400(self):
        response, queries = self.request(
            '/fhir/CodeSystem/$validate-code', 'GET', url=SYSTEM, Code='c1', display=WRONG_DISPLAY)

        self.assertEqual(unbounded_reads(queries), [])
        self.assert_required(response, 'code', 'Code')

    def test_code_system_validate_code_for_an_unknown_system_is_false_with_or_without_a_code(self):
        for code, display in product((None, 'c1'), (None, WRONG_DISPLAY)):
            with self.subTest((code, display)):
                response, queries = self.request(
                    '/fhir/CodeSystem/$validate-code', 'GET', url='http://example.org/unknown', code=code,
                    display=display)

                self.assertEqual(unbounded_reads(queries), [], (code, display))
                self.assert_result(response, False, (code, display))

    def test_value_set_validate_code_reads_only_the_named_code(self):
        paths = [('/fhir/ValueSet/$validate-code', None), ('/fhir/ValueSet/$validate-code', VALUE_SET),
                 (f'/orgs/{self.org.mnemonic}/ValueSet/boundsvs/$validate-code', None)]
        for method, (path, url), code, system, display in product(
                ('GET', 'POST valueString'), paths, (None, 'c1'), (None, SYSTEM), (None, WRONG_DISPLAY)):
            msg = (method, path, url, code, system, display)
            with self.subTest(msg):
                response, queries = self.request(
                    path, method, url=url, code=code, system=system, systemVersion='v1', display=display)

                self.assertEqual(unbounded_reads(queries), [], msg)
                if not code:
                    self.assert_required(response, 'code', msg)
                elif not system:
                    self.assert_required(response, 'system', msg)
                else:
                    self.assert_result(response, display is None, msg)

    def test_translate_reads_only_the_named_code(self):
        for method, url, code, system in product(
                ('GET', 'POST', 'POST valueString'), (None, SYSTEM), (None, 'c1'), (None, SYSTEM)):
            msg = (method, url, code, system)
            with self.subTest(msg):
                response, queries = self.request(
                    '/fhir/ConceptMap/$translate', method, url=url, code=code, system=system)

                self.assertEqual(unbounded_reads(queries), [], msg)
                if not code:
                    self.assert_required(response, 'code', msg)
                elif not system:
                    self.assert_required(response, 'system', msg)
                else:
                    self.assert_result(response, True, msg)
                    self.assertEqual(response.data['parameter'][1]['part'][1]['valueCoding']['code'], 'c2', msg)

    def test_lookup_reads_only_the_named_code(self):
        for system, code in product((None, SYSTEM), (None, 'c1')):
            with self.subTest((system, code)):
                response, queries = self.request('/fhir/CodeSystem/$lookup', 'GET', system=system, code=code)

                self.assertEqual(unbounded_reads(queries), [], (system, code))
                self.assertEqual(response.status_code, 200 if system and code else 400, (system, code))

    def test_operations_take_no_list_shortcut(self):
        """A search, sort, facets, search stats or CSV export never stands in for an operation's own checks."""
        operations = [
            ('/fhir/CodeSystem/$validate-code', {'url': SYSTEM, 'code': 'c1'}),
            ('/fhir/ValueSet/$validate-code',
             {'url': VALUE_SET, 'system': SYSTEM, 'systemVersion': 'v1', 'code': 'c1'}),
            ('/fhir/ConceptMap/$translate', {'url': SYSTEM, 'system': SYSTEM, 'code': 'c1'}),
            ('/fhir/CodeSystem/$lookup', {'system': SYSTEM, 'code': 'c1'}),
        ]
        shortcuts = {
            (BaseAPIView, 'get_search_results_qs'): (Mapping.objects.none(), {}, None, {}),
            (BaseAPIView, 'get_fuzzy_search_results_qs'): (Mapping.objects.none(), {}, None, {}),
            (BaseAPIView, 'get_facets'): {},
            (BaseAPIView, 'get_search_stats'): {},
            (ListWithHeadersMixin, 'get_csv'): Response({'url': 'https://example.org/export.csv'}),
        }
        mocks = {name: Mock(return_value=value) for (_, name), value in shortcuts.items()}
        patches = [patch.object(klass, name, mocks[name]) for klass, name in shortcuts]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        for (path, inputs), (params, headers) in product(operations, LIST_SHORTCUTS):
            msg = (path, params, headers)
            with self.subTest(msg):
                expected, _ = self.request(path, 'GET', **inputs)
                response, queries = self.request(path, 'GET', headers=headers, **inputs, **params)

                self.assertEqual(unbounded_reads(queries), [], msg)
                self.assertEqual(
                    (response.status_code, json.loads(response.content)),
                    (expected.status_code, json.loads(expected.content)), msg)

                without_code = {name: value for name, value in inputs.items() if name != 'code'}
                response, queries = self.request(path, 'GET', headers=headers, **without_code, **params)

                self.assertEqual(unbounded_reads(queries), [], msg)
                self.assertEqual(response.status_code, 400, msg)
        self.assertEqual({name: mock.call_count for name, mock in mocks.items()}, dict.fromkeys(mocks, 0))

    def test_parameters_are_read_with_the_value_types_r4_allows(self):
        serializer = ValidateCodeParametersSerializer(data={'resourceType': 'Parameters', 'parameter': [
            {'name': 'url', 'valueString': SYSTEM},
            {'name': 'code', 'valueString': 'c1'},
            {'name': 'display', 'valueString': DISPLAY},
            {'name': 'version', 'valueCode': 'v1'},
            {'name': 'system', 'valueBoolean': True},
        ]})

        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data['parameters'], {'url': SYSTEM, 'code': 'c1', 'display': DISPLAY})

        serializer = ValidateCodeParametersSerializer(data={'resourceType': 'Parameters', 'parameter': [
            {'name': 'code', 'valueCode': 'c1', 'valueString': 'c2'}]})
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data['parameters'], {'code': 'c1'})
