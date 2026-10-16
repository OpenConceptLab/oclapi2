"""
ConceptMap/$translate (SVCM ITI-101, FHIR R4 $translate) at the global base /fhir/ and at owner bases, GET and POST:
the ConceptMap the request names (url) or the code system the code comes from, resolved as WP2's resolver does
(released versions only, the newest the caller is shown unless one is named, public only at /fhir/); that version's
own live mappings, never another repository's or HEAD's; OCL map types as relationships (SAME-AS = SAME AS = Same As)
written as R4 equivalence; systems by canonical URL; source, targetsystem and reverse; a source the caller isn't shown
answers as an unknown one; a fixed number of queries. OpenConceptLab/ocl_online#451.
"""
import json
from itertools import product
from unittest import expectedFailure

from django.db import connection
from django.test.utils import CaptureQueriesContext
from mock import patch
from rest_framework.test import APIClient

from core.collections.models import Collection
from core.collections.tests.factories import ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLTestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.fhir.ops.translate import repository_paths
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM
from core.fhir.tests_search import source, collection
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.url_registry.factories import GlobalURLRegistryFactory
from core.url_registry.tests_resolution import set_registry_toggle
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory

CIEL = 'https://CIELterminology.org'
ICD10 = 'http://hl7.org/fhir/sid/icd-10'
PIH = 'https://pih.example/fhir/CodeSystem/pih'
PRIVATE = 'http://moh.example/fhir/CodeSystem/private'
UNKNOWN = 'http://example.org/ocl-fhir-conformance/unknown-system'
FIXTURE = 'https://openconceptlab.org/fhir-fixtures/CodeSystem/fixture-codes'
DAK = 'https://joe.aml'
CIEL_VS = 'https://example.org/ValueSet/ciel-set'
ICD10_VS = 'https://example.org/ValueSet/icd-set'
MIXED_VS = 'http://moh.example/fhir/ValueSet/mixed'
SNOMED_PATH = '/orgs/IHTSDO/ConceptMap/SNOMED-CT/'  # a source with no canonical URL is named by its OCL path
OMOP_PATH = '/orgs/OHDSI/ConceptMap/OMOP-Standard/'  # a mapping to a source OCL doesn't hold: the URL it stores
PRIVATE_PATH = '/orgs/MOH/ConceptMap/private-codes/'


class TranslateCorpusMixin:
    """
    CIEL with two releases and mappings in each, one in HEAD only, and one retired; its 113275 maps to ICD-10 (WHO),
    to a SNOMED source with no canonical, to a source OCL doesn't hold, and to MOH's private source. PIH's own
    ConceptMap also maps CIEL 113275. MOH's private source maps back to CIEL; the private fixture org has one release
    with a mapping; a user's ConceptMap (DAK) holds mappings from CIEL into itself. `registry` is the URL registry
    toggle: off by default, as in production; on, with a global entry for each public canonical, as the curated
    registry has them.
    """
    registry = False

    def setUp(self):
        set_registry_toggle(self.registry)
        super().setUp()
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.ciel = OrganizationFactory(mnemonic='CIEL')
        self.who = OrganizationFactory(mnemonic='WHO')
        self.ihtsdo = OrganizationFactory(mnemonic='IHTSDO')
        self.pih = OrganizationFactory(mnemonic='PIH')
        self.moh = OrganizationFactory(mnemonic='MOH')
        self.fixtures = OrganizationFactory(mnemonic='FHIR-Fixtures', public_access=ACCESS_TYPE_NONE)
        self.moh_member = UserProfileFactory(username='moh-member')
        self.moh.members.add(self.moh_member)
        self.member = UserProfileFactory(username='fixture-member')
        self.fixtures.members.add(self.member)
        self.outsider = UserProfileFactory(username='outsider')
        self.jamlung = UserProfileFactory(username='jamlung')

        self.ciel_source = source(self.ciel, 'CIEL', canonical_url=CIEL)
        self.ciel_v1 = source(self.ciel, 'CIEL', 'v1', released=True, canonical_url=CIEL)
        self.ciel_v2 = source(self.ciel, 'CIEL', 'v2', released=True, canonical_url=CIEL)
        self.icd = source(self.who, 'ICD-10-WHO', canonical_url=ICD10)
        self.icd_2019 = source(self.who, 'ICD-10-WHO', 'ICD102019', released=True, canonical_url=ICD10)
        self.snomed = source(self.ihtsdo, 'SNOMED-CT')
        self.private = source(self.moh, 'private-codes', canonical_url=PRIVATE, public_access=ACCESS_TYPE_NONE)
        self.private_v1 = source(
            self.moh, 'private-codes', 'v1', released=True, canonical_url=PRIVATE, public_access=ACCESS_TYPE_NONE)
        self.pih_source = source(self.pih, 'PIH', canonical_url=PIH)
        self.pih_v1 = source(self.pih, 'PIH', 'v1', released=True, canonical_url=PIH)
        self.fixture = source(self.fixtures, 'fixture-codes', canonical_url=FIXTURE, public_access=ACCESS_TYPE_NONE)
        self.fixture_v1 = source(self.fixtures, 'fixture-codes', '1.0.0', released=True, canonical_url=FIXTURE,
                                 public_access=ACCESS_TYPE_NONE)
        self.dak = source(self.jamlung, 'Example-DAK-Source', canonical_url=DAK)
        self.dak_fhir = source(self.jamlung, 'Example-DAK-Source', 'fhir', released=True, canonical_url=DAK)

        if self.registry:
            for url, owner in ((CIEL, self.ciel), (ICD10, self.who), (PIH, self.pih), (DAK, self.jamlung)):
                GlobalURLRegistryFactory(url=url, namespace=owner.uri)

        self.mapping('SAME-AS', self.icd, 'A00', [self.ciel_v1])  # only in the older release
        self.mapping('BROADER-THAN', self.icd, 'O02.1', [self.ciel_v1, self.ciel_v2])
        self.mapping('SAME AS', self.snomed, '371374003', [self.ciel_v2])
        self.mapping('NOT-SAME-AS', self.icd, 'O03', [self.ciel_v2])
        self.mapping('MAPS TO', None, '4154112', [self.ciel_v2], to_source_url='/orgs/OHDSI/sources/OMOP-Standard/')
        self.mapping('Q-AND-A', self.private, 'P1', [self.ciel_v2])
        self.mapping('SAME-AS', self.icd, 'O99', [self.ciel_v2], retired=True)
        self.mapping('SAME-AS', self.icd, 'Z99', [])  # HEAD only: never released
        self.mapping('SAME-AS', self.icd, 'B20', [self.ciel_v2], from_code='138405')
        self.mapping('SAME-AS', self.pih_source, '7880', [self.pih_v1], parent=self.pih_source,
                     from_source=self.ciel_source)  # PIH's map of a CIEL code
        self.mapping('NARROWER-THAN', self.ciel_source, '113275', [self.private_v1], parent=self.private,
                     from_code='P1')
        self.mapping('SAME-AS', self.icd, 'F01', [self.fixture_v1], parent=self.fixture, from_code='FX-1')
        self.mapping('CONCEPT-SET', self.dak, 'ANC.B10.DE73', [self.dak_fhir], parent=self.dak,
                     from_source=self.ciel_source, from_code='45651')  # from CIEL, as production's DAK maps are

    def mapping(  # pylint: disable=too-many-arguments
            self, map_type, to_source, to_code, releases, parent=None, from_source=None, from_code='113275', **kwargs):
        """A mapping held by `parent` (CIEL's HEAD by default) and the `releases` listed, from `from_source`'s code."""
        parent = parent or self.ciel_source
        from_source = from_source or parent
        kwargs.setdefault('to_source_url', to_source.uri if to_source else None)
        return MappingFactory(
            parent=parent, map_type=map_type, from_concept=None, to_concept=None, from_source=from_source,
            from_source_url=from_source.uri, from_concept_code=from_code, to_source=to_source,
            to_concept_code=to_code, sources=releases, **kwargs)

    def get(self, base='/fhir', user=None, **params):
        client = APIClient()
        if user:
            client.force_authenticate(user)
        return client.get(f'{base}/ConceptMap/$translate', params)

    def post(self, parameters, base='/fhir', user=None, **query):
        client = APIClient()
        if user:
            client.force_authenticate(user)
        body = {'resourceType': 'Parameters', 'parameter': parameters}
        path = f'{base}/ConceptMap/$translate' + (f"?{'&'.join(f'{k}={v}' for k, v in query.items())}" if query else '')
        return client.post(path, body, format='json')

    def answer(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['resourceType'], 'Parameters')
        return response.data

    @staticmethod
    def values(parameters, name):
        return [next(value for key, value in item.items() if key.startswith('value'))
                for item in parameters['parameter'] if item['name'] == name]

    @staticmethod
    def matches(parameters):
        """Each match as system|code|equivalence, sorted."""
        found = []
        for item in parameters['parameter']:
            if item['name'] == 'match':
                parts = {part['name']: part for part in item['part']}
                coding = parts['concept']['valueCoding']
                found.append(f"{coding['system']}|{coding['code']}|{parts['equivalence']['valueCode']}")
        return sorted(found)

    def result(self, parameters):
        results = self.values(parameters, 'result')
        self.assertEqual(len(results), 1, parameters)
        return results[0]

    def assertOutcome(  # pylint: disable=invalid-name,too-many-arguments
            self, response, status, code, tx_code, expression=None):
        self.assertEqual(response.status_code, status, response.data)
        self.assertEqual(response.data['resourceType'], 'OperationOutcome')
        issue = response.data['issue'][0]
        self.assertEqual(issue['code'], code)
        self.assertIn({'system': TX_ISSUE_TYPE_SYSTEM, 'code': tx_code}, issue['details']['coding'])
        if expression:
            self.assertEqual(issue['expression'], [expression])
        return issue


