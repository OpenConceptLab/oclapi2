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
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from django.utils import timezone
from django.test import tag
from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig
from elasticsearch import ApiError, ConnectionError as ESConnectionError, ConnectionTimeout, Elasticsearch, \
    NotFoundError
from elasticsearch_dsl.connections import connections
from mock import patch
from rest_framework.test import APIClient

from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.fhir.mapping_index import LAG_WINDOW
from core.fhir.resolve import FhirResolver
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


def es_error(kind, status):
    """An error as the Elasticsearch client raises it for an HTTP status."""
    meta = ApiResponseMeta(status=status, http_version='1.1', headers=HttpHeaders(), duration=0.0,
                           node=NodeConfig('http', 'es', 9200))
    return kind(message='refused', meta=meta, body={'error': {'type': 'refused'}})


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

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        set_registry_toggle(cls.registry)
        if cls.registry:
            GlobalURLRegistryFactory(url=CIEL, namespace=cls.ciel.uri)
            GlobalURLRegistryFactory(url=ICD10, namespace=cls.who.uri)
        cls.ciel_code = ConceptFactory(parent=cls.ciel_source, mnemonic='1427')
        cls.icd_code = ConceptFactory(parent=cls.sehat_icd, mnemonic='A00')
        cls.ciel_to_icd = cls.mapping(cls.ciel_source, cls.ciel_code, cls.icd_code, [cls.ciel_v2])
        cls.my_code = ConceptFactory(parent=cls.jdoe_codes, mnemonic='m1')
        cls.my_codes_to_ciel = cls.mapping(cls.jdoe_codes, cls.my_code, cls.ciel_code, [cls.jdoe_codes_v1])

        cls.private = source(cls.moh, 'private-codes', canonical_url=PRIVATE, public_access=ACCESS_TYPE_NONE)
        source(cls.moh, 'private-codes', 'v1', released=True, canonical_url=PRIVATE, public_access=ACCESS_TYPE_NONE)
        cls.private_code = ConceptFactory(parent=cls.private, mnemonic='P1')
        cls.partner = OrganizationFactory(mnemonic='Partner')
        cls.partner_only = UserProfileFactory(username='partner-member')
        cls.partner.members.add(cls.partner_only)
        cls.partner_map = source(cls.partner, 'partner-map', canonical_url='https://example.org/partner-map')
        cls.partner_map_v1 = source(cls.partner, 'partner-map', 'v1', released=True,
                                    canonical_url='https://example.org/partner-map')
        own = ConceptFactory(parent=cls.partner_map, mnemonic='X1')
        cls.mapping(cls.partner_map, own, cls.private_code, [cls.partner_map_v1])
        cls.mapping(cls.partner_map, cls.private_code, own, [cls.partner_map_v1])
        cls.corpus_mapping_ids = list(Mapping.objects.values_list('id', flat=True))

    def setUp(self):
        super().setUp()
        # ES isn't rolled back with the database: index the shared mappings as stored, whatever an earlier test did
        MappingDocument().update(Mapping.objects.filter(id__in=self.corpus_mapping_ids), refresh=True)

    @staticmethod
    def mapping(parent, from_concept, to_concept, sources, **kwargs):
        """A mapping as OCL stores one: both sides' sources, their URLs, the codes and its repository's access."""
        kwargs.setdefault('to_concept_code', to_concept.mnemonic)
        kwargs.setdefault('public_access', parent.public_access)
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

    @staticmethod
    def unindexed(mapping):
        """The index as it is while a release's indexing runs: the mapping's document lists HEAD alone."""
        connections.get_connection().update(
            index=MappingDocument.Index.name, id=str(mapping.id), doc={'source_version': ['HEAD']}, refresh=True)

    def test_a_release_whose_mappings_are_not_indexed_yet_answers_as_before(self):
        """
        While a release's indexing runs, the documents of its mappings don't list it yet: a release created within
        LAG_WINDOW, of a repository the index shows mapping to the system, is checked in the database all the same.
        The same Bundle before, during and after.
        """
        names = ('target-system', 'target-uri')
        before = {name: self.client.get('/fhir/ConceptMap/', {name: ICD10}).data for name in names}
        self.assertEqual(self.keys(before['target-system']), [f'{CIEL}|v2'])
        self.unindexed(self.ciel_to_icd)
        for name in names:
            during = self.client.get('/fhir/ConceptMap/', {name: ICD10}).data
            self.assertEqual((self.keys(during), during['total']), (self.keys(before[name]), 1), name)
        MappingDocument().update(self.ciel_to_icd)
        self.assertEqual(self.keys(self.search('ConceptMap', **{'target-system': ICD10})), [f'{CIEL}|v2'])

    def unindexed_release(self, target):
        """A public ConceptMap no other test's documents name, its release v1 mapping to `target`: (key, mapping)."""
        name = f'maps-{uuid.uuid4().hex[:8]}'
        maps = source(self.misc, name, canonical_url=f'https://example.org/{name}')
        release = source(self.misc, name, 'v1', released=True, canonical_url=maps.canonical_url)
        return f'{maps.canonical_url}|v1', release, self.mapping(maps, ConceptFactory(parent=maps), target, [release])

    def test_residual_a_release_unindexed_past_the_lag_window_is_missed(self):
        """
        Residual (a decision for Jon): past LAG_WINDOW, a release whose mappings' documents never came to list it (its
        indexing failed) is missed until it's indexed again; WP3's statement found it. Within the window it's found.
        """
        key, release, mapping = self.unindexed_release(self.icd_code)
        self.assertIn(key, self.keys(self.search('ConceptMap', **{'target-system': ICD10})))
        self.unindexed(mapping)
        self.assertIn(key, self.keys(self.search('ConceptMap', **{'target-system': ICD10})))  # within the window
        Source.objects.filter(id=release.id).update(created_at=timezone.now() - LAG_WINDOW - timedelta(hours=1))
        self.assertNotIn(key, self.keys(self.search('ConceptMap', **{'target-system': ICD10})))
        MappingDocument().update(mapping)
        self.assertIn(key, self.keys(self.search('ConceptMap', **{'target-system': ICD10})))

    def test_residual_a_repository_none_of_whose_mappings_are_indexed_is_missed(self):
        """
        Residual (a decision for Jon): a ConceptMap none of whose mappings to the system are in the index yet (an
        import that skipped indexing, or one still indexing) is missed, at any age, until they are indexed; WP3's
        statement found it.
        """
        key, release, _ = self.unindexed_release(self.icd_code)
        mappings = Mapping.objects.filter(parent__mnemonic=release.mnemonic)  # every row: HEAD's latest version too
        MappingDocument().update(mappings, action='delete')
        self.assertNotIn(key, self.keys(self.search('ConceptMap', **{'target-system': ICD10})))
        MappingDocument().update(mappings)
        self.assertIn(key, self.keys(self.search('ConceptMap', **{'target-system': ICD10})))

    def test_residual_an_ocl_path_is_matched_by_the_stored_url_statement(self):
        """
        Residual (a decision for Jon): a system written as an OCL path is matched as WP3 matched it, by the URL each
        mapping stores, a prefix read of that index with no request to the mappings index: its cost follows the
        number of mappings storing the URL (LOINC's 1.08M locally, about 3 s; see the report).
        """
        with CaptureQueriesContext(connection) as context, index_requests() as spy:
            response = self.client.get('/fhir/ConceptMap/', {'target-system': '/orgs/sehatapp/ConceptMap/ICD-10/'})
        self.assertEqual((response.status_code, response.data['total']), (200, 0))
        self.assertEqual(spy.call_args_list, [])
        self.assertIn("LIKE '/orgs/sehatapp/sources/ICD-10/%'", context.captured_queries[1]['sql'])

    def test_one_bounded_index_request_per_parameter(self):
        """
        One request per system parameter, whatever its alternatives, sent only for a system among the sources the base
        shows the caller: none for one OCL doesn't hold or the caller isn't shown. It reads only the documents of the
        ConceptMap versions the base shows the caller, as the database lists them (never a document's own access copy),
        whole (no partial results) within a time limit, and each aggregation has an explicit size and lists only those
        repositories and versions (and HEAD); it lists versions, not mappings.
        """
        unknown = ','.join(f'https://example.org/unknown-{index}' for index in range(19))
        for base, user, params, requests, total in (
                ('/fhir', None, {'source-system': CIEL}, 1, 1),
                ('/fhir', None, {'source-uri': f'{unknown},{CIEL}'}, 1, 1),
                ('/fhir', None, {'source-system': CIEL, 'target-system': ICD10}, 2, 1),
                ('/fhir', None, {'target-system': f'{ICD10},{CIEL}'}, 1, 2),
                ('/fhir', None, {'target-system': unknown}, 0, 0),
                ('/fhir', self.admin, {'target-system': PRIVATE}, 0, 0),
                ('/fhir', self.admin, {'target-system': CIEL}, 1, 1),
                ('/orgs/Partner', self.partner_only, {'target-system': PRIVATE}, 0, 0),
                ('/orgs/Partner', self.admin, {'target-system': PRIVATE}, 1, 1),
                ('/orgs/Partner', None, {'target-system': f'{PRIVATE},{CIEL}'}, 1, 0)):
            with index_requests() as spy:
                response = self.client_for(user).get(f'{base}/ConceptMap/', params)
            self.assertEqual((response.status_code, response.data['total']), (200, total), params)
            self.assertEqual(len(spy.call_args_list), requests, (base, params))
            for call in spy.call_args_list:
                self.assert_bounded(call.kwargs, self.scope(base, user))

    @staticmethod
    def scope(base, user):
        """
        The owner types, owners and repositories (in lowercase, as the index keeps them) and the version names (with
        HEAD) of the ConceptMap versions the database shows `user` at `base`.
        """
        kwargs = {} if base == '/fhir' else {'org': base.rsplit('/', 1)[-1]}
        scope = {'owner_type': set(), 'owner': set(), 'source': set(), 'source_version': {'HEAD'}}
        for mnemonic, org, username, version in FhirResolver(kwargs, user).visible_versions('ConceptMap').values_list(
                'mnemonic', 'organization__mnemonic', 'user__username', 'version'):
            scope['owner_type'].add('Organization' if org else 'User')
            scope['owner'].add((org or username).lower())
            scope['source'].add(mnemonic.lower())
            scope['source_version'].add(version)
        return {field: sorted(values) for field, values in scope.items()}

    def assert_bounded(self, body, scope):  # pylint: disable=invalid-name
        """
        An index request: whole, timed, filtered to the documents of `scope` (never by the documents' own access),
        every aggregation sized, the versions listing only `scope`'s (the filter bounds the rest), no hits.
        """
        self.assertEqual((body['index'], body['size'], body['allow_partial_search_results']),
                         (MappingDocument.Index.name, 0, False))
        self.assertRegex(body['timeout'], r'^[0-9]+ms$')
        self.assertNotIn('public_can_view', json.dumps(body))
        self.assertEqual(body['query']['bool']['filter'][1:],
                         [{'terms': {field: values}} for field, values in scope.items()])
        found = list(aggregations(body['aggs']))
        self.assertNotIn('top_hits', [kind for kind, _ in found])
        terms = [aggregation for kind, aggregation in found if kind == 'terms']
        self.assertEqual([aggregation['field'] for aggregation in terms][:4], list(scope))
        for aggregation in terms:
            self.assertLessEqual(aggregation['size'], 1000, aggregation)
            self.assertEqual(aggregation.get('include'),
                             scope['source_version'] if aggregation['field'] == 'source_version' else None, aggregation)

    def test_queries_stay_three_however_many_mappings(self):
        for index in range(12):
            self.mapping(self.ciel_source, ConceptFactory(parent=self.ciel_source), self.icd_code,
                         [self.ciel_v1, self.ciel_v2], to_concept_code=f'A{index:02}')
        with self.assertNumQueries(3):  # the sources with the canonical, the matches' ids, the page
            response = self.client.get('/fhir/ConceptMap/', {'target-system': ICD10})
        self.assertEqual(self.keys(response.data), [f'{CIEL}|v1', f'{CIEL}|v2'])

    def test_a_version_is_decided_by_all_its_mappings_not_a_few(self):
        """
        However many of a version's newer mappings the database rejects (retired or deleted there, live in the index),
        an older live one still finds it; once that one is retired too, the version isn't found.
        """
        newer = [self.mapping(self.jdoe_codes, ConceptFactory(parent=self.jdoe_codes), self.ciel_code,
                              [self.jdoe_codes_v1]) for _ in range(4)]
        Mapping.objects.filter(id__in=[mapping.id for mapping in newer[:2]]).update(retired=True)
        Mapping.objects.filter(id__in=[mapping.id for mapping in newer[2:]]).update(is_active=False)
        for name in ('target-system', 'target-uri'):
            self.assertEqual(self.keys(self.search('ConceptMap', **{name: CIEL})), [f'{MY_CODES}|v1'], name)
        Mapping.objects.filter(id=self.my_codes_to_ciel.id).update(retired=True)
        self.assertEqual(self.search('ConceptMap', **{'target-system': CIEL})['total'], 0)

    def test_owners_whose_names_differ_only_in_case_are_told_apart(self):
        """
        The index keeps a mapping's owner and repository names in lowercase, so twins share its versions; the database
        decides each twin's version by its own mappings.
        """
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
        Source.objects.filter(mnemonic='Maps', organization__mnemonic='ACME').update(public_access=ACCESS_TYPE_NONE)
        self.assertEqual(self.keys(self.search('ConceptMap', **{'target-system': CIEL})),
                         sorted([f'{MY_CODES}|v1', 'https://example.org/Acme/maps|v1']))

    # cost: a bound on the index's answer and on its time

    def test_a_repository_made_public_is_found_before_its_documents_say_so(self):
        """
        Visibility is the database's: a ConceptMap just made public is found while its mappings' documents still say
        they aren't public (index_source_public_access still running), as WP3 found it.
        """
        name = f'opened-{uuid.uuid4().hex[:8]}'
        head = source(self.misc, name, canonical_url=f'https://example.org/{name}', public_access=ACCESS_TYPE_NONE)
        release = source(self.misc, name, 'v1', released=True, canonical_url=head.canonical_url,
                         public_access=ACCESS_TYPE_NONE)
        self.mapping(head, ConceptFactory(parent=head), self.ciel_code, [release])  # documented as not public
        key = f'{head.canonical_url}|v1'
        self.assertNotIn(key, self.keys(self.search('ConceptMap', **{'target-system': CIEL})))
        Source.objects.filter(mnemonic=name).update(public_access='View')
        for parameter in ('target-system', 'target-uri'):
            self.assertIn(key, self.keys(self.search('ConceptMap', **{parameter: CIEL})), parameter)

    def test_a_public_repository_whose_mappings_say_private_is_found(self):
        """A public ConceptMap's mappings whose own public_access says private are its mappings all the same."""
        name = f'marked-{uuid.uuid4().hex[:8]}'
        head = source(self.misc, name, canonical_url=f'https://example.org/{name}')
        release = source(self.misc, name, 'v1', released=True, canonical_url=head.canonical_url)
        self.mapping(head, ConceptFactory(parent=head), self.ciel_code, [release], public_access=ACCESS_TYPE_NONE)
        for parameter in ('target-system', 'target-uri'):
            self.assertIn(f'{head.canonical_url}|v1', self.keys(self.search('ConceptMap', **{parameter: CIEL})),
                          parameter)

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
        with index_requests() as spy, override_settings(FHIR_SEARCH_TIMEOUT_MS=0):
            response = self.client.get('/fhir/ConceptMap/', {'source-system': CIEL})
        self.assertOutcome(response, 422, 'too-costly', 'too-costly')
        self.assertEqual(spy.call_args_list, [])  # with no time left, it isn't sent

    def test_an_index_answer_that_is_not_whole_is_an_outcome(self):
        """
        A search the index answered only in part, or not at all, is never taken as complete: past its time it's too
        costly; a failed shard, a missing index or any other error is the outcome of an error that isn't the
        request's (a 422 `exception`), never a 5xx or a short Bundle.
        """
        shards = {'total': 2, 'successful': 2, 'skipped': 0, 'failed': 0}
        empty = {'aggregations': {}, 'hits': {'hits': []}}
        for answer, code, tx_issue_type in (
                ({**empty, 'timed_out': True, '_shards': shards}, 'too-costly', 'too-costly'),
                ({**empty, 'timed_out': False, '_shards': {**shards, 'successful': 1, 'failed': 1}}, 'exception',
                 'invalid-data'),
                ({**empty, 'timed_out': False}, 'exception', 'invalid-data'),
                (es_error(NotFoundError, 404), 'exception', 'invalid-data'),
                (es_error(ApiError, 500), 'exception', 'invalid-data'),
                (ESConnectionError('refused'), 'exception', 'invalid-data'),
                (ConnectionTimeout('timed out'), 'too-costly', 'too-costly')):
            effect = {'side_effect': answer} if isinstance(answer, Exception) else {'return_value': answer}
            with patch.object(Elasticsearch, 'search', autospec=True, **effect):
                response = self.client.get('/fhir/ConceptMap/', {'target-system': ICD10})
            self.assertOutcome(response, 422, code, tx_issue_type)

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

    def test_concept_maps_the_caller_is_not_shown_count_toward_no_limit(self):
        """
        A private ConceptMap with more versions than the aggregation lists, mapping into a public system, changes no
        public answer, at /fhir/ or its owner's base: the same bytes, statements and index requests as without it.
        The index reads only the documents of the ConceptMaps the base shows the caller.
        """
        name = f'target-{uuid.uuid4().hex[:8]}'  # a system no other test's documents map to
        target = source(self.misc, name, canonical_url=f'https://example.org/{name}')
        code = ConceptFactory(parent=target)
        shown = source(self.misc, f'{name}-map', canonical_url=f'https://example.org/{name}-map')
        self.mapping(shown, ConceptFactory(parent=shown), code,
                     [source(self.misc, f'{name}-map', 'v1', released=True, canonical_url=shown.canonical_url)])
        hidden = source(self.moh, f'{name}-hidden', public_access=ACCESS_TYPE_NONE)
        for version in ('v1', 'v2', 'v3'):
            self.mapping(hidden, ConceptFactory(parent=hidden), code, [
                source(self.moh, f'{name}-hidden', version, released=True, public_access=ACCESS_TYPE_NONE)])
        callers = [('anonymous', None), ('outsider', self.outsider)]
        requests = [(base, {parameter: target.canonical_url}, callers) for base in ('/fhir', '/orgs/MOH')
                    for parameter in ('target-system', 'target-uri')]
        with patch('core.fhir.mapping_index.MAX_BUCKETS', 2):  # the public map's HEAD and v1 fit; the hidden one's not
            with_it = self.answers(requests)
            self.assertEqual(with_it[('/fhir', json.dumps({'target-system': target.canonical_url}), 'anonymous')][
                1].count(b'"fullUrl"'), 1)
            Source.objects.filter(mnemonic=f'{name}-hidden').delete()
            self.assertEqual(with_it, self.answers(requests))

    def test_versions_the_caller_is_not_shown_count_toward_no_limit(self):
        """
        Private versions of a public ConceptMap holding its mappings into a public system change no public answer: the
        same bytes, statements and index requests as without them. The aggregation lists only versions the base
        shows the caller.
        """
        name = f'target-{uuid.uuid4().hex[:8]}'  # a system no other test's documents map to
        target = source(self.misc, name, canonical_url=f'https://example.org/{name}')
        maps = source(self.misc, f'{name}-map', canonical_url=f'https://example.org/{name}-map')
        versions = [source(self.misc, f'{name}-map', 'v1', released=True, canonical_url=maps.canonical_url)] + [
            source(self.misc, f'{name}-map', f'private-{number}', released=True, public_access=ACCESS_TYPE_NONE)
            for number in range(3)]
        self.mapping(maps, ConceptFactory(parent=maps), ConceptFactory(parent=target), versions)
        callers = [('anonymous', None), ('outsider', self.outsider)]
        requests = [(base, {parameter: target.canonical_url}, callers) for base in ('/fhir', '/orgs/misc')
                    for parameter in ('target-system', 'target-uri')]
        with patch('core.fhir.mapping_index.MAX_BUCKETS', 2):  # HEAD and v1 fit; the private versions don't
            with_them = self.answers(requests)
            self.assertEqual(with_them[('/fhir', json.dumps({'target-system': target.canonical_url}), 'anonymous')][
                1].count(b'"fullUrl"'), 1)
            Source.objects.filter(mnemonic=f'{name}-map', version__startswith='private-').delete()
            self.assertEqual(with_them, self.answers(requests))

    def held_by_a_hidden_version(self, version, lagging, **kwargs):
        """
        A public ConceptMap (HEAD and a public v1) and the system it maps into by one older mapping that only its
        version `version` holds (HEAD no longer does): (the system, that version, the mapping). With `lagging`,
        v1's own mapping to the system has no documents yet.
        """
        name = f'target-{uuid.uuid4().hex[:8]}'  # a system no other test's documents map to
        target = source(self.misc, name, canonical_url=f'https://example.org/{name}')
        to_code = ConceptFactory(parent=target)
        maps = source(self.misc, f'{name}-map', canonical_url=f'https://example.org/{name}-map')
        release = source(self.misc, maps.mnemonic, 'v1', released=True, canonical_url=maps.canonical_url)
        code = ConceptFactory(parent=maps)
        if lagging:
            own = self.mapping(maps, code, to_code, [release])
            MappingDocument().update(Mapping.objects.filter(versioned_object_id=own.versioned_object_id),
                                     action='delete')
        hidden = source(self.misc, maps.mnemonic, version, canonical_url=maps.canonical_url, **kwargs)
        held = self.mapping(maps, code, to_code, [hidden])
        clones = Mapping.objects.filter(versioned_object_id=held.versioned_object_id).exclude(id=held.id)
        MappingDocument().update(clones, action='delete')
        clones.delete()
        held.sources.remove(maps)
        MappingDocument().update(held, refresh=True)
        return target, hidden, held

    def test_a_version_the_caller_is_not_shown_holding_a_mapping_leaves_no_trace(self):
        """
        A public ConceptMap whose only mapping into a system is held by a version the base doesn't show (a private
        release, or an unreleased version) answers as without that version: the same bytes, statements and index
        requests, at /fhir/ and the owner's base, also while v1's own mapping isn't indexed yet.
        """
        callers = [('anonymous', None), ('outsider', self.outsider)]
        for version, kwargs in (('v2', {'released': True, 'public_access': ACCESS_TYPE_NONE}), ('v1.0', {})):
            for lagging in (False, True):
                with self.subTest(version=version, lagging=lagging):
                    target, hidden, held = self.held_by_a_hidden_version(version, lagging, **kwargs)
                    requests = [(base, {parameter: target.canonical_url}, callers) for base in ('/fhir', '/orgs/misc')
                                for parameter in ('target-system', 'target-uri')]
                    with_it = self.answers(requests)
                    MappingDocument().update(Mapping.objects.filter(id=held.id), action='delete')
                    Mapping.objects.filter(id=held.id).delete()
                    Source.objects.filter(id=hidden.id).delete()
                    self.assertEqual(with_it, self.answers(requests))


@tag('es')
class SystemSearchWithoutRegistryTest(SystemSearchCases, OCLTestCase):
    registry = False


@tag('es')
class SystemSearchWithRegistryTest(SystemSearchCases, OCLTestCase):
    registry = True
