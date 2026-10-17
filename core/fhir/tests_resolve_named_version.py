"""
A version named resolves to the repository that holds it (decision 9), with the URL registry toggle off and on. When
the repository resolution picks for a canonical URL doesn't serve a release of the version named, the release of the
lowest-id repository the base shows the caller with that canonical URL that holds one is served; an unreleased version
(or HEAD) only from the first repository the base shows; never a repository or a version the caller isn't shown, and
with no version named nothing changes. A registry entry still decides, and where resolution finds no repository at all
it still finds none. resolve() itself; $lookup, CodeSystem and ValueSet $validate-code (a member's system and version
too), $expand, $translate and search, GET and POST; TerminologyCapabilities; what a hidden holder costs; the queries.
OpenConceptLab/ocl_online#451.
"""
import json

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.collections.tests.factories import ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE, HEAD
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.fhir.resolve import UnresolvedError, resolve
from core.fhir.tests_ops_lookup import name
from core.fhir.tests_search import source, collection
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.url_registry.factories import GlobalURLRegistryFactory, OrganizationURLRegistryFactory
from core.url_registry.models import URLRegistry
from core.url_registry.tests_resolution import set_registry_toggle
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory

ICD10 = 'http://hl7.org/fhir/sid/icd-10'
ATC = 'http://www.whocc.no/atc'
LOCAL = 'http://example.org/fhir/CodeSystem/local'
SETS = 'http://example.org/fhir/ValueSet/sets'
FIRST = 'http://example.org/fhir/CodeSystem/first-private'
ONLY = 'http://example.org/fhir/CodeSystem/only-private'
THREE = 'http://example.org/fhir/CodeSystem/three-holders'
OPEN_HEAD = 'http://example.org/fhir/CodeSystem/open-head'


def client_for(user=None):
    client = APIClient()
    if user:
        client.force_authenticate(user)
    return client


def values(response):
    """A Parameters response as {name: value} (the first of each name), each match as system|code|equivalence."""
    found = {}
    for item in response.data['parameter']:
        if item['name'] == 'match':
            parts = {part['name']: part for part in item['part']}
            coding = parts['concept']['valueCoding']
            found.setdefault('matches', []).append(
                f"{coding['system']}|{coding['code']}|{parts['equivalence']['valueCode']}")
        else:
            value = next((value for key, value in item.items() if key.startswith('value')), None)
            found.setdefault(item['name'], value)
    return found


