"""
ValueSet/$expand (SVCM ITI-97, FHIR R4 $expand) at the global base /fhir/ and at owner bases, GET and POST, at the type
and instance levels, with the URL registry toggle off and on: the value set version resolved as core.fhir.resolve does;
a page of its default stored expansion, never computed or saved; contains with each member's system, version, code,
display and inactive flag; total, offset, the inputs echoed and used-codesystem; count, offset, activeOnly, filter,
displayLanguage and includeDesignations; FHIRsmith's errors; members of code systems the caller isn't shown leave no
trace; a fixed number of queries. OpenConceptLab/ocl_online#451.
"""
import json
import random
import re
import string
from urllib.parse import quote

from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from lxml import etree
from mock.mock import patch
from rest_framework.test import APIClient

from core.collections.models import Collection, Expansion
from core.collections.tests.factories import ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLTestCase, OCLAPITransactionTestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.fhir.outcomes import MESSAGE_ID_EXTENSION, TX_ISSUE_TYPE_SYSTEM
from core.fhir.tests_ops_lookup import CIEL, FEVERS, HIV, NEWEST_PRIVATE, OpsCorpusMixin, REST, name
from core.fhir.tests_search import collection, source
from core.fhir.xml import from_xml
from core.orgs.tests.factories import OrganizationFactory
from core.url_registry.factories import GlobalURLRegistryFactory
from core.url_registry.tests_resolution import set_registry_toggle

UNKNOWN_VALUE_SET = 'http://example.org/ocl-fhir-conformance/unknown-valueset'
PINNED = 'https://example.org/fhir/ValueSet/pinned'
DRAFTS = 'https://example.org/fhir/ValueSet/drafts'
DRAFT_ONLY = 'http://example.org/fhir/CodeSystem/head-only'
INFECTIOUS = {'system': CIEL, 'version': 'v2', 'code': '117053', 'display': 'Infectious enteritis'}
RETIRED = {'system': CIEL, 'version': 'v2', 'code': '103', 'display': 'HIV antibody', 'inactive': True}
BATCH = {'system': REST, 'version': '4.0.1', 'code': 'batch'}
PRIVATE = {'system': HIV, 'version': 'v1', 'code': 'P1', 'display': 'Private code'}
USED = [f'{REST}|4.0.1', f'{CIEL}|v2']
OLD_URL = 'http://example.org/fhir/CodeSystem/renamed-old'
NEW_URL = 'http://example.org/fhir/CodeSystem/renamed'


def used(*systems):
    return [{'name': 'used-codesystem', 'valueUri': system} for system in systems]


def issues_of(response):
    """An OperationOutcome's issues as severity|code|tx-issue-type|expression|message id."""
    return [
        '|'.join([item['severity'], item['code'], item['details']['coding'][0]['code'],
                  ','.join(item.get('expression', [])),
                  ','.join(e['valueString'] for e in item.get('extension', []) if e['url'] == MESSAGE_ID_EXTENSION)])
        for item in response.data['issue']]


def name_matches(queries):
    """The statements that match a filter against concept names."""
    return [query['sql'] for query in queries if re.search(r'\."name"(::text)? ~\*', query['sql'])]


