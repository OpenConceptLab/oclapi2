"""
What a base doesn't show a caller never reaches them through a version named (decision 9), and nothing in an answer
names it. The corpus holds, under one canonical URL, a private first choice that holds the versions, a public one
that holds some, and a public one that holds the rest; the same for value sets. Every operation that resolves a
canonical URL and a version named is asked for each version, GET and POST, at /fhir/ and at an owner base, with the
URL registry toggle off and on, anonymously, as a signed-in non-member, as a member and as staff, and the answers are
compared with the hidden repository and without it. The other shapes a repository or a version can take are here too:
a public repository whose named release is private, one with nothing released, a retired or inactive HEAD, a retired
or deleted release, two owners sharing a canonical and a version name, a version named HEAD, and a registry entry
pointing at a repository the caller can't see. TerminologyCapabilities is checked on its own: every version it names
resolves for the caller it was built for, and nothing else is named.
OpenConceptLab/ocl_online#451.
"""
import json

from core.collections.models import Collection
from core.collections.tests.factories import ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE, HEAD
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.fhir.resolve import FhirResolver, UnresolvedError
from core.fhir.tests_ops_lookup import name
from core.fhir.tests_resolve_named_version import client_for
from core.fhir.tests_search import collection, source
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.url_registry.factories import GlobalURLRegistryFactory, OrganizationURLRegistryFactory
from core.url_registry.tests_resolution import set_registry_toggle
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory

HELD = 'http://example.org/fhir/CodeSystem/held'
SETS = 'http://example.org/fhir/ValueSet/held'
TARGETS = 'http://example.org/fhir/CodeSystem/targets'
SHARED = 'http://example.org/fhir/CodeSystem/shared'
POINTED = 'http://example.org/fhir/CodeSystem/pointed'
OWNED = 'http://example.org/fhir/CodeSystem/owned'
PRIVATE_NAMES = ('Hidden one', 'hidden-first', 'vs-hidden', '"HID"')  # only those shown them may be told these
# a version name a request supplies is echoed in the not-found it gets back, so it is no sign of what OCL holds


def operation_requests(fhir_base, version):
    """
    [(key, method, path, params or body)] for every operation that resolves a canonical URL and a version named, at
    `fhir_base`, naming `version`: $lookup, CodeSystem and ValueSet $validate-code (a member's system and version
    too), $expand, $translate (a code's system and version, a Coding, and a ConceptMap by url and version), and
    search by url and version. GET with the version in its own parameter and piped onto the url, and POST.
    """
    code_system, value_set, concept_map = (f'{fhir_base}/{kind}' for kind in ('CodeSystem', 'ValueSet', 'ConceptMap'))
    return [
        ('lookup', 'GET', f'{code_system}/$lookup', {'system': HELD, 'version': version, 'code': 'C1'}),
        ('lookup-piped', 'GET', f'{code_system}/$lookup', {'system': f'{HELD}|{version}', 'code': 'C1'}),
        ('lookup-post', 'POST', f'{code_system}/$lookup',
         [{'name': 'coding', 'valueCoding': {'system': HELD, 'code': 'C1', 'version': version}}]),
        ('cs-validate', 'GET', f'{code_system}/$validate-code', {'url': HELD, 'version': version, 'code': 'C1'}),
        ('cs-validate-piped', 'GET', f'{code_system}/$validate-code', {'url': f'{HELD}|{version}', 'code': 'C1'}),
        ('cs-validate-post', 'POST', f'{code_system}/$validate-code',
         [{'name': 'url', 'valueUri': HELD}, {'name': 'version', 'valueString': version},
          {'name': 'code', 'valueCode': 'C1'}]),
        ('vs-validate', 'GET', f'{value_set}/$validate-code',
         {'url': SETS, 'valueSetVersion': version, 'system': HELD, 'code': 'C1'}),
        ('vs-validate-post', 'POST', f'{value_set}/$validate-code',
         [{'name': 'url', 'valueUri': SETS}, {'name': 'valueSetVersion', 'valueString': version},
          {'name': 'coding', 'valueCoding': {'system': HELD, 'code': 'C1'}}]),
        ('vs-validate-system', 'GET', f'{value_set}/$validate-code',
         {'url': SETS, 'valueSetVersion': version, 'system': HELD, 'systemVersion': version, 'code': 'C1'}),
        ('vs-validate-outside', 'GET', f'{value_set}/$validate-code',  # not a member: its code system is resolved
         {'url': SETS, 'valueSetVersion': version, 'system': HELD, 'systemVersion': version, 'code': 'C9'}),
        ('expand', 'GET', f'{value_set}/$expand', {'url': SETS, 'valueSetVersion': version}),
        ('expand-piped', 'GET', f'{value_set}/$expand', {'url': f'{SETS}|{version}'}),
        ('expand-post', 'POST', f'{value_set}/$expand',
         [{'name': 'url', 'valueUri': SETS}, {'name': 'valueSetVersion', 'valueString': version}]),
        ('translate', 'GET', f'{concept_map}/$translate', {'system': HELD, 'version': version, 'code': 'C1'}),
        ('translate-piped', 'GET', f'{concept_map}/$translate', {'system': f'{HELD}|{version}', 'code': 'C1'}),
        ('translate-coding', 'POST', f'{concept_map}/$translate',
         [{'name': 'coding', 'valueCoding': {'system': HELD, 'code': 'C1', 'version': version}}]),
        ('translate-map', 'GET', f'{concept_map}/$translate',
         {'url': HELD, 'conceptMapVersion': version, 'system': HELD, 'code': 'C1'}),
        ('search-cs', 'GET', f'{code_system}/', {'url': HELD, 'version': version}),
        ('search-vs', 'GET', f'{value_set}/', {'url': SETS, 'version': version}),
    ]