class NamedVersionCorpusMixin:  # pylint: disable=too-many-instance-attributes
    """
    ICD-10 held by WHO (first, the lower ids, as in production; ICD102019, with S20.4) and sehatapp (v1.0, with D64.2,
    which maps to WHO's S20.4); ATC held by sehatapp first (2026-04-25), as in the local corpus, and WHO (2023AA, with
    R02AB01). HL7's two code systems sharing a canonical (local-a's a1 with A1; local-b's b1 with B1, which maps to A1)
    and Acme's third (c1). Acme's two value sets sharing a canonical: set-a's v1 holds S20.4, set-b's v2 holds D64.2.
    With the registry, the global entries send each canonical to the first holder's owner.
    """
    registry = False

    def setUp(self):  # pylint: disable=too-many-statements
        set_registry_toggle(self.registry)
        super().setUp()
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.who = OrganizationFactory(mnemonic='WHO')
        self.sehatapp = OrganizationFactory(mnemonic='sehatapp')
        self.hl7 = OrganizationFactory(mnemonic='HL7')
        self.acme = OrganizationFactory(mnemonic='Acme')
        self.hl7_member = UserProfileFactory(username='hl7-member')
        self.hl7.members.add(self.hl7_member)
        self.outsider = UserProfileFactory(username='outsider')

        self.who_icd = source(self.who, 'ICD-10-WHO', canonical_url=ICD10)
        self.who_2019 = source(self.who, 'ICD-10-WHO', 'ICD102019', released=True, canonical_url=None)
        self.s204 = ConceptFactory(parent=self.who_icd, mnemonic='S20.4', sources=[self.who_2019], names=[
            name('Other superficial injuries of back wall of thorax', preferred=True)])
        self.sehat_icd = source(self.sehatapp, 'ICD-10', canonical_url=ICD10)
        self.sehat_v1 = source(self.sehatapp, 'ICD-10', 'v1.0', released=True, canonical_url=ICD10)
        self.d642 = ConceptFactory(parent=self.sehat_icd, mnemonic='D64.2', sources=[self.sehat_v1], names=[
            name('Secondary sideroblastic anaemia due to drugs and toxins', preferred=True)])
        self.mapping(self.sehat_icd, 'D64.2', self.who_icd, 'S20.4', [self.sehat_v1])

        self.sehat_atc = source(self.sehatapp, 'ATC', canonical_url=ATC)
        self.sehat_atc_v = source(self.sehatapp, 'ATC', '2026-04-25', released=True, canonical_url=ATC)
        self.who_atc = source(self.who, 'WHOATC', canonical_url=ATC)
        self.who_atc_2023 = source(self.who, 'WHOATC', '2023AA', released=True, canonical_url=None)
        ConceptFactory(parent=self.who_atc, mnemonic='R02AB01', sources=[self.who_atc_2023],
                       names=[name('neomycin', preferred=True)])

        self.local_a = source(self.hl7, 'local-a', canonical_url=LOCAL)
        self.local_a1 = source(self.hl7, 'local-a', 'a1', released=True, canonical_url=LOCAL)
        ConceptFactory(parent=self.local_a, mnemonic='A1', sources=[self.local_a1],
                       names=[name('A one', preferred=True)])
        self.local_b = source(self.hl7, 'local-b', canonical_url=LOCAL)
        self.local_b1 = source(self.hl7, 'local-b', 'b1', released=True, canonical_url=LOCAL)
        ConceptFactory(parent=self.local_b, mnemonic='B1', sources=[self.local_b1],
                       names=[name('B one', preferred=True)])
        self.mapping(self.local_b, 'B1', self.local_a, 'A1', [self.local_b1])
        self.local_c = source(self.acme, 'local-c', canonical_url=LOCAL)
        self.local_c1 = source(self.acme, 'local-c', 'c1', released=True, canonical_url=LOCAL)

        self.set_a = collection(self.acme, 'set-a', canonical_url=SETS)
        self.set_a1 = self.value_set_release('set-a', 'v1', self.s204)
        self.set_b = collection(self.acme, 'set-b', canonical_url=SETS)
        self.set_b2 = self.value_set_release('set-b', 'v2', self.d642)

        if self.registry:
            for url, owner in ((ICD10, self.who), (ATC, self.who), (LOCAL, self.hl7), (SETS, self.acme)):
                GlobalURLRegistryFactory(url=url, namespace=owner.uri)

    def tearDown(self):
        set_registry_toggle(False)
        super().tearDown()

    @staticmethod
    def mapping(parent, from_code, to_source, to_code, releases):
        return MappingFactory(
            parent=parent, map_type='SAME-AS', from_concept=None, to_concept=None, from_source=parent,
            from_source_url=parent.uri, from_concept_code=from_code, to_source=to_source, to_source_url=to_source.uri,
            to_concept_code=to_code, sources=releases)

    def value_set_release(self, mnemonic, version, *concepts):
        release = collection(self.acme, mnemonic, version, released=True, canonical_url=SETS)
        expansion = ExpansionFactory(mnemonic=f'autoexpand-{version}', collection_version=release)
        expansion.concepts.add(*concepts)
        release.expansion_uri = expansion.uri
        release.save()
        return release

    def assertUnresolved(self, url, version=None, kwargs=None, user=None):  # pylint: disable=invalid-name
        with self.assertRaises(UnresolvedError) as context:
            resolve('CodeSystem', url, version, kwargs, user)
        return context.exception

    def resolved(self, url, version=None, kwargs=None, user=None, resource_type='CodeSystem'):  # pylint: disable=too-many-arguments
        return resolve(resource_type, url, version, kwargs, user).id

    @staticmethod
    def get(operation, base='/fhir', user=None, resource='CodeSystem', **params):  # pylint: disable=too-many-arguments
        return client_for(user).get(f'{base}/{resource}/{operation}', params)

    @staticmethod
    def post(operation, parameters, base='/fhir', user=None, resource='CodeSystem'):  # pylint: disable=too-many-arguments
        return client_for(user).post(
            f'{base}/{resource}/{operation}', json.dumps({'resourceType': 'Parameters', 'parameter': parameters}),
            content_type='application/fhir+json')