CIEL_113275_V2 = sorted([
    f'{ICD10}|O02.1|narrower', f'{SNOMED_PATH}|371374003|equivalent', f'{ICD10}|O03|disjoint',
    f'{OMOP_PATH}|4154112|relatedto', f'{PRIVATE_PATH}|P1|relatedto'])


class TranslateMappingsTest(TranslateCorpusMixin, OCLTestCase):
    """What a translation finds: the resolved version's own live mappings of the code, and nothing else."""

    def test_reads_the_mappings_of_the_resolved_release_only(self):
        """
        Without url, the code system's own mappings in the release the request names, else the newest the caller is
        shown: not another release's, not HEAD's, not retired ones, and not another repository's (PIH's map of the
        same CIEL code).
        """
        parameters = self.answer(self.get(system=CIEL, code='113275'))
        self.assertTrue(self.result(parameters))
        self.assertEqual(self.matches(parameters), CIEL_113275_V2)
        self.assertEqual(self.values(parameters, 'message'), [])
        self.assertEqual(self.matches(self.answer(self.get(system=CIEL, code='113275', version='v2'))), CIEL_113275_V2)
        self.assertEqual(
            self.matches(self.answer(self.get(system=CIEL, code='113275', version='v1'))),
            sorted([f'{ICD10}|A00|equivalent', f'{ICD10}|O02.1|narrower']))
        self.assertEqual(self.matches(self.answer(self.get(system=f'{CIEL}|v1', code='113275'))),
                         sorted([f'{ICD10}|A00|equivalent', f'{ICD10}|O02.1|narrower']))

    def test_each_match_names_the_concept_map_it_comes_from(self):
        parameters = self.answer(self.get(system=CIEL, code='138405'))
        self.assertEqual(parameters['parameter'], [
            {'name': 'result', 'valueBoolean': True},
            {'name': 'match', 'part': [
                {'name': 'equivalence', 'valueCode': 'equivalent'},
                {'name': 'concept', 'valueCoding': {'system': ICD10, 'code': 'B20'}},
                {'name': 'source', 'valueUri': f'{CIEL}|v2'}]}])

    def test_map_types_are_relationships_whatever_their_spelling(self):
        """
        Case, spaces, hyphens, underscores and surrounding whitespace don't matter (design D27): SAME-AS is
        equivalent, NARROWER-THAN wider, BROADER-THAN narrower, NOT-SAME-AS disjoint, anything else relatedto.
        """
        spellings = {
            'SAME-AS': 'equivalent', 'SAME AS': 'equivalent', 'Same As': 'equivalent', 'same_as': 'equivalent',
            ' SAME-AS ': 'equivalent', 'NARROWER-THAN ': 'wider', 'Narrower Than': 'wider',
            'BROADER-THAN ': 'narrower', 'broader-than': 'narrower', 'NOT-SAME-AS': 'disjoint',
            'Not Same As': 'disjoint', 'MAPS TO': 'relatedto', 'CONCEPT-SET': 'relatedto', 'Q-AND-A': 'relatedto',
            'REPLACED BY': 'relatedto', 'Associated-finding': 'relatedto', '': 'relatedto'}
        for index, map_type in enumerate(spellings):
            self.mapping(map_type, self.icd, f'S{index}', [self.ciel_v2], from_code=f'spelling-{index}')
        for index, (map_type, equivalence) in enumerate(spellings.items()):
            self.assertEqual(self.matches(self.answer(self.get(system=CIEL, code=f'spelling-{index}'))),
                             [f'{ICD10}|S{index}|{equivalence}'], repr(map_type))

    def test_equivalences_stored_as_map_types_are_their_relationships(self):
        """
        Before WP6, a FHIR ConceptMap write stored every R4 equivalence but equivalent as the map type, verbatim:
        those rows read as the relationship the equivalence is, as do R5 relationship codes, whatever their spelling.
        """
        stored = {
            'equivalent': 'equivalent', 'equal': 'equivalent', 'wider': 'wider', 'subsumes': 'wider',
            'narrower': 'narrower', 'specializes': 'narrower', 'relatedto': 'relatedto', 'inexact': 'relatedto',
            'disjoint': 'disjoint', 'unmatched': 'disjoint', 'related-to': 'relatedto',
            'source-is-narrower-than-target': 'wider', 'source-is-broader-than-target': 'narrower',
            'not-related-to': 'disjoint', 'Disjoint ': 'disjoint', 'WIDER': 'wider', 'Not Related To': 'disjoint'}
        for index, map_type in enumerate(stored):
            self.mapping(map_type, self.icd, f'E{index}', [self.ciel_v2], from_code=f'old-{index}')
        for index, (map_type, equivalence) in enumerate(stored.items()):
            parameters = self.answer(self.get(system=CIEL, code=f'old-{index}'))
            self.assertEqual(self.matches(parameters), [f'{ICD10}|E{index}|{equivalence}'], repr(map_type))
            self.assertEqual(self.result(parameters), equivalence != 'disjoint', repr(map_type))
        group = next(group for group in APIClient().get(f'/fhir/ConceptMap/{self.ciel_v2.id}').data['group']
                     if group['target'] == ICD10)
        read = {element['code']: element['target'][0]['equivalence'] for element in group['element']}
        self.assertEqual([read[f'old-{index}'] for index in range(len(stored))], list(stored.values()))

    def test_the_code_system_version_names_the_mappings_of_that_version(self):
        """
        version (or the coding's version) narrows an explicit ConceptMap's mappings to those of that version of the
        code's system, and the unversioned ones; in reverse, the version is the target side's.
        """
        maps = source(self.pih, 'version-map', canonical_url='https://pih.example/fhir/ConceptMap/versions')
        maps_v1 = source(self.pih, 'version-map', 'v1', released=True, canonical_url=maps.canonical_url)
        if self.registry:
            GlobalURLRegistryFactory(url=maps.canonical_url, namespace=self.pih.uri)
        for from_code, from_version, to_code, to_version in (
                ('9000', 'v1', 'V1', None), ('9000', 'v2', 'V2', None), ('9000', None, 'ANY', None),
                ('R2019', None, 'O55', 'ICD102019'), ('R2010', None, 'O55', '2010'), ('RANY', '', 'O55', '')):
            self.mapping('SAME-AS', self.icd, to_code, [maps_v1], parent=maps, from_source=self.ciel_source,
                         from_code=from_code, from_source_version=from_version, to_source_version=to_version)
        forward = {'url': maps.canonical_url, 'system': CIEL, 'code': '9000'}
        for version, codes in ((None, ['ANY', 'V1', 'V2']), ('v1', ['ANY', 'V1']), ('v2', ['ANY', 'V2']),
                               ('v3', ['ANY'])):
            expected = [f'{ICD10}|{code}|equivalent' for code in codes]
            versioned = {'version': version} if version else {}
            self.assertEqual(self.matches(self.answer(self.get(**forward, **versioned))), expected, version)
            coding = {'system': CIEL, 'code': '9000', **({'version': version} if version else {})}
            self.assertEqual(self.matches(self.answer(self.post([
                {'name': 'url', 'valueUri': maps.canonical_url}, {'name': 'coding', 'valueCoding': coding}]))),
                expected, version)
        reverse = {'url': maps.canonical_url, 'system': ICD10, 'code': 'O55', 'reverse': 'true'}
        for version, codes in ((None, ['R2010', 'R2019', 'RANY']), ('ICD102019', ['R2019', 'RANY']),
                               ('2010', ['R2010', 'RANY'])):
            expected = [f'{CIEL}|{code}|equivalent' for code in codes]
            versioned = {'version': version} if version else {}
            self.assertEqual(self.matches(self.answer(self.get(**reverse, **versioned))), expected, version)
            coding = {'system': ICD10, 'code': 'O55', **({'version': version} if version else {})}
            self.assertEqual(self.matches(self.answer(self.post([
                {'name': 'url', 'valueUri': maps.canonical_url}, {'name': 'coding', 'valueCoding': coding},
                {'name': 'reverse', 'valueBoolean': True}]))), expected, version)

    def test_concept_map_version_names_the_release_of_the_implicit_map(self):
        """
        Without url, conceptMapVersion names the release of the code system's map (in reverse, targetsystem's),
        whatever version names the code's own system version.
        """
        older = sorted([f'{ICD10}|A00|equivalent', f'{ICD10}|O02.1|narrower'])
        for params in ({'conceptMapVersion': 'v1'}, {'conceptMapVersion': 'v1', 'version': 'v2'}):
            parameters = self.answer(self.get(system=CIEL, code='113275', **params))
            self.assertEqual(self.matches(parameters), older, params)
            self.assertEqual(parameters['parameter'][1]['part'][2], {'name': 'source', 'valueUri': f'{CIEL}|v1'},
                             params)
        self.assertEqual(self.matches(self.answer(self.get(system=CIEL, code='113275', conceptMapVersion='v2'))),
                         CIEL_113275_V2)
        parameters = self.answer(self.get(system=CIEL, code='113275', conceptMapVersion='v9'))
        self.assertFalse(self.result(parameters))
        self.assertEqual(len(self.values(parameters, 'message')), 1)
        reverse = {'system': ICD10, 'code': 'A00', 'targetsystem': CIEL, 'reverse': 'true'}
        self.assertEqual(self.matches(self.answer(self.get(**reverse, conceptMapVersion='v1'))),
                         [f'{CIEL}|113275|equivalent'])
        self.assertEqual(self.matches(self.answer(self.get(**reverse))), [])

    def test_a_deleted_mapping_is_no_translation(self):
        """A mapping version deleted (is_active false) though still in the release is read nowhere."""
        deleted = self.mapping('SAME-AS', self.icd, 'D99', [self.ciel_v2], from_code='8000')
        self.mapping('SAME-AS', self.icd, 'D98', [self.ciel_v2], from_code='8000')
        Mapping.objects.filter(id=deleted.id).update(is_active=False)
        self.assertEqual(self.matches(self.answer(self.get(system=CIEL, code='8000'))), [f'{ICD10}|D98|equivalent'])
        for code in ('D99', 'D98'):
            parameters = self.answer(self.get(system=ICD10, code=code, targetsystem=CIEL, reverse='true'))
            self.assertEqual(self.matches(parameters), [] if code == 'D99' else [f'{CIEL}|8000|equivalent'], code)
        group = next(group for group in APIClient().get(f'/fhir/ConceptMap/{self.ciel_v2.id}').data['group']
                     if group['target'] == ICD10)
        self.assertEqual([target['code'] for element in group['element'] if element['code'] == '8000'
                          for target in element['target']], ['D98'])

    def test_not_same_as_alone_is_no_translation(self):
        """result is true only when some match isn't disjoint (R4: not unmatched or disjoint)."""
        self.mapping('NOT-SAME-AS', self.icd, 'Q00', [self.ciel_v2], from_code='5000')
        parameters = self.answer(self.get(system=CIEL, code='5000'))
        self.assertFalse(self.result(parameters))
        self.assertEqual(self.matches(parameters), [f'{ICD10}|Q00|disjoint'])

    def test_targetsystem_keeps_the_matches_into_that_system(self):
        parameters = self.answer(self.get(system=CIEL, code='113275', targetsystem=ICD10))
        self.assertTrue(self.result(parameters))
        self.assertEqual(self.matches(parameters), sorted([f'{ICD10}|O02.1|narrower', f'{ICD10}|O03|disjoint']))
        self.assertEqual(self.matches(self.answer(self.get(system=CIEL, code='113275', targetsystem=SNOMED_PATH))),
                         [f'{SNOMED_PATH}|371374003|equivalent'])
        none = self.answer(self.get(system=CIEL, code='113275', targetsystem=UNKNOWN))
        self.assertFalse(self.result(none))
        self.assertEqual(len(self.values(none, 'message')), 1)

    def test_no_translation_says_why(self):
        """result false comes with a message: an unknown code, or a system no ConceptMap translates from."""
        for params in ({'system': CIEL, 'code': 'no-such-code'},
                       {'system': CIEL, 'code': 'no-such-code', 'version': 'v1'},
                       {'system': UNKNOWN, 'code': '113275'}, {'system': CIEL, 'code': '113275', 'version': 'v9'},
                       {'system': ICD10, 'code': 'O02.1'}):
            parameters = self.answer(self.get(**params))
            self.assertFalse(self.result(parameters), params)
            self.assertEqual(len(self.values(parameters, 'message')), 1, params)
            self.assertEqual(self.matches(parameters), [], params)

    def test_url_names_the_concept_map(self):
        """
        With url (and conceptMapVersion) the ConceptMap is the one named, and only its mappings from the code's system
        count: DAK's ConceptMap holds mappings from CIEL codes into DAK's own.
        """
        self.assertEqual(self.matches(self.answer(self.get(url=CIEL, system=CIEL, code='113275'))), CIEL_113275_V2)
        self.assertEqual(
            self.matches(self.answer(self.get(url=CIEL, conceptMapVersion='v1', system=CIEL, code='113275'))),
            sorted([f'{ICD10}|A00|equivalent', f'{ICD10}|O02.1|narrower']))
        self.assertEqual(self.matches(self.answer(self.get(url=PIH, system=CIEL, code='113275'))),
                         [f'{PIH}|7880|equivalent'])
        self.assertEqual(self.matches(self.answer(self.get(url=DAK, system=CIEL, code='45651'))),
                         [f'{DAK}|ANC.B10.DE73|relatedto'])
        for params in ({'url': DAK, 'system': DAK, 'code': '45651'}, {'system': DAK, 'code': '45651'}):
            parameters = self.answer(self.get(**params))
            self.assertFalse(self.result(parameters), params)
            self.assertEqual(self.matches(parameters), [], params)

    def test_an_unknown_concept_map_is_not_found(self):
        for params in ({'url': UNKNOWN}, {'url': CIEL, 'conceptMapVersion': 'v9'}, {'url': PRIVATE}):
            self.assertOutcome(self.get(system=CIEL, code='113275', **params), 404, 'not-found', 'not-found', 'url')

    def test_reverse_finds_the_codes_that_map_to_the_code(self):
        """
        reverse: the concept named is the target; the ConceptMap is url's, else targetsystem's (the system of the
        codes sought). Each match is a source concept; its equivalence is relative to the code named, so CIEL
        113275 BROADER-THAN O02.1 is a match that is wider than O02.1.
        """
        for params in ({'targetsystem': CIEL}, {'url': CIEL}, {'url': CIEL, 'targetsystem': CIEL}):
            parameters = self.answer(self.get(system=ICD10, code='O02.1', reverse='true', **params))
            self.assertTrue(self.result(parameters), params)
            self.assertEqual(self.matches(parameters), [f'{CIEL}|113275|wider'], params)
        self.assertEqual(self.matches(self.answer(self.get(system=PIH, code='7880', reverse='true', url=PIH))),
                         [f'{CIEL}|113275|equivalent'])
        self.assertEqual(
            self.matches(self.answer(self.get(system=ICD10, code='A00', reverse='true', targetsystem=CIEL))), [])
        for params in ({}, {'targetsystem': UNKNOWN}):  # no ConceptMap to read
            parameters = self.answer(self.get(system=ICD10, code='O02.1', reverse='true', **params))
            self.assertFalse(self.result(parameters), params)
            self.assertEqual(len(self.values(parameters, 'message')), 1, params)
        self.assertEqual(self.matches(self.answer(self.get(system=ICD10, code='O02.1', reverse='false'))), [])

    def test_a_reverse_equivalence_is_relative_to_the_code_named(self):
        """
        R4's equivalence reads from the code translated to the match: forward, a mapping from it NARROWER-THAN its
        target makes a wider match; in reverse the code named is the mapping's target, so the same mapping is a
        narrower match, and BROADER-THAN a wider one. Equivalent, relatedto and disjoint read the same both ways.
        """
        for index, (map_type, forward, backward) in enumerate((
                ('NARROWER-THAN', 'wider', 'narrower'), ('BROADER-THAN', 'narrower', 'wider'),
                ('wider', 'wider', 'narrower'), ('SAME-AS', 'equivalent', 'equivalent'),
                ('NOT-SAME-AS', 'disjoint', 'disjoint'), ('Q-AND-A', 'relatedto', 'relatedto'))):
            self.mapping(map_type, self.icd, f'W{index}', [self.ciel_v2], from_code=f'way-{index}')
            self.assertEqual(self.matches(self.answer(self.get(system=CIEL, code=f'way-{index}'))),
                             [f'{ICD10}|W{index}|{forward}'], map_type)
            self.assertEqual(self.matches(self.answer(self.get(
                system=ICD10, code=f'W{index}', targetsystem=CIEL, reverse='true'))),
                [f'{CIEL}|way-{index}|{backward}'], map_type)

    def test_codes_stored_url_encoded_are_translated_as_codes(self):
        """OCL stores a code with reserved characters percent-encoded (ICD-11's ND53.Y&XK70); FHIR has the code."""
        self.mapping('SAME-AS', self.icd, 'ND53.Y%26XK70', [self.ciel_v2], from_code='7000')
        self.mapping('SAME-AS', self.icd, 'GB41%2FMF8Y', [self.ciel_v2], from_code='7001')
        self.assertEqual(self.matches(self.answer(self.get(system=CIEL, code='7000'))),
                         [f'{ICD10}|ND53.Y&XK70|equivalent'])
        self.assertEqual(self.matches(self.answer(self.get(system=CIEL, code='7001'))),
                         [f'{ICD10}|GB41/MF8Y|equivalent'])
        for code, source_code in (('ND53.Y&XK70', '7000'), ('GB41/MF8Y', '7001')):
            self.assertEqual(
                self.matches(self.answer(self.get(system=ICD10, code=code, reverse='true', targetsystem=CIEL))),
                [f'{CIEL}|{source_code}|equivalent'])

    def test_matches_are_listed_once_and_at_most_match_limit(self):
        self.mapping('SAME-AS', self.icd, 'D1', [self.ciel_v2], from_code='6000')
        self.mapping('Same As', self.icd, 'D1', [self.ciel_v2], from_code='6000')  # the same match again
        for code in ('D2', 'D3', 'D4'):
            self.mapping('SAME-AS', self.icd, code, [self.ciel_v2], from_code='6000')
        self.assertEqual(self.matches(self.answer(self.get(system=CIEL, code='6000'))),
                         [f'{ICD10}|D{index}|equivalent' for index in range(1, 5)])
        with patch('core.fhir.ops.translate.MATCH_LIMIT', 2):
            parameters = self.answer(self.get(system=CIEL, code='6000'))
        self.assertTrue(self.result(parameters))
        self.assertEqual(len(self.matches(parameters)), 2)
        self.assertEqual(len(self.values(parameters, 'message')), 1)