def answers(requests, user=None):
    """{key: (status, content)} for (key, method, path, params or body) requests, a Bundle's own meta dropped."""
    client = client_for(user)
    found = {}
    for key, method, path, payload in requests:
        if method == 'GET':
            response = client.get(path, payload)
        else:
            response = client.post(path, json.dumps({'resourceType': 'Parameters', 'parameter': payload}),
                                   content_type='application/fhir+json')
        content = json.loads(response.content)
        content.pop('meta', None)  # a search Bundle's lastUpdated is the time it was made
        found[key] = (response.status_code, content)
    return found


def terminology_capabilities(fhir_base, user=None):
    return client_for(user).get(f'{fhir_base}/metadata', {'mode': 'terminology'})


class AccessCorpusMixin:
    """
    Lab's three code systems share the canonical HELD: private hidden-first (the lowest id) holds v1 and secret,
    public open-second holds v1 and a private v5 and p9, public open-third holds v2 and a public v5. Each has a C1,
    with its own display and its own mapping into Lab's public targets, so an answer says which one served it. Lab's
    two value sets share SETS the same way: private vs-hidden holds v1 and secret, public vs-open holds v2, whose
    expansion holds hidden-first's C1 beside open-third's. Alpha and Beta share SHARED under the same version name
    s1, Beta's privately. With the registry, a global entry sends each canonical to an owner that holds it.
    """
    registry = False

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        set_registry_toggle(cls.registry)
        cls.admin = UserProfile.objects.get(username='ocladmin')
        cls.lab = OrganizationFactory(mnemonic='Lab')
        cls.lab_member = UserProfileFactory(username='lab-member')
        cls.lab.members.add(cls.lab_member)
        cls.outsider = UserProfileFactory(username='outsider')

        cls.targets = source(cls.lab, 'targets', canonical_url=TARGETS)
        targets_t1 = source(cls.lab, 'targets', 't1', released=True, canonical_url=TARGETS)
        for code in ('HID', 'SEC', 'THI'):
            ConceptFactory(parent=cls.targets, mnemonic=code, sources=[targets_t1],
                           names=[name(f'Target {code}', preferred=True)])

        cls.hidden, hidden_c1 = cls.holder('hidden-first', 'Hidden one', 'HID', ['v1', 'secret'], private=True)
        cls.second, _ = cls.holder('open-second', 'Second one', 'SEC', ['v1'], private_releases=['v5', 'p9'])
        cls.third, third_c1 = cls.holder('open-third', 'Third one', 'THI', ['v2', 'v5'])

        cls.vs_hidden = collection(cls.lab, 'vs-hidden', canonical_url=SETS, public_access=ACCESS_TYPE_NONE)
        cls.vs_hidden_v1 = cls.value_set('vs-hidden', 'v1', hidden_c1, public_access=ACCESS_TYPE_NONE)
        cls.vs_hidden_secret = cls.value_set('vs-hidden', 'secret', hidden_c1, public_access=ACCESS_TYPE_NONE)
        cls.vs_open = collection(cls.lab, 'vs-open', canonical_url=SETS)
        cls.vs_open_v2 = cls.value_set('vs-open', 'v2', third_c1, hidden_c1)

        cls.alpha = OrganizationFactory(mnemonic='Alpha')
        cls.beta = OrganizationFactory(mnemonic='Beta')
        cls.beta_member = UserProfileFactory(username='beta-member')
        cls.beta.members.add(cls.beta_member)
        source(cls.beta, 'beta-shared', canonical_url=SHARED, public_access=ACCESS_TYPE_NONE)
        cls.beta_s1 = source(cls.beta, 'beta-shared', 's1', released=True, canonical_url=SHARED,
                             public_access=ACCESS_TYPE_NONE)
        source(cls.alpha, 'alpha-shared', canonical_url=SHARED)
        cls.alpha_s1 = source(cls.alpha, 'alpha-shared', 's1', released=True, canonical_url=SHARED)

        if cls.registry:
            for url, owner in ((HELD, cls.lab), (SETS, cls.lab), (TARGETS, cls.lab), (SHARED, cls.alpha)):
                GlobalURLRegistryFactory(url=url, namespace=owner.uri)

    def tearDown(self):
        set_registry_toggle(False)
        super().tearDown()

    @classmethod
    def holder(  # pylint: disable=too-many-arguments
            cls, mnemonic, display, target, releases, private=False, private_releases=()):
        """(HEAD, C1) for a code system of Lab with the canonical HELD, its releases, and a C1 mapping to `target`."""
        access = {'public_access': ACCESS_TYPE_NONE} if private else {}
        head = source(cls.lab, mnemonic, canonical_url=HELD, **access)
        rows = [source(cls.lab, mnemonic, version, released=True, canonical_url=HELD, **access)
                for version in releases]
        rows += [source(cls.lab, mnemonic, version, released=True, canonical_url=HELD,
                        public_access=ACCESS_TYPE_NONE) for version in private_releases]
        concept = ConceptFactory(parent=head, mnemonic='C1', sources=rows, names=[name(display, preferred=True)])
        MappingFactory(parent=head, map_type='SAME-AS', from_concept=None, to_concept=None, from_source=head,
                       from_source_url=head.uri, from_concept_code='C1', to_source=cls.targets,
                       to_source_url=cls.targets.uri, to_concept_code=target, sources=rows)
        return head, concept

    @classmethod
    def value_set(cls, mnemonic, version, *concepts, **kwargs):
        release = collection(cls.lab, mnemonic, version, released=True, canonical_url=SETS, **kwargs)
        expansion = ExpansionFactory(mnemonic=f'autoexpand-{mnemonic}-{version}', collection_version=release)
        expansion.concepts.add(*concepts)
        release.expansion_uri = expansion.uri
        release.save()
        return release

    def row(self, mnemonic, version):
        return Source.objects.get(organization=self.lab, mnemonic=mnemonic, version=version)

    def drop_the_hidden_repositories(self):
        Source.objects.filter(organization=self.lab, mnemonic='hidden-first').delete()
        Collection.objects.filter(organization=self.lab, mnemonic='vs-hidden').delete()

    def resolved(  # pylint: disable=too-many-arguments
            self, url, version=None, kwargs=None, user=None, resource_type='CodeSystem'):
        return FhirResolver(kwargs, user).resolve(resource_type, url, version).id

    def assertUnresolved(  # pylint: disable=invalid-name,too-many-arguments
            self, url, version=None, kwargs=None, user=None, resource_type='CodeSystem'):
        with self.assertRaises(UnresolvedError):
            FhirResolver(kwargs, user).resolve(resource_type, url, version)