class NamedVersionResolveCases(NamedVersionCorpusMixin):
    """resolve() with a version named, toggle off and on (the subclasses)."""

    def test_a_version_another_repository_holds_resolves_to_its_holder(self):
        """At /fhir/ without the registry, the lowest-id repository the base shows that holds the release; with it,
        the global entry decides, as before."""
        if self.registry:
            self.assertUnresolved(ICD10, 'v1.0')
            self.assertUnresolved(f'{ICD10}|v1.0')
            self.assertUnresolved(ATC, '2026-04-25')
        else:
            self.assertEqual(self.resolved(ICD10, 'v1.0'), self.sehat_v1.id)
            self.assertEqual(self.resolved(f'{ICD10}|v1.0'), self.sehat_v1.id)
            self.assertEqual(self.resolved(ATC, '2026-04-25'), self.sehat_atc_v.id)
        self.assertEqual(self.resolved(ATC, '2023AA'), self.who_atc_2023.id)
        self.assertEqual(self.resolved(f'{ATC}|2023AA'), self.who_atc_2023.id)
        self.assertEqual(self.resolved(ICD10, 'ICD102019'), self.who_2019.id)
        # each owner base finds its own, as before
        self.assertEqual(self.resolved(ICD10, 'v1.0', {'org': 'sehatapp'}), self.sehat_v1.id)
        self.assertEqual(self.resolved(ATC, '2023AA', {'org': 'WHO'}), self.who_atc_2023.id)
        self.assertUnresolved(ICD10, 'ICD102019', {'org': 'sehatapp'})

    def test_the_holder_keeps_the_canonical_url_it_was_named_by(self):
        instance = resolve('CodeSystem', ATC, '2023AA')
        self.assertEqual((instance.canonical_url, instance.is_fqdn, instance.resolution_url), (ATC, True, ATC))
        self.assertEqual(instance.repository_public_access, self.who_atc.public_access)

    def test_with_no_version_named_nothing_changes(self):
        self.assertEqual(self.resolved(ICD10), self.who_2019.id)
        self.assertEqual(self.resolved(ATC), self.who_atc_2023.id if self.registry else self.sehat_atc_v.id)
        self.assertEqual(self.resolved(LOCAL, kwargs={'org': 'HL7'}), self.local_a1.id)
        self.assertEqual(self.resolved(LOCAL), self.local_a1.id)

    def test_the_first_choice_keeps_a_release_it_holds(self):
        """If several repositories hold the release, today's order between them: the first choice's own."""
        own = source(self.who, 'ICD-10-WHO', 'v1.0', released=True)
        self.assertEqual(self.resolved(ICD10, 'v1.0'), own.id)

    def test_several_holders_keep_todays_order(self):
        """The lowest-id repository holding the release, then the next once it no longer does."""
        source(self.hl7, 'icd-copy', canonical_url=ICD10)
        copy = source(self.hl7, 'icd-copy', 'v1.0', released=True, canonical_url=ICD10)
        if self.registry:
            self.assertUnresolved(ICD10, 'v1.0')
            return
        self.assertEqual(self.resolved(ICD10, 'v1.0'), self.sehat_v1.id)
        self.sehat_v1.is_active = False
        self.sehat_v1.save()
        self.assertEqual(self.resolved(ICD10, 'v1.0'), copy.id)

    def test_no_holder_keeps_todays_not_found(self):
        error = self.assertUnresolved(ICD10, 'nope')
        self.assertEqual((error.url, error.version), (ICD10, 'nope'))
        self.assertEqual(str(error.detail), f"A definition for CodeSystem '{ICD10}' version 'nope' could not be found")
        self.assertUnresolved(LOCAL, 'c1', {'org': 'HL7'})  # another owner's, at an owner base

    def test_a_release_wins_over_an_unreleased_version_of_the_first_choice(self):
        """
        The first choice holds v2.0 unreleased and sehatapp a release of it: the release. Without one, the first
        choice's unreleased version, as before (and with the registry, whose entry decides, always).
        """
        unreleased = source(self.who, 'ICD-10-WHO', 'v2.0')
        release = source(self.sehatapp, 'ICD-10', 'v2.0', released=True, canonical_url=ICD10)
        self.assertEqual(self.resolved(ICD10, 'v2.0'), unreleased.id if self.registry else release.id)
        release.is_active = False
        release.save()
        self.assertEqual(self.resolved(ICD10, 'v2.0'), unreleased.id)

    def test_another_repositorys_unreleased_version_is_never_served(self):
        draft = source(self.sehatapp, 'ICD-10', 'draft', canonical_url=ICD10)
        self.assertUnresolved(ICD10, 'draft')
        self.assertEqual(self.resolved(ICD10, 'draft', {'org': 'sehatapp'}), draft.id)

    def test_head_named_is_the_first_choices(self):
        self.assertEqual(self.resolved(ICD10, HEAD), self.who_icd.id)
        self.assertEqual(self.resolved(f'{ICD10}|HEAD'), self.who_icd.id)
        self.assertEqual(self.resolved(LOCAL, HEAD, {'org': 'HL7'}), self.local_a.id)

    def test_an_owner_base_picks_among_the_owners_code_systems(self):
        """With the registry too: HL7's registry has no entry for LOCAL, so HL7's own code systems are the step that
        picks. At /fhir/ without the registry every public holder is a candidate; with it, the global entry decides."""
        for user in (None, self.outsider, self.hl7_member):
            self.assertEqual(self.resolved(LOCAL, 'b1', {'org': 'HL7'}, user), self.local_b1.id, user)
        self.assertUnresolved(LOCAL, 'c1', {'org': 'HL7'})
        self.assertEqual(self.resolved(LOCAL, 'c1', {'org': 'Acme'}), self.local_c1.id)
        self.assertUnresolved(LOCAL, 'b1', {'org': 'Acme'})
        if self.registry:
            self.assertUnresolved(LOCAL, 'b1')
            self.assertUnresolved(LOCAL, 'c1')
        else:
            self.assertEqual(self.resolved(LOCAL, 'b1'), self.local_b1.id)
            self.assertEqual(self.resolved(LOCAL, 'c1'), self.local_c1.id)

    def test_an_owner_registry_entry_decides(self):
        """Acme's registry sends LOCAL to HL7: there, HL7's first code system is the answer, whatever another holds."""
        OrganizationURLRegistryFactory(organization=self.acme, url=LOCAL, namespace=self.hl7.uri)
        acme = {'org': 'Acme'}
        if self.registry:
            self.assertEqual(self.resolved(LOCAL, 'a1', acme), self.local_a1.id)
            self.assertUnresolved(LOCAL, 'b1', acme)
            self.assertUnresolved(LOCAL, 'c1', acme)
        else:
            self.assertEqual(self.resolved(LOCAL, 'c1', acme), self.local_c1.id)
            self.assertUnresolved(LOCAL, 'a1', acme)

    def test_where_resolution_finds_no_repository_it_still_finds_none(self):
        """With the registry, at /fhir/ a canonical the global registry doesn't hold stays unresolved, named version or
        not; an owner base still finds its own. Without it, nothing is registered anyway."""
        URLRegistry.objects.filter(url__in=[ICD10, LOCAL]).delete()
        for version in ('v1.0', 'ICD102019'):
            if self.registry:
                self.assertUnresolved(ICD10, version)
            else:
                self.resolved(ICD10, version)
        self.assertEqual(self.resolved(ICD10, 'v1.0', {'org': 'sehatapp'}), self.sehat_v1.id)
        self.assertEqual(self.resolved(LOCAL, 'b1', {'org': 'HL7'}), self.local_b1.id)
        self.assertUnresolved('http://example.org/fhir/CodeSystem/nobody', 'v1.0')

    def test_a_retired_release_is_served_named_beside_a_live_one(self):
        retired = source(self.sehatapp, 'ICD-10', 'v0.9', released=True, retired=True, canonical_url=ICD10)
        if self.registry:
            self.assertUnresolved(ICD10, 'v0.9')
        else:
            self.assertEqual(self.resolved(ICD10, 'v0.9'), retired.id)

    def test_query_counts(self):
        """
        A hot path: the first choice serves the release named in the queries it always has (the toggle, the HEAD, the
        version; the registry adds its entry and the namespace's owner). Only when it doesn't, one more: the holder's.
        An owner base adds the owner; however many repositories hold the canonical.
        """
        extra = 2 if self.registry else 0
        holder = 0 if self.registry else 1  # with the registry, the entry decides: no holder is looked for
        with self.assertNumQueries(3 + extra):
            resolve('CodeSystem', ICD10, 'ICD102019')
        with self.assertNumQueries(3 + extra + holder):
            self.assertEqual(resolve('CodeSystem', ATC, '2023AA').id, self.who_atc_2023.id)
        with self.assertNumQueries(3 + extra + holder):
            self.assertUnresolved(ICD10, 'nope')
        owner = 2 if self.registry else 0  # the owner's registry entry, the owner again
        with self.assertNumQueries(4 + owner):
            resolve('CodeSystem', LOCAL, 'a1', {'org': 'HL7'})
        with self.assertNumQueries(5 + owner):
            self.assertEqual(resolve('CodeSystem', LOCAL, 'b1', {'org': 'HL7'}).id, self.local_b1.id)
        for number in range(20):
            org = OrganizationFactory(mnemonic=f'holder-{number}')
            source(org, 'atc', canonical_url=ATC)
            source(org, 'atc', '2023AA', released=True, canonical_url=ATC)
            source(self.hl7, f'local-{number}', canonical_url=LOCAL)
            source(self.hl7, f'local-{number}', 'b1', released=True, canonical_url=LOCAL)
        with self.assertNumQueries(3 + extra + holder):
            self.assertEqual(resolve('CodeSystem', ATC, '2023AA').id, self.who_atc_2023.id)
        with self.assertNumQueries(5 + owner):
            self.assertEqual(resolve('CodeSystem', LOCAL, 'b1', {'org': 'HL7'}).id, self.local_b1.id)


