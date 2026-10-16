"""
core.fhir.search: FHIR search and read of CodeSystem, ValueSet and ConceptMap (SVCM ITI-95, ITI-96 and ITI-100) at the
global base /fhir/ and at owner bases. Released versions only, never HEAD; public ones at the global base whoever asks;
an opaque id per released version at the global base, the mnemonic at an owner base; SVCM's search parameters with
:exact and :contains; _count, paging, the real total and a fullUrl on every entry; a CodeSystem read is complete up to
the concept limit, else not-present. OpenConceptLab/ocl_online#451.
"""
from datetime import datetime, timezone as dt_timezone
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import connection, OperationalError
from django.test import TransactionTestCase
from mock import patch
from rest_framework.test import APIClient

from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE, HEAD
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory, ConceptDescriptionFactory
from core.fhir import resolve as fhir_resolve, search as fhir_search
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM, TooCostlyError
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


def at(year):
    return datetime(year, 6, 15, 12, tzinfo=dt_timezone.utc)


class SearchCorpusMixin:
    """
    ICD-10 shared by WHO (whose released version has no canonical of its own, as in production) and sehatapp (with a
    newer unreleased version); CIEL with a retired release and a live one, with concepts and mappings; repositories
    nothing visible comes from (nothing released, only a retired release, a HEAD flagged released); the private fixture
    org with two releases; a user's repository; two value sets with stored expansions.
    """
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.who = OrganizationFactory(mnemonic='WHO')
        self.sehatapp = OrganizationFactory(mnemonic='sehatapp')
        self.ciel = OrganizationFactory(mnemonic='CIEL')
        self.misc = OrganizationFactory(mnemonic='misc')
        self.fixtures = OrganizationFactory(mnemonic='FHIR-Fixtures', public_access=ACCESS_TYPE_NONE)
        self.member = UserProfileFactory(username='fixture-member')
        self.outsider = UserProfileFactory(username='outsider')
        self.fixtures.members.add(self.member)
        self.jdoe = UserProfileFactory(username='jdoe')

        self.who_icd = source(self.who, 'ICD-10-WHO', canonical_url=ICD10, full_name='ICD-10 WHO Edition')
        self.who_icd_2019 = source(
            self.who, 'ICD-10-WHO', 'ICD102019', released=True, canonical_url=None, full_name='ICD-10 WHO Edition',
            publisher='World Health Organization', identifier=[HL7_OID])
        self.sehat_icd = source(self.sehatapp, 'ICD-10', canonical_url=ICD10, full_name='ICD-10 for Sehat')
        self.sehat_icd_v1 = source(
            self.sehatapp, 'ICD-10', 'v1.0', released=True, canonical_url=ICD10, full_name='ICD-10 for Sehat')
        self.sehat_icd_v2 = source(self.sehatapp, 'ICD-10', 'v2.0', canonical_url=ICD10)  # newest, unreleased

        self.ciel_source = source(
            self.ciel, 'CIEL', canonical_url=CIEL, full_name='Columbia International eHealth Laboratory',
            description='Concepts for clinical care')
        self.ciel_v1 = source(
            self.ciel, 'CIEL', 'v1', released=True, retired=True, canonical_url=CIEL,
            full_name='Columbia International eHealth Laboratory', description='Concepts for clinical care')
        self.ciel_v2 = source(
            self.ciel, 'CIEL', 'v2', released=True, canonical_url=CIEL,
            full_name='Columbia International eHealth Laboratory', description='Concepts for clinical care')

        # nothing visible comes from these
        self.draft = source(self.misc, 'draft', canonical_url='https://example.org/draft')
        self.draft_v1 = source(self.misc, 'draft', 'v1', canonical_url='https://example.org/draft')
        self.gone = source(self.misc, 'gone', canonical_url='https://example.org/gone')
        self.gone_v1 = source(
            self.misc, 'gone', 'v1', released=True, retired=True, canonical_url='https://example.org/gone')
        self.flagged = source(self.misc, 'flagged', released=True, canonical_url='https://example.org/flagged')

        self.fixture = source(self.fixtures, 'fixture-codes', canonical_url=FIXTURE, public_access=ACCESS_TYPE_NONE)
        self.fixture_v1 = source(
            self.fixtures, 'fixture-codes', '1.0.0', released=True, canonical_url=FIXTURE,
            public_access=ACCESS_TYPE_NONE)
        self.fixture_v2 = source(
            self.fixtures, 'fixture-codes', '2.0.0', released=True, canonical_url=FIXTURE,
            public_access=ACCESS_TYPE_NONE)

        self.jdoe_codes = source(self.jdoe, 'my-codes', canonical_url='https://example.org/my-codes')
        self.jdoe_codes_v1 = source(
            self.jdoe, 'my-codes', 'v1', released=True, canonical_url='https://example.org/my-codes')

        # the value sets: sehatapp's draws on its ICD-10 v1.0, the CIEL one on CIEL's HEAD; the released version of
        # the ICD-10 one has no canonical of its own
        self.icd_vs = collection(self.sehatapp, 'icd10-all', canonical_url=ICD10_VS, full_name='All ICD-10 codes')
        self.icd_vs_v1 = collection(
            self.sehatapp, 'icd10-all', 'v1.0', released=True, canonical_url=None, full_name='All ICD-10 codes')
        self.with_expansion(self.icd_vs_v1, explicit=[self.sehat_icd_v1])
        self.ciel_vs = collection(self.ciel, 'ciel-set', canonical_url=CIEL_VS)
        self.ciel_vs_v1 = collection(self.ciel, 'ciel-set', 'v1', released=True, canonical_url=CIEL_VS)
        self.with_expansion(self.ciel_vs_v1, evaluated=[self.ciel_source])
        self.ciel_vs_v2 = collection(self.ciel, 'ciel-set', 'v2', canonical_url=CIEL_VS)  # unreleased

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
            ('code', f'{CIEL}|', [f'{CIEL}|v1|a,b', f'{CIEL}|v2']),
            ('code', f'{ICD10}|', [f'{ICD10}|ICD102019', icd_v1[0]]),
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

    def test_code(self):
        concept = ConceptFactory(parent=self.ciel_source, mnemonic='1427', sources=[self.ciel_v2])
        self.assertEqual(self.keys(self.search('CodeSystem', code=concept.mnemonic)), [f'{CIEL}|v2'])
        self.assertEqual(self.search('CodeSystem', code='no-such-code')['total'], 0)

    def test_language(self):
        Source.objects.filter(id=self.ciel_v2.id).update(default_locale='fr')
        self.assertEqual(self.keys(self.search('CodeSystem', language='fr')), [f'{CIEL}|v2'])

    def test_unsupported_parameters_and_values_are_400_outcomes(self):
        for query in ('unsupported_parameter=1', 'q=CIEL', 'limit=10', 'name:missing=true', 'url:below=http://x',
                      '_sort=name', 'reference=http://x', 'source-system=http://x'):
            issue = self.assertOutcome(self.client.get(f'/fhir/CodeSystem/?{query}'), 400, 'not-supported',
                                       'not-supported')
            self.assertIn(query.split('=', maxsplit=1)[0].split(':')[0], issue['details']['text'], query)
        for query in ('_count=-1', '_count=ten', '_lastUpdated=yesterday', '_lastUpdated=ap2020',
                      '_lastUpdated=2020-13', 'page=0'):
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

        with patch('core.fhir.search.MAX_COUNT', 3):
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

        with patch('core.fhir.search.CONCEPT_LIMIT', 0):
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


