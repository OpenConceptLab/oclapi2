"""
core.fhir.search: FHIR search and read of CodeSystem, ValueSet and ConceptMap (SVCM ITI-95, ITI-96 and ITI-100) at the
global base /fhir/ and at owner bases. Released versions only, never HEAD; public ones at the global base whoever asks;
an opaque id per released version at the global base, the mnemonic at an owner base; SVCM's search parameters with
:exact and :contains; _count, paging, the real total and a fullUrl on every entry; a CodeSystem read is complete up to
the concept limit, else not-present. OpenConceptLab/ocl_online#451.
"""
import time
from datetime import datetime, timezone as dt_timezone
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import connection, OperationalError
from django.test import TransactionTestCase, tag
from django.test.utils import CaptureQueriesContext, override_settings
from mock import patch
from rest_framework.test import APIClient

from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE, HEAD
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory, ConceptDescriptionFactory
from core.fhir import resolve as fhir_resolve, search as fhir_search
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM, TooCostlyError
from core.fhir.resolve import FhirResolver
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory

ICD10 = 'http://hl7.org/fhir/sid/icd-10'
CIEL = 'https://CIELterminology.org'
FIXTURE = 'https://openconceptlab.org/fhir-fixtures/CodeSystem/fixture-codes'
ICD10_VS = 'http://sahra.tools/fhir/ValueSet/icd10-all'
CIEL_VS = 'https://example.org/ValueSet/ciel-set'
HIV = 'http://moh.example/fhir/CodeSystem/hiv'
HIV_VS = 'http://moh.example/fhir/ValueSet/hiv'
SUBSETTED = {'system': 'http://terminology.hl7.org/CodeSystem/v3-ObservationValue', 'code': 'SUBSETTED'}
HL7_OID = {'system': 'urn:ietf:rfc:3986', 'value': 'urn:oid:2.16.840.1.113883.6.90'}
ACSN = {'text': 'Accession ID', 'coding': [{'system': 'http://hl7.org/fhir/v2/0203', 'code': 'ACSN'}]}


def source(owner, mnemonic, version=HEAD, **kwargs):
    if isinstance(owner, UserProfile):
        return UserSourceFactory(user=owner, mnemonic=mnemonic, name=mnemonic, version=version, **kwargs)
    return OrganizationSourceFactory(organization=owner, mnemonic=mnemonic, name=mnemonic, version=version, **kwargs)


def collection(owner, mnemonic, version=HEAD, **kwargs):
    return OrganizationCollectionFactory(
        organization=owner, mnemonic=mnemonic, name=mnemonic, version=version, **kwargs)


def indexing():
    """
    Saves indexed, for the mappings index ConceptMap system search reads: an earlier test's pause_indexing() can leave
    indexing off for the rest of a run (resume_indexing does nothing in TEST_MODE).
    """
    return override_settings(ES_SYNC=True, ELASTICSEARCH_DSL_AUTOSYNC=True, ELASTICSEARCH_DSL_AUTO_REFRESH=True)


def at(year):
    return datetime(year, 6, 15, 12, tzinfo=dt_timezone.utc)


class SearchCorpusMixin:
    """
    ICD-10 shared by WHO (whose released version has no canonical of its own, as in production) and sehatapp (with a
    newer unreleased version); CIEL with a retired release and a live one, with concepts and mappings; repositories
    nothing visible comes from (nothing released, only a retired release, a HEAD flagged released); the private fixture
    org with two releases; a user's repository; MOH's code system and value set, made private after a public release;
    three value sets with stored expansions.
    """
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(indexing())
        super().setUpClass()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = UserProfile.objects.get(username='ocladmin')
        cls.who = OrganizationFactory(mnemonic='WHO')
        cls.sehatapp = OrganizationFactory(mnemonic='sehatapp')
        cls.ciel = OrganizationFactory(mnemonic='CIEL')
        cls.misc = OrganizationFactory(mnemonic='misc')
        cls.fixtures_org = OrganizationFactory(mnemonic='FHIR-Fixtures', public_access=ACCESS_TYPE_NONE)
        cls.member = UserProfileFactory(username='fixture-member')
        cls.outsider = UserProfileFactory(username='outsider')
        cls.fixtures_org.members.add(cls.member)
        cls.jdoe = UserProfileFactory(username='jdoe')

        cls.who_icd = source(cls.who, 'ICD-10-WHO', canonical_url=ICD10, full_name='ICD-10 WHO Edition')
        cls.who_icd_2019 = source(
            cls.who, 'ICD-10-WHO', 'ICD102019', released=True, canonical_url=None, full_name='ICD-10 WHO Edition',
            publisher='World Health Organization', identifier=[HL7_OID])
        cls.sehat_icd = source(cls.sehatapp, 'ICD-10', canonical_url=ICD10, full_name='ICD-10 for Sehat')
        cls.sehat_icd_v1 = source(
            cls.sehatapp, 'ICD-10', 'v1.0', released=True, canonical_url=ICD10, full_name='ICD-10 for Sehat')
        cls.sehat_icd_v2 = source(cls.sehatapp, 'ICD-10', 'v2.0', canonical_url=ICD10)  # newest, unreleased

        cls.ciel_source = source(
            cls.ciel, 'CIEL', canonical_url=CIEL, full_name='Columbia International eHealth Laboratory',
            description='Concepts for clinical care')
        cls.ciel_v1 = source(
            cls.ciel, 'CIEL', 'v1', released=True, retired=True, canonical_url=CIEL,
            full_name='Columbia International eHealth Laboratory', description='Concepts for clinical care')
        cls.ciel_v2 = source(
            cls.ciel, 'CIEL', 'v2', released=True, canonical_url=CIEL,
            full_name='Columbia International eHealth Laboratory', description='Concepts for clinical care')

        # nothing visible comes from these
        cls.draft = source(cls.misc, 'draft', canonical_url='https://example.org/draft')
        cls.draft_v1 = source(cls.misc, 'draft', 'v1', canonical_url='https://example.org/draft')
        cls.gone = source(cls.misc, 'gone', canonical_url='https://example.org/gone')
        cls.gone_v1 = source(
            cls.misc, 'gone', 'v1', released=True, retired=True, canonical_url='https://example.org/gone')
        cls.flagged = source(cls.misc, 'flagged', released=True, canonical_url='https://example.org/flagged')

        cls.fixture = source(cls.fixtures_org, 'fixture-codes', canonical_url=FIXTURE, public_access=ACCESS_TYPE_NONE)
        cls.fixture_v1 = source(
            cls.fixtures_org, 'fixture-codes', '1.0.0', released=True, canonical_url=FIXTURE,
            public_access=ACCESS_TYPE_NONE)
        cls.fixture_v2 = source(
            cls.fixtures_org, 'fixture-codes', '2.0.0', released=True, canonical_url=FIXTURE,
            public_access=ACCESS_TYPE_NONE)

        cls.jdoe_codes = source(cls.jdoe, 'my-codes', canonical_url='https://example.org/my-codes')
        cls.jdoe_codes_v1 = source(
            cls.jdoe, 'my-codes', 'v1', released=True, canonical_url='https://example.org/my-codes')

        # repositories made private after a public release: the release keeps its own public access, but its
        # repository (HEAD) is private, so it's shown only at MOH's base, to MOH's members and staff
        cls.moh = OrganizationFactory(mnemonic='MOH')
        cls.moh_member = UserProfileFactory(username='moh-member')
        cls.moh.members.add(cls.moh_member)
        cls.hiv = source(cls.moh, 'hiv-codes', canonical_url=HIV, public_access=ACCESS_TYPE_NONE)
        cls.hiv_v1 = source(cls.moh, 'hiv-codes', 'v1', released=True, canonical_url=None)  # the repository's

        # the value sets: sehatapp's draws on its ICD-10 v1.0, the CIEL one on CIEL's HEAD; the released version of
        # the ICD-10 one has no canonical of its own
        cls.icd_vs = collection(cls.sehatapp, 'icd10-all', canonical_url=ICD10_VS, full_name='All ICD-10 codes')
        cls.icd_vs_v1 = collection(
            cls.sehatapp, 'icd10-all', 'v1.0', released=True, canonical_url=None, full_name='All ICD-10 codes')
        cls.with_expansion(cls.icd_vs_v1, explicit=[cls.sehat_icd_v1])
        cls.ciel_vs = collection(cls.ciel, 'ciel-set', canonical_url=CIEL_VS)
        cls.ciel_vs_v1 = collection(cls.ciel, 'ciel-set', 'v1', released=True, canonical_url=CIEL_VS)
        cls.with_expansion(cls.ciel_vs_v1, evaluated=[cls.ciel_source])
        cls.ciel_vs_v2 = collection(cls.ciel, 'ciel-set', 'v2', canonical_url=CIEL_VS)  # unreleased
        cls.hiv_vs = collection(cls.moh, 'hiv-set', canonical_url=HIV_VS, public_access=ACCESS_TYPE_NONE)
        cls.hiv_vs_v1 = collection(cls.moh, 'hiv-set', 'v1', released=True, canonical_url=HIV_VS)
        cls.with_expansion(cls.hiv_vs_v1, explicit=[cls.ciel_v2])

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    @staticmethod
    def with_expansion(collection_version, explicit=(), evaluated=()):
        expansion = ExpansionFactory(collection_version=collection_version)
        expansion.explicit_source_versions.set(explicit)
        expansion.evaluated_source_versions.set(evaluated)
        Collection.objects.filter(id=collection_version.id).update(expansion_uri=expansion.uri)

    def get(self, path, user=None, **params):
        client = APIClient()
        if user:
            client.force_authenticate(user)
        return client.get(path, params)

    def read(self, path, user=None, **params):
        response = self.get(path, user, **params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def search(self, resource_type, base='/fhir', user=None, **params):
        response = self.get(f'{base}/{resource_type}/', user, **params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['resourceType'], response.data['type']), ('Bundle', 'searchset'))
        return response.data

    @staticmethod
    def keys(bundle):
        return sorted(f"{entry['resource'].get('url')}|{entry['resource']['version']}"
                      for entry in bundle.get('entry', []))

    @staticmethod
    def ids(bundle):
        return sorted(entry['resource']['id'] for entry in bundle.get('entry', []))

    def assertOutcome(self, response, status, code, tx_issue_type):  # pylint: disable=invalid-name
        self.assertEqual(response.status_code, status, response.data)
        self.assertEqual(response.data['resourceType'], 'OperationOutcome')
        issue = response.data['issue'][0]
        self.assertEqual(issue['code'], code)
        self.assertIn({'system': TX_ISSUE_TYPE_SYSTEM, 'code': tx_issue_type}, issue['details']['coding'])
        return issue

    def assertNotFound(self, path, user=None, **params):  # pylint: disable=invalid-name
        response = self.get(path, user, **params)
        self.assertOutcome(response, 404, 'not-found', 'not-found')