class HiddenHolderCases(NamedVersionCorpusMixin):
    """A repository or version the caller isn't shown never holds the version named: the same bytes and statements as
    a world without it, at /fhir/ and at an owner base."""

    def answers(self, requests):
        """{request: (status, content, SQL statements)} for (base, user, operation, params) requests."""
        found = {}
        for base, user, operation, params in requests:
            with CaptureQueriesContext(connection) as context:
                response = self.get(operation, base=base, user=user, **params)
            content = json.loads(response.content)
            content.pop('meta', None)  # a search Bundle's lastUpdated is the time it was made
            found[(base, user.username if user else None, operation, tuple(sorted(params.items())))] = (
                response.status_code, content, [query['sql'] for query in context.captured_queries])
        return found

    def test_at_the_global_base(self):
        """A private repository with the canonical, a public one's private release, and a public release of a private
        repository: none is found, for anyone, and the answers and statements are those without them."""
        private_owner = UserProfileFactory(username='jdoe')
        source(private_owner, 'icd-private', canonical_url=ICD10, public_access=ACCESS_TYPE_NONE)
        source(private_owner, 'icd-private', 'p9', released=True, canonical_url=ICD10, public_access=ACCESS_TYPE_NONE)
        source(self.sehatapp, 'ICD-10', 'v8', released=True, canonical_url=ICD10, public_access=ACCESS_TYPE_NONE)
        moh = OrganizationFactory(mnemonic='MOH')
        source(moh, 'icd-moh', canonical_url=ICD10, public_access=ACCESS_TYPE_NONE)
        source(moh, 'icd-moh', 'v7', released=True, canonical_url=ICD10)
        requests = [('/fhir', user, operation, {**params, 'version': version})
                    for user in (None, self.outsider, private_owner, self.admin) for version in ('p9', 'v8', 'v7')
                    for operation, params in (('$lookup', {'system': ICD10, 'code': 'X'}),
                                              ('$validate-code', {'url': ICD10, 'code': 'X'}))]
        with_them = self.answers(requests)
        for (_, _, operation, _), (status, _, _) in with_them.items():
            self.assertEqual(status, {'$lookup': 422, '$validate-code': 200}[operation])
        searched = self.answers([('/fhir', None, '', {'url': ICD10, 'version': version}) for version in ('p9', 'v7')])
        capabilities = client_for(self.admin).get('/fhir/metadata', {'mode': 'terminology'}).content
        self.assertNotIn(b'p9', capabilities)
        private_owner.source_set.all().delete()
        moh.source_set.all().delete()
        self.sehatapp.source_set.filter(version='v8').delete()
        self.assertEqual(self.answers(requests), with_them)
        self.assertEqual(client_for(self.admin).get('/fhir/metadata', {'mode': 'terminology'}).content, capabilities)
        # search doesn't resolve: its answer is the same (its statements name every repository with the URL, as before)
        self.assertEqual({key: answer[:2] for key, answer in self.answers(
            [('/fhir', None, '', {'url': ICD10, 'version': version}) for version in ('p9', 'v7')]).items()},
                         {key: answer[:2] for key, answer in searched.items()})

    def test_at_an_owner_base(self):
        """HL7's private local-private holds p1: those who may not view it answer as if it weren't there; members and
        staff are served it."""
        private = source(self.hl7, 'local-private', canonical_url=LOCAL, public_access=ACCESS_TYPE_NONE)
        p1 = source(self.hl7, 'local-private', 'p1', released=True, canonical_url=LOCAL, public_access=ACCESS_TYPE_NONE)
        ConceptFactory(parent=private, mnemonic='P1', sources=[p1], names=[name('Private one', preferred=True)])
        for user in (self.hl7_member, self.admin):
            self.assertEqual(self.resolved(LOCAL, 'p1', {'org': 'HL7'}, user), p1.id, user)
            self.assertEqual(self.get('$lookup', '/orgs/HL7', user, system=LOCAL, version='p1', code='P1').status_code,
                             200, user)
        requests = [('/orgs/HL7', user, '$lookup', {'system': LOCAL, 'version': 'p1', 'code': 'P1'})
                    for user in (None, self.outsider)]
        requests += [('/orgs/HL7', user, '$validate-code', {'url': LOCAL, 'version': 'p1', 'code': 'P1'})
                     for user in (None, self.outsider)]
        with_it = self.answers(requests)
        capabilities = client_for(self.outsider).get('/orgs/HL7/metadata', {'mode': 'terminology'}).content
        Source.objects.filter(organization=self.hl7, mnemonic='local-private').delete()
        self.assertEqual(self.answers(requests), with_it)
        self.assertEqual(client_for(self.outsider).get('/orgs/HL7/metadata', {'mode': 'terminology'}).content,
                         capabilities)

    def test_a_hidden_first_choice_counts_as_missing(self):  # pylint: disable=too-many-locals
        """
        Lab's private first-private is its lowest-id code system with the canonical. With a version named, those who
        may not view it are answered from second-public as if first-private weren't there: its release, its unreleased
        version, its HEAD, or not found. Anonymously, a release or HEAD in as many queries; otherwise one fewer, the
        version of second-public the resolver reads when it picks it, as a hidden first choice resolves to nothing
        before any version is read (decision A.3, as today). With none named, A.3 is unchanged: not found.
        """
        lab = OrganizationFactory(mnemonic='Lab')
        lab_member = UserProfileFactory(username='lab-member')
        lab.members.add(lab_member)
        hidden = source(lab, 'first-private', canonical_url=FIRST, public_access=ACCESS_TYPE_NONE)
        hidden_v1 = source(lab, 'first-private', 'v1', released=True, canonical_url=FIRST,
                           public_access=ACCESS_TYPE_NONE)
        ConceptFactory(parent=hidden, mnemonic='C1', sources=[hidden_v1], names=[name('Hidden one', preferred=True)])
        shown = source(lab, 'second-public', canonical_url=FIRST)
        releases = [source(lab, 'second-public', version, released=True, canonical_url=FIRST)
                    for version in ('v1', 'v2')]
        unreleased = source(lab, 'second-public', 'u1', canonical_url=FIRST)
        ConceptFactory(parent=shown, mnemonic='C1', sources=[*releases, unreleased],
                       names=[name('Shown one', preferred=True)])
        kwargs = {'org': 'Lab'}
        self.assertEqual(self.resolved(FIRST, 'v1', kwargs, lab_member), hidden_v1.id)
        expected = {'v1': releases[0], 'v2': releases[1], 'u1': unreleased, HEAD: shown}
        for user in (None, self.outsider):
            for version, instance in expected.items():
                self.assertEqual(self.resolved(FIRST, version, kwargs, user), instance.id, (user, version))
            self.assertUnresolved(FIRST, None, kwargs, user)  # decision A.3

        def answers():
            found = {}
            for user in (None, self.outsider):
                for version in (*expected, 'nope'):
                    with CaptureQueriesContext(connection) as context:
                        response = self.get('$lookup', '/orgs/Lab', user, system=FIRST, version=version, code='C1')
                    found[(user, version)] = (response.status_code, response.content, len(context.captured_queries))
            return found

        with_it = answers()
        Source.objects.filter(organization=lab, mnemonic='first-private').delete()
        without = answers()
        for key, (status, content, count) in with_it.items():
            self.assertEqual((status, content), without[key][:2], key)
            if key[0] is None:  # anonymous: no membership to check
                self.assertEqual(count, without[key][2] - (key[1] in ('u1', 'nope')), key)

    def test_a_hidden_holder_alone_costs_what_an_unknown_canonical_does(self):
        """Anonymous, at an owner base, with a version named: a private code system costs an unknown one's queries.
        With the registry an owner-base match stops the fall-through to the global entry (decision A.3)."""
        if self.registry:
            self.skipTest('with the registry, an owner-base match stops the fall-through (decision A.3)')
        lab = OrganizationFactory(mnemonic='Lab')
        source(lab, 'only-private', canonical_url=ONLY, public_access=ACCESS_TYPE_NONE)
        source(lab, 'only-private', 'x1', released=True, canonical_url=ONLY, public_access=ACCESS_TYPE_NONE)
        counts = []
        for system in (ONLY, 'http://example.org/fhir/CodeSystem/unknown'):
            with CaptureQueriesContext(connection) as context:
                response = self.get('$lookup', '/orgs/Lab', system=system, version='x1', code='X')
            self.assertEqual(response.status_code, 422, system)
            counts.append(len(context.captured_queries))
        self.assertEqual(counts[0], counts[1])

    def test_a_release_found_through_the_fallback_costs_what_no_hidden_repository_does(self):
        """
        Three of Lab's code systems share a canonical: private first-private (the lowest id) holds a released v2,
        second-public holds no v2, third-public holds v2 released. Anonymously and for a signed-in non-member, v2
        named is third-public's with and without first-private, byte for byte, so nothing in the answer says the
        hidden one is there. The statement count still differs by one: anonymously one more without it, the version
        read of second-public the resolver makes when it picks it, since a HEAD the caller isn't shown resolves to
        nothing before any version is read; for a non-member, without the registry, one fewer, its check of the
        right to the hidden repository. That is the A.3 residual of the shared resolver's early stop, not of this
        rule: the same deltas were measured on the code before this rule, where each world answers 422 instead of
        200. Closing them needs a shared-resolver change.
        """
        lab = OrganizationFactory(mnemonic='Lab')
        hidden = source(lab, 'first-private', canonical_url=THREE, public_access=ACCESS_TYPE_NONE)
        ConceptFactory(parent=hidden, mnemonic='C1', names=[name('Hidden one', preferred=True)], sources=[source(
            lab, 'first-private', 'v2', released=True, canonical_url=THREE, public_access=ACCESS_TYPE_NONE)])
        second = source(lab, 'second-public', canonical_url=THREE)
        ConceptFactory(parent=second, mnemonic='C1', names=[name('Second one', preferred=True)],
                       sources=[source(lab, 'second-public', 'v1', released=True, canonical_url=THREE)])
        third = source(lab, 'third-public', canonical_url=THREE)
        third_v2 = source(lab, 'third-public', 'v2', released=True, canonical_url=THREE)
        ConceptFactory(parent=third, mnemonic='C1', sources=[third_v2], names=[name('Third one', preferred=True)])
        self.assertEqual(self.resolved(THREE, 'v2', {'org': 'Lab'}), third_v2.id)

        def answers():
            found = {}
            for user in (None, self.outsider):
                with CaptureQueriesContext(connection) as context:
                    response = self.get('$lookup', '/orgs/Lab', user, system=THREE, version='v2', code='C1')
                found[user] = (response.status_code, response.content, len(context.captured_queries))
            return found

        with_it = answers()
        Source.objects.filter(organization=lab, mnemonic='first-private').delete()
        without = answers()
        deltas = {None: 1, self.outsider: 0 if self.registry else -1}  # statements without it, less those with it
        for user, (status, content, count) in with_it.items():
            self.assertEqual(status, 200, user)
            self.assertEqual((status, content), without[user][:2], user)
            self.assertEqual(without[user][2] - count, deltas[user], user)

    def test_a_repository_whose_releases_are_all_private_is_answered_as_a_missing_one(self):
        """
        Lab's first-open-head is its lowest-id code system with the canonical: its HEAD is public, every release of
        it is private, and it holds no v2. second-public holds v2 released. A repository with no release the base
        shows the caller is not shown at all, whatever version is named, so v2 named is second-public's with and
        without first-open-head, byte for byte, anonymously and for a signed-in non-member, under either registry
        state. It costs one statement more with it: the shared resolver shows its public HEAD, so it reads the v2
        it doesn't have before the holder is looked for. Before this rule the same corpus answered 422 with it and
        200 without, in two statements fewer; the rule closes the difference in the answer and leaves the one
        statement, which needs a shared-resolver change to close.
        """
        lab = OrganizationFactory(mnemonic='Lab')
        first = source(lab, 'first-open-head', canonical_url=OPEN_HEAD)
        ConceptFactory(parent=first, mnemonic='C1', names=[name('First one', preferred=True)], sources=[source(
            lab, 'first-open-head', 'v1', released=True, canonical_url=OPEN_HEAD, public_access=ACCESS_TYPE_NONE)])
        second = source(lab, 'second-public', canonical_url=OPEN_HEAD)
        second_v2 = source(lab, 'second-public', 'v2', released=True, canonical_url=OPEN_HEAD)
        ConceptFactory(parent=second, mnemonic='C1', sources=[second_v2], names=[name('Second one', preferred=True)])
        self.assertEqual(self.resolved(OPEN_HEAD, 'v2', {'org': 'Lab'}), second_v2.id)

        requests = [('/orgs/Lab', user, '$lookup', {'system': OPEN_HEAD, 'version': 'v2', 'code': 'C1'})
                    for user in (None, self.outsider)]
        with_it = self.answers(requests)
        Source.objects.filter(organization=lab, mnemonic='first-open-head').delete()
        without = self.answers(requests)
        for key, (status, content, sql) in with_it.items():
            self.assertEqual(status, 200, key)
            self.assertEqual((status, content), without[key][:2], key)
            self.assertEqual(len(without[key][2]) - len(sql), -1, key)  # one more with it: the v2 it doesn't have