class ConceptMapSearchTest(SearchCorpusMixin, OCLTestCase):
    def setUp(self):
        super().setUp()
        icd_code = ConceptFactory(parent=self.sehat_icd, mnemonic='A00')
        ciel_code = ConceptFactory(parent=self.ciel_source, mnemonic='1427')
        MappingFactory(
            parent=self.ciel_source, from_concept=ciel_code, to_concept=icd_code, from_source=self.ciel_source,
            to_source=self.sehat_icd, from_concept_code='1427', to_concept_code='A00', sources=[self.ciel_v2])

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

    def read(self, path):
        response = self.get(path)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_no_query_per_entry(self):
        with self.assertNumQueries(4):  # the target's repositories and their rows, the matches' ids, the page
            self.assertEqual(self.client.get('/fhir/ConceptMap/', {'target-system': ICD10}).data['total'], 1)


class ReadTest(SearchCorpusMixin, OCLTestCase):
    def read(self, path, user=None, **params):
        response = self.get(path, user, **params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def assertNotFound(self, path, user=None, **params):  # pylint: disable=invalid-name
        response = self.get(path, user, **params)
        self.assertOutcome(response, 404, 'not-found', 'not-found')

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
        with patch('core.fhir.search.CONCEPT_LIMIT', 1):
            resource = self.read(f'/fhir/CodeSystem/{self.ciel_v2.id}')
        self.assertEqual((resource['content'], resource['count']), ('not-present', 3))
        self.assertNotIn('concept', resource)

    def test_concept_map_read(self):
        icd_code = ConceptFactory(parent=self.sehat_icd, mnemonic='A00')
        ciel_code = ConceptFactory(parent=self.ciel_source, mnemonic='1427')
        MappingFactory(
            parent=self.ciel_source, from_concept=ciel_code, to_concept=icd_code, from_source=self.ciel_source,
            to_source=self.sehat_icd, from_concept_code='1427', to_concept_code='A00', sources=[self.ciel_v2])

        resource = self.read(f'/fhir/ConceptMap/{self.ciel_v2.id}')

        self.assertEqual(resource['group'][0]['source'], CIEL)
        self.assertEqual(resource['group'][0]['target'], ICD10)
        self.assertEqual(resource['group'][0]['element'][0]['code'], '1427')

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

    def test_writes_keep_the_owner_check(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.put(f'/fhir/CodeSystem/{self.ciel_v2.id}/', {}, format='json')
        self.assertEqual(response.status_code, 404)


class VisibleVersionsTest(SearchCorpusMixin, OCLTestCase):
    """visible_versions is the queryset form of is_visible and has_release: they agree on every row."""
    def test_the_queryset_agrees_with_is_visible_and_has_release(self):
        cases = [({}, user) for user in (AnonymousUser(), self.member, self.admin)] + [
            ({'org': 'FHIR-Fixtures'}, user) for user in (AnonymousUser(), self.outsider, self.member, self.admin)] + [
            ({'org': 'CIEL'}, AnonymousUser()), ({'org': 'misc'}, self.admin), ({'user': 'jdoe'}, AnonymousUser())]
        for kwargs, user in cases:
            for resource_type in ('CodeSystem', 'ValueSet'):
                model = fhir_resolve.REPO_TYPES[resource_type]
                namespace = fhir_resolve.namespace_of(kwargs)
                expected = {
                    row.id for row in model.objects.filter(is_active=True, released=True).exclude(version=HEAD)
                    if (namespace is None or row.parent.uri == namespace) and
                    fhir_resolve.is_visible(row, namespace, user) and fhir_resolve.has_release(row)}
                visible = set(fhir_resolve.visible_versions(resource_type, kwargs, user).values_list('id', flat=True))
                self.assertEqual(visible, expected, (kwargs, user, resource_type))


class SearchTimeoutTest(TransactionTestCase):
    """A search the database cancels at SEARCH_TIMEOUT_MS is too costly (a 422 outcome), never a 5xx."""
    def test_a_cancelled_query_is_too_costly(self):
        def slow():
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_sleep(1)')

        with patch('core.fhir.search.SEARCH_TIMEOUT_MS', 50):
            with self.assertRaises(TooCostlyError) as context:
                fhir_search.within_search_timeout(slow)
        self.assertEqual((context.exception.status_code, context.exception.issue_code), (422, 'too-costly'))

        self.assertEqual(fhir_search.within_search_timeout(lambda: 'answered'), 'answered')
        with connection.cursor() as cursor:  # the timeout ended with the search
            cursor.execute('SHOW statement_timeout')
            self.assertEqual(cursor.fetchone()[0], '0')

    def test_other_database_errors_are_raised(self):
        def broken():
            raise OperationalError('connection lost')

        with self.assertRaises(OperationalError):
            fhir_search.within_search_timeout(broken)