class TranslateMappingsWithRegistryTest(TranslateMappingsTest):
    """The same, with the URL registry on: the global base resolves a canonical through its global entry."""
    registry = True


class TranslatePathTest(OCLTestCase):
    def test_repository_paths(self):
        """An OCL path's repository, whatever its owner and id are called."""
        self.assertIsNone(repository_paths(CIEL))
        self.assertEqual(repository_paths('/orgs/sources/ConceptMap/sources/', ('sources', 'ConceptMap')),
                         ['/orgs/sources/sources/sources/', '/orgs/sources/ConceptMap/sources/'])
        self.assertEqual(repository_paths('/users/jdoe/CodeSystem/codes'), ['/users/jdoe/sources/codes/'])


class TranslateRequestTest(TranslateCorpusMixin, OCLTestCase):
    """The request: GET or POST (a Parameters resource, as HAPI sends it), the code in any of its forms."""

    def test_post_answers_as_get(self):
        """POST $translate with a Parameters body, code and system, coding or codeableConcept (HAPI's form)."""
        expected = sorted([f'{ICD10}|O02.1|narrower', f'{ICD10}|O03|disjoint'])
        for parameters in (
                [{'name': 'system', 'valueUri': CIEL}, {'name': 'code', 'valueCode': '113275'},
                 {'name': 'targetsystem', 'valueUri': ICD10}],
                [{'name': 'coding', 'valueCoding': {'system': CIEL, 'code': '113275'}},
                 {'name': 'targetsystem', 'valueUri': ICD10}],
                [{'name': 'codeableConcept', 'valueCodeableConcept': {'coding': [{'system': CIEL, 'code': '113275'}]}},
                 {'name': 'targetsystem', 'valueUri': ICD10}],
                [{'name': 'url', 'valueUri': CIEL}, {'name': 'system', 'valueUri': CIEL},
                 {'name': 'version', 'valueString': 'v2'}, {'name': 'code', 'valueCode': '113275'},
                 {'name': 'targetsystem', 'valueUri': ICD10}, {'name': 'reverse', 'valueBoolean': False}]):
            response = self.post(parameters)
            self.assertEqual(self.matches(self.answer(response)), expected, parameters)
        reverse = self.post([{'name': 'system', 'valueUri': ICD10}, {'name': 'code', 'valueCode': 'O02.1'},
                             {'name': 'targetsystem', 'valueUri': CIEL}, {'name': 'reverse', 'valueBoolean': True}])
        self.assertEqual(self.matches(self.answer(reverse)), [f'{CIEL}|113275|wider'])

    def test_get_takes_a_coding_as_a_token(self):
        self.assertEqual(self.matches(self.answer(self.get(coding=f'{CIEL}|138405'))), [f'{ICD10}|B20|equivalent'])

    def test_trailing_slash_and_owner_bases(self):
        client = APIClient()
        response = client.get('/fhir/ConceptMap/$translate/', {'system': CIEL, 'code': '138405'})
        self.assertEqual(self.matches(self.answer(response)), [f'{ICD10}|B20|equivalent'])
        self.assertEqual(self.matches(self.answer(self.get(base='/orgs/CIEL', system=CIEL, code='138405'))),
                         [f'{ICD10}|B20|equivalent'])
        self.assertEqual(self.matches(self.answer(self.get(base='/users/jamlung', url=DAK, system=CIEL, code='45651'))),
                         [f'{DAK}|ANC.B10.DE73|relatedto'])
        parameters = self.answer(self.get(base='/orgs/WHO', system=CIEL, code='138405'))  # not WHO's
        self.assertFalse(self.result(parameters))

    def test_xml(self):
        response = APIClient().get(
            '/fhir/ConceptMap/$translate', {'system': CIEL, 'code': '138405', '_format': 'xml'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('application/fhir+xml'))
        content = response.content.decode()
        for fragment in ('<Parameters xmlns="http://hl7.org/fhir">', '<valueCode value="equivalent"/>',
                         f'<valueUri value="{CIEL}|v2"/>', '<code value="B20"/>'):
            self.assertIn(fragment, content)

    def test_bad_requests(self):
        for params, code, expression in (
                ({}, 'invalid', 'code'),
                ({'system': CIEL}, 'invalid', 'code'),
                ({'url': CIEL, 'system': CIEL}, 'invalid', 'code'),
                ({'code': '113275'}, 'invalid', 'system'),
                ({'system': CIEL, 'code': '113275', 'reverse': 'maybe'}, 'invalid', 'reverse'),
                ({'coding': 'no-bar'}, 'invalid', 'coding'),
                ({'system': CIEL, 'code': '113275', 'dependency': 'x'}, 'not-supported', 'dependency'),
        ):
            issue = self.assertOutcome(self.get(**params), 400, code, 'not-supported' if code == 'not-supported'
                                       else 'invalid-data', expression)
            self.assertTrue(issue['details']['text'], params)
        for parameters, expression in (
                ([{'name': 'codeableConcept', 'valueCodeableConcept': {'text': 'no coding'}}], 'codeableConcept'),
                ([{'name': 'coding', 'valueCoding': {'code': '113275'}}], 'coding'),
                ([{'name': 'system', 'valueUri': CIEL}, {'name': 'code', 'valueBoolean': True}], 'code'),
                ([{'name': 'system', 'valueUri': CIEL}, {'name': 'code', 'valueCode': '1'},
                  {'name': 'conceptMap', 'resource': {'resourceType': 'ConceptMap'}}], 'conceptMap')):
            response = self.post(parameters)
            self.assertEqual(response.status_code, 400, (parameters, response.data))
            self.assertEqual(response.data['issue'][0]['expression'], [expression])
        self.assertOutcome(
            APIClient().post('/fhir/ConceptMap/$translate', {'resourceType': 'Bundle'}, format='json'),
            400, 'invalid', 'invalid-data')


class TranslateBoundsTest(TranslateCorpusMixin, OCLTestCase):
    """
    No request reads mappings other than those of the code it names: without a code, or a code without its system, it
    is a 400 naming the parameter, whatever the method and however the values are typed (a POST's valueString too).
    """
    TYPES = {'url': 'valueUri', 'system': 'valueUri', 'code': 'valueCode'}

    def test_reads_only_the_named_code(self):
        for method, url, code, system in product(('GET', 'POST', 'POST valueString'), (None, CIEL), (None, '138405'),
                                                 (None, CIEL)):
            msg = (method, url, code, system)
            inputs = {name: value for name, value in (('url', url), ('code', code), ('system', system)) if value}
            with self.subTest(msg), CaptureQueriesContext(connection) as context:
                if method == 'GET':
                    response = self.get(**inputs)
                else:
                    response = self.post([{'name': name, ('valueString' if 'valueString' in method
                                                          else self.TYPES[name]): value}
                                          for name, value in inputs.items()])
            reads = [query['sql'] for query in context.captured_queries if ' FROM "mappings"' in query['sql']]
            self.assertTrue(all('"mappings"."from_concept_code" IN (\'138405\'' in sql for sql in reads), msg)
            if not code or not system:
                self.assertOutcome(response, 400, 'invalid', 'invalid-data', 'system' if code else 'code')
                self.assertEqual(reads, [], msg)
            else:
                self.assertEqual(self.matches(self.answer(response)), [f'{ICD10}|B20|equivalent'], msg)


class TranslateScopeTest(TranslateCorpusMixin, OCLTestCase):
    """
    source: the value set (or code system) the code was chosen from; in reverse, target is. The other narrows nothing
    (FHIRsmith's reading).
    """

    def setUp(self):
        super().setUp()
        for owner, mnemonic, url, code_system, code in ((self.ciel, 'ciel-set', CIEL_VS, self.ciel_source, '113275'),
                                                        (self.who, 'icd-set', ICD10_VS, self.icd, 'O02.1')):
            concept = ConceptFactory(parent=code_system, mnemonic=code)
            collection(owner, mnemonic, canonical_url=url)
            value_set = collection(owner, mnemonic, 'v1', released=True, canonical_url=url)
            expansion = ExpansionFactory(collection_version=value_set)
            expansion.concepts.set([concept])
            Collection.objects.filter(id=value_set.id).update(expansion_uri=expansion.uri)
        # MOH's public value set holds a code of its private source and one of SNOMED, which has no canonical URL
        snomed_code = ConceptFactory(parent=self.snomed, mnemonic='371374003')
        collection(self.moh, 'mixed-set', canonical_url=MIXED_VS)
        mixed = collection(self.moh, 'mixed-set', 'v1', released=True, canonical_url=MIXED_VS)
        expansion = ExpansionFactory(collection_version=mixed)
        expansion.concepts.set([ConceptFactory(parent=self.private, mnemonic='P1'), snomed_code])
        Collection.objects.filter(id=mixed.id).update(expansion_uri=expansion.uri)
        self.mapping('SAME-AS', self.icd, 'S371', [self.ciel_v2], from_source=self.snomed, from_code='371374003')

    def capture(self, **params):
        with CaptureQueriesContext(connection) as context:
            parameters = self.answer(self.get(**params))
        return parameters, [query['sql'] for query in context.captured_queries]

    def test_a_scope_holds_only_the_codes_of_systems_the_caller_is_shown(self):
        """
        A public value set holding a private source's code says nothing about it to those who can't view the source: a
        private system answers word for word, and with the same statements, as an unknown one, forward and reverse.
        """
        for user in (None, self.outsider, self.moh_member, self.admin):
            for params in ({'source': MIXED_VS}, {'target': MIXED_VS, 'reverse': 'true', 'targetsystem': CIEL}):
                private, private_sql = self.capture(system=PRIVATE, code='P1', user=user, **params)
                unknown, unknown_sql = self.capture(system=UNKNOWN, code='P1', user=user, **params)
                self.assertEqual(json.loads(json.dumps(private).replace(PRIVATE, 'SYSTEM')),
                                 json.loads(json.dumps(unknown).replace(UNKNOWN, 'SYSTEM')), (user, params))
                self.assertEqual([sql.replace(PRIVATE, 'SYSTEM') for sql in private_sql],
                                 [sql.replace(UNKNOWN, 'SYSTEM') for sql in unknown_sql], (user, params))
                self.assertFalse(self.result(private), (user, params))
        # to a member of MOH at its base, P1 is in the scope
        parameters = self.answer(self.get(base='/orgs/MOH', user=self.moh_member, system=PRIVATE, code='P1',
                                          source=MIXED_VS))
        self.assertEqual(self.matches(parameters), [f'{CIEL}|113275|wider'])

    def test_a_deleted_concept_is_no_member_of_a_scope(self):
        Concept.objects.filter(parent=self.ciel_source, mnemonic='113275').update(is_active=False)  # every version
        parameters = self.answer(self.get(system=CIEL, code='113275', source=CIEL_VS))
        self.assertFalse(self.result(parameters))
        self.assertEqual(self.matches(parameters), [])

    def test_a_scope_holds_codes_of_a_system_named_by_its_ocl_path(self):
        """SNOMED has no canonical URL: in a scope, as in a match, its system is its OCL path."""
        parameters = self.answer(self.get(url=CIEL, system=SNOMED_PATH, code='371374003', source=MIXED_VS))
        self.assertEqual(self.matches(parameters), [f'{ICD10}|S371|equivalent'])
        parameters = self.answer(self.get(system=SNOMED_PATH, code='371374003', targetsystem=CIEL, reverse='true',
                                          target=MIXED_VS))
        self.assertEqual(self.matches(parameters), [f'{CIEL}|113275|equivalent'])
        for params in ({'url': CIEL, 'source': CIEL_VS}, {'targetsystem': CIEL, 'reverse': 'true', 'target': CIEL_VS}):
            parameters = self.answer(self.get(system=SNOMED_PATH, code='371374003', **params))
            self.assertFalse(self.result(parameters), params)

    def test_a_code_in_the_source_scope_is_translated(self):
        for scope in (CIEL, f'{CIEL}?fhir_vs', CIEL_VS):
            parameters = self.answer(self.get(system=CIEL, code='113275', source=scope))
            self.assertEqual(self.matches(parameters), CIEL_113275_V2, scope)

    def test_a_code_outside_the_source_scope_has_no_translation(self):
        for params in ({'code': '138405', 'source': CIEL_VS}, {'code': '113275', 'source': ICD10}):
            parameters = self.answer(self.get(system=CIEL, **params))
            self.assertFalse(self.result(parameters), params)
            self.assertEqual(self.matches(parameters), [], params)
            self.assertEqual(len(self.values(parameters, 'message')), 1, params)

    def test_an_unknown_source_scope_is_not_found(self):
        for scope in (UNKNOWN, PRIVATE):
            self.assertOutcome(self.get(system=CIEL, code='113275', source=scope), 404, 'not-found', 'not-found',
                               'source')

    def test_target_narrows_nothing(self):
        for target in (ICD10, CIEL_VS, UNKNOWN):
            self.assertEqual(self.matches(self.answer(self.get(system=CIEL, code='113275', target=target))),
                             CIEL_113275_V2, target)

    def test_reverse_swaps_the_scopes(self):
        """
        reverse reverses the meaning of source and target (R4): the code named, now a target code, must be in the
        target scope, and source, now the scope of the codes sought, narrows nothing, as target does forward.
        """
        reverse = {'system': ICD10, 'code': 'O02.1', 'targetsystem': CIEL, 'reverse': 'true'}
        for scopes in ({'target': ICD10}, {'target': ICD10_VS}, {'target': f'{ICD10}?fhir_vs'}, {'source': CIEL_VS},
                       {'source': CIEL}, {'source': UNKNOWN}, {'source': ICD10_VS},
                       {'source': CIEL_VS, 'target': ICD10_VS}):
            parameters = self.answer(self.get(**reverse, **scopes))
            self.assertEqual(self.matches(parameters), [f'{CIEL}|113275|wider'], scopes)
        for scopes in ({'target': CIEL_VS}, {'target': CIEL}, {'source': ICD10_VS, 'target': CIEL_VS}):
            parameters = self.answer(self.get(**reverse, **scopes))
            self.assertFalse(self.result(parameters), scopes)
            self.assertEqual(self.matches(parameters), [], scopes)
            self.assertEqual(len(self.values(parameters, 'message')), 1, scopes)
        for scope in (UNKNOWN, PRIVATE):
            self.assertOutcome(self.get(**reverse, target=scope), 404, 'not-found', 'not-found', 'target')

    def test_svcm_shape(self):
        """SVCM's ITI-101 request: url, source, target, system and code."""
        parameters = self.answer(self.get(url=CIEL, source=CIEL, target=ICD10, system=CIEL, code='113275'))
        self.assertEqual(self.matches(parameters), CIEL_113275_V2)


class TranslateAccessTest(TranslateCorpusMixin, OCLTestCase):
    """
    Resolution and access are core.fhir.resolve's. A system or ConceptMap the caller isn't shown is unknown to them,
    and a mapping into a source they aren't shown names it as a mapping into a source OCL doesn't hold would: by the
    URL it stores, never by the source's canonical URL.
    """

    def capture(self, **params):
        """The answer, and the SQL it ran."""
        with CaptureQueriesContext(connection) as context:
            parameters = self.answer(self.get(**params))
        return parameters, [query['sql'] for query in context.captured_queries]

    def test_a_system_the_caller_is_not_shown_answers_as_an_unknown_one(self):
        """
        The same answer, word for word but the system's name, and no mapping read. (resolve() runs one query more
        for a repository it finds and then doesn't show.)
        """
        for user in (None, self.outsider, self.moh_member, self.admin):
            private, private_sql = self.capture(system=PRIVATE, code='P1', user=user)
            unknown, unknown_sql = self.capture(system=UNKNOWN, code='P1', user=user)
            self.assertEqual(json.loads(json.dumps(private).replace(PRIVATE, 'SYSTEM')),
                             json.loads(json.dumps(unknown).replace(UNKNOWN, 'SYSTEM')), user)
            self.assertFalse(self.result(private), user)
            for sql in (private_sql, unknown_sql):
                self.assertFalse([query for query in sql if '"mappings"' in query], user)

    def test_at_its_owner_base_to_those_who_may_view_it(self):
        for user, matches in ((None, []), (self.outsider, []), (self.moh_member, [f'{CIEL}|113275|wider']),
                              (self.admin, [f'{CIEL}|113275|wider'])):
            parameters = self.answer(self.get(base='/orgs/MOH', user=user, system=PRIVATE, code='P1'))
            self.assertEqual(self.matches(parameters), matches, user)
        for user, matches in ((None, []), (self.outsider, []), (self.member, [f'{ICD10}|F01|equivalent'])):
            parameters = self.answer(self.get(base='/orgs/FHIR-Fixtures', user=user, system=FIXTURE, code='FX-1'))
            self.assertEqual(self.matches(parameters), matches, user)

    def test_a_target_the_caller_is_not_shown_is_named_by_the_url_the_mapping_stores(self):
        """Only at an owner base, and only to those who may view it, is MOH's private source named by its canonical."""
        for base, user, system in (
                ('/fhir', None, PRIVATE_PATH), ('/fhir', self.moh_member, PRIVATE_PATH),
                ('/fhir', self.admin, PRIVATE_PATH), ('/orgs/CIEL', None, PRIVATE_PATH),
                ('/orgs/CIEL', self.outsider, PRIVATE_PATH), ('/orgs/CIEL', self.moh_member, PRIVATE),
                ('/orgs/CIEL', self.admin, PRIVATE)):
            parameters = self.answer(self.get(base=base, user=user, system=CIEL, code='113275', targetsystem=system))
            self.assertEqual(self.matches(parameters), [f'{system}|P1|relatedto'], (base, user))
            other = PRIVATE if system == PRIVATE_PATH else PRIVATE_PATH
            self.assertEqual(self.matches(self.answer(self.get(
                base=base, user=user, system=CIEL, code='113275', targetsystem=other))), [], (base, user))

    @expectedFailure  # F1.2: until WP4+WP5's resolve() resolves a default to the newest release the caller is shown
    def test_the_default_version_is_the_newest_release_the_caller_is_shown(self):
        """
        A newer private release doesn't hide the public one before it: without a version (and without url, or with
        it), the newest release the caller may see is the ConceptMap.
        """
        source(self.ciel, 'CIEL', 'v3', released=True, canonical_url=CIEL, public_access=ACCESS_TYPE_NONE)
        for params in ({'system': CIEL, 'code': '138405'}, {'url': CIEL, 'system': CIEL, 'code': '138405'}):
            parameters = self.answer(self.get(**params))
            self.assertEqual(self.matches(parameters), [f'{ICD10}|B20|equivalent'], params)
            self.assertEqual(parameters['parameter'][1]['part'][2]['valueUri'], f'{CIEL}|v2', params)

    def test_a_reverse_from_a_source_the_caller_is_not_shown(self):
        """MOH's private map of P1 is no ConceptMap to those it isn't shown."""
        for base, user, matches in (('/fhir', self.moh_member, []), ('/orgs/MOH', None, []),
                                    ('/orgs/MOH', self.moh_member, [f'{PRIVATE}|P1|narrower'])):
            parameters = self.answer(self.get(base=base, user=user, system=CIEL, code='113275', reverse='true',
                                              targetsystem=PRIVATE))
            self.assertEqual(self.matches(parameters), matches, (base, user))


class TranslateCostTest(TranslateCorpusMixin, OCLTestCase):
    """A fixed number of queries per call, whatever the number of mappings."""

    def test_query_counts(self):
        """
        Resolution (the registry toggle, the repository, its newest release), the sources the systems name, then the
        mappings, with whether the caller is shown their sources. url resolves the ConceptMap the same way.
        """
        for count in (1, 20):
            for index in range(count):
                self.mapping('SAME-AS', self.icd, f'N{count}-{index}', [self.ciel_v2], from_code=f'n{count}')
            for params in ({'system': CIEL, 'code': f'n{count}'},
                           {'system': CIEL, 'code': f'n{count}', 'targetsystem': ICD10},
                           {'url': CIEL, 'system': CIEL, 'code': f'n{count}'},
                           {'system': ICD10, 'code': f'N{count}-0', 'reverse': 'true', 'targetsystem': CIEL}):
                with self.subTest(count=count, params=params), self.assertNumQueries(5):
                    self.assertTrue(self.result(self.answer(self.get(**params))))
        with self.assertNumQueries(2):  # the toggle, no repository: no ConceptMap, no mappings read
            self.answer(self.get(system=UNKNOWN, code='1'))
        with self.assertNumQueries(6):  # + the owner
            self.answer(self.get(base='/orgs/CIEL', system=CIEL, code='n1'))


class ConceptMapGroupTest(TranslateCorpusMixin, OCLTestCase):
    """
    A ConceptMap read's groups use $translate's relationships and codes (ConceptMapGroupField). The read names a target
    by its source's canonical URL whoever asks; $translate names one the caller isn't shown by the URL it stores.
    """

    def test_group_targets(self):
        self.mapping('SAME-AS', self.icd, 'ND53.Y%26XK70', [self.ciel_v2], from_code='7000')
        response = APIClient().get(f'/fhir/ConceptMap/{self.ciel_v2.id}')
        self.assertEqual(response.status_code, 200, response.data)
        targets = sorted(
            (group['target'], element['code'], target['code'], target['equivalence'])
            for group in response.data['group'] for element in group['element'] for target in element['target'])
        self.assertEqual(targets, sorted([
            (ICD10, '113275', 'O02.1', 'narrower'), (SNOMED_PATH, '113275', '371374003', 'equivalent'),
            (ICD10, '113275', 'O03', 'disjoint'), (OMOP_PATH, '113275', '4154112', 'relatedto'),
            (PRIVATE, '113275', 'P1', 'relatedto'), (ICD10, '138405', 'B20', 'equivalent'),
            (ICD10, '7000', 'ND53.Y&XK70', 'equivalent')]))
        self.assertEqual(sorted(element['code'] for group in response.data['group'] if group['target'] == ICD10
                                for element in group['element']), ['113275', '138405', '7000'])

    def test_a_deleted_mapping_is_no_part_of_a_concept_map(self):
        """The read leaves out a deleted mapping version still in the release, so the searches don't count it."""
        loinc = source(self.who, 'LOINC', canonical_url='http://loinc.org')
        deleted = self.mapping('SAME-AS', loinc, '1234-5', [self.ciel_v2], from_code='8001')
        search = {'target-system': 'http://loinc.org'}
        self.assertEqual(APIClient().get('/fhir/ConceptMap/', search).data['total'], 1)
        Mapping.objects.filter(id=deleted.id).update(is_active=False)
        self.assertEqual(APIClient().get('/fhir/ConceptMap/', search).data['total'], 0)
        groups = APIClient().get(f'/fhir/ConceptMap/{self.ciel_v2.id}').data['group']
        self.assertNotIn('http://loinc.org', [group['target'] for group in groups])