class NamedVersionOperationsCases(NamedVersionCorpusMixin):
    """Every FHIR operation that resolves by canonical URL and version, GET and POST."""

    def test_lookup(self):
        for base, system, version, code, display, served in (
                ('/fhir', ICD10, 'v1.0', 'D64.2', 'Secondary sideroblastic anaemia due to drugs and toxins',
                 not self.registry),
                ('/fhir', ATC, '2023AA', 'R02AB01', 'neomycin', True),
                ('/orgs/HL7', LOCAL, 'b1', 'B1', 'B one', True),
                ('/fhir', LOCAL, 'b1', 'B1', 'B one', not self.registry)):
            answers = [
                self.get('$lookup', base, system=system, version=version, code=code),
                self.get('$lookup', base, system=f'{system}|{version}', code=code),
                self.post('$lookup', [{'name': 'system', 'valueUri': system}, {'name': 'code', 'valueCode': code},
                                      {'name': 'version', 'valueString': version}], base),
                self.post('$lookup', [{'name': 'coding', 'valueCoding': {
                    'system': system, 'code': code, 'version': version}}], base)]
            for response in answers:
                if not served:
                    self.assertEqual(response.status_code, 422, (base, system, response.data))
                    continue
                self.assertEqual(response.status_code, 200, (base, system, response.data))
                answer = values(response)
                self.assertEqual((answer['version'], answer['display'], answer['system']), (version, display, system))

    def test_lookup_query_counts(self):
        """$lookup's fixed number of queries (resolution, the concept, its names; the registry adds its entry and the
        namespace's owner): a release its holder serves costs one more than the first choice's, however many
        repositories hold the canonical URL."""
        extra = 2 if self.registry else 0
        holder = 0 if self.registry else 1  # with the registry, WHO's entry decides: WHOATC is the first choice
        with self.assertNumQueries(5 + extra):
            self.assertEqual(self.get('$lookup', system=ICD10, version='ICD102019', code='S20.4').status_code, 200)
        for number in range(10):
            org = OrganizationFactory(mnemonic=f'holder-{number}')
            source(org, 'atc', canonical_url=ATC)
            source(org, 'atc', '2023AA', released=True, canonical_url=ATC)
        with self.assertNumQueries(5 + extra + holder):
            self.assertEqual(self.get('$lookup', system=ATC, version='2023AA', code='R02AB01').status_code, 200)

    def test_code_system_validate_code(self):
        for base, url, version, code, served in (
                ('/fhir', ICD10, 'v1.0', 'D64.2', not self.registry), ('/fhir', ATC, '2023AA', 'R02AB01', True),
                ('/orgs/HL7', LOCAL, 'b1', 'B1', True)):
            answers = [
                self.get('$validate-code', base, url=url, version=version, code=code),
                self.get('$validate-code', base, url=f'{url}|{version}', code=code),
                self.post('$validate-code', [{'name': 'url', 'valueUri': url}, {'name': 'code', 'valueCode': code},
                                             {'name': 'version', 'valueString': version}], base),
                self.post('$validate-code', [{'name': 'url', 'valueUri': url}, {'name': 'coding', 'valueCoding': {
                    'system': url, 'code': code, 'version': version}}], base)]
            for response in answers:
                self.assertEqual(response.status_code, 200, (base, url, response.data))
                answer = values(response)
                self.assertIs(answer['result'], served, (base, url, answer))
                if served:
                    self.assertEqual(answer['version'], version)
                else:
                    self.assertEqual(answer['x-caused-by-unknown-system'], f'{url}|{version}')

    def test_value_set_validate_code(self):
        """The value set valueSetVersion names: set-b's v2, which holds sehatapp's D64.2."""
        for base, served in (('/fhir', not self.registry), ('/orgs/Acme', True)):
            answers = [
                self.get('$validate-code', base, resource='ValueSet', url=SETS, valueSetVersion='v2', system=ICD10,
                         code='D64.2'),
                self.get('$validate-code', base, resource='ValueSet', url=f'{SETS}|v2', system=ICD10, code='D64.2'),
                self.post('$validate-code', [
                    {'name': 'url', 'valueUri': SETS}, {'name': 'valueSetVersion', 'valueString': 'v2'},
                    {'name': 'coding', 'valueCoding': {'system': ICD10, 'code': 'D64.2'}}], base, resource='ValueSet')]
            for response in answers:
                if not served:
                    self.assertEqual(response.status_code, 422, (base, response.data))
                    continue
                self.assertEqual(response.status_code, 200, (base, response.data))
                answer = values(response)
                self.assertEqual((answer['result'], answer['version']), (True, 'v1.0'), base)

    def test_value_set_validate_code_resolves_a_members_system_and_version(self):
        """D64.2 isn't in set-a's v1: the answer says what its code system, ICD-10 at systemVersion v1.0, says of it,
        which is sehatapp's (without the registry; with it, the global entry's WHO doesn't hold v1.0)."""
        answers = [
            self.get('$validate-code', resource='ValueSet', url=SETS, valueSetVersion='v1', system=ICD10,
                     systemVersion='v1.0', code='D64.2'),
            self.post('$validate-code', [
                {'name': 'url', 'valueUri': SETS}, {'name': 'valueSetVersion', 'valueString': 'v1'},
                {'name': 'coding', 'valueCoding': {'system': ICD10, 'code': 'D64.2', 'version': 'v1.0'}}],
                resource='ValueSet')]
        for response in answers:
            self.assertEqual(response.status_code, 200, response.data)
            answer = values(response)
            self.assertIs(answer['result'], False)
            if self.registry:
                self.assertEqual(answer['x-unknown-system'], f'{ICD10}|v1.0')
            else:
                self.assertNotIn('x-unknown-system', answer)
                self.assertEqual((answer['version'], answer['display']),
                                 ('v1.0', 'Secondary sideroblastic anaemia due to drugs and toxins'))

    def test_expand(self):
        for base, served in (('/fhir', not self.registry), ('/orgs/Acme', True)):
            answers = [
                self.get('$expand', base, resource='ValueSet', url=SETS, valueSetVersion='v2'),
                self.get('$expand', base, resource='ValueSet', url=f'{SETS}|v2'),
                self.post('$expand', [{'name': 'url', 'valueUri': SETS},
                                      {'name': 'valueSetVersion', 'valueString': 'v2'}], base, resource='ValueSet')]
            for response in answers:
                if not served:
                    self.assertEqual(response.status_code, 422, (base, response.data))
                    continue
                self.assertEqual(response.status_code, 200, (base, response.data))
                self.assertEqual((response.data['url'], response.data['version']), (SETS, 'v2'))
                self.assertEqual([(item['system'], item['code']) for item in response.data['expansion']['contains']],
                                 [(ICD10, 'D64.2')])
        self.assertEqual(self.get('$expand', resource='ValueSet', url=SETS).data['version'], 'v1')  # the default

    def test_translate(self):
        """The code system version a code's system and version name, whose own mappings are its map; and a ConceptMap
        by url and conceptMapVersion."""
        for base, system, version, code, match, served in (
                ('/fhir', ICD10, 'v1.0', 'D64.2', f'{ICD10}|S20.4|equivalent', not self.registry),
                ('/orgs/HL7', LOCAL, 'b1', 'B1', f'{LOCAL}|A1|equivalent', True)):
            answers = [
                client_for().get(f'{base}/ConceptMap/$translate', {'system': system, 'version': version, 'code': code}),
                client_for().get(f'{base}/ConceptMap/$translate', {'system': f'{system}|{version}', 'code': code}),
                client_for().post(f'{base}/ConceptMap/$translate', {'resourceType': 'Parameters', 'parameter': [
                    {'name': 'coding', 'valueCoding': {'system': system, 'code': code, 'version': version}}]},
                    format='json'),
                client_for().get(f'{base}/ConceptMap/$translate', {
                    'url': system, 'conceptMapVersion': version, 'system': system, 'code': code})]
            for index, response in enumerate(answers):
                if not served and index == 3:  # the ConceptMap url names doesn't resolve
                    self.assertEqual(response.status_code, 404, response.data)
                    continue
                self.assertEqual(response.status_code, 200, (base, index, response.data))
                answer = values(response)
                if served:
                    self.assertEqual((answer['result'], answer['matches']), (True, [match]), (base, index))
                else:
                    self.assertEqual((answer['result'], answer['message']), (
                        False, f"No ConceptMap is available to translate from '{system}' to '(unspecified)'"))

    def test_search_by_url_and_version_finds_the_holders_release(self):
        """Search lists every version the base shows with the URL, so it finds the holder's as it always has."""
        for resource, url, version, expected in (('CodeSystem', ICD10, 'v1.0', self.sehat_v1),
                                                 ('ValueSet', SETS, 'v2', self.set_b2)):
            response = client_for().get(f'/fhir/{resource}/', {'url': url, 'version': version})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual([entry['resource']['id'] for entry in response.data['entry']], [str(expected.id)])

    def test_terminology_capabilities_lists_the_holders_versions_under_the_canonical(self):
        """
        Each canonical lists the releases its first choice serves, then those of the other repositories the base shows
        with it that the operations now serve by name, the default unchanged; with the registry, where an entry
        decides, the entry's repository's alone. Each listed version resolves.
        """
        def listed(fhir_base):
            response = client_for().get(f'{fhir_base}/metadata', {'mode': 'terminology'})
            return {item['uri']: [version['code'] + ('*' if version.get('isDefault') else '')
                                  for version in item.get('version', [])] for item in response.data['codeSystem']}

        at_fhir = listed('/fhir')
        if self.registry:
            self.assertEqual((at_fhir[ICD10], at_fhir[ATC], at_fhir[LOCAL]), (['ICD102019*'], ['2023AA*'], ['a1*']))
        else:
            self.assertEqual((at_fhir[ICD10], at_fhir[ATC], at_fhir[LOCAL]),
                             (['ICD102019*', 'v1.0'], ['2026-04-25*', '2023AA'], ['a1*', 'b1', 'c1']))
        self.assertEqual(at_fhir['/orgs/sehatapp/CodeSystem/ICD-10/'], ['v1.0*'])
        self.assertEqual(listed('/orgs/HL7')[LOCAL], ['a1*', 'b1'])
        self.assertEqual(listed('/orgs/Acme')[LOCAL], ['c1*'])
        for fhir_base, kwargs in (('/fhir', {}), ('/orgs/HL7', {'org': 'HL7'})):
            for uri, codes in listed(fhir_base).items():
                for code in codes:
                    instance = resolve('CodeSystem', uri, code.rstrip('*'), kwargs)
                    self.assertEqual(instance.version, code.rstrip('*'), (fhir_base, uri, code))
                default = next(code.rstrip('*') for code in codes if code.endswith('*'))
                self.assertEqual(resolve('CodeSystem', uri, None, kwargs).version, default, (fhir_base, uri))


class NamedVersionResolveWithoutRegistryTest(NamedVersionResolveCases, OCLTestCase):
    registry = False


class NamedVersionResolveWithRegistryTest(NamedVersionResolveCases, OCLTestCase):
    registry = True


class HiddenHolderWithoutRegistryTest(HiddenHolderCases, OCLTestCase):
    registry = False


class HiddenHolderWithRegistryTest(HiddenHolderCases, OCLTestCase):
    registry = True


class NamedVersionOperationsWithoutRegistryTest(NamedVersionOperationsCases, OCLTestCase):
    registry = False


class NamedVersionOperationsWithRegistryTest(NamedVersionOperationsCases, OCLTestCase):
    registry = True