@tag('es')
class CodeSystemSearchTest(SearchCorpusMixin, OCLTestCase):  # pylint: disable=too-many-public-methods
    PUBLIC_RELEASES = sorted([
        f'{CIEL}|v1', f'{CIEL}|v2', f'{ICD10}|ICD102019', f'{ICD10}|v1.0', 'https://example.org/my-codes|v1'])

    def test_global_search_lists_every_released_public_version_with_an_opaque_id(self):
        bundle = self.search('CodeSystem')

        # never HEAD, an unreleased version, a repository with nothing (or only a retired release) released, or a
        # private one; a retired release of a repository with a live one is listed
        self.assertEqual(self.keys(bundle), self.PUBLIC_RELEASES)
        self.assertEqual(bundle['total'], 5)
        versions = [self.ciel_v1, self.ciel_v2, self.who_icd_2019, self.sehat_icd_v1, self.jdoe_codes_v1]
        self.assertEqual(self.ids(bundle), sorted(str(version.id) for version in versions))
        for entry in bundle['entry']:
            self.assertEqual(entry['fullUrl'], f"http://testserver/fhir/CodeSystem/{entry['resource']['id']}")
            self.assertEqual(entry['search'], {'mode': 'match'})
        self.assertEqual(bundle['link'][0]['relation'], 'self')

    def test_global_base_is_public_only_whoever_asks(self):
        for user in (self.member, self.admin, self.jdoe):
            self.assertEqual(self.keys(self.search('CodeSystem', user=user)), self.PUBLIC_RELEASES, user.username)

    def test_a_version_with_its_own_canonical_is_found_by_that_one_only(self):
        """The repository's (HEAD's) canonical stands in only for a version with none, as CanonicalUrlField shows it."""
        old = 'https://old.example.org/ciel'
        Source.objects.filter(id=self.ciel_v1.id).update(canonical_url=old)
        ConceptFactory(parent=self.ciel_source, mnemonic='1427', sources=[self.ciel_v1, self.ciel_v2])

        self.assertEqual(self.keys(self.search('CodeSystem', url=CIEL)), [f'{CIEL}|v2'])
        self.assertEqual(self.keys(self.search('CodeSystem', url=old)), [f'{old}|v1'])
        self.assertEqual(self.keys(self.search('CodeSystem', system=old)), [f'{old}|v1'])
        self.assertEqual(self.keys(self.search('CodeSystem', code=f'{CIEL}|1427')), [f'{CIEL}|v2'])
        self.assertEqual(self.keys(self.search('CodeSystem', code=f'{old}|1427')), [f'{old}|v1'])
        Source.objects.filter(id=self.ciel_v1.id).update(canonical_url='')  # an empty one is none
        self.assertEqual(self.keys(self.search('CodeSystem', url=CIEL)), [f'{CIEL}|v1', f'{CIEL}|v2'])

    def test_url_matches_the_version_canonical_or_its_repository_canonical(self):
        bundle = self.search('CodeSystem', url=ICD10)

        self.assertEqual(self.keys(bundle), [f'{ICD10}|ICD102019', f'{ICD10}|v1.0'])
        who = next(entry['resource'] for entry in bundle['entry'] if entry['resource']['version'] == 'ICD102019')
        self.assertEqual(who['url'], ICD10)  # the repository's canonical, as the version has none
        self.assertEqual(self.keys(self.search('CodeSystem', system=ICD10)), self.keys(bundle))

        bundle = self.search('CodeSystem', url=ICD10, version='ICD102019')
        self.assertEqual((self.keys(bundle), bundle['total']), ([f'{ICD10}|ICD102019'], 1))
        self.assertEqual(self.search('CodeSystem', url='http://example.org/unknown')['total'], 0)
        self.assertNotIn('entry', self.search('CodeSystem', url='http://example.org/unknown'))

    def test_id_is_the_opaque_id_at_the_global_base_and_the_mnemonic_at_an_owner_base(self):
        bundle = self.search('CodeSystem', _id=str(self.sehat_icd_v1.id))
        self.assertEqual(self.keys(bundle), [f'{ICD10}|v1.0'])
        for missing in ('ICD-10', str(self.sehat_icd.id), str(self.sehat_icd_v2.id), 'ocl-no-such-id'):
            self.assertEqual(self.search('CodeSystem', _id=missing)['total'], 0, missing)

        bundle = self.search('CodeSystem', base='/orgs/CIEL', _id='CIEL')
        self.assertEqual(self.keys(bundle), [f'{CIEL}|v1', f'{CIEL}|v2'])
        self.assertEqual(self.ids(bundle), ['CIEL', 'CIEL'])
        self.assertEqual(self.search('CodeSystem', base='/orgs/CIEL', _id=str(self.ciel_v2.id))['total'], 0)

    def test_string_parameters_and_their_modifiers(self):
        # no modifier: starts with, ignoring case
        self.assertEqual(self.keys(self.search('CodeSystem', title='icd-10')), [f'{ICD10}|ICD102019', f'{ICD10}|v1.0'])
        self.assertEqual(self.keys(self.search('CodeSystem', name='ciel')), [f'{CIEL}|v1', f'{CIEL}|v2'])
        self.assertEqual(self.search('CodeSystem', title='WHO')['total'], 0)
        # :exact is the whole value, case included
        self.assertEqual(
            self.keys(self.search('CodeSystem', **{'title:exact': 'ICD-10 WHO Edition'})), [f'{ICD10}|ICD102019'])
        self.assertEqual(self.search('CodeSystem', **{'title:exact': 'icd-10 who edition'})['total'], 0)
        self.assertEqual(self.search('CodeSystem', **{'title:exact': 'ICD-10'})['total'], 0)
        # :contains anywhere, ignoring case
        self.assertEqual(self.keys(self.search('CodeSystem', **{'title:contains': 'who'})), [f'{ICD10}|ICD102019'])
        self.assertEqual(
            self.keys(self.search('CodeSystem', **{'description:contains': 'CLINICAL'})), [f'{CIEL}|v1', f'{CIEL}|v2'])
        self.assertEqual(
            self.keys(self.search('CodeSystem', publisher='world health')), [f'{ICD10}|ICD102019'])
        self.assertEqual(self.keys(self.search('CodeSystem', **{'name:exact': 'CIEL'})), [f'{CIEL}|v1', f'{CIEL}|v2'])

    def test_strings_ignore_accents_except_exact(self):
        Source.objects.filter(id=self.ciel_v2.id).update(full_name='Édition française')

        self.assertEqual(self.keys(self.search('CodeSystem', title='edition')), [f'{CIEL}|v2'])
        self.assertEqual(self.keys(self.search('CodeSystem', title='ÉDITION')), [f'{CIEL}|v2'])
        self.assertEqual(self.keys(self.search('CodeSystem', **{'title:contains': 'francaise'})), [f'{CIEL}|v2'])
        self.assertEqual(self.keys(self.search('CodeSystem', **{'title:contains': 'FRANÇAISE'})), [f'{CIEL}|v2'])
        self.assertEqual(self.keys(self.search('CodeSystem', **{'title:exact': 'Édition française'})), [f'{CIEL}|v2'])
        self.assertEqual(self.search('CodeSystem', **{'title:exact': 'Edition francaise'})['total'], 0)
        for wildcard in ('%', '_', 'É_ition'):  # LIKE's wildcards are plain characters
            self.assertEqual(self.search('CodeSystem', **{'title:contains': wildcard})['total'], 0, wildcard)

    def test_a_comma_is_or_and_a_repeated_parameter_is_and(self):
        self.assertEqual(
            self.keys(self.search('CodeSystem', version='v1,v1.0')),
            sorted([f'{CIEL}|v1', f'{ICD10}|v1.0', 'https://example.org/my-codes|v1']))
        response = self.client.get('/fhir/CodeSystem/?name=ci&name=ICD')
        self.assertEqual(response.data['total'], 0)
        response = self.client.get('/fhir/CodeSystem/?name=ci&name=CIEL')
        self.assertEqual(self.keys(response.data), [f'{CIEL}|v1', f'{CIEL}|v2'])
        # an escaped comma is part of the value
        Source.objects.filter(id=self.ciel_v2.id).update(full_name='Columbia, International')
        self.assertEqual(self.keys(self.search('CodeSystem', title=r'Columbia\, Int')), [f'{CIEL}|v2'])

    def test_status(self):
        self.assertEqual(
            self.keys(self.search('CodeSystem', status='active')),
            sorted([f'{CIEL}|v2', f'{ICD10}|ICD102019', f'{ICD10}|v1.0', 'https://example.org/my-codes|v1']))
        self.assertEqual(self.keys(self.search('CodeSystem', status='retired')), [f'{CIEL}|v1'])
        self.assertEqual(self.search('CodeSystem', status='draft')['total'], 0)
        self.assertEqual(self.search('CodeSystem', status='unknown')['total'], 0)
        self.assertEqual(self.search('CodeSystem', status='retired,active')['total'], 5)

    def test_identifier(self):
        for token in ('urn:ietf:rfc:3986|urn:oid:2.16.840.1.113883.6.90', 'urn:oid:2.16.840.1.113883.6.90',
                      'urn:ietf:rfc:3986|'):
            self.assertEqual(self.keys(self.search('CodeSystem', identifier=token)), [f'{ICD10}|ICD102019'], token)
        for token in ('urn:ietf:rfc:3986|urn:oid:1.2.3', 'http://example.org|urn:oid:2.16.840.1.113883.6.90'):
            self.assertEqual(self.search('CodeSystem', identifier=token)['total'], 0, token)

        # the OCL accession identifier every resource carries
        bundle = self.search('CodeSystem', identifier='/orgs/sehatapp/CodeSystem/ICD-10/')
        self.assertEqual(self.keys(bundle), [f'{ICD10}|v1.0'])
        accession = bundle['entry'][0]['resource']['identifier'][0]
        self.assertEqual(
            self.keys(self.search('CodeSystem', identifier=f"{accession['system']}|{accession['value']}")),
            [f'{ICD10}|v1.0'])
        self.assertEqual(self.search('CodeSystem', identifier='/orgs/sehatapp/ValueSet/ICD-10/')['total'], 0)
        self.assertEqual(
            self.search('CodeSystem', identifier='/orgs/FHIR-Fixtures/CodeSystem/fixture-codes/')['total'], 0)

    def test_a_stored_accession_without_a_value_leaves_the_generated_one(self):
        """IdentifierSerializer.find_ocl_identifier needs an accession value; without one the generated id shows."""
        api = settings.API_BASE_URL
        for stored in ([{'system': 'urn:external', 'type': ACSN}],
                       [{'system': 'urn:external', 'value': '', 'type': ACSN}],
                       [{'system': 'urn:external', 'value': None, 'type': ACSN}]):
            Source.objects.filter(id=self.who_icd_2019.id).update(identifier=stored)
            resource = self.search('CodeSystem', url=ICD10, version='ICD102019')['entry'][0]['resource']
            self.assertIn({'system': api, 'value': '/orgs/WHO/CodeSystem/ICD-10-WHO/'},
                          [{'system': item.get('system'), 'value': item.get('value')}
                           for item in resource['identifier']], stored)
            for token in (f'{api}|', '/orgs/WHO/CodeSystem/ICD-10-WHO/', f'{api}|/orgs/WHO/CodeSystem/ICD-10-WHO/'):
                self.assertIn(f'{ICD10}|ICD102019', self.keys(self.search('CodeSystem', identifier=token)), token)

        # with a value, the stored one is shown instead; a coding's missing code and system show ACSN's defaults
        path = '/orgs/WHO/CodeSystem/ICD-10-WHO/'
        for coding in (ACSN['coding'][0], {}, {'code': 'ACSN'}):
            Source.objects.filter(id=self.who_icd_2019.id).update(
                identifier=[{'system': 'urn:external', 'value': path, 'type': {'coding': [coding]}}])
            self.assertNotIn(f'{ICD10}|ICD102019', self.keys(self.search('CodeSystem', identifier=f'{api}|')), coding)
            self.assertEqual(
                self.keys(self.search('CodeSystem', identifier=f'urn:external|{path}')), [f'{ICD10}|ICD102019'])
        # another type, or a coding whose code or system is null, is no accession
        for coding in ({'code': 'MR'}, {'code': None}, {'code': 'ACSN', 'system': None}):
            Source.objects.filter(id=self.who_icd_2019.id).update(
                identifier=[{'system': 'urn:external', 'value': path, 'type': {'coding': [coding]}}])
            self.assertIn(f'{ICD10}|ICD102019', self.keys(self.search('CodeSystem', identifier=f'{api}|')), coding)

    def test_identifier_system_is_matched_on_the_same_identifier(self):
        """
        |value is an identifier without a system; system| is any value of that system. Both are checked on one
        identifier, as it's shown: a stored identifier without a system key shows API_BASE_URL's, a null or empty one
        none; the generated accession identifier (API_BASE_URL) shows when no accession identifier is stored.
        """
        oid = 'urn:oid:2.16.840.1.113883.6.90'
        Source.objects.filter(id=self.ciel_v2.id).update(identifier=[{'system': None, 'value': oid}])
        Source.objects.filter(id=self.sehat_icd_v1.id).update(identifier=[{'system': '', 'value': oid}])
        Source.objects.filter(id=self.ciel_v1.id).update(identifier=[{'value': oid}, {'system': 'urn:x', 'value': 'y'}])
        Source.objects.filter(id=self.jdoe_codes_v1.id).update(identifier=[
            {'system': 'https://fhir.openconceptlab.org', 'value': '/users/jdoe/CodeSystem/my-codes/', 'type': ACSN}])
        api = settings.API_BASE_URL

        self.assertEqual(
            self.keys(self.search('CodeSystem', identifier=f'|{oid}')), sorted([f'{CIEL}|v2', f'{ICD10}|v1.0']))
        self.assertEqual(self.keys(self.search('CodeSystem', identifier=oid)), sorted(
            [f'{CIEL}|v1', f'{CIEL}|v2', f'{ICD10}|ICD102019', f'{ICD10}|v1.0']))
        self.assertEqual(self.keys(self.search('CodeSystem', identifier=f'{api}|{oid}')), [f'{CIEL}|v1'])
        self.assertEqual(self.search('CodeSystem', identifier=f'urn:x|{oid}')['total'], 0)  # not the same identifier
        self.assertEqual(self.search('CodeSystem', identifier='|/orgs/sehatapp/CodeSystem/ICD-10/')['total'], 0)
        self.assertEqual(self.search('CodeSystem', identifier='|')['total'], 0)

        # system|: the generated accession identifiers too, for a repository with no stored identifier at all
        Source.objects.filter(id=self.who_icd_2019.id).update(identifier={})
        bundle = self.search('CodeSystem', identifier=f'{api}|')
        self.assertEqual(
            self.keys(bundle), sorted([f'{CIEL}|v1', f'{CIEL}|v2', f'{ICD10}|ICD102019', f'{ICD10}|v1.0']))
        for entry in bundle['entry']:
            self.assertIn(api, [identifier.get('system') for identifier in entry['resource']['identifier']])
        # a stored accession identifier is shown instead of the generated one
        self.assertEqual(
            self.keys(self.search('CodeSystem', identifier='https://fhir.openconceptlab.org|')),
            ['https://example.org/my-codes|v1'])
        self.assertEqual(self.search('CodeSystem', identifier=f'{api}|/users/jdoe/CodeSystem/my-codes/')['total'], 0)
        self.assertEqual(
            self.keys(self.search('CodeSystem', identifier='/users/jdoe/CodeSystem/my-codes/')),
            ['https://example.org/my-codes|v1'])
        self.assertEqual(self.keys(self.search('CodeSystem', identifier='urn:x|')), [f'{CIEL}|v1'])

        # identifiers that aren't a list aren't shown, so they aren't found either
        Source.objects.filter(id=self.ciel_v1.id).update(identifier={'system': 'urn:x', 'value': 'y'})
        self.assertEqual(self.search('CodeSystem', identifier='urn:x|')['total'], 0)

    def test_language_system_only_needs_a_language(self):
        Source.objects.filter(id=self.ciel_v2.id).update(default_locale='')
        self.assertEqual(self.keys(self.search('CodeSystem', language='urn:ietf:bcp:47|')), sorted(
            [f'{CIEL}|v1', f'{ICD10}|ICD102019', f'{ICD10}|v1.0', 'https://example.org/my-codes|v1']))

    def test_code_needs_a_concept_the_read_lists(self):
        """
        code searches concept.code, so code=<system>| is a release of that code system with a concept, and code=<code> a
        release defining it. A concept counts as the read lists it: linked and not deleted; a retired one counts.
        """
        ConceptFactory(parent=self.who_icd, mnemonic='A00', retired=True, sources=[self.who_icd_2019])
        ConceptFactory(parent=self.ciel_source, mnemonic='gone', is_active=False, sources=[self.ciel_v1])

        self.assertEqual(self.keys(self.search('CodeSystem', code=f'{ICD10}|')), [f'{ICD10}|ICD102019'])
        self.assertEqual(self.keys(self.search('CodeSystem', code=f'{ICD10}|A00')), [f'{ICD10}|ICD102019'])
        self.assertEqual(self.search('CodeSystem', code=f'{CIEL}|')['total'], 0)
        self.assertEqual(self.search('CodeSystem', code='gone')['total'], 0)

    def test_token_parameters_take_their_system(self):
        """
        R4 tokens: code, system|code, or |code (no system). status's system is publication-status, language's BCP 47,
        code's the code system itself; version and _id have none, so |v is v, and any system finds nothing.
        """
        status, bcp47 = 'http://hl7.org/fhir/publication-status', 'urn:ietf:bcp:47'
        Source.objects.filter(id=self.ciel_v2.id).update(default_locale='fr')
        Source.objects.filter(id=self.ciel_v1.id).update(version='v1|a,b')
        ConceptFactory(parent=self.ciel_source, mnemonic='1427', sources=[self.ciel_v2])
        ConceptFactory(parent=self.sehat_icd, mnemonic='1427', sources=[self.sehat_icd_v1])
        active = sorted([f'{CIEL}|v2', f'{ICD10}|ICD102019', f'{ICD10}|v1.0', 'https://example.org/my-codes|v1'])
        ciel_v2, icd_v1, ciel_v1 = [f'{CIEL}|v2'], [f'{ICD10}|v1.0'], [f'{CIEL}|v1|a,b']
        pk = str(self.sehat_icd_v1.id)
        cases = [
            ('status', 'active', active), ('status', f'{status}|active', active),
            ('status', f'{status}|retired', ciel_v1),
            ('status', f'{status}|active,{status}|retired', self.keys(self.search('CodeSystem'))),
            ('status', '|active', []), ('status', 'http://example.org/other|active', []),
            ('status', f'{status}|', self.keys(self.search('CodeSystem'))), ('status', 'http://example.org/other|', []),
            ('language', f'{bcp47}|', self.keys(self.search('CodeSystem'))), ('language', 'urn:other|', []),
            ('code', f'{CIEL}|', ciel_v2), ('code', f'{ICD10}|', icd_v1),  # releases with concepts only
            ('code', 'http://example.org/other|', []), ('version', 'urn:x|', []), ('_id', 'urn:x|', []),
            ('language', 'fr', ciel_v2), ('language', f'{bcp47}|fr', ciel_v2), ('language', '|fr', []),
            ('language', 'urn:other|fr', []),
            ('code', '1427', sorted(ciel_v2 + icd_v1)), ('code', f'{CIEL}|1427', ciel_v2),
            ('code', f'{ICD10}|1427', icd_v1),
            ('code', f'{CIEL}|1427,{ICD10}|1427', sorted(ciel_v2 + icd_v1)), ('code', '|1427', []),
            ('code', 'http://example.org/other|1427', []), ('code', f'{CIEL}|A00', []),
            ('version', 'v1.0', icd_v1), ('version', '|v1.0', icd_v1), ('version', 'urn:x|v1.0', []),
            ('version', r'v1\|a\,b', ciel_v1), ('version', r'|v1\|a\,b', ciel_v1), ('version', 'v1|a', []),
            ('_id', pk, icd_v1), ('_id', f'|{pk}', icd_v1), ('_id', f'urn:x|{pk}', []),
        ]
        for name, value, keys in cases:
            self.assertEqual(self.keys(self.search('CodeSystem', **{name: value})), keys, f'{name}={value}')
        owner = [('_id', 'CIEL', 2), ('_id', '|CIEL', 2), ('_id', 'urn:x|CIEL', 0)]
        for name, value, total in owner:
            self.assertEqual(self.search('CodeSystem', base='/orgs/CIEL', **{name: value})['total'], total, value)

    def test_last_updated_and_its_prefixes(self):
        Source.objects.filter(id=self.ciel_v2.id).update(updated_at=at(2020))
        Source.objects.filter(id=self.sehat_icd_v1.id).update(updated_at=at(2022))
        pinned = {'url': CIEL, 'version': 'v2'}
        for value, found in (('2020', True), ('eq2020-06', True), ('2020-06-15', True), ('2020-06-16', False),
                             ('gt2019', True), ('gt2020', False), ('sa2019-12-31', True), ('ge2020-06-15', True),
                             ('ge2020-06-16', False), ('lt2021', True), ('lt2020-06-15', False), ('eb2020-06-16', True),
                             ('le2020-06-15', True), ('le2020-06-14', False), ('ne2020', False), ('ne2021', True),
                             ('ge2020-06-15T12:00:00Z', True), ('gt2020-06-15T12:00:00Z', False),
                             ('lt2020-06-15T13:00:00+01:00', False), ('le2020-06-15T13:00+01:00', True)):
            self.assertEqual(self.search('CodeSystem', _lastUpdated=value, **pinned)['total'], int(found), value)

        response = self.client.get('/fhir/CodeSystem/?_lastUpdated=ge2020&_lastUpdated=lt2021')
        self.assertEqual(self.keys(response.data), [f'{CIEL}|v2'])
        self.assertEqual(
            self.keys(self.search('CodeSystem', _lastUpdated='2020,2022')), sorted([f'{CIEL}|v2', f'{ICD10}|v1.0']))

    def test_last_updated_at_the_end_of_time(self):
        """A value in year 9999 has no end to build (year 10000): its range runs on unbounded."""
        for value, total in (('lt9999', 5), ('eb9999', 5), ('le9999', 5), ('le9999-12', 5), ('le9999-12-31', 5),
                             ('le9999-12-31T23:59:59Z', 5), ('le9999-12-31T23:59:59.999999Z', 5), ('ne9999-12', 5),
                             ('9999', 0), ('9999-12', 0), ('9999-12-31', 0), ('gt9999', 0), ('sa9999-12', 0),
                             ('gt9999-12-31', 0), ('ge9999-12-31', 0), ('gt9999-12-31T23:59:59Z', 0)):
            self.assertEqual(self.search('CodeSystem', _lastUpdated=value)['total'], total, value)
        self.assertEqual(self.search('CodeSystem', _lastUpdated='le9999,lt2000')['total'], 5)  # an alternative too

    def test_unsupported_parameters_and_values_are_400_outcomes(self):
        for query in ('unsupported_parameter=1', 'q=CIEL', 'limit=10', 'name:missing=true', 'url:below=http://x',
                      '_sort=name', 'reference=http://x', 'source-system=http://x'):
            issue = self.assertOutcome(self.client.get(f'/fhir/CodeSystem/?{query}'), 400, 'not-supported',
                                       'not-supported')
            self.assertIn(query.split('=', maxsplit=1)[0].split(':')[0], issue['details']['text'], query)
        for query in ('_count=-1', '_count=ten', '_count=%205', '_lastUpdated=yesterday', '_lastUpdated=ap2020',
                      '_lastUpdated=2020-13', '_lastUpdated=2020-06-15T12:00%2B25:00', 'page=0'):
            self.assertOutcome(self.client.get(f'/fhir/CodeSystem/?{query}'), 400, 'invalid', 'invalid-data')
        # the format parameters choose the response, they aren't search input
        self.assertEqual(self.search('CodeSystem', _format='json', _pretty='true')['total'], 5)
        self.assertEqual(self.search('CodeSystem', name='')['total'], 5)  # an empty value is no criterion

    def test_count_paging_total_and_links(self):
        bundle = self.search('CodeSystem', _count='2')
        self.assertEqual((len(bundle['entry']), bundle['total']), (2, 5))
        links = {link['relation']: link['url'] for link in bundle['link']}
        self.assertEqual(set(links), {'self', 'next'})
        query = parse_qs(urlsplit(links['next']).query)
        self.assertEqual((query['page'], query['_count']), (['2'], ['2']))
        self.assertTrue(links['next'].startswith('http://testserver/fhir/CodeSystem/?'))

        page_2 = self.get(links['next']).data
        self.assertEqual({link['relation'] for link in page_2['link']}, {'self', 'next', 'previous'})
        page_3 = self.search('CodeSystem', _count='2', page='3')
        self.assertEqual({link['relation'] for link in page_3['link']}, {'self', 'previous'})
        seen = [entry['resource']['id'] for page in (bundle, page_2, page_3) for entry in page['entry']]
        self.assertEqual(sorted(seen), self.ids(self.search('CodeSystem')))  # every version once, in a stable order

        bundle = self.search('CodeSystem', _count='0')
        self.assertEqual(bundle['total'], 5)
        self.assertNotIn('entry', bundle)

        with override_settings(FHIR_SEARCH_MAX_COUNT=3):
            bundle = self.search('CodeSystem', _count='1000')
            self.assertEqual((len(bundle['entry']), bundle['total']), (3, 5))
            self.assertEqual(len(self.search('CodeSystem')['entry']), 3)  # the default is the maximum

    def test_entries_are_summaries_marked_subsetted(self):
        ConceptFactory(parent=self.ciel_source, sources=[self.ciel_v2])
        for entry in self.search('CodeSystem')['entry']:
            resource = entry['resource']
            self.assertNotIn('concept', resource)
            self.assertNotIn('count', resource)
            self.assertEqual(resource['content'], 'complete')
            self.assertIn(SUBSETTED, resource['meta']['tag'])
            self.assertIn('lastUpdated', resource['meta'])

        with override_settings(FHIR_CODE_SYSTEM_CONCEPT_LIMIT=0):
            contents = {entry['resource']['version']: entry['resource']['content']
                        for entry in self.search('CodeSystem', url=CIEL)['entry']}
        self.assertEqual(contents, {'v1': 'complete', 'v2': 'not-present'})

    def test_owner_base_search(self):
        bundle = self.search('CodeSystem', base='/orgs/CIEL')
        self.assertEqual(self.keys(bundle), [f'{CIEL}|v1', f'{CIEL}|v2'])
        for entry in bundle['entry']:
            self.assertEqual(entry['resource']['id'], 'CIEL')
            self.assertEqual(entry['fullUrl'], 'http://testserver/orgs/CIEL/CodeSystem/CIEL')

        self.assertEqual(self.keys(self.search('CodeSystem', base='/users/jdoe')), ['https://example.org/my-codes|v1'])
        self.assertEqual(self.search('CodeSystem', base='/orgs/nobody')['total'], 0)
        self.assertEqual(self.search('CodeSystem', base='/orgs/misc')['total'], 0)

        # a private owner's repositories: to its members and staff only
        fixture_releases = [f'{FIXTURE}|1.0.0', f'{FIXTURE}|2.0.0']
        for user, keys in ((None, []), (self.outsider, []), (self.member, fixture_releases),
                           (self.admin, fixture_releases)):
            self.assertEqual(
                self.keys(self.search('CodeSystem', base='/orgs/FHIR-Fixtures', user=user)), keys, user)
        bundle = self.search('CodeSystem', base='/orgs/FHIR-Fixtures', user=self.member, url=FIXTURE, version='1.0.0')
        self.assertEqual(self.ids(bundle), ['fixture-codes'])

    def test_no_query_per_entry(self):
        def count_queries(expected, **params):
            with self.assertNumQueries(expected):
                return self.client.get('/fhir/CodeSystem/', params).data

        # the count and the page, its summaries included (and the url's repositories)
        one = count_queries(3, url=CIEL, version='v2')
        every = count_queries(2)
        self.assertEqual((one['total'], every['total']), (1, 5))
        with self.assertNumQueries(3):  # + the owner
            self.client.get('/orgs/CIEL/CodeSystem/')

    def test_releases_without_a_canonical_url_of_their_own_add_no_query(self):
        """Each shows its repository's canonical URL from the search's annotation, not from a query of its own."""
        with CaptureQueriesContext(connection) as before:
            total = self.client.get('/fhir/CodeSystem/').data['total']
        org = OrganizationFactory(mnemonic='Unnamed-Releases')
        source(org, 'codes', canonical_url='https://example.org/unnamed-releases')
        for version in ('v1', 'v2', 'v3'):
            source(org, 'codes', version, released=True, canonical_url=None)
        with self.assertNumQueries(len(before.captured_queries)):
            bundle = self.client.get('/fhir/CodeSystem/').data
        self.assertEqual(bundle['total'], total + 3)
        unnamed = {f'https://example.org/unnamed-releases|{version}' for version in ('v1', 'v2', 'v3')}
        self.assertLessEqual(unnamed, set(self.keys(bundle)))


