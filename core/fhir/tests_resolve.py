"""
core.fhir.resolve: FHIR's canonical resolution through the shared resolver, with the URL registry toggle off and on.
Shared canonicals (ICD-10, ATC), a Source and a Collection sharing one, a repository with nothing released, the
fixture org's private namespace, unknown systems and versions, and HEAD. OpenConceptLab/ocl_online#451.
"""
from django.contrib.auth.models import AnonymousUser

from core.collections.models import Collection
from core.common.constants import ACCESS_TYPE_NONE, HEAD
from core.common.tests import OCLTestCase
from core.fhir import resolve as fhir_resolve
from core.fhir.outcomes import NotFoundError, TX_ISSUE_TYPE_SYSTEM
from core.fhir.utils import namespace_of
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import UserSourceFactory
from core.url_registry.factories import GlobalURLRegistryFactory
from core.url_registry.models import URLRegistry
from core.url_registry.tests_resolution import (
    set_registry_toggle, source, collection, ResolverCorpusMixin, ICD10, PBD, DRAFT)
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory

ATC = 'http://www.whocc.no/atc'
CIEL = 'https://CIELterminology.org'
FIXTURE = 'https://openconceptlab.org/fhir-fixtures/CodeSystem/fixture-codes'
UNKNOWN = 'http://example.org/ocl-fhir-conformance/unknown-system'
ANONYMOUS = AnonymousUser()


class NamespaceTest(OCLTestCase):
    def test_namespace_is_the_base_url_owner(self):
        self.assertIsNone(namespace_of({}))
        self.assertIsNone(namespace_of(None))
        self.assertEqual(namespace_of({'org': 'CIEL', 'source': 'x'}), '/orgs/CIEL/')
        self.assertEqual(namespace_of({'user': 'jdoe'}), '/users/jdoe/')

    def test_unresolved_error(self):
        error = fhir_resolve.UnresolvedError('CodeSystem', UNKNOWN, expression='system')
        self.assertIsInstance(error, NotFoundError)
        self.assertEqual(error.status_code, 404)
        self.assertEqual(
            error.outcome()['issue'],
            [{
                'severity': 'error', 'code': 'not-found',
                'details': {
                    'coding': [{'system': TX_ISSUE_TYPE_SYSTEM, 'code': 'not-found'}],
                    'text': f"A definition for CodeSystem '{UNKNOWN}' could not be found"
                },
                'expression': ['system']
            }])
        self.assertEqual((error.resource_type, error.url, error.version), ('CodeSystem', UNKNOWN, None))

        error = fhir_resolve.UnresolvedError('ValueSet', UNKNOWN, '1.0')
        self.assertEqual(str(error.detail), f"A definition for ValueSet '{UNKNOWN}' version '1.0' could not be found")
        self.assertNotIn('expression', error.outcome()['issue'][0])