class HiddenIsMissingCases(AccessCorpusMixin):
    """A repository the base doesn't show the caller changes nothing in any answer it gives them."""

    versions = ('v1', 'v2', 'secret', 'nope')

    def requests_for(self, fhir_base):
        return [(f'{version}:{key}', method, path, payload) for version in self.versions
                for key, method, path, payload in operation_requests(fhir_base, version)]

    def test_at_an_owner_base_every_operation_answers_as_if_they_were_not_there(self):
        """
        Anonymously and signed in as a non-member, at Lab's base, with the registry off and on: every operation that
        resolves, for every version the hidden repositories hold and for versions they don't, answers the same bytes
        with them and without them, and never names anything of theirs.
        """
        with_them = {}
        for user in (None, self.outsider):
            found = answers(self.requests_for('/orgs/Lab'), user)
            body = json.dumps(found)
            for private in PRIVATE_NAMES:
                self.assertNotIn(private, body, user)
            with_them[user] = found
        self.drop_the_hidden_repositories()
        for user, found in with_them.items():
            after = answers(self.requests_for('/orgs/Lab'), user)
            self.assertEqual([key for key in found if after[key] != found[key]], [], user)

    def test_at_the_global_base_every_operation_answers_as_if_they_were_not_there(self):
        """
        The same at /fhir/, which shows public repositories only. With the registry a global entry decides, and the
        entry's namespace is read as it is written: the private hidden-first is the lowest-id code system Lab holds
        with the canonical, so with the registry on the entry stops there and nothing under HELD or SETS resolves
        while it is there. That is the shared resolver's registry lookup, which decision 9 leaves alone; without the
        registry the global base's own match passes it over and the answers are the same with it and without it.
        """
        with_them = {user: answers(self.requests_for('/fhir'), user) for user in (None, self.outsider)}
        for user, found in with_them.items():
            body = json.dumps(found)
            for private in PRIVATE_NAMES:
                self.assertNotIn(private, body, user)
        self.drop_the_hidden_repositories()
        for user, found in with_them.items():
            after = answers(self.requests_for('/fhir'), user)
            differing = [key for key in found if after[key] != found[key]]
            self.assertEqual(differing, self.global_differences(), user)

    def global_differences(self):
        """
        The requests /fhir/ answers differently with the hidden repositories and without them: none without the
        registry, and with it those the entry would reach once hidden-first and vs-hidden are gone, which are the
        versions open-second and vs-open hold (v1's code system operations, v2's value set ones). The shared
        resolver reads an entry's namespace as it is written, so its lowest-id repository with the canonical is the
        one the entry stops at whether or not the base shows it, and no other is tried; decision 9 leaves an entry
        to decide. The same difference is there on the code before the rule.
        """
        if not self.registry:
            return []
        operations = {
            'v1': ('lookup', 'lookup-piped', 'lookup-post', 'cs-validate', 'cs-validate-piped', 'cs-validate-post',
                   'translate', 'translate-piped', 'translate-coding', 'translate-map'),
            'v2': ('vs-validate', 'vs-validate-post', 'vs-validate-system', 'vs-validate-outside', 'expand',
                   'expand-piped', 'expand-post')}
        return [f'{version}:{key}' for version in self.versions for key in operations.get(version, ())]

    def test_with_no_version_named_a_hidden_first_choice_still_decides(self):
        """
        With no version named the shared resolver stops at the lowest-id repository with the canonical, whether or
        not the base shows it, so Lab's base answers not found while a world without hidden-first answers from
        open-second. Decision 9 reads a version named, so it leaves this as it is; closing it needs a change in the
        shared resolver. The same difference is there on the code before the rule.
        """
        lab = {'org': 'Lab'}
        requests = [('cs', 'GET', '/orgs/Lab/CodeSystem/$lookup', {'system': HELD, 'code': 'C1'}),
                    ('vs', 'GET', '/orgs/Lab/ValueSet/$expand', {'url': SETS})]
        with_them = {}
        for user in (None, self.outsider):
            self.assertUnresolved(HELD, None, lab, user)
            self.assertUnresolved(SETS, None, lab, user, 'ValueSet')
            with_them[user] = answers(requests, user)
            self.assertEqual([status for status, _ in with_them[user].values()], [422, 422], user)
        self.drop_the_hidden_repositories()
        for user in (None, self.outsider):
            self.assertEqual(self.resolved(HELD, None, lab, user), self.row('open-second', 'v1').id, user)
            self.assertEqual([status for status, _ in answers(requests, user).values()], [200, 200], user)

    def test_a_member_and_staff_are_served_what_the_hidden_repositories_hold(self):
        """The positive control: the corpus does distinguish its holders, so the comparisons have something to show.
        At Lab's base those shown the private repositories are served them; at /fhir/ nobody is."""
        lab = {'org': 'Lab'}
        for user in (self.lab_member, self.admin):
            self.assertEqual(self.resolved(HELD, 'secret', lab, user), self.row('hidden-first', 'secret').id, user)
            self.assertEqual(self.resolved(HELD, 'v1', lab, user), self.row('hidden-first', 'v1').id, user)
            self.assertEqual(self.resolved(SETS, 'secret', lab, user, 'ValueSet'), self.vs_hidden_secret.id, user)
            self.assertEqual(self.resolved(SETS, 'v1', lab, user, 'ValueSet'), self.vs_hidden_v1.id, user)
            found = answers(operation_requests('/orgs/Lab', 'secret'), user)
            self.assertEqual(found['lookup'][0], 200, user)
            self.assertIn('Hidden one', json.dumps(found['lookup'][1]), user)
        for user in (None, self.outsider, self.lab_member, self.admin):
            self.assertUnresolved(HELD, 'secret', None, user)
            self.assertUnresolved(SETS, 'secret', None, user, 'ValueSet')

    def test_a_hidden_repository_never_blocks_a_shown_one(self):
        """v1 is hidden-first's (the lowest id) and open-second's: those not shown the first are served the second's,
        and v2, which only open-third holds, is open-third's."""
        lab = {'org': 'Lab'}
        for user in (None, self.outsider):
            self.assertEqual(self.resolved(HELD, 'v1', lab, user), self.row('open-second', 'v1').id, user)
            self.assertEqual(self.resolved(HELD, 'v2', lab, user), self.row('open-third', 'v2').id, user)
            self.assertEqual(self.resolved(SETS, 'v2', lab, user, 'ValueSet'), self.vs_open_v2.id, user)
        self.assertEqual(self.resolved(HELD, 'v1', lab, self.lab_member), self.row('hidden-first', 'v1').id)

    def test_a_public_repositorys_private_release_is_never_served(self):
        """open-second's v5 is private and open-third's is public: v5 named is open-third's to those not shown
        open-second's, and open-second's to those shown it (the lower id). /fhir/ shows neither its private v5 nor,
        with the registry, anything behind the entry's private first code system."""
        lab = {'org': 'Lab'}
        for user in (None, self.outsider):
            self.assertEqual(self.resolved(HELD, 'v5', lab, user), self.row('open-third', 'v5').id, user)
        for user in (self.lab_member, self.admin):
            self.assertEqual(self.resolved(HELD, 'v5', lab, user), self.row('open-second', 'v5').id, user)
        for user in (None, self.outsider, self.lab_member, self.admin):
            if self.registry:
                self.assertUnresolved(HELD, 'v5', None, user)
            else:
                self.assertEqual(self.resolved(HELD, 'v5', None, user), self.row('open-third', 'v5').id, user)

    def test_an_expansion_lists_only_the_members_whose_code_system_the_caller_is_shown(self):
        """vs-open's v2 holds hidden-first's C1 and open-third's: those not shown hidden-first are served the one,
        those shown it both."""
        for user, count in ((None, 1), (self.outsider, 1), (self.lab_member, 2), (self.admin, 2)):
            response = client_for(user).get('/orgs/Lab/ValueSet/$expand', {'url': SETS, 'valueSetVersion': 'v2'})
            self.assertEqual(response.status_code, 200, user)
            self.assertEqual(len(response.data['expansion']['contains']), count, user)

    def test_a_version_named_head_is_the_first_repository_the_base_shows(self):
        """HEAD named is a repository's own HEAD, so it is the first one the base shows with a release it shows:
        open-second's to a non-member, hidden-first's to a member."""
        lab = {'org': 'Lab'}
        for user in (None, self.outsider):
            self.assertEqual(self.resolved(HELD, HEAD, lab, user), self.second.id, user)
        for user in (self.lab_member, self.admin):
            self.assertEqual(self.resolved(HELD, HEAD, lab, user), self.hidden.id, user)