@tag('es')
class ValueSetSearchTest(SearchCorpusMixin, OCLTestCase):
    def test_global_search_and_the_repository_canonical(self):
        bundle = self.search('ValueSet')

        self.assertEqual(self.keys(bundle), [f'{ICD10_VS}|v1.0', f'{CIEL_VS}|v1'])
        self.assertEqual(self.ids(bundle), sorted([str(self.icd_vs_v1.id), str(self.ciel_vs_v1.id)]))
        self.assertEqual(self.keys(self.search('ValueSet', url=ICD10_VS)), [f'{ICD10_VS}|v1.0'])
        for entry in bundle['entry']:
            self.assertEqual(entry['fullUrl'], f"http://testserver/fhir/ValueSet/{entry['resource']['id']}")
            self.assertNotIn('compose', entry['resource'])
            self.assertIn(SUBSETTED, entry['resource']['meta']['tag'])

    def test_reference_is_a_code_system_the_stored_expansion_draws_on(self):
        self.assertEqual(self.keys(self.search('ValueSet', reference=ICD10)), [f'{ICD10_VS}|v1.0'])
        self.assertEqual(self.keys(self.search('ValueSet', reference=CIEL)), [f'{CIEL_VS}|v1'])
        self.assertEqual(self.search('ValueSet', reference='http://loinc.org')['total'], 0)

    def test_owner_base(self):
        bundle = self.search('ValueSet', base='/orgs/CIEL')
        self.assertEqual((self.keys(bundle), self.ids(bundle)), ([f'{CIEL_VS}|v1'], ['ciel-set']))

    def test_unsupported(self):
        self.assertOutcome(self.client.get('/fhir/ValueSet/?system=http://x'), 400, 'not-supported', 'not-supported')

    def test_status_takes_its_system(self):
        for resource_type in ('ValueSet', 'ConceptMap'):
            bare = self.search(resource_type, status='active')['total']
            self.assertGreater(bare, 0)
            qualified = self.search(resource_type, status='http://hl7.org/fhir/publication-status|active')
            self.assertEqual(qualified['total'], bare, resource_type)
            self.assertEqual(self.search(resource_type, status='urn:x|active')['total'], 0, resource_type)

    def test_no_query_per_entry(self):
        with self.assertNumQueries(2):
            self.client.get('/fhir/ValueSet/')


