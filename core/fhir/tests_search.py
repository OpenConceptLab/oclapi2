"""
FHIR search and read of CodeSystem, ValueSet and ConceptMap (SVCM ITI-95, ITI-96 and ITI-100): what a base shows.
OpenConceptLab/ocl_online#451.
"""
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth.models import AnonymousUser
from rest_framework.test import APIClient

from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE, HEAD
from core.common.tests import OCLTestCase
from core.fhir import resolve as fhir_resolve
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM
from core.orgs.tests.factories import OrganizationFactory
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