class FhirResolveCases(ResolverCorpusMixin):  # pylint: disable=too-many-public-methods
    """
    Every case runs with the toggle off and on (the subclasses); `registry` says which. The resolver's corpus (ICD-10
    shared by WHO and sehatapp, padam's Source and Collection, an unreleased draft), plus ATC shared the same way,
    CIEL, and the private fixture org.
    """
    registry = None

    def setUp(self):
        set_registry_toggle(self.registry)
        super().setUp()
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.ciel = OrganizationFactory(mnemonic='CIEL')
        self.hl7 = OrganizationFactory(mnemonic='HL7')
        self.fixtures = OrganizationFactory(mnemonic='FHIR-Fixtures', public_access=ACCESS_TYPE_NONE)
        self.member = UserProfileFactory(username='fixture-member')
        self.outsider = UserProfileFactory(username='outsider')
        self.fixtures.members.add(self.member)

        # WHO first, so WHO has the lower ids, as in production (the corpus does the same for ICD-10)
        self.who_atc = source(self.who, 'WHOATC', canonical_url=ATC)
        self.who_atc_2023 = source(self.who, 'WHOATC', '2023AA', released=True, canonical_url=ATC)
        self.who_atc_2024 = source(self.who, 'WHOATC', 'ATC_2024_01_19', canonical_url=ATC)  # newest, unreleased
        self.sehat_atc = source(self.sehatapp, 'ATC', canonical_url=ATC)
        self.sehat_atc_v2 = source(self.sehatapp, 'ATC', 'v2', released=True, canonical_url=ATC)
        self.ciel_source = source(self.ciel, 'CIEL', canonical_url=CIEL)
        self.ciel_v1 = source(self.ciel, 'CIEL', 'v2026-09-24', released=True, canonical_url=CIEL)
        self.fixture = source(
            self.fixtures, 'fixture-codes', canonical_url=FIXTURE, public_access=ACCESS_TYPE_NONE)
        self.fixture_v1 = source(
            self.fixtures, 'fixture-codes', '1.0.0', released=True, canonical_url=FIXTURE,
            public_access=ACCESS_TYPE_NONE)
        self.fixture_at = source(
            self.fixtures, 'codes@fixture', canonical_url=f'{FIXTURE}-at', public_access=ACCESS_TYPE_NONE)
        self.fixture_at_v1 = source(
            self.fixtures, 'codes@fixture', '1.0.0', released=True, canonical_url=f'{FIXTURE}-at',
            public_access=ACCESS_TYPE_NONE)
        if self.registry:
            # the curated global registry (design D24): shared canonicals to the owners chosen, none for private repos
            for url, owner in ((ICD10, self.who), (ATC, self.who), (CIEL, self.ciel), (PBD, self.padam),
                               (DRAFT, self.drafts)):
                GlobalURLRegistryFactory(url=url, namespace=owner.uri)

    resolve = staticmethod(fhir_resolve.resolve)

    def assertUnresolved(self, resource_type, url, version=None, kwargs=None, user=ANONYMOUS):  # pylint: disable=invalid-name,too-many-arguments
        with self.assertRaises(fhir_resolve.UnresolvedError) as context:
            self.resolve(resource_type, url, version, kwargs, user)
        self.assertEqual(
            (context.exception.resource_type, context.exception.url), (resource_type, url.partition('|')[0]))
        return context.exception

    def test_shared_canonicals_at_the_global_base(self):
        version = self.resolve('CodeSystem', ICD10)
        self.assertEqual(version.id, self.who_icd_2019.id)
        self.assertEqual(version.canonical_url, ICD10)

        # the latest released version, not the newest one
        self.assertEqual(self.resolve('CodeSystem', ATC).id, self.who_atc_2023.id)
        self.assertEqual(self.resolve('CodeSystem', CIEL).id, self.ciel_v1.id)

    def test_shared_canonicals_at_an_owner_base(self):
        sehatapp = {'org': 'sehatapp'}
        self.assertEqual(self.resolve('CodeSystem', ICD10, kwargs=sehatapp).id, self.sehat_icd_v1.id)
        self.assertEqual(self.resolve('CodeSystem', ATC, kwargs=sehatapp).id, self.sehat_atc_v2.id)
        self.assertEqual(self.resolve('CodeSystem', ATC, kwargs={'org': 'WHO'}).id, self.who_atc_2023.id)

    def test_resource_type(self):
        self.assertEqual(self.resolve('CodeSystem', PBD).id, self.pbd_source_v1.id)
        self.assertEqual(self.resolve('ConceptMap', PBD).id, self.pbd_source_v1.id)
        version = self.resolve('ValueSet', PBD)
        self.assertEqual(version.id, self.pbd_collection_v1.id)
        self.assertIsInstance(version, Collection)

        self.assertUnresolved('ValueSet', ICD10)
        self.assertUnresolved('ValueSet', ICD10, kwargs={'org': 'sehatapp'})

    def test_unknown_system(self):
        error = self.assertUnresolved('CodeSystem', UNKNOWN)
        self.assertEqual(error.status_code, 404)
        self.assertIsNone(error.version)
        self.assertUnresolved('CodeSystem', UNKNOWN, kwargs={'org': 'WHO'})
        self.assertUnresolved('ValueSet', UNKNOWN)
        self.assertUnresolved('CodeSystem', '')

    def test_unknown_version(self):
        error = self.assertUnresolved('CodeSystem', ICD10, 'nope')
        self.assertEqual(error.version, 'nope')

        # the version of the repository that lost the resolution: without the registry, it holds the release named, so
        # it is served (decision 9, core.fhir.tests_resolve_named_version); with it, the global entry decides
        if self.registry:
            self.assertUnresolved('CodeSystem', ICD10, 'v1.0')
        else:
            self.assertEqual(self.resolve('CodeSystem', ICD10, 'v1.0').id, self.sehat_icd_v1.id)
        self.assertEqual(self.resolve('CodeSystem', ICD10, 'v1.0', {'org': 'sehatapp'}).id, self.sehat_icd_v1.id)

    def test_named_version(self):
        self.assertEqual(self.resolve('CodeSystem', ICD10, 'ICD102019').id, self.who_icd_2019.id)
        # an unreleased version is served when named
        self.assertEqual(self.resolve('CodeSystem', ATC, 'ATC_2024_01_19').id, self.who_atc_2024.id)
        self.assertEqual(self.resolve('CodeSystem', f'{ATC}|2023AA').id, self.who_atc_2023.id)
        # the version parameter wins over url|version, as in the resolver
        self.assertEqual(self.resolve('CodeSystem', f'{ATC}|nope', '2023AA').id, self.who_atc_2023.id)
        error = self.assertUnresolved('CodeSystem', f'{ATC}|nope')
        self.assertEqual((error.url, error.version), (ATC, 'nope'))
        # a second pipe is no version OCL holds: not found, never a server error
        self.assertEqual(self.assertUnresolved('CodeSystem', f'{ATC}|2023AA|x').version, '2023AA|x')

    def test_head_only_when_named(self):
        self.assertEqual(self.resolve('CodeSystem', ICD10, HEAD).id, self.who_icd.id)
        self.assertEqual(self.resolve('CodeSystem', f'{ICD10}|HEAD').id, self.who_icd.id)
        self.assertNotEqual(self.resolve('CodeSystem', ICD10).version, HEAD)

    def test_a_repository_with_nothing_released_is_not_visible(self):
        self.assertUnresolved('CodeSystem', DRAFT)
        self.assertUnresolved('CodeSystem', DRAFT, HEAD)
        self.assertUnresolved('CodeSystem', DRAFT, HEAD, {'org': 'drafts'}, self.admin)

        source(self.drafts, 'draft', 'v1', released=True, canonical_url=DRAFT)
        self.assertEqual(self.resolve('CodeSystem', DRAFT, HEAD).id, self.draft.id)

    def test_a_head_flagged_released_is_no_release(self):
        """HEAD's released flag neither makes HEAD the default nor makes a repository visible"""
        flagged = 'http://example.org/flagged-head'
        head = source(self.hl7, 'flagged', released=True, canonical_url=flagged)
        if self.registry:
            GlobalURLRegistryFactory(url=flagged, namespace=self.hl7.uri)
        for version, kwargs, user in ((None, None, ANONYMOUS), (HEAD, None, ANONYMOUS),
                                      (None, {'org': 'HL7'}, self.admin), (HEAD, {'org': 'HL7'}, self.admin)):
            self.assertUnresolved('CodeSystem', flagged, version, kwargs, user)

        released = source(self.hl7, 'flagged', 'v1', released=True, canonical_url=flagged)
        self.assertEqual(self.resolve('CodeSystem', flagged).id, released.id)
        self.assertEqual(self.resolve('CodeSystem', flagged, HEAD).id, head.id)

    def test_a_relative_url_keeps_its_own_resource_type(self):
        """An OCL path names a Source or a Collection by itself; a reference of the other type is not found"""
        acme = OrganizationFactory(mnemonic='acme')
        source(acme, 'codes')
        codes = source(acme, 'codes', 'v1', released=True)
        collection(acme, 'codes')
        value_set = collection(acme, 'codes', 'v1', released=True)
        for url in ('/orgs/acme/CodeSystem/codes/', '/orgs/acme/ConceptMap/codes/', '/orgs/acme/sources/codes/'):
            self.assertEqual(self.resolve('CodeSystem', url).id, codes.id)
            self.assertEqual(self.resolve('ConceptMap', url).id, codes.id)
            self.assertUnresolved('ValueSet', url)
        for url in ('/orgs/acme/ValueSet/codes/', '/orgs/acme/collections/codes/'):
            self.assertEqual(self.resolve('ValueSet', url).id, value_set.id)
            self.assertUnresolved('CodeSystem', url)
            self.assertUnresolved('ConceptMap', url)
        self.assertEqual(self.resolve('CodeSystem', '/orgs/acme/CodeSystem/codes/|v1').id, codes.id)
        self.assertUnresolved('ValueSet', '/orgs/acme/CodeSystem/codes/|v1')

    def test_fixture_namespace(self):
        fixtures = {'org': 'FHIR-Fixtures'}
        for user in (self.member, self.admin):
            self.assertEqual(self.resolve('CodeSystem', FIXTURE, kwargs=fixtures, user=user).id, self.fixture_v1.id)
            self.assertEqual(
                self.resolve('CodeSystem', f'{FIXTURE}-at', '1.0.0', fixtures, user).id, self.fixture_at_v1.id)
            self.assertEqual(
                self.resolve('CodeSystem', '/orgs/FHIR-Fixtures/CodeSystem/codes@fixture/', kwargs=fixtures,
                             user=user).id,
                self.fixture_at_v1.id)
        for user in (ANONYMOUS, self.outsider, None):
            self.assertUnresolved('CodeSystem', FIXTURE, kwargs=fixtures, user=user)

        # the global base shows public repositories only, to anyone
        for user in (ANONYMOUS, self.member, self.admin):
            self.assertUnresolved('CodeSystem', FIXTURE, user=user)

    def test_a_user_base(self):
        user = UserProfileFactory(username='jdoe')
        head = UserSourceFactory(user=user, mnemonic='mine', canonical_url='http://example.org/mine')
        released = UserSourceFactory(
            user=user, mnemonic='mine', version='1', released=True, canonical_url='http://example.org/mine')
        self.assertEqual(self.resolve('CodeSystem', 'http://example.org/mine', kwargs={'user': 'jdoe'}).id, released.id)
        self.assertEqual(
            self.resolve('CodeSystem', 'http://example.org/mine', HEAD, kwargs={'user': 'jdoe'}).id, head.id)

    def test_an_unknown_owner_base(self):
        self.assertUnresolved('CodeSystem', ICD10, kwargs={'org': 'nobody'})
        self.assertUnresolved('CodeSystem', ICD10, kwargs={'user': 'nobody'})

    def test_a_deleted_repository(self):
        self.ciel_source.is_active = False
        self.ciel_source.save()
        self.assertUnresolved('CodeSystem', CIEL)

    def test_another_owners_canonical_at_an_owner_base(self):
        """With the registry, an owner base falls through to the global entry; without it, an owner base is strict."""
        if self.registry:
            self.assertEqual(self.resolve('CodeSystem', CIEL, kwargs={'org': 'sehatapp'}).id, self.ciel_v1.id)
        else:
            self.assertUnresolved('CodeSystem', CIEL, kwargs={'org': 'sehatapp'})

    def test_a_private_copy_of_a_canonical_and_a_public_one(self):
        """
        Without the registry, the global base matches a canonical only among the repositories it shows, so the private
        copy of a shared canonical (the lower id) never hides the public one. A registry entry is the curated answer:
        when it names the private copy's namespace, nothing else is tried. Each owner base still finds its own.
        """
        shared = 'http://example.org/shared-codes'
        private = source(self.fixtures, 'shared', canonical_url=shared, public_access=ACCESS_TYPE_NONE)
        private_v1 = source(self.fixtures, 'shared', 'v1', released=True, canonical_url=shared,
                            public_access=ACCESS_TYPE_NONE)
        source(self.hl7, 'shared', canonical_url=shared)
        public_v1 = source(self.hl7, 'shared', 'v1', released=True, canonical_url=shared)
        self.assertLess(private.id, public_v1.id)
        if self.registry:
            GlobalURLRegistryFactory(url=shared, namespace=self.fixtures.uri)

        for user in (ANONYMOUS, self.member, self.admin):
            if self.registry:
                self.assertUnresolved('CodeSystem', shared, user=user)
            else:
                self.assertEqual(self.resolve('CodeSystem', shared, user=user).id, public_v1.id)
                self.assertEqual(self.resolve('CodeSystem', shared, 'v1', user=user).id, public_v1.id)
        self.assertEqual(
            self.resolve('CodeSystem', shared, kwargs={'org': 'FHIR-Fixtures'}, user=self.member).id, private_v1.id)
        self.assertUnresolved('CodeSystem', shared, kwargs={'org': 'FHIR-Fixtures'}, user=self.outsider)
        self.assertEqual(self.resolve('CodeSystem', shared, kwargs={'org': 'HL7'}).id, public_v1.id)

    def test_a_public_release_of_a_private_repository(self):
        """
        A repository's HEAD must be viewable for its releases to be: a release made public before its repository was
        made private resolves only at its owner's base, for those who may view the owner's private repositories.
        """
        url = 'http://moh.example/fhir/CodeSystem/hiv'
        moh = OrganizationFactory(mnemonic='MOH')
        moh_member = UserProfileFactory(username='moh-member')
        moh.members.add(moh_member)
        source(moh, 'hiv', canonical_url=url, public_access=ACCESS_TYPE_NONE)
        release = source(moh, 'hiv', 'v1', released=True, canonical_url=url)
        if self.registry:
            GlobalURLRegistryFactory(url=url, namespace=moh.uri)

        for user in (ANONYMOUS, self.outsider, moh_member, self.admin):
            for reference in (url, f'{url}|v1', '/orgs/MOH/CodeSystem/hiv/'):
                self.assertUnresolved('CodeSystem', reference, user=user)
        for user in (ANONYMOUS, self.outsider):
            self.assertUnresolved('CodeSystem', url, kwargs={'org': 'MOH'}, user=user)
        for user in (moh_member, self.admin):
            for reference in (url, f'{url}|v1', '/orgs/MOH/CodeSystem/hiv/'):
                self.assertEqual(self.resolve('CodeSystem', reference, kwargs={'org': 'MOH'}, user=user).id, release.id)

    def test_the_default_version_is_the_newest_release_the_caller_is_shown(self):
        """
        A newest release that is private doesn't hide an older public one at the global base, or from those who may
        not view the owner's private repositories at its base; those who may get the newest. A named one is as named.
        """
        url = 'http://example.org/newest-private'
        source(self.hl7, 'newest', canonical_url=url)
        public_v1 = source(self.hl7, 'newest', 'v1', released=True, canonical_url=url)
        private_v2 = source(self.hl7, 'newest', 'v2', released=True, canonical_url=url, public_access=ACCESS_TYPE_NONE)
        if self.registry:
            GlobalURLRegistryFactory(url=url, namespace=self.hl7.uri)
        for kwargs, user in ((None, ANONYMOUS), (None, self.admin), ({'org': 'HL7'}, self.outsider)):
            self.assertEqual(self.resolve('CodeSystem', url, kwargs=kwargs, user=user).id, public_v1.id)
            self.assertUnresolved('CodeSystem', url, 'v2', kwargs, user)
        self.assertEqual(self.resolve('CodeSystem', url, kwargs={'org': 'HL7'}, user=self.admin).id, private_v2.id)

    def test_a_version_in_an_ocl_path_is_a_version_named(self):
        """
        /orgs/{org}/sources/{id}/{version}/ names that version, so a private one answers as a missing one: it never
        falls back to the newest public release, as a request naming no version does (Codex F1.1).
        """
        source(self.hl7, 'newest')
        public_v1 = source(self.hl7, 'newest', 'v1', released=True)
        private_v2 = source(self.hl7, 'newest', 'v2', released=True, public_access=ACCESS_TYPE_NONE)
        for path in ('/orgs/HL7/sources/newest/v2/', '/orgs/HL7/CodeSystem/newest/v2/',
                     '/orgs/HL7/sources/newest/v2/concepts/c1/'):
            error = self.assertUnresolved('CodeSystem', path)
            self.assertEqual(error.version, 'v2')
            self.assertEqual(self.resolve('CodeSystem', path, kwargs={'org': 'HL7'}, user=self.admin).id, private_v2.id)
        self.assertEqual(self.resolve('CodeSystem', '/orgs/HL7/CodeSystem/newest/v1/').id, public_v1.id)
        self.assertEqual(self.resolve('CodeSystem', '/orgs/HL7/CodeSystem/newest/').id, public_v1.id)

    def test_query_counts(self):
        """
        A hot path: these counts may only fall. Without the registry: the toggle, the repository and its latest
        released (or named) version; an owner base adds the owner, and a private repository its owner and the
        caller's membership. The registry adds the entry and the owner of its namespace; at an owner base, the
        owner's own entry instead of the global one, and the owner a second time.
        """
        extra = 2 if self.registry else 0
        for count, args in (
                (3 + extra, ('CodeSystem', ICD10)),
                (3 + extra, ('CodeSystem', ICD10, 'ICD102019')),
                (4 + extra, ('ValueSet', PBD, None, {'org': 'padam'})),
                # the caller's right to the owner's private repositories is checked once, on HEAD; the registry finds
                # HEAD through its owner, so its organization is already known
                (6 + extra - bool(self.registry), ('CodeSystem', FIXTURE, None, {'org': 'FHIR-Fixtures'}, self.member)),
        ):
            with self.subTest(args=args), self.assertNumQueries(count):
                self.resolve(*args)


class FhirResolveWithoutRegistryTest(FhirResolveCases, OCLTestCase):
    registry = False


class FhirResolveWithRegistryTest(FhirResolveCases, OCLTestCase):
    registry = True

    def test_an_unregistered_canonical_is_unresolved_at_the_global_base(self):
        """The registry's rule: at the global base only a global entry resolves, whatever repositories hold."""
        URLRegistry.objects.filter(url=ICD10).delete()
        self.assertUnresolved('CodeSystem', ICD10)
        self.assertEqual(self.resolve('CodeSystem', ICD10, kwargs={'org': 'WHO'}).id, self.who_icd_2019.id)