@tag('es')
class ConceptMapSearchTest(SearchCorpusMixin, OCLTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        icd_code = ConceptFactory(parent=cls.sehat_icd, mnemonic='A00')
        ciel_code = ConceptFactory(parent=cls.ciel_source, mnemonic='1427')
        MappingFactory(
            parent=cls.ciel_source, from_concept=ciel_code, to_concept=icd_code, from_source=cls.ciel_source,
            to_source=cls.sehat_icd, from_concept_code='1427', to_concept_code='A00', sources=[cls.ciel_v2])

    def test_source_and_target(self):
        everything = self.search('ConceptMap')
        self.assertEqual(self.keys(everything), CodeSystemSearchTest.PUBLIC_RELEASES)
        for entry in everything['entry']:
            self.assertNotIn('group', entry['resource'])
            self.assertEqual(entry['fullUrl'], f"http://testserver/fhir/ConceptMap/{entry['resource']['id']}")

        for name in ('source-system', 'source-uri'):
            self.assertEqual(self.keys(self.search('ConceptMap', **{name: CIEL})), [f'{CIEL}|v2'], name)
            self.assertEqual(self.search('ConceptMap', **{name: ICD10})['total'], 0, name)
        for name in ('target-system', 'target-uri'):
            self.assertEqual(self.keys(self.search('ConceptMap', **{name: ICD10})), [f'{CIEL}|v2'], name)
            self.assertEqual(self.search('ConceptMap', **{name: CIEL})['total'], 0, name)

    def test_source_system_is_the_mappings_source_as_the_read_shows_it(self):
        """
        group.source is each live mapping's from_source canonical, or for a source without one its OCL path as a
        ConceptMap; group.target the same for to_source. A container's own canonical isn't its source system.
        """
        uncanonical = source(self.misc, 'local-codes')
        local = ConceptFactory(parent=uncanonical, mnemonic='L1')
        icd_code = ConceptFactory(parent=self.sehat_icd, mnemonic='B01')
        mine = ConceptFactory(parent=self.jdoe_codes, mnemonic='m1')
        for from_source, from_concept in ((self.sehat_icd, icd_code), (uncanonical, local)):
            MappingFactory(
                parent=self.jdoe_codes, from_concept=from_concept, to_concept=mine, from_source=from_source,
                to_source=self.jdoe_codes, from_source_url=from_source.uri, to_source_url=self.jdoe_codes.uri,
                sources=[self.jdoe_codes_v1])
        my_codes = 'https://example.org/my-codes|v1'
        groups = self.read(f'/fhir/ConceptMap/{self.jdoe_codes_v1.id}')['group']
        self.assertEqual(
            sorted(group['source'] for group in groups), sorted([ICD10, '/orgs/misc/ConceptMap/local-codes/']))

        for name in ('source-system', 'source-uri'):
            self.assertEqual(self.keys(self.search('ConceptMap', **{name: ICD10})), [my_codes], name)
            self.assertEqual(
                self.keys(self.search('ConceptMap', **{name: '/orgs/misc/ConceptMap/local-codes/'})), [my_codes], name)
            self.assertEqual(self.search('ConceptMap', **{name: 'https://example.org/my-codes'})['total'], 0, name)
            self.assertEqual(self.keys(self.search('ConceptMap', **{name: CIEL})), [f'{CIEL}|v2'], name)
        self.assertEqual(
            self.keys(self.search('ConceptMap', **{'target-system': 'https://example.org/my-codes'})), [my_codes])
        # a target with a canonical is shown by it, not by its OCL path
        self.assertEqual(self.search('ConceptMap', **{'target-system': '/users/jdoe/ConceptMap/my-codes/'})['total'], 0)

    def test_retired_mappings_are_no_part_of_a_concept_map(self):
        """A read leaves retired mappings out (ConceptMapGroupField), so source and target searches don't count them."""
        loinc = source(self.misc, 'LOINC', canonical_url='http://loinc.org')
        loinc_code = ConceptFactory(parent=loinc, mnemonic='1234-5')
        MappingFactory(
            parent=self.ciel_source, from_concept=ConceptFactory(parent=self.ciel_source), to_concept=loinc_code,
            from_source=self.ciel_source, to_source=loinc, to_concept_code='1234-5', retired=True,
            sources=[self.ciel_v2])
        jdoe_code = ConceptFactory(parent=self.jdoe_codes, mnemonic='x1')
        retired = MappingFactory(
            parent=self.jdoe_codes, from_concept=jdoe_code, to_concept=loinc_code, from_source=self.jdoe_codes,
            to_source=loinc, retired=True, sources=[self.jdoe_codes_v1])

        for name in ('target-system', 'target-uri'):
            self.assertEqual(self.search('ConceptMap', **{name: 'http://loinc.org'})['total'], 0, name)
            self.assertEqual(self.keys(self.search('ConceptMap', **{name: ICD10})), [f'{CIEL}|v2'], name)
        resource = self.read(f'/fhir/ConceptMap/{self.ciel_v2.id}')
        self.assertEqual([group['target'] for group in resource['group']], [ICD10])
        for name in ('source-system', 'source-uri'):
            self.assertEqual(self.search('ConceptMap', **{name: 'https://example.org/my-codes'})['total'], 0, name)

        type(retired).objects.filter(id=retired.id).update(retired=False)
        self.assertEqual(
            self.keys(self.search('ConceptMap', **{'source-system': 'https://example.org/my-codes'})),
            ['https://example.org/my-codes|v1'])
        self.assertEqual(self.keys(self.search('ConceptMap', **{'target-system': 'http://loinc.org'})),
                         ['https://example.org/my-codes|v1'])

    def test_no_query_per_entry(self):
        with self.assertNumQueries(3):  # the sources with the target's canonical, the matches' ids, the page
            self.assertEqual(self.client.get('/fhir/ConceptMap/', {'target-system': ICD10}).data['total'], 1)


@tag('es')
class ReferencedSourceAccessTest(SearchCorpusMixin, OCLTestCase):
    """
    source-system, target-system (and their -uri forms) and reference look a canonical up only among the sources the
    base shows the caller: public ones at /fhir/; at an owner base also, whoever owns them, those the caller may view.
    A mapping whose source is another one is matched only by the URL it stores. So the canonical of a source the caller
    isn't shown answers exactly as one no source has: the same total, the same queries.
    """
    PRIVATE = 'http://moh.example/fhir/CodeSystem/private'
    UNKNOWN = 'http://moh.example/fhir/CodeSystem/unknown'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.private = source(cls.moh, 'private-codes', canonical_url=cls.PRIVATE, public_access=ACCESS_TYPE_NONE)
        cls.private_v1 = source(cls.moh, 'private-codes', 'v1', released=True, canonical_url=cls.PRIVATE,
                                public_access=ACCESS_TYPE_NONE)
        cls.private_code = ConceptFactory(parent=cls.private, mnemonic='P1')
        for _ in range(3):
            MappingFactory(parent=cls.private, from_source=cls.private, to_source=cls.private,
                           from_concept=cls.private_code, to_concept=cls.private_code, sources=[cls.private_v1])
        drawing = 'https://example.org/ValueSet/drawing'  # a public value set whose stored expansion drew on it
        collection(cls.sehatapp, 'drawing', canonical_url=drawing)
        cls.with_expansion(collection(cls.sehatapp, 'drawing', 'v1', released=True, canonical_url=drawing),
                           explicit=[cls.private_v1])

    def with_partner(self):
        """
        Partner's public ConceptMap maps to and from MOH's private source, and its public value set drew on it; a
        member of both orgs, and a member of Partner only.
        """
        partner = OrganizationFactory(mnemonic='Partner')
        self.both = UserProfileFactory(username='both-member')
        self.partner_only = UserProfileFactory(username='partner-member')
        partner.members.add(self.both, self.partner_only)
        self.moh.members.add(self.both)
        maps = source(partner, 'partner-map', canonical_url='https://example.org/partner-map')
        maps_v1 = source(partner, 'partner-map', 'v1', released=True, canonical_url=maps.canonical_url)
        own = ConceptFactory(parent=maps, mnemonic='X1')
        MappingFactory(parent=maps, from_source=maps, to_source=self.private, from_concept=own,
                       to_concept=self.private_code, sources=[maps_v1])
        MappingFactory(parent=maps, from_source=self.private, to_source=maps, from_concept=self.private_code,
                       to_concept=own, sources=[maps_v1])
        collection(partner, 'partner-set', canonical_url='https://example.org/partner-set')
        self.with_expansion(
            collection(partner, 'partner-set', 'v1', released=True, canonical_url='https://example.org/partner-set'),
            explicit=[self.private_v1])

    def answer(self, path, user, name, value, base='/fhir'):  # pylint: disable=too-many-arguments
        """The total and the SQL of a search, with the value searched for written as URL."""
        with CaptureQueriesContext(connection) as context:
            total = self.search(path, base=base, user=user, **{name: value})['total']
        return total, [query['sql'].replace(value, 'URL') for query in context.captured_queries]

    def test_a_source_the_caller_is_not_shown_answers_as_an_unknown_one(self):
        for user in (None, self.outsider, self.admin):
            for resource_type, name in (('ConceptMap', 'source-system'), ('ConceptMap', 'target-system'),
                                        ('ConceptMap', 'source-uri'), ('ConceptMap', 'target-uri'),
                                        ('ValueSet', 'reference')):
                private = self.answer(resource_type, user, name, self.PRIVATE)
                self.assertEqual(private, self.answer(resource_type, user, name, self.UNKNOWN), (user, name))
                self.assertEqual(private[0], 0, (user, name))

    def test_at_its_owner_base_to_those_who_may_view_it(self):
        for name in ('source-system', 'target-system'):
            for user, keys in ((None, []), (self.outsider, []), (self.moh_member, [f'{self.PRIVATE}|v1']),
                               (self.admin, [f'{self.PRIVATE}|v1'])):
                self.assertEqual(self.keys(self.search(
                    'ConceptMap', base='/orgs/MOH', user=user, **{name: self.PRIVATE})), keys, (name, user))

    def test_across_owners_by_the_callers_own_rights(self):
        """
        At an owner base, another owner's private source is matched for a caller who may view it by their own rights
        (staff, a member of its org). A member of the base's org only, or an anonymous caller, gets what an unknown
        canonical gets. The global base stays public only, whoever asks.
        """
        self.with_partner()
        for resource_type, name, key in (
                ('ConceptMap', 'target-system', 'https://example.org/partner-map|v1'),
                ('ConceptMap', 'source-system', 'https://example.org/partner-map|v1'),
                ('ConceptMap', 'target-uri', 'https://example.org/partner-map|v1'),
                ('ConceptMap', 'source-uri', 'https://example.org/partner-map|v1'),
                ('ValueSet', 'reference', 'https://example.org/partner-set|v1')):
            for user in (self.both, self.admin):
                self.assertEqual(self.keys(self.search(
                    resource_type, base='/orgs/Partner', user=user, **{name: self.PRIVATE})), [key], (name, user))
                self.assertEqual(self.search(resource_type, user=user, **{name: self.PRIVATE})['total'], 0)
            for user in (self.partner_only, None):
                private = self.answer(resource_type, user, name, self.PRIVATE, '/orgs/Partner')
                self.assertEqual(private, self.answer(resource_type, user, name, self.UNKNOWN, '/orgs/Partner'),
                                 (name, user))
                self.assertEqual(private[0], 0, (name, user))
        # the caller's own rights are a subquery of the source lookup, not a query of their own
        with self.assertNumQueries(5):  # the owner, the caller's membership of it, the sources, the matches, the page
            self.search('ConceptMap', base='/orgs/Partner', user=self.both, **{'target-system': self.PRIVATE})

    def test_the_source_filter_agrees_with_is_visible(self):
        """viewable_rows is is_visible's queryset form, over every source row, HEAD and versions alike."""
        self.with_partner()
        for kwargs in ({}, {'org': 'Partner'}, {'org': 'MOH'}, {'org': 'FHIR-Fixtures'}):
            for user in (AnonymousUser(), self.outsider, self.member, self.moh_member, self.partner_only, self.both,
                         self.admin):
                resolver = FhirResolver(kwargs, user)
                expected = {row.id for row in Source.objects.all() if resolver.is_visible(row)}
                shown = set(resolver.viewable_rows(Source).values_list('id', flat=True))
                self.assertEqual(shown, expected, (kwargs, user))
        self.assertIn(self.private.id, FhirResolver({'org': 'Partner'}, self.both).viewable_rows(Source).values_list(
            'id', flat=True))


@tag('es')
class SearchCostTest(SearchCorpusMixin, OCLTestCase):
    """
    A search's cost is bounded: the canonical URLs a parameter names are looked up together, in one query, and a
    parameter takes at most MAX_ALTERNATIVES values (its alternatives and repeats together), a search at most
    MAX_SEARCH_ALTERNATIVES; past either the search is too costly, a 400 naming the parameter.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        ConceptFactory(parent=cls.ciel_source, mnemonic='c19', sources=[cls.ciel_v2])
        MappingFactory(parent=cls.ciel_source, from_source=cls.ciel_source, to_source=cls.sehat_icd,
                       sources=[cls.ciel_v2])

    @staticmethod
    def alternatives(count, last):
        return ','.join([f'https://example.org/unknown-{index}' for index in range(count - 1)] + [last])

    def test_canonicals_are_looked_up_in_one_query_per_parameter(self):
        for resource_type, name, url, total in (
                ('CodeSystem', 'url', CIEL, 2), ('CodeSystem', 'system', ICD10, 2), ('ValueSet', 'url', CIEL_VS, 1),
                ('ValueSet', 'reference', CIEL, 1), ('ConceptMap', 'source-system', CIEL, 1),
                ('ConceptMap', 'target-uri', ICD10, 1)):
            for count in (1, 20):
                with self.assertNumQueries(3):  # the canonicals' repositories or sources, the matches' ids, the page
                    response = self.client.get(f'/fhir/{resource_type}/', {name: self.alternatives(count, url)})
                self.assertEqual(response.data['total'], total, (resource_type, name, count))
        with self.assertNumQueries(4):  # one lookup for each parameter
            response = self.client.get('/fhir/CodeSystem/', {
                'url': self.alternatives(20, CIEL), 'code': ','.join(f'{CIEL}|c{index}' for index in range(20))})
        self.assertEqual(self.keys(response.data), [f'{CIEL}|v2'])

    def test_a_parameter_takes_at_most_20_values(self):
        for params, expression in (
                ({'url': self.alternatives(21, CIEL)}, 'url'),
                ({'url': [self.alternatives(15, CIEL), self.alternatives(6, ICD10)]}, 'url'),  # repeats count too
                ({'_id': ','.join(['1'] * 21)}, '_id'),
                ({'name': ','.join(['a'] * 11), 'name:exact': ','.join(['b'] * 10)}, 'name:exact')):
            with self.assertNumQueries(0):
                response = self.client.get('/fhir/CodeSystem/', params)
            issue = self.assertOutcome(response, 400, 'too-costly', 'too-costly')
            self.assertEqual(issue['expression'], [expression], params)
        self.assertEqual(self.search('CodeSystem', url=self.alternatives(20, CIEL))['total'], 2)

    def test_a_search_takes_at_most_50_values(self):
        params = {'url': self.alternatives(20, CIEL), 'system': self.alternatives(20, CIEL),
                  'version': ','.join(f'v{index}' for index in range(10))}
        self.assertEqual(self.search('CodeSystem', **params)['total'], 2)
        params['_id'] = '1'
        issue = self.assertOutcome(self.client.get('/fhir/CodeSystem/', params), 400, 'too-costly', 'too-costly')
        self.assertEqual(issue['expression'], ['_id'])


@tag('es')
class VersionIdTest(SearchCorpusMixin, OCLTestCase):
    """
    Every released version has a stable meta.versionId, its opaque id, so an owner-base Bundle with several releases
    of one repository (one mnemonic id, one fullUrl) still identifies each entry (R4 bdl-7).
    """
    def test_owner_base_entries_are_told_apart_by_version_id(self):
        Collection.objects.filter(id=self.ciel_vs_v2.id).update(released=True)
        for resource_type, versions in (('CodeSystem', (self.ciel_v1, self.ciel_v2)),
                                        ('ConceptMap', (self.ciel_v1, self.ciel_v2)),
                                        ('ValueSet', (self.ciel_vs_v1, self.ciel_vs_v2))):
            bundle = self.search(resource_type, base='/orgs/CIEL')
            identities = [(entry['fullUrl'], entry['resource']['meta']['versionId']) for entry in bundle['entry']]
            self.assertEqual(len(identities), 2, resource_type)
            self.assertEqual(len(set(identities)), 2, resource_type)
            self.assertEqual(len({fullurl for fullurl, _ in identities}), 1, resource_type)  # ids stay mnemonics
            self.assertEqual(sorted(version_id for _, version_id in identities),
                             sorted(str(version.id) for version in versions), resource_type)
            for entry in bundle['entry']:
                meta = entry['resource']['meta']
                self.assertIn('lastUpdated', meta)
                self.assertIn(SUBSETTED, meta['tag'])

    def test_reads_and_global_entries_carry_it_too(self):
        for resource_type, version, owner_path in (
                ('CodeSystem', self.ciel_v2, '/orgs/CIEL/CodeSystem/CIEL/'),
                ('ConceptMap', self.ciel_v2, '/orgs/CIEL/ConceptMap/CIEL/'),
                ('ValueSet', self.ciel_vs_v1, '/orgs/CIEL/ValueSet/ciel-set/')):
            for path in (f'/fhir/{resource_type}/{version.id}', owner_path):
                meta = self.get(path).data['meta']
                self.assertEqual(meta['versionId'], str(version.id), path)
                self.assertIn('lastUpdated', meta)
            entry = next(entry for entry in self.search(resource_type)['entry']
                         if entry['resource']['id'] == str(version.id))
            self.assertEqual(entry['resource']['meta']['versionId'], entry['resource']['id'])


@tag('es')
class ReadTest(SearchCorpusMixin, OCLTestCase):
    def test_global_read_by_opaque_id(self):
        for resource_type, version in (('CodeSystem', self.who_icd_2019), ('CodeSystem', self.ciel_v1),
                                       ('ConceptMap', self.ciel_v2), ('ValueSet', self.icd_vs_v1)):
            resource = self.read(f'/fhir/{resource_type}/{version.id}')
            self.assertEqual(
                (resource['resourceType'], resource['id'], resource['version']),
                (resource_type, str(version.id), version.version))
            self.assertEqual(self.read(f'/fhir/{resource_type}/{version.id}/')['id'], str(version.id))

        self.assertEqual(self.read(f'/fhir/CodeSystem/{self.who_icd_2019.id}')['url'], ICD10)  # the repository's
        self.assertEqual(self.read(f'/fhir/ValueSet/{self.icd_vs_v1.id}')['url'], ICD10_VS)
        self.assertEqual(self.read(f'/fhir/CodeSystem/{self.ciel_v1.id}')['status'], 'retired')

    def test_global_read_finds_only_what_global_search_lists(self):
        for version in (self.who_icd, self.sehat_icd_v2, self.draft_v1, self.gone_v1, self.flagged, self.fixture_v1):
            for user in (None, self.member, self.admin):
                self.assertNotFound(f'/fhir/CodeSystem/{version.id}', user)
        self.assertNotFound(f'/fhir/ValueSet/{self.ciel_vs_v2.id}')
        for missing in ('CIEL', 'ICD-10', '0', f'0{self.ciel_v2.id}', '99999999', '12345678901234567890123'):
            self.assertNotFound(f'/fhir/CodeSystem/{missing}')
            self.assertNotFound(f'/fhir/ConceptMap/{missing}')

    def test_owner_read_is_the_latest_release_or_the_version_named(self):
        Source.objects.filter(id=self.ciel_v1.id).update(retired=False)
        newest = source(self.ciel, 'CIEL', 'v3', canonical_url=CIEL)  # unreleased
        resource = self.read('/orgs/CIEL/CodeSystem/CIEL/')
        self.assertEqual((resource['id'], resource['version']), ('CIEL', 'v2'))
        self.assertEqual(self.read('/orgs/CIEL/CodeSystem/CIEL', version='v1')['version'], 'v1')
        self.assertEqual(self.read('/orgs/CIEL/ConceptMap/CIEL/')['version'], 'v2')
        self.assertEqual(self.read('/orgs/sehatapp/ValueSet/icd10-all/')['url'], ICD10_VS)
        self.assertEqual(self.read('/users/jdoe/CodeSystem/my-codes/')['version'], 'v1')
        for version in (newest.version, HEAD, 'no-such-version'):
            self.assertNotFound('/orgs/CIEL/CodeSystem/CIEL/', version=version)
        for path in ('/orgs/misc/CodeSystem/draft/', '/orgs/misc/CodeSystem/gone/', '/orgs/misc/CodeSystem/flagged/',
                     '/orgs/nobody/CodeSystem/CIEL/', '/orgs/WHO/CodeSystem/CIEL/', '/orgs/CIEL/ValueSet/CIEL/',
                     '/orgs/CIEL/ConceptMap/nothing/'):
            self.assertNotFound(path)

    def test_a_retired_release_is_the_default_only_when_named(self):
        self.assertEqual(self.read('/orgs/CIEL/CodeSystem/CIEL/')['version'], 'v2')
        self.assertEqual(self.read('/orgs/CIEL/CodeSystem/CIEL/', version='v1')['status'], 'retired')

    def test_private_owner_read(self):
        for user in (self.member, self.admin):
            resource = self.read('/orgs/FHIR-Fixtures/CodeSystem/fixture-codes/', user)
            self.assertEqual((resource['id'], resource['version']), ('fixture-codes', '2.0.0'))
        for user in (None, self.outsider):
            self.assertNotFound('/orgs/FHIR-Fixtures/CodeSystem/fixture-codes/', user)

    def test_code_system_content_is_complete_up_to_the_limit(self):
        names = [ConceptNameFactory.build(name='Malaria', locale='en', locale_preferred=True),
                 ConceptNameFactory.build(name='Paludisme', locale='fr')]
        malaria = ConceptFactory(parent=self.ciel_source, mnemonic='1427', names=names, sources=[self.ciel_v2],
                                 descriptions=[ConceptDescriptionFactory.build(name='A mosquito-borne disease')])
        retired = ConceptFactory(parent=self.ciel_source, mnemonic='9999', retired=True, sources=[self.ciel_v2])
        ConceptFactory(parent=self.ciel_source, mnemonic='gone', is_active=False, sources=[self.ciel_v2])

        resource = self.read(f'/fhir/CodeSystem/{self.ciel_v2.id}')

        self.assertEqual((resource['content'], resource['count']), ('complete', 2))
        concepts = {concept['code']: concept for concept in resource['concept']}
        self.assertEqual(set(concepts), {malaria.mnemonic, retired.mnemonic})
        self.assertEqual(concepts['1427']['display'], 'Malaria')
        self.assertEqual(concepts['1427']['definition'], 'A mosquito-borne disease')
        self.assertEqual(
            sorted((name['language'], name['value']) for name in concepts['1427']['designation']),
            [('en', 'Malaria'), ('fr', 'Paludisme')])
        self.assertIn({'code': 'inactive', 'valueBoolean': True}, concepts['9999']['property'])
        self.assertNotIn('tag', resource['meta'])

        # above the limit the count is the version's concept links, an index-only count: a deleted concept still
        # linked would count (production has none on a released version)
        with override_settings(FHIR_CODE_SYSTEM_CONCEPT_LIMIT=1):
            resource = self.read(f'/fhir/CodeSystem/{self.ciel_v2.id}')
        self.assertEqual((resource['content'], resource['count']), ('not-present', 3))
        self.assertNotIn('concept', resource)

    def test_reads_run_no_query_per_concept_or_mapping(self):
        def read_queries(path):
            with self.assertNumQueries(5):  # the version, its concept count, the concepts, their names, descriptions
                self.read(path)

        ConceptFactory(parent=self.ciel_source, sources=[self.ciel_v2], names=1, descriptions=1)
        read_queries(f'/fhir/CodeSystem/{self.ciel_v2.id}')
        for _ in range(3):
            ConceptFactory(parent=self.ciel_source, sources=[self.ciel_v2], names=2, descriptions=1)
        read_queries(f'/fhir/CodeSystem/{self.ciel_v2.id}')
        read_queries('/orgs/CIEL/CodeSystem/CIEL/')

        for _ in range(3):
            MappingFactory(parent=self.ciel_source, from_source=self.ciel_source, to_source=self.sehat_icd,
                           sources=[self.ciel_v2])
        with self.assertNumQueries(2):  # the version, its mappings
            self.read(f'/fhir/ConceptMap/{self.ciel_v2.id}')

    def test_value_set_compose_caps_only_concept_references(self):
        for code in ('M1', 'M2', 'M3'):
            self.ciel_vs_v1.references.create(expression=code, reference_type='mappings', system=CIEL, code=code)
        with override_settings(FHIR_VALUE_SET_REFERENCE_LIMIT=2):
            with self.assertNumQueries(2):
                resource = self.read(f'/fhir/ValueSet/{self.ciel_vs_v1.id}')
            self.assertNotIn('compose', resource)
            self.assertNotIn('tag', resource['meta'])

            for count, code in enumerate(('C', 'B', 'A'), 1):
                self.ciel_vs_v1.references.create(expression=code, reference_type='concepts', system=CIEL,
                                                 code=code, include=code != 'B')
                with self.assertNumQueries(2) as queries:
                    resource = self.read(f'/fhir/ValueSet/{self.ciel_vs_v1.id}')

                self.assertEqual(resource['compose']['include'], [{'system': CIEL, 'concept': [{'code': 'C'}]}])
                if count == 1:
                    self.assertNotIn('exclude', resource['compose'])
                else:
                    self.assertEqual(resource['compose']['exclude'], [{'system': CIEL, 'concept': [{'code': 'B'}]}])
                if count > 2:
                    self.assertEqual(resource['meta']['tag'], [SUBSETTED])
                else:
                    self.assertNotIn('tag', resource['meta'])
                self.assertIn('lastUpdated', resource['meta'])
                self.assertEqual(resource['meta']['versionId'], str(self.ciel_vs_v1.id))
                self.assertIn('LIMIT 3', queries.captured_queries[-1]['sql'])

    def test_concept_map_group_caps_only_live_mappings(self):
        for flags in ({'retired': True}, {'is_active': False}):
            MappingFactory(parent=self.ciel_source, from_source=self.ciel_source, to_source=self.sehat_icd,
                           sources=[self.ciel_v2], **flags)
        with override_settings(FHIR_CONCEPT_MAP_MAPPING_LIMIT=2):
            with self.assertNumQueries(2):
                resource = self.read(f'/fhir/ConceptMap/{self.ciel_v2.id}')
            self.assertNotIn('group', resource)
            self.assertNotIn('tag', resource['meta'])

            for count, code in enumerate(('C', 'B', 'A'), 1):
                MappingFactory(parent=self.ciel_source, from_source=self.ciel_source, to_source=self.sehat_icd,
                               from_concept_code='1', to_concept_code=code, sources=[self.ciel_v2])
                with self.assertNumQueries(2) as queries:
                    resource = self.read(f'/fhir/ConceptMap/{self.ciel_v2.id}')

                targets = [target['code'] for group in resource['group'] for element in group['element']
                           for target in element['target']]
                self.assertEqual(targets, ['C'] if count == 1 else ['C', 'B'])
                if count > 2:
                    self.assertEqual(resource['meta']['tag'], [SUBSETTED])
                else:
                    self.assertNotIn('tag', resource['meta'])
                self.assertIn('lastUpdated', resource['meta'])
                self.assertEqual(resource['meta']['versionId'], str(self.ciel_v2.id))
                self.assertIn('LIMIT 3', queries.captured_queries[-1]['sql'])

    def test_subsetted_reads_keep_existing_metadata(self):
        example_tag = {'system': 'http://example.org/tags', 'code': 'example'}
        meta = {'lastUpdated': '2026-01-01T00:00:00Z', 'tag': [example_tag]}
        for code in ('A', 'B'):
            self.ciel_vs_v1.references.create(expression=code, reference_type='concepts', system=CIEL, code=code)
            MappingFactory(parent=self.ciel_source, from_source=self.ciel_source, to_source=self.sehat_icd,
                           from_concept_code='1', to_concept_code=code, sources=[self.ciel_v2])
        for resource_type, version, module, serializer, limit in (
                ('ValueSet', self.ciel_vs_v1, 'value_sets', 'ValueSetDetailSerializer',
                 'FHIR_VALUE_SET_REFERENCE_LIMIT'),
                ('ConceptMap', self.ciel_v2, 'concept_maps', 'ConceptMapDetailSerializer',
                 'FHIR_CONCEPT_MAP_MAPPING_LIMIT')):
            with override_settings(**{limit: 1}), \
                    patch(f'core.{module}.serializers.{serializer}.get_meta', return_value=meta):
                resource = self.read(f'/fhir/{resource_type}/{version.id}')

            self.assertEqual(resource['meta'], {**meta, 'versionId': str(version.id), 'tag': [example_tag, SUBSETTED]})
            self.assertEqual(meta['tag'], [example_tag])

    def test_writes_keep_the_owner_check(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.put(f'/fhir/CodeSystem/{self.ciel_v2.id}/', {}, format='json')
        self.assertEqual(response.status_code, 404)


@tag('es')
class RepositoryAccessTest(SearchCorpusMixin, OCLTestCase):
    """
    A version is shown only where its repository is too: its HEAD, which governs access to its concepts and mappings,
    must be viewable as well as the version. MOH made hiv-codes and hiv-set private after releasing v1 public, so v1
    is shown at MOH's base to MOH's members and staff, and nowhere else. A retired release counts only beside a live
    one the caller is shown, and an owner-base read without a version serves the newest release the caller is shown.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        ConceptFactory(parent=cls.hiv, mnemonic='H1', sources=[cls.hiv_v1])
        MappingFactory(parent=cls.hiv, from_source=cls.hiv, to_source=cls.ciel_source, sources=[cls.hiv_v1])
        cls.users = (None, cls.outsider, cls.member, cls.moh_member, cls.admin)
        cls.viewers = (cls.moh_member, cls.admin)

    def test_search_shows_a_release_where_its_repository_is_shown(self):
        hiv, hiv_set = [f'{HIV}|v1'], [f'{HIV_VS}|v1']
        for user in self.users:
            for base, shown in (('/fhir', False), ('/orgs/MOH', user in self.viewers)):
                for resource_type, params, keys in (
                        ('CodeSystem', {}, hiv), ('CodeSystem', {'url': HIV}, hiv), ('CodeSystem', {'code': 'H1'}, hiv),
                        ('CodeSystem', {'code': f'{HIV}|'}, hiv), ('ConceptMap', {}, hiv),
                        ('ConceptMap', {'url': HIV}, hiv), ('ValueSet', {}, hiv_set),
                        ('ValueSet', {'url': HIV_VS}, hiv_set), ('ValueSet', {'reference': CIEL}, hiv_set)):
                    bundle = self.search(resource_type, base=base, user=user, **params)
                    found = [key for key in self.keys(bundle) if key in keys]
                    self.assertEqual(found, keys if shown else [], (user, base, resource_type, params))
                    if base == '/orgs/MOH':
                        self.assertEqual(bundle['total'], len(found), (user, resource_type, params))

    def test_read_serves_a_release_where_its_repository_is_shown(self):
        for user in self.users:
            for resource_type, version in (
                    ('CodeSystem', self.hiv_v1), ('ConceptMap', self.hiv_v1), ('ValueSet', self.hiv_vs_v1)):
                self.assertNotFound(f'/fhir/{resource_type}/{version.id}', user)
            for path, url in (('/orgs/MOH/CodeSystem/hiv-codes/', HIV), ('/orgs/MOH/ConceptMap/hiv-codes/', HIV),
                              ('/orgs/MOH/ValueSet/hiv-set/', HIV_VS)):
                if user in self.viewers:
                    resource = self.read(path, user)
                    self.assertEqual((resource['version'], resource['url']), ('v1', url), (path, user))
                else:
                    self.assertNotFound(path, user)
                    self.assertNotFound(path, user, version='v1')
        self.assertEqual([concept['code'] for concept in self.read(
            '/orgs/MOH/CodeSystem/hiv-codes/', self.moh_member)['concept']], ['H1'])

    def test_a_retired_release_counts_beside_a_live_one_the_caller_is_shown(self):
        url = 'http://moh.example/fhir/CodeSystem/mixed'
        source(self.moh, 'mixed', canonical_url=url)
        retired = source(self.moh, 'mixed', 'v1', released=True, retired=True, canonical_url=url)
        source(self.moh, 'mixed', 'v2', released=True, canonical_url=url, public_access=ACCESS_TYPE_NONE)
        for user in self.users:
            shown = user in self.viewers
            self.assertEqual(self.search('CodeSystem', user=user, url=url)['total'], 0, user)
            self.assertNotFound(f'/fhir/CodeSystem/{retired.id}', user)
            self.assertEqual(self.keys(self.search('CodeSystem', base='/orgs/MOH', user=user, url=url)),
                             [f'{url}|v1', f'{url}|v2'] if shown else [], user)
            if shown:
                self.assertEqual(self.read('/orgs/MOH/CodeSystem/mixed/', user, version='v1')['status'], 'retired')
            else:
                self.assertNotFound('/orgs/MOH/CodeSystem/mixed/', user, version='v1')

    def test_an_owner_read_serves_the_newest_release_the_caller_is_shown(self):
        url = 'http://moh.example/fhir/CodeSystem/newest'
        source(self.moh, 'newest', canonical_url=url)
        source(self.moh, 'newest', 'v1', released=True, canonical_url=url)
        source(self.moh, 'newest', 'v2', released=True, canonical_url=url, public_access=ACCESS_TYPE_NONE)
        for user in self.users:
            newest = 'v2' if user in self.viewers else 'v1'
            self.assertEqual(self.read('/orgs/MOH/CodeSystem/newest/', user)['version'], newest, user)
            listed = self.keys(self.search('CodeSystem', base='/orgs/MOH', user=user, _id='newest'))
            self.assertIn(f'{url}|{newest}', listed, user)


@tag('es')
class VisibleVersionsTest(SearchCorpusMixin, OCLTestCase):
    """visible_versions is the queryset form of is_visible and has_release: they agree on every row."""
    def test_the_queryset_agrees_with_is_visible_and_has_release(self):
        # a retired public release beside a private live one; a private newest release; a deleted repository (HEAD)
        source(self.moh, 'mixed', canonical_url='urn:mixed')
        source(self.moh, 'mixed', 'v1', released=True, retired=True, canonical_url='urn:mixed')
        source(self.moh, 'mixed', 'v2', released=True, canonical_url='urn:mixed', public_access=ACCESS_TYPE_NONE)
        source(self.moh, 'newest', canonical_url='urn:newest')
        source(self.moh, 'newest', 'v1', released=True, canonical_url='urn:newest')
        source(self.moh, 'newest', 'v2', released=True, canonical_url='urn:newest', public_access=ACCESS_TYPE_NONE)
        Source.objects.filter(id=self.jdoe_codes.id).update(is_active=False)
        users = (AnonymousUser(), self.outsider, self.member, self.moh_member, self.admin)
        cases = [({}, user) for user in users] + [
            ({'org': org}, user) for org in ('FHIR-Fixtures', 'MOH') for user in users] + [
            ({'org': 'CIEL'}, AnonymousUser()), ({'org': 'misc'}, self.admin), ({'user': 'jdoe'}, AnonymousUser()),
            ({'user': 'jdoe'}, self.jdoe)]
        for kwargs, user in cases:
            for resource_type in ('CodeSystem', 'ValueSet'):
                model = fhir_resolve.REPO_TYPES[resource_type]
                resolver = FhirResolver(kwargs, user)
                namespace = resolver.namespace
                expected = {
                    row.id for row in model.objects.filter(is_active=True, released=True).exclude(version=HEAD)
                    if (namespace is None or row.parent.uri == namespace) and
                    resolver.is_visible(row) and resolver.has_release(row)}
                visible = set(resolver.visible_versions(resource_type).values_list('id', flat=True))
                self.assertEqual(visible, expected, (kwargs, user, resource_type))
        self.assertIn(self.hiv_v1.id, FhirResolver({'org': 'MOH'}, self.moh_member).visible_versions(
            'CodeSystem').values_list('id', flat=True))


class SearchTimeoutTest(TransactionTestCase):
    """A search the database cancels at FHIR_SEARCH_TIMEOUT_MS is too costly (a 422 outcome), never a 5xx."""
    def test_a_cancelled_query_is_too_costly(self):
        def slow():
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_sleep(1)')

        with override_settings(FHIR_SEARCH_TIMEOUT_MS=50):
            with self.assertRaises(TooCostlyError) as context:
                fhir_search.within_search_timeout(slow)
        self.assertEqual((context.exception.status_code, context.exception.issue_code), (422, 'too-costly'))

        self.assertEqual(fhir_search.within_search_timeout(lambda: 'answered'), 'answered')
        with connection.cursor() as cursor:  # the timeout ended with the search
            cursor.execute('SHOW statement_timeout')
            self.assertEqual(cursor.fetchone()[0], '0')

    def test_the_timeout_bounds_the_search_as_a_whole(self):
        """Each query may take only the time the search has left, so queries that fit alone can still add up past it."""
        def slow():
            with connection.cursor() as cursor:
                for _ in range(4):
                    cursor.execute('SELECT pg_sleep(0.04)')

        with override_settings(FHIR_SEARCH_TIMEOUT_MS=100):
            with self.assertRaises(TooCostlyError):
                fhir_search.within_search_timeout(slow)
            self.assertEqual(fhir_search.within_search_timeout(lambda: 'answered'), 'answered')

    def test_a_query_with_no_time_left_is_too_costly(self):
        """A query that starts after the search's time is up isn't sent: the search is too costly already."""
        def late():
            time.sleep(0.06)
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')

        with override_settings(FHIR_SEARCH_TIMEOUT_MS=50):
            with self.assertRaises(TooCostlyError):
                fhir_search.within_search_timeout(late)

    def test_other_database_errors_are_raised(self):
        def broken():
            raise OperationalError('connection lost')

        with self.assertRaises(OperationalError):
            fhir_search.within_search_timeout(broken)