class RepositoryShapeCases(AccessCorpusMixin):
    """The shapes a repository or a version can take that keep it out of a named version's answer."""

    def test_a_repository_with_nothing_released_holds_no_version_named(self):
        """open-draft's HEAD is public and d1, unreleased, is its only version: nobody is served d1, and
        TerminologyCapabilities never names it."""
        source(self.lab, 'open-draft', canonical_url=HELD)
        source(self.lab, 'open-draft', 'd1', canonical_url=HELD)
        for user in (None, self.outsider, self.lab_member, self.admin):
            self.assertUnresolved(HELD, 'd1', {'org': 'Lab'}, user)
            self.assertUnresolved(HELD, 'd1', None, user)
            self.assertNotIn(b'"d1"', terminology_capabilities('/orgs/Lab', user).content)

    def test_a_retired_or_inactive_head_holds_no_version_named(self):
        """The resolver passes a retired HEAD over and an inactive one is no repository: their public releases are
        not served under the canonical and are not named in TerminologyCapabilities."""
        source(self.lab, 'open-retired', canonical_url=HELD, retired=True)
        source(self.lab, 'open-retired', 'v7', released=True, canonical_url=HELD)
        source(self.lab, 'open-inactive', canonical_url=HELD, is_active=False)
        source(self.lab, 'open-inactive', 'v8', released=True, canonical_url=HELD)
        for user in (None, self.outsider, self.lab_member, self.admin):
            for version in ('v7', 'v8'):
                self.assertUnresolved(HELD, version, {'org': 'Lab'}, user)
                self.assertUnresolved(HELD, version, None, user)
            body = terminology_capabilities('/orgs/Lab', user).content
            self.assertNotIn(b'"v7"', body)
            self.assertNotIn(b'"v8"', body)

    def test_a_retired_release_is_served_beside_a_live_one_and_a_deleted_one_never_is(self):
        retired = source(self.lab, 'open-third', 'v9', released=True, retired=True, canonical_url=HELD)
        source(self.lab, 'open-third', 'v10', released=True, is_active=False, canonical_url=HELD)
        for user in (None, self.outsider, self.lab_member, self.admin):
            self.assertEqual(self.resolved(HELD, 'v9', {'org': 'Lab'}, user), retired.id, user)
            self.assertUnresolved(HELD, 'v10', {'org': 'Lab'}, user)

    def test_two_owners_sharing_a_canonical_and_a_version_name(self):
        """Beta's beta-shared is private and holds s1, Alpha's alpha-shared is public and holds s1 too. /fhir/ and
        Alpha's base serve Alpha's to everyone; Beta's base serves Beta's only to those shown it."""
        for user in (None, self.outsider, self.beta_member, self.admin):
            self.assertEqual(self.resolved(SHARED, 's1', None, user), self.alpha_s1.id, user)
            self.assertEqual(self.resolved(SHARED, 's1', {'org': 'Alpha'}, user), self.alpha_s1.id, user)
        for user in (None, self.outsider):
            self.assertUnresolved(SHARED, 's1', {'org': 'Beta'}, user)
        for user in (self.beta_member, self.admin):
            self.assertEqual(self.resolved(SHARED, 's1', {'org': 'Beta'}, user), self.beta_s1.id, user)

    def test_an_owner_base_never_reaches_another_owners_holder(self):
        """Beta's public beta-open holds b2 under SHARED, which Alpha doesn't: Alpha's base finds nothing, Beta's
        and /fhir/ find Beta's. The base comes from the route, so a version named never crosses it."""
        source(self.beta, 'beta-open', canonical_url=SHARED)
        b2 = source(self.beta, 'beta-open', 'b2', released=True, canonical_url=SHARED)
        for user in (None, self.outsider, self.beta_member, self.admin):
            self.assertUnresolved(SHARED, 'b2', {'org': 'Alpha'}, user)
            self.assertEqual(self.resolved(SHARED, 'b2', {'org': 'Beta'}, user), b2.id, user)
            if not self.registry:  # with it, the global entry sends SHARED to Alpha, which holds no b2
                self.assertEqual(self.resolved(SHARED, 'b2', None, user), b2.id, user)
            else:
                self.assertUnresolved(SHARED, 'b2', None, user)

    def test_a_user_base_serves_the_users_own_by_the_callers_rights(self):
        """jdoe's private jdoe-first (the lowest id) and public jdoe-second both hold u1 under one canonical: only
        jdoe and staff are served the private one, everyone else the public one, and another owner's base finds
        neither, and /fhir/ shows the public one to everyone, jdoe included. A user base reads the caller's rights
        as an org base does."""
        jdoe = UserProfileFactory(username='jdoe')
        source(jdoe, 'jdoe-first', canonical_url=OWNED, public_access=ACCESS_TYPE_NONE)
        first_u1 = source(jdoe, 'jdoe-first', 'u1', released=True, canonical_url=OWNED,
                          public_access=ACCESS_TYPE_NONE)
        source(jdoe, 'jdoe-second', canonical_url=OWNED)
        second_u1 = source(jdoe, 'jdoe-second', 'u1', released=True, canonical_url=OWNED)
        base = {'user': 'jdoe'}
        for user in (None, self.outsider, self.lab_member):
            self.assertEqual(self.resolved(OWNED, 'u1', base, user), second_u1.id, user)
        for user in (jdoe, self.admin):
            self.assertEqual(self.resolved(OWNED, 'u1', base, user), first_u1.id, user)
        for user in (None, self.outsider, jdoe, self.admin):
            self.assertUnresolved(OWNED, 'u1', {'org': 'Lab'}, user)  # another owner's base
            if self.registry:  # no global entry sends OWNED anywhere
                self.assertUnresolved(OWNED, 'u1', None, user)
            else:  # /fhir/ shows the public one to everyone, jdoe included
                self.assertEqual(self.resolved(OWNED, 'u1', None, user), second_u1.id, user)

    def test_a_version_name_that_collides_with_a_repository_mnemonic(self):
        """Lab's code system v2 shares the canonical and is named as open-third's release is: a version named is
        read as a version, so v2 is open-third's release and r1 is the code system v2's."""
        source(self.lab, 'v2', canonical_url=HELD)
        r1 = source(self.lab, 'v2', 'r1', released=True, canonical_url=HELD)
        for user in (None, self.outsider, self.lab_member, self.admin):
            self.assertEqual(self.resolved(HELD, 'v2', {'org': 'Lab'}, user), self.row('open-third', 'v2').id, user)
            self.assertEqual(self.resolved(HELD, 'r1', {'org': 'Lab'}, user), r1.id, user)

    def test_a_registry_entry_pointing_where_the_caller_is_shown_nothing(self):
        """
        Lab's registry sends POINTED to Hid, whose code system is private, and Lab holds POINTED publicly too. With
        the registry the entry decides: those not shown Hid's are answered not found and Lab's own is not tried,
        staff is served Hid's. The answer is byte for byte the one a world where Hid holds nothing at all gives, so
        it says nothing of Hid's code system. Without the registry Lab's own answers.
        """
        hid = OrganizationFactory(mnemonic='Hid')
        source(hid, 'hid-pointed', canonical_url=POINTED, public_access=ACCESS_TYPE_NONE)
        hidden_p1 = source(hid, 'hid-pointed', 'p1', released=True, canonical_url=POINTED,
                           public_access=ACCESS_TYPE_NONE)
        source(self.lab, 'lab-pointed', canonical_url=POINTED)
        lab_p1 = source(self.lab, 'lab-pointed', 'p1', released=True, canonical_url=POINTED)
        OrganizationURLRegistryFactory(organization=self.lab, url=POINTED, namespace=hid.uri)
        lab = {'org': 'Lab'}
        if not self.registry:
            for user in (None, self.outsider, self.admin):
                self.assertEqual(self.resolved(POINTED, 'p1', lab, user), lab_p1.id, user)
            return
        with_it = {}
        for user in (None, self.outsider):
            self.assertUnresolved(POINTED, 'p1', lab, user)
            with_it[user] = answers(pointed_requests(), user)
        self.assertEqual(self.resolved(POINTED, 'p1', lab, self.admin), hidden_p1.id)
        Source.objects.filter(organization=hid).delete()
        for user in (None, self.outsider):  # the entry still decides, and finds nothing: the same answer
            self.assertUnresolved(POINTED, 'p1', lab, user)
            self.assertEqual(answers(pointed_requests(), user), with_it[user], user)


