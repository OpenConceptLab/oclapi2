"""
core.fhir.ops: the release a value set member is reported in, when the stored expansion holds a concept's
versioned-object row, which only HEAD holds. It carries the concept's newest content, so the newest live release the
caller is shown that holds another, not deleted, version answers, whatever the expansion pins. A member a release
holds itself follows the explicit pin, then the evaluated pin, then the newest holding release. $expand
(contains.version, used-codesystem) and ValueSet $validate-code (version, and systemVersion naming a holding release)
agree, with the URL registry off and on.
"""
import re

from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.collections.tests.factories import ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLTestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.fhir.tests_operation_bounds import unbounded_expansion_reads
from core.fhir.tests_ops_lookup import OpsCorpusMixin, name
from core.fhir.tests_search import collection, source
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.url_registry.factories import GlobalURLRegistryFactory

DEMO = 'http://example.org/fhir/CodeSystem/demo'
DEMO_SET = 'http://example.org/fhir/ValueSet/demo-set'


class ConceptVersionCases(OpsCorpusMixin):
    """
    Acme's code system demo has releases 1 and 2 and the private release 3. Codes A and B each have a versioned-object
    row, which HEAD holds, and a version row in each release. The value set demo-set 1's stored expansion holds the
    versioned-object rows.
    """
    def setUp(self):
        super().setUp()
        self.demo = source(self.acme, 'demo', canonical_url=DEMO)
        self.demo_v1 = source(self.acme, 'demo', '1', released=True, canonical_url=DEMO)
        self.demo_v2 = source(self.acme, 'demo', '2', released=True, canonical_url=DEMO)
        self.demo_v3 = source(self.acme, 'demo', '3', released=True, canonical_url=DEMO,
                              public_access=ACCESS_TYPE_NONE)
        self.roots = {}
        for code in ('A', 'B'):
            root = ConceptFactory(parent=self.demo, mnemonic=code, names=[name(f'Code {code}', preferred=True)])
            for release in (self.demo_v1, self.demo_v2, self.demo_v3):
                ConceptFactory(parent=self.demo, mnemonic=code, version=f'{code}-{release.version}',
                               versioned_object=root, sources=[release],
                               names=[name(f'Code {code} in {release.version}', preferred=True)])
            self.roots[code] = root
        self.value_set = collection(self.acme, 'demo-set', '1', released=True, canonical_url=DEMO_SET)
        collection(self.acme, 'demo-set', canonical_url=DEMO_SET)
        self.stored = ExpansionFactory(mnemonic='autoexpand-1', collection_version=self.value_set)
        self.stored.concepts.add(*self.roots.values())
        self.stored.explicit_source_versions.add(self.demo_v1)
        self.value_set.expansion_uri = self.stored.uri
        self.value_set.save()
        if self.registry:
            for url in (DEMO, DEMO_SET):
                GlobalURLRegistryFactory(url=url, namespace=self.acme.uri)

    def expanded(self, base='/fhir', user=None, **params):
        """The expansion answered at this base, with the given paging and filtering inputs."""
        response = self.get('$expand', base=base, user=user, resource='ValueSet', url=DEMO_SET, **params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['expansion']

    def members(self, base='/fhir', user=None):
        return [(member['code'], member.get('version')) for member in self.expanded(base, user)['contains']]

    def used(self, base='/fhir', user=None):
        return [item['valueUri'] for item in self.expanded(base, user)['parameter']
                if item['name'] == 'used-codesystem']

    def validated(self, system=DEMO, **params):
        response = self.get('$validate-code', resource='ValueSet', url=DEMO_SET, system=system, **params)
        self.assertEqual(response.status_code, 200, response.data)
        return {item['name']: next(value for key, value in item.items() if key != 'name')
                for item in response.data['parameter'] if item['name'] in ('result', 'version', 'display')}

    def test_a_versioned_object_member_is_reported_in_the_newest_holding_release_the_caller_is_shown(self):
        self.assertEqual(self.members(), [('A', '2'), ('B', '2')])
        self.assertEqual(self.used(), [f'{DEMO}|2'])

    def test_pins_do_not_change_the_release_of_a_versioned_object_member(self):
        for pinned in ('explicit', 'evaluated', 'none'):
            with self.subTest(pinned=pinned):
                self.stored.explicit_source_versions.clear()
                self.stored.evaluated_source_versions.clear()
                if pinned == 'explicit':
                    self.stored.explicit_source_versions.add(self.demo_v1)
                elif pinned == 'evaluated':
                    self.stored.evaluated_source_versions.add(self.demo_v1)
                self.assertEqual(self.members(), [('A', '2'), ('B', '2')])
                self.assertEqual(self.used(), [f'{DEMO}|2'])
                self.assertEqual(self.validated(code='A'), {'result': True, 'version': '2', 'display': 'Code A'})

    def test_a_release_the_caller_is_not_shown_is_never_reported(self):
        self.stored.explicit_source_versions.clear()
        self.stored.explicit_source_versions.add(self.demo_v3)
        for user in (None, self.outsider):
            self.assertEqual(self.members(user=user), [('A', '2'), ('B', '2')])
        self.assertEqual(self.members(base='/orgs/Acme', user=self.admin), [('A', '3'), ('B', '3')])

    def test_validate_code_answers_the_release_and_finds_the_code_in_it(self):
        """The version is the release's; the concept, and so the display, is the member's, as $expand shows it."""
        self.assertEqual(self.validated(code='A'), {'result': True, 'version': '2', 'display': 'Code A'})
        for version in ('1', '2'):
            self.assertEqual(self.validated(code='A', systemVersion=version)['result'], True)
        self.assertEqual(self.validated(code='A', systemVersion='3')['result'], False)  # not shown

    def test_a_coding_expand_returns_validates(self):
        for member in self.expanded()['contains']:
            self.assertEqual(self.validated(system=member['system'], code=member['code'],
                                            systemVersion=member['version'], display=member['display']),
                             {'result': True, 'version': member['version'], 'display': member['display']})

    def test_a_deleted_version_of_the_concept_is_never_the_release(self):
        """A release whose version of the concept is deleted doesn't hold it: the next in order answers."""
        Concept.objects.filter(versioned_object=self.roots['A'], sources=self.demo_v2).update(is_active=False)
        self.assertEqual(self.members(), [('A', '1'), ('B', '2')])
        self.assertEqual(self.validated(code='A', systemVersion='2')['result'], False)
        self.assertEqual(self.validated(code='A'), {'result': True, 'version': '1', 'display': 'Code A'})

    def test_a_member_a_release_holds_itself_answers_as_before(self):
        """A version row stays in the release that holds it, though the expansion pins another one."""
        held = ConceptFactory(parent=self.demo, mnemonic='C', sources=[self.demo_v2],
                              names=[name('Code C', preferred=True)])
        ConceptFactory(parent=self.demo, mnemonic='C', version='C-1', versioned_object=held, sources=[self.demo_v1],
                       names=[name('Code C in 1', preferred=True)])
        self.stored.concepts.add(held)
        self.assertEqual(self.members()[-1], ('C', '2'))
        self.assertEqual(self.validated(code='C'), {'result': True, 'version': '2', 'display': 'Code C'})
        self.assertEqual(self.validated(code='C', systemVersion='1')['result'], False)  # 1 holds another version
        self.assertEqual(self.validated(code='C', systemVersion='2')['result'], True)

    def test_a_member_its_release_holds_outranks_another_concepts_pinned_version(self):
        """Two members share the canonical, code and release name: the one a release holds itself answers first."""
        other = OrganizationFactory(mnemonic='Other')
        source(other, 'demo', canonical_url=DEMO)
        other_v1 = source(other, 'demo', '1', released=True, canonical_url=DEMO)
        held = ConceptFactory(parent=Source.objects.get(organization=other, mnemonic='demo', version='HEAD'),
                              mnemonic='A', sources=[other_v1], names=[name('Other A', preferred=True)])
        self.stored.concepts.add(held)
        self.assertEqual(self.validated(code='A', systemVersion='1', display='Other A'),
                         {'result': True, 'version': '1', 'display': 'Other A'})

    def test_a_member_a_release_holds_itself_still_follows_the_pins(self):
        """For a member held by both releases, an explicit pin wins over an evaluated pin."""
        root = ConceptFactory(parent=self.demo, mnemonic='C')
        held = ConceptFactory(parent=self.demo, mnemonic='C', version='C-1', versioned_object=root,
                              sources=[self.demo_v1, self.demo_v2], names=[name('Code C', preferred=True)])
        self.stored.concepts.set([held])
        for explicit, evaluated, version in ((True, False, '1'), (False, True, '2'), (True, True, '1')):
            with self.subTest(explicit=explicit, evaluated=evaluated):
                self.stored.explicit_source_versions.set([self.demo_v1] if explicit else [])
                self.stored.evaluated_source_versions.set([self.demo_v2] if evaluated else [])
                self.assertEqual(self.members(), [('C', version)])
                self.assertEqual(self.used(), [f'{DEMO}|{version}'])
                self.assertEqual(self.validated(code='C'), {'result': True, 'version': version, 'display': 'Code C'})

    def test_the_statement_count_does_not_grow_with_either_kind_of_member(self):
        """Adding members costs no more statements, whether releases hold their rows or other versions."""
        def statements(roots):
            self.stored.concepts.set(roots if roots else self.demo_v1.concepts.all())
            with CaptureQueriesContext(connection) as context:
                expansion = self.expanded()
            self.assertEqual(len(expansion['contains']), len(self.roots))
            self.assertEqual(unbounded_expansion_reads(context.captured_queries, self.stored.id), [])
            return len(context.captured_queries)

        before = statements(None), statements(self.roots.values())
        for code in ('C', 'D', 'E'):
            root = ConceptFactory(parent=self.demo, mnemonic=code)
            for release in (self.demo_v1, self.demo_v2, self.demo_v3):
                ConceptFactory(parent=self.demo, mnemonic=code, version=f'{code}-{release.version}',
                               versioned_object=root, sources=[release])
            self.roots[code] = root
        self.assertEqual((statements(None), statements(self.roots.values())), before)

    def test_releases_holding_every_concept_need_no_correlated_version_lookup(self):
        """The newest releases are read as sets, whatever the pins, even when only the total is requested."""
        correlated = re.compile(r'"versioned_object_id" = \("concepts"\."versioned_object_id"\)')
        for pinned in ('explicit', 'evaluated', 'none'):
            self.stored.explicit_source_versions.clear()
            self.stored.evaluated_source_versions.clear()
            if pinned == 'explicit':
                self.stored.explicit_source_versions.add(self.demo_v1)
            elif pinned == 'evaluated':
                self.stored.explicit_source_versions.add(self.demo)
                self.stored.evaluated_source_versions.add(self.demo_v1)
            for count in (0, 1, 1000):
                with self.subTest(pinned=pinned, count=count):
                    with CaptureQueriesContext(connection) as context:
                        expansion = self.expanded(count=count)
                    self.assertEqual(expansion['total'], 2)
                    self.assertEqual([item.get('version') for item in expansion.get('contains', [])],
                                     ['2'] * min(count, 2))
                    self.assertEqual([item['valueUri'] for item in expansion['parameter']
                                      if item['name'] == 'used-codesystem'], [f'{DEMO}|2'])
                    self.assertEqual([query['sql'] for query in context.captured_queries
                                      if correlated.search(query['sql'])], [])
                    self.assertEqual(unbounded_expansion_reads(context.captured_queries, self.stored.id), [])

    def test_the_newest_visible_release_of_each_repository_answers(self):
        """A private newest release answers only at a base where the caller may view it."""
        root = ConceptFactory(parent=self.ciel, mnemonic='another')
        ConceptFactory(parent=self.ciel, mnemonic='another', version='another-1', versioned_object=root,
                       sources=[self.ciel_v1, self.ciel_v2])
        self.stored.concepts.add(root)
        for base, user, version in (('/fhir', None, '2'), ('/orgs/Acme', self.outsider, '2'),
                                    ('/orgs/Acme', self.admin, '3')):
            with self.subTest(base=base, version=version):
                self.assertEqual(self.members(base=base, user=user),
                                 [('A', version), ('B', version), ('another', 'v2')])
                self.assertEqual(self.used(base=base, user=user),
                                 sorted([f'{DEMO}|{version}', f'{self.ciel.canonical_url}|v2']))

    def test_an_older_release_answers_when_the_newest_no_longer_holds_the_concept(self):
        """Missing newest links fall back to an older holding release, or no version when none holds it."""
        self.stored.explicit_source_versions.clear()
        Concept.sources.through.objects.filter(
            source=self.demo_v2, concept__versioned_object=self.roots['A']).delete()
        Concept.sources.through.objects.filter(
            source__in=[self.demo_v1, self.demo_v2, self.demo_v3],
            concept__versioned_object=self.roots['B']).delete()
        with CaptureQueriesContext(connection) as context:
            expansion = self.expanded()
        self.assertEqual([(item['code'], item.get('version')) for item in expansion['contains']],
                         [('A', '1'), ('B', None)])
        self.assertEqual([item['valueUri'] for item in expansion['parameter'] if item['name'] == 'used-codesystem'],
                         [DEMO, f'{DEMO}|1'])
        self.assertEqual(unbounded_expansion_reads(context.captured_queries, self.stored.id), [])


class ConceptVersionWithoutRegistryTest(ConceptVersionCases, OCLTestCase):
    registry = False


class ConceptVersionWithRegistryTest(ConceptVersionCases, OCLTestCase):
    registry = True
