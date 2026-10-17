"""
core.fhir.search: ConceptMap source-system, source-uri, target-system and target-uri (SVCM ITI-100), with the URL
registry toggle off and on. The versions holding a source's mappings are read from OCL's mappings index, one bounded
request per parameter, and the database checks the few mappings the index names for each version: the same Bundle as
before in a fixed number of queries, however many mappings a source has. A system or a ConceptMap the caller isn't
shown leaves no trace: the same bytes, statements and index requests as one OCL doesn't hold.
OpenConceptLab/ocl_online#451.
"""
import json
import re
from datetime import datetime, timezone as dt_timezone

from django.db import connection
from django.test.utils import CaptureQueriesContext
from elasticsearch import Elasticsearch, ConnectionTimeout
from mock import patch
from rest_framework.test import APIClient

from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.fhir.tests_search import SearchCorpusMixin, source, CIEL, ICD10
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.url_registry.factories import GlobalURLRegistryFactory
from core.url_registry.tests_resolution import set_registry_toggle
from core.users.tests.factories import UserProfileFactory

PRIVATE = 'http://moh.example/fhir/CodeSystem/private'
MY_CODES = 'https://example.org/my-codes'
NOW = datetime(2026, 10, 16, 12, tzinfo=dt_timezone.utc)


def index_requests():
    """A spy on the mappings index's search requests: patch it in, then read its call_args_list."""
    return patch.object(Elasticsearch, 'search', autospec=True, side_effect=Elasticsearch.search)


def aggregations(node):
    """Every aggregation of an index request's aggs, nested ones included: (kind, its body)."""
    for aggregation in node.values():
        for kind, body in aggregation.items():
            if kind == 'aggs':
                yield from aggregations(body)
            else:
                yield kind, body