class ExpandCases(OpsCorpusMixin):
    def expand(self, base='/fhir', user=None, **params):
        return self.get('$expand', base=base, user=user, resource='ValueSet', **params)

    def post_expand(self, parameters, base='/fhir', user=None):
        return self.post('$expand', parameters, base=base, user=user, resource='ValueSet')

    def expansion(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['resourceType'], 'ValueSet')
        return response.data['expansion']

    def register(self, url, owner):
        if self.registry:
            GlobalURLRegistryFactory(url=url, namespace=owner.uri)

    # the route, the envelope and the members

    def test_a_value_set_version_named_with_a_path_takes_its_repository_canonical(self):
        draft = collection(self.acme, 'fevers', 'v2')
        expansion = ExpansionFactory(mnemonic='autoexpand-v2', collection_version=draft)
        draft.expansion_uri = expansion.uri
        draft.save()

        for params in ({'url': '/orgs/Acme/ValueSet/fevers/', 'valueSetVersion': 'v2'},
                       {'url': '/orgs/Acme/ValueSet/fevers/v2/'}):
            response = self.expand(**params)

            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual((response.data['url'], response.data['version']), (FEVERS, 'v2'))

    def test_a_type_level_get_answers_the_value_set_with_its_stored_expansion(self):
        response = self.expand(url=FEVERS)

        expansion = self.expansion(response)
        self.assertEqual((response.data['url'], response.data['version'], response.data['id']),
                         (FEVERS, 'v1', str(self.fevers_v1.id)))
        self.assertEqual(response.data['status'], 'active')
        self.assertNotIn('compose', response.data)
        self.assertEqual(expansion['identifier'], self.fevers_v1.expansion_uri)
        self.assertIn('timestamp', expansion)
        self.assertEqual((expansion['total'], expansion['offset']), (3, 0))
        self.assertEqual(expansion['parameter'], used(*USED))
        self.assertEqual(expansion['contains'], [INFECTIOUS, RETIRED, BATCH])
        self.assertEqual(self.expand(url=FEVERS, valueSetVersion='v1').content, response.content)
        self.assertEqual(self.expand(url=f'{FEVERS}|v1').content, response.content)
        self.assertEqual(self.client_for().get('/fhir/ValueSet/$expand/', {'url': FEVERS}).content, response.content)

    def test_a_post_answers_as_a_get_does(self):
        response = self.expand(url=FEVERS, count='2', activeOnly='true')
        for parameters in (
                [{'name': 'url', 'valueUri': FEVERS}, {'name': 'count', 'valueInteger': 2},
                 {'name': 'activeOnly', 'valueBoolean': True}],
                [{'name': 'url', 'valueString': FEVERS}, {'name': 'count', 'valueString': '2'},
                 {'name': 'activeOnly', 'valueString': 'true'}]):
            posted = self.post_expand(parameters)
            self.assertEqual((posted.status_code, posted.content), (response.status_code, response.content))

    def test_an_owner_base_shows_the_callers_private_members(self):
        expansion = self.expansion(self.expand(base='/orgs/Acme', user=self.moh_member, url=FEVERS))
        self.assertEqual(expansion['total'], 4)
        self.assertEqual(expansion['contains'], [INFECTIOUS, RETIRED, BATCH, PRIVATE])
        self.assertEqual(expansion['parameter'], used(f'{REST}|4.0.1', f'{HIV}|v1', f'{CIEL}|v2'))
        for base, user in (('/fhir', self.moh_member), ('/orgs/Acme', None), ('/orgs/Acme', self.outsider)):
            self.assertEqual(self.expansion(self.expand(base=base, user=user, url=FEVERS))['total'], 3, (base, user))

    # paging, activeOnly, filter and the echo

    def test_count_and_offset_page_the_members(self):
        first = self.expansion(self.expand(url=FEVERS, count='2', offset='0'))
        second = self.expansion(self.expand(url=FEVERS, count='2', offset='2'))
        self.assertEqual((first['total'], first['offset'], first['contains']), (3, 0, [INFECTIOUS, RETIRED]))
        self.assertEqual((second['total'], second['offset'], second['contains']), (3, 2, [BATCH]))
        self.assertEqual(first['parameter'], [
            {'name': 'offset', 'valueInteger': 0}, {'name': 'count', 'valueInteger': 2}, *used(*USED)])
        past = self.expansion(self.expand(url=FEVERS, offset='10'))
        self.assertEqual((past['total'], past['offset'], 'contains' in past), (3, 10, False))

    def test_count_0_answers_the_total_alone(self):
        expansion = self.expansion(self.expand(url=FEVERS, count='0'))
        self.assertEqual(expansion['total'], 3)
        self.assertNotIn('contains', expansion)
        self.assertEqual(expansion['parameter'], [{'name': 'count', 'valueInteger': 0}, *used(*USED)])

    def test_count_is_at_most_1000(self):
        with CaptureQueriesContext(connection) as context:
            expansion = self.expansion(self.expand(url=FEVERS, count='5000'))
        self.assertEqual(expansion['parameter'][0], {'name': 'count', 'valueInteger': 1000})
        self.assertEqual(len(expansion['contains']), 3)
        pages = [query['sql'] for query in context.captured_queries if re.search(r'\bLIMIT 1000\b', query['sql'])]
        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].startswith('SELECT') and 'FROM "concepts"' in pages[0])
        with CaptureQueriesContext(connection) as context:
            self.expand(url=FEVERS)  # without count: a page of 1000 too, not echoed
        self.assertEqual(len([query for query in context.captured_queries if 'LIMIT 1000' in query['sql']]), 1)

    def test_active_only_leaves_retired_members_out(self):
        expansion = self.expansion(self.expand(url=FEVERS, activeOnly='true'))
        self.assertEqual((expansion['total'], expansion['contains']), (2, [INFECTIOUS, BATCH]))
        self.assertEqual(expansion['parameter'][0], {'name': 'activeOnly', 'valueBoolean': True})
        self.assertEqual(expansion['parameter'][1:], used(*USED))  # used-codesystem: the whole value set's
        self.assertEqual(self.expansion(self.expand(url=FEVERS, activeOnly='false'))['total'], 3)

    def test_a_filter_keeps_members_with_a_word_starting_with_each_of_its_words(self):
        for text, members in (('infect', [INFECTIOUS]), ('INFECT', [INFECTIOUS]), ('entérite', [INFECTIOUS]),
                              ('origin enteritis', [INFECTIOUS]), ('bat', [BATCH]), ('10', [RETIRED]),
                              ('hiv', [RETIRED]), ('fect', []), ('antibody enteritis', []), ('', [INFECTIOUS, RETIRED,
                                                                                                   BATCH])):
            expansion = self.expansion(self.expand(url=FEVERS, filter=text))
            self.assertEqual((expansion['total'], expansion.get('contains', [])), (len(members), members), text)
        expansion = self.expansion(self.expand(url=FEVERS, filter='infect', activeOnly='true', count='1'))
        self.assertEqual(expansion['parameter'], [
            {'name': 'filter', 'valueString': 'infect'}, {'name': 'activeOnly', 'valueBoolean': True},
            {'name': 'count', 'valueInteger': 1}, *used(*USED)])

    def test_a_filter_matches_a_word_in_a_name_longer_than_a_btree_entry(self):
        """A name too long for a btree entry, even compressed, is saved, and a filter still matches its words."""
        noise = random.Random(564)
        text = ' '.join(''.join(noise.choices(string.ascii_letters + string.digits, k=40)) for _ in range(100))
        self.fevers_v1.expansion.concepts.add(ConceptFactory(
            parent=self.ciel, mnemonic='long', sources=[self.ciel_v1, self.ciel_v2],
            names=[name(f'{text[:2000]} Zebrafish {text[2000:]}', preferred=True)]))
        expansion = self.expansion(self.expand(url=FEVERS, filter='zebrafish'))
        self.assertEqual((expansion['total'], [member['code'] for member in expansion['contains']]), (1, ['long']))

    def test_display_language_and_designations(self):
        expansion = self.expansion(self.expand(url=FEVERS, displayLanguage='fr', includeDesignations='true', count='1'))
        member = expansion['contains'][0]
        self.assertEqual(member['display'], 'Entérite infectieuse')
        self.assertEqual(sorted((item['language'], item['value']) for item in member['designation']), [
            ('en', 'Enteritis of infectious origin'), ('en', 'Infectious enteritis'), ('es', 'enteritis infecciosa'),
            ('fr', 'Entérite infectieuse')])
        self.assertEqual(expansion['parameter'][:2], [
            {'name': 'displayLanguage', 'valueCode': 'fr'}, {'name': 'includeDesignations', 'valueBoolean': True}])
        self.assertNotIn('designation', self.expansion(self.expand(url=FEVERS))['contains'][0])

    # versions: only releases the caller is shown

    def test_a_members_version_is_the_release_the_expansion_pins_then_evaluated_then_newest(self):
        pinned = collection(self.acme, 'pinned', 'v1', released=True, canonical_url=PINNED)
        collection(self.acme, 'pinned', canonical_url=PINNED)
        expansion = ExpansionFactory(mnemonic='autoexpand-v1', collection_version=pinned)
        expansion.concepts.add(self.infectious, self.older)  # 117053 in CIEL v1 and v2, 999 in v1 only
        expansion.explicit_source_versions.add(self.ciel_v1)
        expansion.evaluated_source_versions.add(self.ciel_v2)
        pinned.expansion_uri = expansion.uri
        pinned.save()
        self.register(PINNED, self.acme)

        answer = self.expansion(self.expand(url=PINNED))
        self.assertEqual([(member['code'], member['version']) for member in answer['contains']],
                         [('117053', 'v1'), ('999', 'v1')])
        self.assertEqual(answer['parameter'], used(f'{CIEL}|v1'))
        expansion.explicit_source_versions.clear()
        answer = self.expansion(self.expand(url=PINNED))
        self.assertEqual([(member['code'], member['version']) for member in answer['contains']],
                         [('117053', 'v2'), ('999', 'v1')])
        self.assertEqual(answer['parameter'], used(f'{CIEL}|v1', f'{CIEL}|v2'))
        expansion.evaluated_source_versions.clear()
        self.assertEqual([member['version'] for member in self.expansion(self.expand(url=PINNED))['contains']],
                         ['v2', 'v1'])  # the newest release holding it

    def test_a_version_is_only_a_release_the_caller_is_shown_never_head(self):
        drafts = OrganizationFactory(mnemonic='Drafts')
        head_only = source(drafts, 'head-only', canonical_url=DRAFT_ONLY)
        value_set = collection(self.acme, 'drafts', 'v1', released=True, canonical_url=DRAFTS)
        collection(self.acme, 'drafts', canonical_url=DRAFTS)
        expansion = ExpansionFactory(mnemonic='autoexpand-v1', collection_version=value_set)
        newest = Concept.objects.filter(parent=self.newest_private, mnemonic='N1').first()  # in public v1, private v2
        expansion.concepts.add(
            ConceptFactory(parent=head_only, mnemonic='D1', names=[name('D one', preferred=True)]), newest)
        expansion.explicit_source_versions.add(head_only, self.newest_private_v2)
        value_set.expansion_uri = expansion.uri
        value_set.save()
        self.register(DRAFTS, self.acme)

        answer = self.expansion(self.expand(url=DRAFTS))
        self.assertEqual(answer['contains'], [  # N1 is the older concept
            {'system': NEWEST_PRIVATE, 'version': 'v1', 'code': 'N1', 'display': 'N one'},
            {'system': DRAFT_ONLY, 'code': 'D1', 'display': 'D one'}])
        self.assertEqual(answer['parameter'], used(DRAFT_ONLY, f'{NEWEST_PRIVATE}|v1'))
        staff = self.expansion(self.expand(base='/orgs/Acme', user=self.admin, url=DRAFTS))
        self.assertIn(f'{NEWEST_PRIVATE}|v2', [item['valueUri'] for item in staff['parameter']])

    def test_head_is_expanded_only_when_named(self):
        """HEAD's own stored expansion answers when valueSetVersion names HEAD; the default stays the release."""
        head_expansion = ExpansionFactory(mnemonic='autoexpand-HEAD', collection_version=self.fevers)
        head_expansion.concepts.add(self.infectious)
        self.fevers.expansion_uri = head_expansion.uri
        self.fevers.save()
        response = self.expand(url=FEVERS, valueSetVersion='HEAD')
        self.assertEqual((response.data['version'], self.expansion(response)['contains']), ('HEAD', [INFECTIOUS]))
        self.assertEqual(self.expansion(self.expand(url=FEVERS))['total'], 3)

    def test_a_member_of_a_code_system_without_a_canonical_url_is_named_by_its_path(self):
        local = source(self.acme, 'local', canonical_url=None)
        local_v1 = source(self.acme, 'local', 'v1', released=True, canonical_url=None)
        self.fevers_v1.expansion.concepts.add(ConceptFactory(
            parent=local, mnemonic='L1', sources=[local_v1], names=[name('Local one', preferred=True)]))
        answer = self.expansion(self.expand(url=FEVERS, filter='local'))
        self.assertEqual(answer['contains'], [
            {'system': '/orgs/Acme/CodeSystem/local/', 'version': 'v1', 'code': 'L1', 'display': 'Local one'}])
        self.assertIn('/orgs/Acme/CodeSystem/local/|v1', [item.get('valueUri') for item in answer['parameter']])

    def test_deleted_concepts_and_releases_are_gone(self):
        deleted = ConceptFactory(parent=self.ciel, mnemonic='555', sources=[self.ciel_v2], is_active=False,
                                 names=[name('Deleted', preferred=True)])
        self.fevers_v1.expansion.concepts.add(deleted)
        self.assertEqual(self.expansion(self.expand(url=FEVERS))['total'], 3)
        self.rest_v1.is_active = False
        self.rest_v1.save()
        answer = self.expansion(self.expand(url=FEVERS))
        self.assertEqual(answer['contains'][2], {'system': REST, 'code': 'batch'})
        self.assertEqual(answer['parameter'], used(REST, f'{CIEL}|v2'))

    def test_a_filter_matches_the_words_of_a_code_as_fhir_spells_it(self):
        """filter matches a stored code by its decoded words, ignoring case, non-ASCII letters too: never by the
        characters of an escape, and a word starts after an encoded character only when that character, decoded, isn't
        a word character (Codex F1.2, F2.1)."""
        codes = {'ND53.Y%26XK70': 'Encoded', quote('café'): 'Coffee shop', quote('caféine'): 'Stimulant',
                 quote('été'): 'Summer', quote('QX–ZZ9'): 'Range', '50%off': 'Discount', '%FFyy': 'Replacement',
                 quote('cm.²'): 'Area'}
        for code, display in codes.items():
            self.fevers_v1.expansion.concepts.add(ConceptFactory(
                parent=self.ciel, mnemonic=code, sources=[self.ciel_v2], names=[name(display, preferred=True)]))
        for text, decoded in (
                ('26', []), ('c3', []), ('A9', []), ('80', []),  # the characters of an escape
                ('CAFÉ', ['café', 'caféine']), ('Café', ['café', 'caféine']), ('ÉTÉ', ['été']),  # case, non-ASCII too
                ('CAFÉINE', ['caféine']), ('ine', []), ('té', []),  # é is a word character: no word starts after it
                ('zz9', ['QX–ZZ9']), ('XK70', ['ND53.Y&XK70']),  # – and & are not
                ('ND53.Y&XK70', ['ND53.Y&XK70']), ('xk', ['ND53.Y&XK70']), ('Y&XK', ['ND53.Y&XK70']),  # decoded, whole
                ('encoded', ['ND53.Y&XK70']), ('K70', []),  # by a name; a word's middle
                ('CAF', ['café', 'caféine']),
                ('off', ['50%off']), ('yy', ['\ufffdyy']),  # a % that is no escape; a byte that is no character
                ('²', []), ('cm', ['cm.²'])):  # \m, as for names: ² is no word character to Postgres
            with self.subTest(text):
                expansion = self.expansion(self.expand(url=FEVERS, filter=text))
                self.assertEqual([member['code'] for member in expansion.get('contains', [])], decoded)
                self.assertEqual(expansion['total'], len(decoded))

    def test_a_members_system_is_its_releases_own_canonical_url(self):
        """A release with a canonical URL of its own names its members' system, else its repository's does, in contains
        and in used-codesystem (Codex F1.3; search and read follow the same rule)."""
        renamed = source(self.acme, 'renamed', canonical_url=NEW_URL)
        old = source(self.acme, 'renamed', 'v1', released=True, canonical_url=OLD_URL)
        plain = source(self.acme, 'renamed', 'v2', released=True, canonical_url=None)
        for code, sources in (('R1', [old]), ('R2', [plain]), ('R3', [])):
            self.fevers_v1.expansion.concepts.add(ConceptFactory(
                parent=renamed, mnemonic=code, sources=sources, names=[name(f'Renamed {code}', preferred=True)]))
        answer = self.expansion(self.expand(url=FEVERS, filter='renamed'))
        self.assertEqual([(member['system'], member.get('version'), member['code']) for member in answer['contains']], [
            (OLD_URL, 'v1', 'R1'), (NEW_URL, 'v2', 'R2'), (NEW_URL, None, 'R3')])
        self.assertEqual([item['valueUri'] for item in answer['parameter'] if item['name'] == 'used-codesystem'],
                         sorted([*USED, f'{OLD_URL}|v1', f'{NEW_URL}|v2', NEW_URL]))

    # what the caller isn't shown leaves no trace

    def sent(self, requests):
        """{(method, path, params, caller): (status, bytes, statements run, ids and numbers normalised)}, for each of
        requests' (method, path, params, callers)."""
        answers = {}
        for method, path, params, callers in requests:
            for who, user in callers:
                with CaptureQueriesContext(connection) as context:
                    if method == 'GET':
                        response = self.client_for(user).get(path, params)
                    else:
                        response = self.client_for(user).post(
                            path, json.dumps({'resourceType': 'Parameters', 'parameter': params}),
                            content_type='application/fhir+json')
                answers[(method, path, json.dumps(params), who)] = (
                    response.status_code, response.content,
                    [re.sub(r'\b\d+\b', 'N', query['sql']) for query in context.captured_queries])
        return answers

    def assert_same_when_missing(self, hidden, missing, bytes_only=lambda key: False):
        """The two worlds' answers are alike in status and bytes, and in the statements run, except for the requests
        bytes_only(key) names (statements that resolve() itself runs differently, outside this operation)."""
        self.assertEqual(hidden.keys(), missing.keys())
        for key, (status, content, statements) in hidden.items():
            self.assertEqual((status, content), missing[key][:2], key)
            if not bytes_only(key):
                self.assertEqual(statements, missing[key][2], key)

    def test_a_member_the_caller_is_not_shown_leaves_no_trace(self):
        """An expansion with a hidden member answers byte for byte, and with the same statements, as one without it:
        in contains, total, used-codesystem and the versions reported, for any input that shapes the answer."""
        late = ConceptFactory(parent=self.ciel, mnemonic='Z9', sources=[self.ciel_v2], names=[
            name('Zed nine code', preferred=True), name('Zède', 'fr', True)])
        expansion = self.fevers_v1.expansion
        expansion.concepts.add(late)  # after the hidden member in id order: a page can run across it
        expansion.explicit_source_versions.add(self.hiv_v1)  # its release, pinned
        expansion.evaluated_source_versions.add(self.hiv_v1)
        callers = [('anonymous', None), ('outsider', self.outsider), ('moh-member', self.moh_member),
                   ('staff', self.admin)]
        inputs = ({}, {'count': '1', 'offset': '1'}, {'count': '1', 'offset': '2'}, {'count': '2', 'offset': '2'},
                  {'offset': '4'}, {'offset': '10'}, {'activeOnly': 'true'}, {'filter': 'private'}, {'filter': 'p1'},
                  {'filter': 'private', 'count': '0'}, {'count': '0'},
                  {'displayLanguage': 'fr', 'includeDesignations': 'true'}, {'includeDesignations': 'true'})
        requests = [('GET', '/fhir/ValueSet/$expand', {'url': FEVERS, **params}, callers) for params in inputs] + [
            ('GET', '/orgs/Acme/ValueSet/$expand', {'url': FEVERS, **params}, callers[:2]) for params in inputs] + [
            ('POST', '/fhir/ValueSet/$expand', [{'name': 'url', 'valueUri': FEVERS}, {'name': 'count',
                                                                                    'valueInteger': 1}], callers),
            ('GET', f'/fhir/ValueSet/{self.fevers_v1.id}/$expand', {'offset': '2'}, callers),
            ('GET', '/orgs/Acme/ValueSet/fevers/$expand', {'filter': 'p'}, callers[:2])]
        hidden = self.sent(requests)
        self.assertEqual(hidden[('GET', '/fhir/ValueSet/$expand', json.dumps({'url': FEVERS}), 'moh-member')][0], 200)
        expansion.concepts.remove(self.private_code)
        expansion.explicit_source_versions.remove(self.hiv_v1)
        expansion.evaluated_source_versions.remove(self.hiv_v1)
        self.assert_same_when_missing(hidden, self.sent(requests))

    def test_a_value_set_the_caller_is_not_shown_answers_as_a_missing_one(self):
        url = 'https://example.org/hidden'
        private = collection(self.moh, 'hidden', 'v1', released=True, canonical_url=url, public_access=ACCESS_TYPE_NONE)
        collection(self.moh, 'hidden', canonical_url=url, public_access=ACCESS_TYPE_NONE)
        private.expansion_uri = ExpansionFactory(mnemonic='autoexpand-v1', collection_version=private).uri
        private.save()
        self.register(url, self.moh)
        self.assertEqual(self.expand(base='/orgs/MOH', user=self.moh_member, url=url).status_code, 200)
        everyone = [('anonymous', None), ('outsider', self.outsider), ('moh-member', self.moh_member),
                    ('staff', self.admin)]
        outside = everyone[:2]
        by_path = {'url': '/orgs/MOH/ValueSet/hidden/'}
        path_v1 = {'url': '/orgs/MOH/ValueSet/hidden/v1/'}  # resolve() reads the version's HEAD first, hidden or not
        requests = [(method, f'{base}/ValueSet/$expand', params, callers) for base, callers in (
            ('/fhir', everyone), ('/orgs/MOH', outside)) for method, params in (
            ('GET', {'url': url}), ('GET', {'url': f'{url}|v1'}), ('GET', {'url': url, 'valueSetVersion': 'v1'}),
            ('GET', {'url': url, 'valueSetVersion': 'HEAD'}), ('GET', {'url': url, 'valueSetVersion': 'v9'}),
            ('GET', by_path), ('GET', path_v1), ('GET', {'url': url, 'filter': 'inf', 'count': '0'}),
            ('POST', [{'name': 'url', 'valueUri': url}]),
            ('POST', [{'name': 'url', 'valueString': url}, {'name': 'valueSetVersion', 'valueString': 'v1'}]))]
        requests += [('GET', f'/fhir/ValueSet/{private.id}/$expand', params, everyone) for params in (
            {}, {'valueSetVersion': 'HEAD'})] + [
            ('POST', f'/fhir/ValueSet/{private.id}/$expand', [], everyone),
            ('GET', '/orgs/MOH/ValueSet/hidden/$expand', {}, outside),
            ('GET', '/orgs/MOH/ValueSet/hidden/$expand', {'valueSetVersion': 'HEAD'}, outside)]
        hidden = self.sent(requests)
        Collection.objects.filter(mnemonic='hidden').delete()
        missing = self.sent(requests)
        self.assertEqual(issues_of(self.expand(url=url)), ['error|not-found|not-found||Unable_to_resolve_value_Set_'])
        for key, (status, _, _) in hidden.items():
            self.assertIn(status, (404, 422), key)
        # the same statements everywhere but where resolve() itself differs: for a caller who is signed in, at an owner
        # base (a membership check, only when a private row is found), with the registry on at an owner base (its
        # lookup is skipped for a private canonical), and for an OCL path naming a version (its HEAD is read first)
        self.assert_same_when_missing(
            hidden, missing, lambda key: key[1].startswith('/orgs/MOH') or key[2] == json.dumps(path_v1))

    def test_a_private_version_of_a_value_set_answers_as_a_missing_one(self):
        """The newest release is private: named, or by its id, it answers as a version that doesn't exist."""
        v2 = collection(self.acme, 'fevers', 'v2', released=True, canonical_url=FEVERS, public_access=ACCESS_TYPE_NONE)
        v2.expansion_uri = ExpansionFactory(mnemonic='autoexpand-v2', collection_version=v2).uri
        v2.save()
        everyone = [('anonymous', None), ('outsider', self.outsider), ('moh-member', self.moh_member),
                    ('staff', self.admin)]
        requests = [('GET', '/fhir/ValueSet/$expand', params, everyone) for params in (
            {'url': FEVERS, 'valueSetVersion': 'v2'}, {'url': f'{FEVERS}|v2'},
            {'url': '/orgs/Acme/ValueSet/fevers/v2/'})] + [
            ('POST', '/fhir/ValueSet/$expand', [{'name': 'url', 'valueUri': FEVERS},
                                                {'name': 'valueSetVersion', 'valueString': 'v2'}], everyone),
            ('GET', f'/fhir/ValueSet/{v2.id}/$expand', {}, everyone),
            ('GET', '/orgs/Acme/ValueSet/$expand', {'url': FEVERS, 'valueSetVersion': 'v2'}, everyone[:3])]
        hidden = self.sent(requests)
        Collection.objects.filter(id=v2.id).delete()
        # an OCL path naming a version reads its HEAD first, and an owner base checks a signed-in caller's membership
        # once a private row is found: statements resolve() itself runs differently, not this operation
        self.assert_same_when_missing(hidden, self.sent(requests), lambda key: key[1].startswith('/orgs/Acme') or
                                      '/fevers/v2/' in key[2])

    # errors

    def test_errors(self):
        response = self.expand(url=UNKNOWN_VALUE_SET)
        self.assert_outcome(response, 422, 'not-found', 'not-found')
        self.assertEqual(self.expand(url=FEVERS, valueSetVersion='ocl-fhir-conformance-no-such-version').status_code,
                         422)
        for params, expression in (({}, 'url'), ({'count': '-1'}, 'count'), ({'count': 'all'}, 'count'),
                                   ({'offset': '1.5'}, 'offset'), ({'activeOnly': 'maybe'}, 'activeOnly'),
                                   ({'includeDesignations': '1'}, 'includeDesignations'),
                                   # no padding, as no FHIR integer or boolean has any
                                   ({'count': ' 5'}, 'count'), ({'activeOnly': 'true '}, 'activeOnly')):
            response = self.expand(**({'url': FEVERS} if expression != 'url' else {}), **params)
            self.assert_outcome(response, 400, 'invalid', 'invalid-data')
            self.assertEqual(response.data['issue'][0]['expression'], [expression], params)
        for parameter in ({'name': 'valueSet', 'resource': {'resourceType': 'ValueSet', 'status': 'active'}},
                          {'name': 'context', 'valueUri': 'http://example.org/context'}):
            response = self.post_expand([{'name': 'url', 'valueUri': FEVERS}, parameter])
            self.assert_outcome(response, 400, 'not-supported', 'not-supported')
            self.assertEqual(response.data['issue'][0]['expression'], [parameter['name']])
        response = self.post_expand([{'name': 'url', 'valueUri': FEVERS}, {'name': 'count', 'valueBoolean': True}])
        self.assert_outcome(response, 400, 'invalid', 'invalid-data')

    def test_inputs_it_does_not_honour_are_refused(self):
        """Every R4 $expand input either shapes the answer, is one a stored expansion always satisfies, or is a 400
        not-supported naming it, GET and POST: none is ignored (Codex F1.1). Names that aren't $expand inputs are."""
        refused = {
            'date': ('valueDateTime', '2020-01-01'), 'exclude-system': ('valueCanonical', CIEL),
            'system-version': ('valueCanonical', f'{CIEL}|v1'),
            'check-system-version': ('valueCanonical', f'{CIEL}|v1'),
            'force-system-version': ('valueCanonical', f'{CIEL}|v1'), 'designation': ('valueString', 'fr'),
            'contextDirection': ('valueCode', 'incoming'), 'includeDefinition': ('valueBoolean', True),
            'excludePostCoordinated': ('valueBoolean', True)}  # Codex F2.2: nothing leaves such codes out
        for name_, (value_type, value) in refused.items():
            responses = (self.expand(url=FEVERS, **{name_: 'true' if value is True else value}),
                         self.post_expand([{'name': 'url', 'valueUri': FEVERS}, {'name': name_, value_type: value}]))
            for response in responses:
                self.assert_outcome(response, 400, 'not-supported', 'not-supported')
                self.assertEqual(response.data['issue'][0]['expression'], [name_], name_)
        expected = self.expand(url=FEVERS).content
        for params in ({'excludeNested': 'true'}, {'excludeNested': 'false'}, {'excludeNotForUI': 'true'},
                       {'excludePostCoordinated': 'false'}, {'includeDefinition': 'false'}, {'q': 'anything'}):
            self.assertEqual(self.expand(url=FEVERS, **params).content, expected, params)
        for name_ in ('excludePostCoordinated', 'includeDefinition'):
            posted = self.post_expand([{'name': 'url', 'valueUri': FEVERS}, {'name': name_, 'valueBoolean': False}])
            self.assertEqual((posted.status_code, posted.content), (200, expected), name_)
        self.assert_outcome(self.expand(url=FEVERS, excludeNested='perhaps'), 400, 'invalid', 'invalid-data')

    def test_an_expansion_that_cannot_be_served_is_a_422(self):
        expansion = self.fevers_v1.expansion
        expansion.unresolved_repo_versions = [
            {'url': 'http://snomed.info/sct', 'type': 'reference.system', 'version': '', 'namespace': None}]
        expansion.save()
        response = self.expand(url=FEVERS)
        self.assertEqual(issues_of(response), ['error|not-found|not-found||UNKNOWN_CODESYSTEM_EXP'])
        self.assertEqual(response.status_code, 422)
        self.assertIn("CodeSystem 'http://snomed.info/sct' could not be found", response.data['issue'][0]['details'][
            'text'])
        expansion.unresolved_repo_versions = [{'url': 'http://example.org/vs', 'type': 'reference.valueset',
                                               'version': '2'}]
        expansion.save()
        self.assertEqual(issues_of(self.expand(url=FEVERS)),
                         ['error|not-found|not-found||VS_EXP_IMPORT_UNK_PINNED'])
        expansion.unresolved_repo_versions = []
        expansion.is_processing = True
        expansion.save()
        self.assert_outcome(self.expand(url=FEVERS), 422, 'processing', 'invalid-data')
        self.fevers_v1.expansion_uri = None
        self.fevers_v1.save()
        self.assert_outcome(self.expand(url=FEVERS), 422, 'invalid', 'vs-invalid')

    def test_a_post_never_persists(self):
        expansions = list(Expansion.objects.values_list('id', 'parameters'))
        members = self.fevers_v1.expansion.concepts.count()
        for parameters in ([{'name': 'url', 'valueUri': FEVERS}, {'name': 'filter', 'valueString': 'infect'}],
                           [{'name': 'url', 'valueUri': FEVERS}, {'name': 'activeOnly', 'valueBoolean': True}]):
            self.assertLess(self.expansion(self.post_expand(parameters))['total'], 3)
        self.assertEqual(list(Expansion.objects.values_list('id', 'parameters')), expansions)
        self.assertEqual(self.fevers_v1.expansion.concepts.count(), members)

    # the instance level, XML

    def test_the_instance_level(self):
        expected = self.expansion(self.expand(url=FEVERS))
        for path in (f'/fhir/ValueSet/{self.fevers_v1.id}/$expand', '/orgs/Acme/ValueSet/fevers/$expand',
                     '/orgs/Acme/ValueSet/fevers/$expand/'):
            response = self.client_for().get(path)
            self.assertEqual(self.expansion(response), expected, path)
        response = self.client_for().get('/orgs/Acme/ValueSet/fevers/$expand', {'valueSetVersion': 'v1'})
        self.assertEqual(self.expansion(response), expected)
        for params, expression in (({'url': CIEL}, 'url'), ({'valueSetVersion': 'v9'}, 'valueSetVersion')):
            response = self.client_for().get('/orgs/Acme/ValueSet/fevers/$expand', params)
            self.assertEqual(response.status_code, 400 if expression == 'url' else 404, params)
        self.assertEqual(self.client_for().get(f'/fhir/ValueSet/{self.fevers_v1.id}/$expand', {'url': CIEL})
                         .status_code, 400)
        self.assertEqual(self.client_for().get('/orgs/Acme/ValueSet/nothing/$expand').status_code, 404)

    def test_xml(self):
        response = self.client_for().get('/fhir/ValueSet/$expand', {'url': FEVERS, '_format': 'xml'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/fhir+xml; charset=utf-8')
        root = etree.fromstring(response.content)
        self.assertEqual(root.tag, '{http://hl7.org/fhir}ValueSet')
        value_set = from_xml(response.content)
        self.assertEqual(value_set['expansion']['total'], 3)
        self.assertEqual([member['code'] for member in value_set['expansion']['contains']], ['117053', '103', 'batch'])
        self.assertTrue(value_set['expansion']['contains'][1]['inactive'])

    # cost

    def test_a_filtered_page_matches_names_once(self):
        """A page larger than the matching members reads the name match once, for both total and contains."""
        with CaptureQueriesContext(connection) as context:
            expansion = self.expansion(self.expand(url=FEVERS, filter='infect', count='1000'))
        self.assertEqual((expansion['total'], expansion['offset'], expansion['contains']), (1, 0, [INFECTIOUS]))
        self.assertEqual(len(name_matches(context.captured_queries)), 1)

    def test_a_filtered_page_with_an_offset_matches_names_once(self):
        """The total counts every match, and the offset selects a page in id order without matching names again."""
        for number in range(30):
            self.fevers_v1.expansion.concepts.add(ConceptFactory(
                parent=self.ciel, mnemonic=f'x{number}', sources=[self.ciel_v1, self.ciel_v2],
                names=[name(f'Extra {number}', preferred=True)]))
        with CaptureQueriesContext(connection) as context:
            expansion = self.expansion(self.expand(url=FEVERS, filter='extra', count='2', offset='1'))
        self.assertEqual((expansion['total'], expansion['offset']), (30, 1))
        self.assertEqual(expansion['contains'], [
            {'system': CIEL, 'version': 'v2', 'code': 'x1', 'display': 'Extra 1'},
            {'system': CIEL, 'version': 'v2', 'code': 'x2', 'display': 'Extra 2'}])
        self.assertEqual(len(name_matches(context.captured_queries)), 1)

    def test_an_empty_filtered_page_reads_no_page_or_names(self):
        """A zero count, an offset past the matches and no matches read no page or names."""
        resolution = 5 if self.registry else 3
        for text, count, offset, total in (('infect', '0', '0', 1), ('infect', '2', '1', 1),
                                            ('missing', '1000', '0', 0)):
            with self.assertNumQueries(resolution + 3):
                expansion = self.expansion(self.expand(url=FEVERS, filter=text, count=count, offset=offset))
            self.assertEqual((expansion['total'], expansion['offset']), (total, int(offset)))
            self.assertNotIn('contains', expansion)

    def test_a_fixed_number_of_queries(self):
        """Resolution, the stored expansion, the total, used-codesystem, one page and its names: whatever the size of
        the expansion, and with count=0 no page."""
        resolution = 5 if self.registry else 3  # the toggle, the repository, its release (+ the registry's two)
        with self.assertNumQueries(resolution + 5):
            self.expand(url=FEVERS)
        for number in range(30):
            self.fevers_v1.expansion.concepts.add(ConceptFactory(
                parent=self.ciel, mnemonic=f'x{number}', sources=[self.ciel_v1, self.ciel_v2],
                names=[name(f'Extra {number}', preferred=True)]))
        self.fevers_v1.expansion.concepts.add(ConceptFactory(  # a word after an encoded character: same statements
            parent=self.ciel, mnemonic=quote('QX–ZZ9'), sources=[self.ciel_v2], names=[name('Range', preferred=True)]))
        for params in ({}, {'count': '10', 'offset': '20', 'filter': 'extra', 'activeOnly': 'true'},
                       {'displayLanguage': 'fr', 'includeDesignations': 'true'}, {'filter': 'zz9 range'}):
            with self.assertNumQueries(resolution + 5):
                self.assertEqual(self.expand(url=FEVERS, **params).status_code, 200)
        with self.assertNumQueries(resolution + 3):
            self.expand(url=FEVERS, count='0')
        with self.assertNumQueries(resolution + 4):  # an empty page: no names to read
            self.expand(url=FEVERS, offset='100')


class ExpandWithoutRegistryTest(ExpandCases, OCLTestCase):
    registry = False


class ExpandWithRegistryTest(ExpandCases, OCLTestCase):
    registry = True


class ExpandTimeoutTest(OCLAPITransactionTestCase):
    """$expand's statements are bounded as a search's are: one the database cancels is too costly (422)."""

    def test_a_cancelled_statement_is_too_costly(self):
        set_registry_toggle(False)
        org = OrganizationFactory(mnemonic='Acme')
        value_set = collection(org, 'fevers', 'v1', released=True, canonical_url=FEVERS)
        collection(org, 'fevers', canonical_url=FEVERS)
        value_set.expansion_uri = ExpansionFactory(mnemonic='autoexpand-v1', collection_version=value_set).uri
        value_set.save()

        def slow(*_):
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_sleep(1)')

        with override_settings(FHIR_SEARCH_TIMEOUT_MS=50), patch('core.fhir.ops.expand.Expander.used', slow):
            response = APIClient().get('/fhir/ValueSet/$expand', {'url': FEVERS})
        self.assertEqual(response.status_code, 422, response.data)
        issue = response.data['issue'][0]
        self.assertEqual(issue['code'], 'too-costly')
        self.assertIn({'system': TX_ISSUE_TYPE_SYSTEM, 'code': 'too-costly'}, issue['details']['coding'])
        self.assertIn('expansion', issue['details']['text'])
        self.assertEqual(json.loads(APIClient().get('/fhir/ValueSet/$expand', {'url': FEVERS}).content)['expansion'][
            'total'], 0)