def pointed_requests():
    """$lookup and CodeSystem $validate-code at Lab's base for POINTED's p1."""
    return [('lookup', 'GET', '/orgs/Lab/CodeSystem/$lookup', {'system': POINTED, 'version': 'p1', 'code': 'C1'}),
            ('validate', 'GET', '/orgs/Lab/CodeSystem/$validate-code',
             {'url': POINTED, 'version': 'p1', 'code': 'C1'})]


class CapabilitiesAccessCases(AccessCorpusMixin):
    """TerminologyCapabilities names only what the base shows the caller, and every version it names resolves."""

    def test_every_version_named_resolves_for_the_caller_it_was_built_for(self):
        """
        What TerminologyCapabilities names, the operations serve that caller, with one exception each way, both of
        them the shared resolver's pick: a base names the versions of the lowest-id repository with a canonical that
        it shows the caller, while with no version named the resolver stops at the lowest-id one there is, shown or
        not, and with the registry on an entry's namespace is read the same way. So at Lab's base, where the
        lowest-id code system with the canonical is private, those not shown it are named a default they are
        answered not found for; and at /fhir/ with the registry, where the entry sends the canonical to Lab, every
        caller is. Decision 9 reads a version named; both exceptions are there on the code before it.
        """
        self.assertEqual(self.unserved(), self.expected_unserved())

    def expected_unserved(self):
        callers = (None, 'outsider', 'lab-member', 'beta-member', 'ocladmin')
        not_shown = (None, 'outsider', 'beta-member')  # not shown Lab's private code systems
        expected = [] if not self.registry else [
            ('/fhir', caller, HELD, code, 'not found') for caller in callers for code in ('v1', None)]
        return expected + [('/orgs/Lab', caller, HELD, None, 'not found') for caller in not_shown]

    def unserved(self):
        """[(base, caller, identifier, version, what resolve() answers)] for each version TerminologyCapabilities
        names that resolve() doesn't serve that caller under that identifier, the default among them."""
        found = []
        for fhir_base, kwargs in (('/fhir', {}), ('/orgs/Lab', {'org': 'Lab'}), ('/orgs/Beta', {'org': 'Beta'})):
            for user in (None, self.outsider, self.lab_member, self.beta_member, self.admin):
                for identifier, codes, default in self.listed(fhir_base, user):
                    for code in (*codes, None):
                        try:
                            served = FhirResolver(kwargs, user).resolve('CodeSystem', identifier, code).version
                        except UnresolvedError:
                            served = 'not found'
                        if served != (code or default):
                            found.append((fhir_base, getattr(user, 'username', None), identifier, code, served))
        return found

    def test_nothing_the_caller_isnt_shown_is_named(self):
        """Lab's base names secret only to those shown the private repositories; /fhir/ names it to no one, and
        Beta's base names Beta's s1 only to Beta's own."""
        for user in (None, self.outsider):
            body = terminology_capabilities('/orgs/Lab', user).content
            self.assertNotIn(b'"secret"', body)
            self.assertIn(b'"v1"', body)
            self.assertEqual(self.codes('/orgs/Beta', user), {})
        for user in (self.lab_member, self.admin):
            self.assertIn(b'"secret"', terminology_capabilities('/orgs/Lab', user).content)
        for user in (None, self.outsider, self.lab_member, self.admin):
            self.assertNotIn(b'"secret"', terminology_capabilities('/fhir', user).content)
        self.assertEqual(self.codes('/orgs/Beta', self.beta_member)[SHARED], ['s1'])

    def test_a_private_release_of_a_public_repository_is_named_only_to_those_shown_it(self):
        """p9 is open-second's and private, and no other code system holds it: Lab's base names it to those shown
        it alone, /fhir/ to no one, and it resolves for exactly those it is named to."""
        for user in (None, self.outsider):
            self.assertNotIn(b'"p9"', terminology_capabilities('/orgs/Lab', user).content)
            self.assertUnresolved(HELD, 'p9', {'org': 'Lab'}, user)
        for user in (self.lab_member, self.admin):
            self.assertIn(b'"p9"', terminology_capabilities('/orgs/Lab', user).content)
            self.assertEqual(self.resolved(HELD, 'p9', {'org': 'Lab'}, user), self.row('open-second', 'p9').id, user)
        for user in (None, self.outsider, self.lab_member, self.admin):
            self.assertNotIn(b'"p9"', terminology_capabilities('/fhir', user).content)
            self.assertUnresolved(HELD, 'p9', None, user)

    def test_a_hidden_repository_changes_nothing_in_it(self):
        before = {(fhir_base, user): terminology_capabilities(fhir_base, user).content
                  for fhir_base in ('/fhir', '/orgs/Lab') for user in (None, self.outsider)}
        self.drop_the_hidden_repositories()
        for (fhir_base, user), body in before.items():
            self.assertEqual(terminology_capabilities(fhir_base, user).content, body, (fhir_base, user))

    def codes(self, fhir_base, user):
        return {item['uri']: [version['code'] for version in item.get('version', [])]
                for item in terminology_capabilities(fhir_base, user).data.get('codeSystem', [])}

    def listed(self, fhir_base, user):
        """(identifier, codes, the default) for each code system TerminologyCapabilities names at `fhir_base`."""
        response = terminology_capabilities(fhir_base, user)
        self.assertEqual(response.status_code, 200, (fhir_base, user))
        return [(item['uri'], [version['code'] for version in item.get('version', [])],
                 next(version['code'] for version in item.get('version', []) if version.get('isDefault')))
                for item in response.data.get('codeSystem', [])]


class HiddenIsMissingWithoutRegistryTest(HiddenIsMissingCases, OCLTestCase):
    registry = False


class HiddenIsMissingWithRegistryTest(HiddenIsMissingCases, OCLTestCase):
    registry = True


class RepositoryShapeWithoutRegistryTest(RepositoryShapeCases, OCLTestCase):
    registry = False


class RepositoryShapeWithRegistryTest(RepositoryShapeCases, OCLTestCase):
    registry = True


class CapabilitiesAccessWithoutRegistryTest(CapabilitiesAccessCases, OCLTestCase):
    registry = False


class CapabilitiesAccessWithRegistryTest(CapabilitiesAccessCases, OCLTestCase):
    registry = True
