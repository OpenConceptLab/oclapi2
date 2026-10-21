"""
core.fhir.ops: the release a value set member is reported in, when the stored expansion holds a concept's
versioned-object row, which only HEAD holds, rather than the version row a release holds. The release that holds
another version of that concept answers: the expansion's explicit one first, then the one it evaluated, then the
newest, as for a member a release holds itself. A member a release holds itself answers as before. $expand
(contains.version, used-codesystem) and ValueSet $validate-code (version, and systemVersion naming the release), with
the URL registry off and on. OpenConceptLab/ocl_online#451.
"""
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.collections.tests.factories import ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLTestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.fhir.tests_ops_lookup import OpsCorpusMixin, name
from core.fhir.tests_search import collection, source
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

    def expanded(self, base='/fhir', user=None):
        response = self.get('$expand', base=base, user=user, resource='ValueSet', url=DEMO_SET)
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

    def test_a_versioned_object_member_is_reported_in_the_release_the_expansion_pins(self):
        self.assertEqual(self.members(), [('A', '1'), ('B', '1')])
        self.assertEqual(self.used(), [f'{DEMO}|1'])

    def test_then_the_release_it_evaluated_then_the_newest(self):
        self.stored.explicit_source_versions.clear()
        self.stored.evaluated_source_versions.add(self.demo_v1)
        self.assertEqual(self.members(), [('A', '1'), ('B', '1')])
        self.stored.evaluated_source_versions.clear()
        self.assertEqual(self.members(), [('A', '2'), ('B', '2')])  # the newest the caller is shown
        self.assertEqual(self.used(), [f'{DEMO}|2'])

    def test_a_release_the_caller_is_not_shown_is_never_reported(self):
        self.stored.explicit_source_versions.clear()
        self.stored.explicit_source_versions.add(self.demo_v3)
        for user in (None, self.outsider):
            self.assertEqual(self.members(user=user), [('A', '2'), ('B', '2')])
        self.assertEqual(self.members(base='/orgs/Acme', user=self.admin), [('A', '3'), ('B', '3')])

    def test_validate_code_answers_the_release_and_finds_the_code_in_it(self):
        """The version is the release's; the concept, and so the display, is the member's, as $expand shows it."""
        self.assertEqual(self.validated(code='A'), {'result': True, 'version': '1', 'display': 'Code A'})
        self.assertEqual(self.validated(code='B', systemVersion='2')['result'], True)
        self.assertEqual(self.validated(code='A', systemVersion='3')['result'], False)  # not shown

    def test_a_coding_expand_returns_validates(self):
        for member in self.expanded()['contains']:
            self.assertEqual(self.validated(system=member['system'], code=member['code'],
                                            systemVersion=member['version'], display=member['display']),
                             {'result': True, 'version': member['version'], 'display': member['display']})

    def test_a_deleted_version_of_the_concept_is_never_the_release(self):
        """A release whose version of the concept is deleted doesn't hold it: the next in order answers."""
        Concept.objects.filter(versioned_object=self.roots['A'], sources=self.demo_v1).update(is_active=False)
        self.assertEqual(self.members(), [('A', '2'), ('B', '1')])
        self.assertEqual(self.validated(code='A', systemVersion='1')['result'], False)
        self.assertEqual(self.validated(code='A'), {'result': True, 'version': '2', 'display': 'Code A'})

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

    def test_the_statement_count_is_that_of_an_expansion_of_version_rows(self):
        def statements():
            with CaptureQueriesContext(connection) as context:
                self.expanded()
            return len(context.captured_queries)
        with_roots = statements()
        self.stored.concepts.clear()
        self.stored.concepts.add(*(concept for concept in self.demo_v1.concepts.all()))
        self.assertEqual(self.members(), [('A', '1'), ('B', '1')])
        self.assertEqual(statements(), with_roots)


class ConceptVersionWithoutRegistryTest(ConceptVersionCases, OCLTestCase):
    registry = False


class ConceptVersionWithRegistryTest(ConceptVersionCases, OCLTestCase):
    registry = True