class SystemSearchCases(SearchCorpusMixin):
    """
    CIEL v2 maps to sehatapp's ICD-10; jdoe's my-codes v1 maps to CIEL. MOH's private source is mapped to and from by
    Partner's public map. `registry` is the URL registry toggle, on with global entries for CIEL and ICD-10.
    """
    registry = False

    def setUp(self):
        super().setUp()
        set_registry_toggle(self.registry)
        if self.registry:
            GlobalURLRegistryFactory(url=CIEL, namespace=self.ciel.uri)
            GlobalURLRegistryFactory(url=ICD10, namespace=self.who.uri)
        self.ciel_code = ConceptFactory(parent=self.ciel_source, mnemonic='1427')
        self.icd_code = ConceptFactory(parent=self.sehat_icd, mnemonic='A00')
        self.ciel_to_icd = self.mapping(self.ciel_source, self.ciel_code, self.icd_code, [self.ciel_v2])
        self.my_code = ConceptFactory(parent=self.jdoe_codes, mnemonic='m1')
        self.my_codes_to_ciel = self.mapping(self.jdoe_codes, self.my_code, self.ciel_code, [self.jdoe_codes_v1])

        self.private = source(self.moh, 'private-codes', canonical_url=PRIVATE, public_access=ACCESS_TYPE_NONE)
        source(self.moh, 'private-codes', 'v1', released=True, canonical_url=PRIVATE, public_access=ACCESS_TYPE_NONE)
        self.private_code = ConceptFactory(parent=self.private, mnemonic='P1')
        self.partner = OrganizationFactory(mnemonic='Partner')
        self.partner_only = UserProfileFactory(username='partner-member')
        self.partner.members.add(self.partner_only)
        self.partner_map = source(self.partner, 'partner-map', canonical_url='https://example.org/partner-map')
        self.partner_map_v1 = source(self.partner, 'partner-map', 'v1', released=True,
                                     canonical_url='https://example.org/partner-map')
        own = ConceptFactory(parent=self.partner_map, mnemonic='X1')
        self.mapping(self.partner_map, own, self.private_code, [self.partner_map_v1])
        self.mapping(self.partner_map, self.private_code, own, [self.partner_map_v1])

    @staticmethod
    def mapping(parent, from_concept, to_concept, sources, **kwargs):
        """A mapping as OCL stores one: both sides' sources, their URLs and the codes."""
        kwargs.setdefault('to_concept_code', to_concept.mnemonic)
        return MappingFactory(
            parent=parent, from_concept=from_concept, to_concept=to_concept, from_source=from_concept.parent,
            to_source=to_concept.parent, from_source_url=from_concept.parent.uri, to_source_url=to_concept.parent.uri,
            from_concept_code=from_concept.mnemonic, sources=sources, **kwargs)

    @staticmethod
    def client_for(user):
        client = APIClient()
        if user:
            client.force_authenticate(user)
        return client

    # the versions come from the mappings index

    def test_a_version_is_found_once_its_mappings_are_indexed(self):
        """
        A version whose mappings the index doesn't hold yet (a new release while its indexing runs) isn't found until
        they are, as in OCL's own mapping search; the database still decides what the version shows.
        """
        for name in ('target-system', 'target-uri'):
            self.assertEqual(self.keys(self.search('ConceptMap', **{name: ICD10})), [f'{CIEL}|v2'], name)
        MappingDocument().update(self.ciel_to_icd, action='delete')
        for name in ('target-system', 'target-uri'):
            self.assertEqual(self.search('ConceptMap', **{name: ICD10})['total'], 0, name)
        MappingDocument().update(self.ciel_to_icd)
        self.assertEqual(self.keys(self.search('ConceptMap', **{'target-system': ICD10})), [f'{CIEL}|v2'])

    def test_one_bounded_index_request_per_parameter(self):
        """
        One request per system parameter, whatever its alternatives, sent only for a system among the sources the base
        shows the caller: none for one OCL doesn't hold or the caller isn't shown. Each aggregation has an explicit
        size, leaves HEAD out and lists a few mappings of each version.
        """
        unknown = ','.join(f'https://example.org/unknown-{index}' for index in range(19))
        for base, user, params, requests, total in (
                ('/fhir', None, {'source-system': CIEL}, 1, 1),
                ('/fhir', None, {'source-uri': f'{unknown},{CIEL}'}, 1, 1),
                ('/fhir', None, {'source-system': CIEL, 'target-system': ICD10}, 2, 1),
                ('/fhir', None, {'target-system': f'{ICD10},{CIEL}'}, 1, 2),
                ('/fhir', None, {'target-system': unknown}, 0, 0),
                ('/fhir', self.admin, {'target-system': PRIVATE}, 0, 0),
                ('/orgs/Partner', self.partner_only, {'target-system': PRIVATE}, 0, 0),
                ('/orgs/Partner', self.admin, {'target-system': PRIVATE}, 1, 1)):
            with index_requests() as spy:
                response = self.client_for(user).get(f'{base}/ConceptMap/', params)
            self.assertEqual((response.status_code, response.data['total']), (200, total), params)
            self.assertEqual(len(spy.call_args_list), requests, (base, params))
            for call in spy.call_args_list:
                body = call.kwargs
                self.assertEqual((body['index'], body['size']), (MappingDocument.Index.name, 0))
                found = list(aggregations(body['aggs']))
                self.assertTrue(found)
                for kind, aggregation in found:
                    if kind in ('terms', 'top_hits'):
                        self.assertLessEqual(aggregation['size'], 1000, aggregation)
                    if kind == 'terms' and aggregation['field'] == 'source_version':
                        self.assertEqual(aggregation['exclude'], 'HEAD')

    def test_the_database_checks_a_few_mappings_of_each_version(self):
        """However many mappings a version holds, the database reads the few the index names: a fixed query count."""
        for index in range(12):
            self.mapping(self.ciel_source, ConceptFactory(parent=self.ciel_source), self.icd_code,
                         [self.ciel_v1, self.ciel_v2], to_concept_code=f'A{index:02}')
        with CaptureQueriesContext(connection) as context:
            response = self.client.get('/fhir/ConceptMap/', {'target-system': ICD10})
        self.assertEqual(self.keys(response.data), [f'{CIEL}|v1', f'{CIEL}|v2'])
        self.assertEqual(len(context.captured_queries), 3)  # the sources with the canonical, the matches' ids, the page
        checked = re.findall(r'"mapping_id" IN \(([^)]*)\)', context.captured_queries[1]['sql'])
        self.assertEqual(len(checked), 1, context.captured_queries[1]['sql'])
        self.assertLessEqual(len(checked[0].split(',')), 2 * 3)  # two versions, three mappings each

    def test_a_version_with_newer_retired_mappings_is_found_by_an_older_live_one(self):
        for _ in range(4):
            self.mapping(self.jdoe_codes, ConceptFactory(parent=self.jdoe_codes), self.ciel_code,
                         [self.jdoe_codes_v1], retired=True)
        self.assertEqual(self.keys(self.search('ConceptMap', **{'target-system': CIEL})), [f'{MY_CODES}|v1'])
        Mapping.objects.filter(id=self.my_codes_to_ciel.id).update(retired=True)  # the index still has it live
        self.assertEqual(self.search('ConceptMap', **{'target-system': CIEL})['total'], 0)

    def test_owners_whose_names_differ_only_in_case_are_told_apart(self):
        """The index keeps a mapping's owner and repository names in lowercase; the database tells such twins apart."""
        maps = []
        for mnemonic in ('Acme', 'ACME'):
            owner = OrganizationFactory(mnemonic=mnemonic)
            head = source(owner, 'Maps', canonical_url=f'https://example.org/{mnemonic}/maps')
            maps.append(source(owner, 'Maps', 'v1', released=True, canonical_url=head.canonical_url))
        self.mapping(maps[0].head, ConceptFactory(parent=maps[0].head), self.ciel_code, [maps[0]])
        self.assertEqual(self.keys(self.search('ConceptMap', **{'target-system': CIEL})),
                         sorted([f'{MY_CODES}|v1', 'https://example.org/Acme/maps|v1']))
        self.mapping(maps[1].head, ConceptFactory(parent=maps[1].head), self.ciel_code, [maps[1]])
        self.assertEqual(self.keys(self.search('ConceptMap', **{'target-system': CIEL})), sorted(
            [f'{MY_CODES}|v1', 'https://example.org/ACME/maps|v1', 'https://example.org/Acme/maps|v1']))

    # cost: a bound on the index's answer and on its time

    def test_more_versions_than_the_index_lists_is_too_costly(self):
        self.mapping(self.ciel_source, ConceptFactory(parent=self.ciel_source), self.icd_code, [self.ciel_v1])
        self.assertEqual(self.search('ConceptMap', **{'target-system': ICD10})['total'], 2)
        with patch('core.fhir.mapping_index.MAX_BUCKETS', 1):
            response = self.client.get('/fhir/ConceptMap/', {'target-system': ICD10})
        self.assertOutcome(response, 422, 'too-costly', 'too-costly')

    def test_an_index_request_past_the_search_time_is_too_costly(self):
        with patch.object(Elasticsearch, 'search', autospec=True, side_effect=ConnectionTimeout('timed out')):
            response = self.client.get('/fhir/ConceptMap/', {'target-system': ICD10})
        self.assertOutcome(response, 422, 'too-costly', 'too-costly')
        with index_requests() as spy, patch('core.fhir.search.SEARCH_TIMEOUT_MS', 0):
            response = self.client.get('/fhir/ConceptMap/', {'source-system': CIEL})
        self.assertOutcome(response, 422, 'too-costly', 'too-costly')
        self.assertEqual(spy.call_args_list, [])  # with no time left, it isn't sent

    # what the caller isn't shown leaves no trace

    def answers(self, requests):
        """{(base, params, caller): (status, bytes, statements, index requests)}, the ids the index names as N."""
        answers = {}
        for base, params, callers in requests:
            for who, user in callers:
                with CaptureQueriesContext(connection) as context, index_requests() as spy, patch(
                        'core.fhir.search.timezone') as clock:
                    clock.now.return_value = NOW
                    response = self.client_for(user).get(f'{base}/ConceptMap/', params)
                statements = [re.sub(r'\b\d+\b', 'N', re.sub(r'"mapping_id" IN \([^)]*\)', '"mapping_id" IN (N)',
                                                             query['sql'])) for query in context.captured_queries]
                answers[(base, json.dumps(params), who)] = (
                    response.status_code, response.content, statements, len(spy.call_args_list))
        return answers

    def test_a_system_the_caller_is_not_shown_answers_as_a_missing_one(self):
        """
        MOH's private source, by canonical URL and by OCL path, on either side, answers with the same bytes,
        statements and index requests as once it's gone: Partner's mappings then store its URL with no source, as
        mappings into a source OCL doesn't hold do.
        """
        callers = [('anonymous', None), ('outsider', self.outsider), ('partner-member', self.partner_only),
                   ('staff', self.admin)]
        requests = [(base, {name: value}, callers if base == '/fhir' else callers[:3])
                    for base in ('/fhir', '/orgs/Partner', '/orgs/MOH')
                    for value in (PRIVATE, '/orgs/MOH/ConceptMap/private-codes/')
                    for name in ('source-system', 'source-uri', 'target-system', 'target-uri')]
        hidden = self.answers(requests)
        self.assertEqual(hidden[('/fhir', json.dumps({'target-system': '/orgs/MOH/ConceptMap/private-codes/'}),
                                 'anonymous')][1].count(b'"fullUrl"'), 1)  # Partner's map, by the stored URL
        Source.objects.filter(mnemonic='private-codes').delete()
        missing = self.answers(requests)
        self.assertEqual(hidden.keys(), missing.keys())
        for key, answer in hidden.items():
            self.assertEqual(answer, missing[key], key)

    def test_a_concept_map_the_caller_is_not_shown_leaves_no_trace(self):
        """
        A private ConceptMap mapping into CIEL changes no public answer: the same bytes, statements and index requests
        as without it, though the index lists its versions too.
        """
        hidden = source(self.moh, 'moh-map', canonical_url='https://moh.example/fhir/ConceptMap/moh',
                        public_access=ACCESS_TYPE_NONE)
        hidden_v1 = source(self.moh, 'moh-map', 'v1', released=True, canonical_url=hidden.canonical_url,
                           public_access=ACCESS_TYPE_NONE)
        for _ in range(3):
            self.mapping(hidden, ConceptFactory(parent=hidden), self.ciel_code, [hidden_v1])
            self.mapping(hidden, self.ciel_code, ConceptFactory(parent=hidden), [hidden_v1])
        callers = [('anonymous', None), ('outsider', self.outsider)]
        requests = [(base, {name: value}, callers) for base in ('/fhir', '/orgs/CIEL') for value in (CIEL, ICD10)
                    for name in ('source-system', 'target-system')]
        with_it = self.answers(requests)
        self.assertEqual(with_it[('/fhir', json.dumps({'target-system': CIEL}), 'anonymous')][:1], (200,))
        Source.objects.filter(mnemonic='moh-map').delete()
        self.assertEqual(with_it, self.answers(requests))


class SystemSearchWithoutRegistryTest(SystemSearchCases, OCLTestCase):
    registry = False


class SystemSearchWithRegistryTest(SystemSearchCases, OCLTestCase):
    registry = True
