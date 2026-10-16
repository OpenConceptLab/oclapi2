"""
CodeSystem/$lookup (SVCM ITI-98, FHIR R4 $lookup) at the global base /fhir/ and at owner bases, GET and POST, at the
type and instance levels: the code system resolved as core.fhir.resolve does (released versions only, the newest the
caller is shown unless one is named, public only at /fhir/), with the URL registry toggle off and on; name, version
(the one resolved), display, code, system, abstract, designations and the inactive property; retired codes found and
flagged; displayLanguage; FHIRsmith's errors; a code system the caller isn't shown answers as a missing one; a fixed
number of queries. OpenConceptLab/ocl_online#451.
"""
import json

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.collections.tests.factories import ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM
from core.fhir.tests_search import source, collection
from core.orgs.tests.factories import OrganizationFactory
from core.url_registry.factories import GlobalURLRegistryFactory
from core.url_registry.tests_resolution import set_registry_toggle
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory

CIEL = 'https://CIELterminology.org'
ICD10 = 'http://hl7.org/fhir/sid/icd-10'
HIV = 'http://moh.example/fhir/CodeSystem/hiv'
REST = 'http://hl7.org/fhir/restful-interaction'
SHARED = 'http://example.org/fhir/CodeSystem/shared'
NEWEST_PRIVATE = 'http://example.org/fhir/CodeSystem/newest-private'
UNKNOWN = 'http://example.org/ocl-fhir-conformance/unknown-system'
FEVERS = 'https://example.org/fhir/ValueSet/fevers'
INACTIVE = {'name': 'property', 'part': [
    {'name': 'code', 'valueCode': 'inactive'}, {'name': 'value', 'valueBoolean': False}]}


def name(text, locale='en', preferred=False, name_type='Fully-Specified'):
    return ConceptNameFactory.build(name=text, locale=locale, locale_preferred=preferred, type=name_type)


class OpsCorpusMixin:
    """
    CIEL with two releases: 117053 in both (names in three languages), retired 103 in the newer, 999 in the older only.
    WHO's ICD-10, whose release has no canonical of its own; HL7's restful-interaction, whose 'batch' has no names.
    MOH's private code system (members see it at MOH's base). A canonical held privately (lower id) and publicly; a
    code system whose newest release is private. Acme's public value set 'fevers', whose release's default expansion
    holds CIEL 117053 and 103, MOH's P1 and HL7's batch. `registry` is the URL registry toggle: off as in production,
    or on with the curated registry's global entries (none for private repositories).
    """
    registry = False

    def setUp(self):  # pylint: disable=too-many-statements
        set_registry_toggle(self.registry)
        super().setUp()
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.ciel_org = OrganizationFactory(mnemonic='CIEL')
        self.who = OrganizationFactory(mnemonic='WHO')
        self.hl7 = OrganizationFactory(mnemonic='HL7')
        self.moh = OrganizationFactory(mnemonic='MOH')
        self.acme = OrganizationFactory(mnemonic='Acme')
        self.moh_member = UserProfileFactory(username='moh-member')
        self.moh.members.add(self.moh_member)
        self.outsider = UserProfileFactory(username='outsider')
        self.jdoe = UserProfileFactory(username='jdoe')

        self.ciel = source(self.ciel_org, 'CIEL', canonical_url=CIEL, default_locale='en', supported_locales=['en'])
        self.ciel_v1 = source(self.ciel_org, 'CIEL', 'v1', released=True, canonical_url=CIEL)
        self.ciel_v2 = source(self.ciel_org, 'CIEL', 'v2', released=True, canonical_url=CIEL)
        self.infectious = ConceptFactory(
            parent=self.ciel, mnemonic='117053', sources=[self.ciel_v1, self.ciel_v2], names=[
                name('Infectious enteritis', preferred=True), name('Entérite infectieuse', 'fr', True),
                name('enteritis infecciosa', 'es'), name('Enteritis of infectious origin', name_type='Synonym')])
        self.retired = ConceptFactory(parent=self.ciel, mnemonic='103', retired=True, sources=[self.ciel_v2],
                                      names=[name('HIV antibody', preferred=True)])
        self.older = ConceptFactory(parent=self.ciel, mnemonic='999', sources=[self.ciel_v1],
                                    names=[name('Older code', preferred=True)])

        self.icd = source(self.who, 'ICD-10-WHO', canonical_url=ICD10)
        self.icd_2019 = source(self.who, 'ICD-10-WHO', 'ICD102019', released=True, canonical_url=None)
        ConceptFactory(parent=self.icd, mnemonic='S20.4', sources=[self.icd_2019],
                       names=[name('Other superficial injuries of back wall of thorax', preferred=True)])
        self.rest = source(self.hl7, 'restful-interaction', canonical_url=REST)
        self.rest_v1 = source(self.hl7, 'restful-interaction', '4.0.1', released=True, canonical_url=REST)
        self.batch = ConceptFactory(parent=self.rest, mnemonic='batch', sources=[self.rest_v1])

        self.hiv = source(self.moh, 'hiv', canonical_url=HIV, public_access=ACCESS_TYPE_NONE)
        self.hiv_v1 = source(self.moh, 'hiv', 'v1', released=True, canonical_url=HIV, public_access=ACCESS_TYPE_NONE)
        self.private_code = ConceptFactory(parent=self.hiv, mnemonic='P1', sources=[self.hiv_v1],
                                           names=[name('Private code', preferred=True)])

        private_shared = source(self.jdoe, 'shared', canonical_url=SHARED, public_access=ACCESS_TYPE_NONE)
        private_shared_v1 = source(self.jdoe, 'shared', 'v1', released=True, canonical_url=SHARED,
                                   public_access=ACCESS_TYPE_NONE)
        ConceptFactory(parent=private_shared, mnemonic='S1', sources=[private_shared_v1],
                       names=[name('Private copy', preferred=True)])
        self.public_shared = source(self.hl7, 'shared', canonical_url=SHARED)
        self.public_shared_v1 = source(self.hl7, 'shared', 'v1', released=True, canonical_url=SHARED)
        ConceptFactory(parent=self.public_shared, mnemonic='S1', sources=[self.public_shared_v1],
                       names=[name('Public copy', preferred=True)])

        self.newest_private = source(self.hl7, 'newest', canonical_url=NEWEST_PRIVATE)
        self.newest_public_v1 = source(self.hl7, 'newest', 'v1', released=True, canonical_url=NEWEST_PRIVATE)
        self.newest_private_v2 = source(self.hl7, 'newest', 'v2', released=True, canonical_url=NEWEST_PRIVATE,
                                        public_access=ACCESS_TYPE_NONE)
        ConceptFactory(parent=self.newest_private, mnemonic='N1',
                       sources=[self.newest_public_v1, self.newest_private_v2], names=[name('N one', preferred=True)])

        self.fevers = collection(self.acme, 'fevers', canonical_url=FEVERS)
        self.fevers_v1 = collection(self.acme, 'fevers', 'v1', released=True, canonical_url=FEVERS)
        expansion = ExpansionFactory(mnemonic='autoexpand-v1', collection_version=self.fevers_v1)
        expansion.concepts.add(self.infectious, self.retired, self.private_code, self.batch)
        expansion.evaluated_source_versions.add(self.ciel_v2)
        self.fevers_v1.expansion_uri = expansion.uri
        self.fevers_v1.save()

        if self.registry:
            for url, owner in ((CIEL, self.ciel_org), (ICD10, self.who), (REST, self.hl7), (SHARED, self.hl7),
                               (NEWEST_PRIVATE, self.hl7), (FEVERS, self.acme)):
                GlobalURLRegistryFactory(url=url, namespace=owner.uri)

    @staticmethod
    def client_for(user=None):
        client = APIClient()
        if user:
            client.force_authenticate(user)
        return client

    def get(self, operation, base='/fhir', user=None, resource='CodeSystem', **params):
        return self.client_for(user).get(f'{base}/{resource}/{operation}', params)

    def post(  # pylint: disable=too-many-arguments
            self, operation, parameters, base='/fhir', user=None, resource='CodeSystem'):
        return self.client_for(user).post(
            f'{base}/{resource}/{operation}', json.dumps({'resourceType': 'Parameters', 'parameter': parameters}),
            content_type='application/fhir+json')

    @staticmethod
    def by_name(response):
        """A Parameters response's parameters: {name: parameter}, each designation in `designations`."""
        parameters = {}
        for parameter in response.data['parameter']:
            if parameter['name'] == 'designation':
                parameters.setdefault('designations', []).append(parameter)
            else:
                parameters[parameter['name']] = parameter
        return parameters

    def assert_outcome(self, response, status_code, issue, tx_issue_type):
        self.assertEqual(response.status_code, status_code, response.data)
        self.assertEqual(response.data['resourceType'], 'OperationOutcome')
        self.assertEqual(response.data['issue'][0]['code'], issue)
        self.assertEqual(response.data['issue'][0]['details']['coding'],
                         [{'system': TX_ISSUE_TYPE_SYSTEM, 'code': tx_issue_type}])


class LookupCases(OpsCorpusMixin):
    def lookup(self, **params):
        return self.get('$lookup', **params)

    def test_answers_name_version_display_code_system_abstract_designations_and_inactive(self):
        response = self.lookup(system=CIEL, code='117053')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['parameter'][:6], [
            {'name': 'name', 'valueString': 'CIEL'},
            {'name': 'version', 'valueString': 'v2'},
            {'name': 'display', 'valueString': 'Infectious enteritis'},
            {'name': 'code', 'valueCode': '117053'},
            {'name': 'system', 'valueUri': CIEL},
            {'name': 'abstract', 'valueBoolean': False},
        ])
        self.assertEqual(response.data['parameter'][-1], INACTIVE)
        designations = sorted(
            [{part['name']: next(value for key, value in part.items() if key.startswith('value'))
              for part in designation['part']} for designation in self.by_name(response)['designations']],
            key=lambda designation: designation['value'])
        self.assertEqual(designations, [
            {'language': 'en', 'use': {'code': 'Synonym'}, 'value': 'Enteritis of infectious origin'},
            {'language': 'fr', 'use': {'code': 'Fully-Specified'}, 'value': 'Entérite infectieuse'},
            {'language': 'en', 'use': {'code': 'Fully-Specified'}, 'value': 'Infectious enteritis'},
            {'language': 'es', 'use': {'code': 'Fully-Specified'}, 'value': 'enteritis infecciosa'},
        ])

    def test_answers_the_version_it_resolved(self):
        """The version named, else the newest release; never the repository's latest when another was named."""
        self.assertEqual(self.by_name(self.lookup(system=CIEL, code='117053', version='v1'))['version']['valueString'],
                         'v1')
        older = self.lookup(system=CIEL, code='999', version='v1')
        self.assertEqual(older.status_code, 200, older.data)
        self.assertEqual((self.by_name(older)['version']['valueString'], self.by_name(older)['display']['valueString']),
                         ('v1', 'Older code'))
        self.assert_outcome(self.lookup(system=CIEL, code='999'), 404, 'not-found', 'invalid-code')
        self.assertEqual(self.by_name(self.lookup(system=f'{CIEL}|v1', code='999'))['version']['valueString'], 'v1')

    def test_a_release_without_a_canonical_of_its_own_resolves_by_its_repository(self):
        response = self.lookup(system=ICD10, code='S20.4', version='ICD102019')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.by_name(response)['version']['valueString'], 'ICD102019')
        self.assertEqual(self.by_name(response)['system']['valueUri'], ICD10)

    def test_a_retired_code_is_found_and_flagged_inactive(self):
        response = self.lookup(system=CIEL, code='103')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.by_name(response)['property']['part'][1], {'name': 'value', 'valueBoolean': True})
        self.assertEqual(self.by_name(response)['display']['valueString'], 'HIV antibody')

    def test_display_language(self):
        response = self.lookup(system=CIEL, code='117053', displayLanguage='fr')
        self.assertEqual(self.by_name(response)['display']['valueString'], 'Entérite infectieuse')
        # a language the concept has no name in: its own display
        response = self.lookup(system=CIEL, code='117053', displayLanguage='ru')
        self.assertEqual(self.by_name(response)['display']['valueString'], 'Infectious enteritis')

    def test_a_concept_without_names_is_displayed_by_its_code(self):
        response = self.lookup(system=REST, code='batch')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.by_name(response)['display'], {'name': 'display', 'valueString': 'batch'})
        self.assertNotIn('designations', self.by_name(response))

    def test_errors(self):
        self.assert_outcome(self.lookup(system=CIEL, code='nope'), 404, 'not-found', 'invalid-code')
        self.assertNotIn('expression', self.lookup(system=CIEL, code='nope').data['issue'][0])
        for params in ({'system': UNKNOWN, 'code': '1'}, {'system': CIEL, 'code': '117053', 'version': 'nope'}):
            response = self.lookup(**params)
            self.assert_outcome(response, 422, 'not-found', 'not-found')
            self.assertNotIn('expression', response.data['issue'][0])
        for params, missing in (({'system': CIEL}, 'code'), ({'code': '117053'}, 'system'), ({}, 'code')):
            response = self.lookup(**params)
            self.assert_outcome(response, 400, 'invalid', 'invalid-data')
            self.assertEqual(response.data['issue'][0]['expression'], [missing])

    def test_post(self):
        for value_types in (('valueUri', 'valueCode', 'valueString'), ('valueString', 'valueString', 'valueString')):
            response = self.post('$lookup', [
                {'name': 'system', value_types[0]: CIEL}, {'name': 'code', value_types[1]: '117053'},
                {'name': 'version', value_types[2]: 'v1'}])

            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(self.by_name(response)['version']['valueString'], 'v1')
            self.assertEqual(self.by_name(response)['code']['valueCode'], '117053')
        response = self.client_for().post(
            '/fhir/CodeSystem/$lookup',
            '<Parameters xmlns="http://hl7.org/fhir"><parameter><name value="system"/><valueUri value="'
            f'{CIEL}"/></parameter><parameter><name value="code"/><valueCode value="103"/></parameter></Parameters>',
            content_type='application/fhir+xml', HTTP_ACCEPT='application/fhir+xml')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<valueString value="HIV antibody"/>', response.content)

    def test_a_coding(self):
        response = self.post('$lookup', [
            {'name': 'coding', 'valueCoding': {'system': CIEL, 'code': '999', 'version': 'v1'}}])
        self.assertEqual(self.by_name(response)['display']['valueString'], 'Older code')
        response = self.post('$lookup', [{'name': 'coding', 'valueCoding': {'system': CIEL}}])
        self.assert_outcome(response, 400, 'invalid', 'invalid-data')
        self.assertEqual(response.data['issue'][0]['expression'], ['coding'])

    def test_the_instance_level(self):
        response = self.get(f'{self.ciel_v1.id}/$lookup', code='999')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.by_name(response)['version']['valueString'], 'v1')
        response = self.get(f'{self.icd_2019.id}/$lookup', code='S20.4')  # a release with no canonical of its own
        self.assertEqual(self.by_name(response)['system']['valueUri'], ICD10)

        response = self.get('CIEL/$lookup', base='/orgs/CIEL', code='117053')
        self.assertEqual(self.by_name(response)['version']['valueString'], 'v2')
        response = self.get('CIEL/$lookup', base='/orgs/CIEL', code='999', version='v1')
        self.assertEqual(self.by_name(response)['display']['valueString'], 'Older code')
        response = self.post('CIEL/$lookup/', [{'name': 'code', 'valueCode': '103'}], base='/orgs/CIEL')
        self.assertEqual(self.by_name(response)['display']['valueString'], 'HIV antibody')

        self.assert_outcome(self.get('CIEL/$lookup', code='117053'), 404, 'not-found', 'not-found')
        self.assert_outcome(self.get(f'{self.hiv_v1.id}/$lookup', code='P1', user=self.moh_member),
                            404, 'not-found', 'not-found')
        response = self.get('hiv/$lookup', base='/orgs/MOH', code='P1', user=self.moh_member)
        self.assertEqual(self.by_name(response)['display']['valueString'], 'Private code')

    def test_a_code_system_the_caller_is_not_shown_answers_as_a_missing_one(self):
        member = self.get('$lookup', base='/orgs/MOH', user=self.moh_member, system=HIV, code='P1')
        self.assertEqual(member.status_code, 200, member.data)
        self.assertEqual(self.by_name(member)['display']['valueString'], 'Private code')

        hidden = [self.get('$lookup', base=base, user=user, system=HIV, code=code)
                  for base, user in (('/fhir', None), ('/fhir', self.outsider), ('/fhir', self.moh_member),
                                     ('/orgs/MOH', None), ('/orgs/MOH', self.outsider))
                  for code in ('P1', 'nope')]
        self.hiv.is_active = False  # now it doesn't exist
        self.hiv.save()
        missing = self.get('$lookup', system=HIV, code='P1')
        self.assertEqual(missing.status_code, 422)
        for response in hidden:
            self.assertEqual((response.status_code, response.content), (missing.status_code, missing.content))

    def test_a_private_copy_of_a_canonical_does_not_hide_the_public_one(self):
        response = self.lookup(system=SHARED, code='S1')
        self.assertEqual(self.by_name(response)['display']['valueString'], 'Public copy')

    def test_the_default_version_is_the_newest_release_the_caller_is_shown(self):
        response = self.lookup(system=NEWEST_PRIVATE, code='N1')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.by_name(response)['version']['valueString'], 'v1')
        response = self.get('$lookup', base='/orgs/HL7', user=self.admin, system=NEWEST_PRIVATE, code='N1')
        self.assertEqual(self.by_name(response)['version']['valueString'], 'v2')

    def test_a_hidden_version_in_a_path_answers_as_a_missing_one(self):
        """A private version named in an OCL path is not found, exactly as one that doesn't exist (Codex F1.1)."""
        path = '/orgs/HL7/CodeSystem/newest/v2/'
        hidden = self.lookup(system=path, code='N1')
        self.assertEqual(hidden.status_code, 422, hidden.data)
        self.assertEqual(self.by_name(self.lookup(system='/orgs/HL7/CodeSystem/newest/v1/', code='N1'))['version'],
                         {'name': 'version', 'valueString': 'v1'})
        self.newest_private_v2.is_active = False
        self.newest_private_v2.save()
        self.assertEqual(self.lookup(system=path, code='N1').content, hidden.content)

    def test_a_path_naming_a_version_and_another_version_named(self):
        """
        An OCL path naming one version with another named (version, url|version, HEAD, a coding's) names no version
        OCL holds: a private version in the path answers byte for byte as a missing one, at /fhir/ and at an owner base
        for a non-member (adversarial A.1).
        """
        path = '/orgs/HL7/CodeSystem/newest/v2/'
        requests = [
            ('get', {'system': path, 'version': 'v1', 'code': 'N1'}), ('get', {'system': f'{path}|v1', 'code': 'N1'}),
            ('get', {'system': path, 'version': 'HEAD', 'code': 'N1'}),
            ('post', [{'name': 'coding', 'valueCoding': {'system': path, 'version': 'v1', 'code': 'N1'}}])]
        def ask(base, user, method, inputs):
            if method == 'get':
                return self.get('$lookup', base=base, user=user, **inputs)
            return self.post('$lookup', inputs, base=base, user=user)
        bases = [('/fhir', None), ('/orgs/HL7', None), ('/orgs/HL7', self.outsider)]
        hidden = [(base, user, method, inputs, ask(base, user, method, inputs))
                  for base, user in bases for method, inputs in requests]
        self.newest_private_v2.is_active = False
        self.newest_private_v2.save()
        for base, user, method, inputs, response in hidden:
            missing = ask(base, user, method, inputs)
            self.assertEqual((response.status_code, response.content), (missing.status_code, missing.content),
                             (base, method, inputs))
            self.assertEqual(response.status_code, 422, (base, method, inputs))

    def test_an_owner_base_costs_the_same_for_a_hidden_and_an_unknown_code_system(self):
        """Anonymous, at an owner base: a private code system costs the queries an unknown one does (adversarial A.3).
        For a signed-in non-member the membership check remains, and with the registry an owner-base match stops the
        fall-through to the global entry: both are decision A.3's."""
        if self.registry:
            self.skipTest('with the registry, an owner-base match stops the fall-through (decision A.3)')
        counts = {}
        for name_, operation, params, status_code in (
                ('hidden', '$lookup', {'system': HIV}, 422),
                ('unknown', '$lookup', {'system': 'http://moh.example/fhir/CodeSystem/nope'}, 422),
                ('hidden instance', 'hiv/$lookup', {}, 404), ('unknown instance', 'nope/$lookup', {}, 404)):
            with CaptureQueriesContext(connection) as context:
                response = self.get(operation, base='/orgs/MOH', code='P1', **params)
            self.assertEqual(response.status_code, status_code, name_)
            counts[name_] = len(context.captured_queries)
        self.assertEqual(counts['hidden'], counts['unknown'])
        self.assertEqual(counts['hidden instance'], counts['unknown instance'])

    def test_an_instance_and_a_coding_of_another_system(self):
        """At the instance level, a coding names the code system it is from: another one's code isn't looked up."""
        response = self.post('CIEL/$lookup', [{'name': 'coding', 'valueCoding': {'system': ICD10, 'code': '117053'}}],
                             base='/orgs/CIEL')
        self.assert_outcome(response, 400, 'invalid', 'invalid-data')
        self.assertEqual(response.data['issue'][0]['expression'], ['Coding.system'])
        response = self.post('CIEL/$lookup', [{'name': 'coding', 'valueCoding': {'system': CIEL, 'code': '117053'}}],
                             base='/orgs/CIEL')
        self.assertEqual(response.status_code, 200, response.data)

    def test_an_instance_and_another_version_or_system_named(self):
        """
        At the instance level, the id names the code system version: a version (the version parameter, or a coding's)
        or a system parameter naming another one is a 400, never answered from the instance (Codex F2.1, F2.2).
        """
        global_ciel_v1 = f'{self.ciel_v1.id}/$lookup'
        for base, operation, params, expression in (
                ('/fhir', global_ciel_v1, {'code': '999', 'version': 'v2'}, 'version'),
                ('/fhir', global_ciel_v1, {'code': '999', 'system': ICD10}, 'system'),
                ('/orgs/CIEL', 'CIEL/$lookup', {'code': '117053', 'system': ICD10}, 'system')):
            response = self.get(operation, base=base, **params)
            self.assert_outcome(response, 400, 'invalid', 'invalid-data')
            self.assertEqual(response.data['issue'][0]['expression'], [expression], (base, params))
        for base, operation, extra, expression in (
                ('/fhir', global_ciel_v1, [], 'Coding.version'),
                ('/orgs/CIEL', 'CIEL/$lookup', [{'name': 'version', 'valueString': 'v1'}], 'Coding.version'),
                ('/orgs/CIEL', 'CIEL/$lookup', [{'name': 'system', 'valueUri': ICD10}], 'system')):
            coding = {'system': CIEL, 'code': '999', 'version': 'v2' if expression.endswith('version') else 'v1'}
            response = self.post(operation, [{'name': 'coding', 'valueCoding': coding}, *extra], base=base)
            self.assert_outcome(response, 400, 'invalid', 'invalid-data')
            self.assertEqual(response.data['issue'][0]['expression'], [expression], (base, extra))
        # the instance's own version, named any of these ways, is answered
        response = self.get(global_ciel_v1, code='999', version='v1', system=f'{CIEL}|v1')
        self.assertEqual(self.by_name(response)['version']['valueString'], 'v1')
        response = self.post('CIEL/$lookup', [{'name': 'coding', 'valueCoding': {
            'system': CIEL, 'code': '999', 'version': 'v1'}}], base='/orgs/CIEL')
        self.assertEqual(self.by_name(response)['version']['valueString'], 'v1')

    def test_an_owner_instance_serves_head_when_named(self):
        """
        At an owner base, CodeSystem/{id}/$lookup serves HEAD when version=HEAD names it, as the type level does
        (design D6), with HEAD's visibility rule; a global id names a release, so HEAD there is a conflict (Codex F3.1).
        """
        ConceptFactory(parent=self.ciel, mnemonic='H1', names=[name('In HEAD only', preferred=True)])
        response = self.get('CIEL/$lookup', base='/orgs/CIEL', code='H1', version='HEAD')
        self.assertEqual(response.status_code, 200, response.data)
        answer = self.by_name(response)
        self.assertEqual((answer['version']['valueString'], answer['display']['valueString']), ('HEAD', 'In HEAD only'))
        self.assert_outcome(self.get('CIEL/$lookup', base='/orgs/CIEL', code='H1'), 404, 'not-found', 'invalid-code')
        response = self.get('hiv/$lookup', base='/orgs/MOH', user=self.moh_member, code='P1', version='HEAD')
        self.assertEqual(self.by_name(response)['version']['valueString'], 'HEAD')
        hidden = self.get('hiv/$lookup', base='/orgs/MOH', user=self.outsider, code='P1', version='HEAD')
        self.assertEqual(hidden.status_code, 404)
        self.hiv.is_active = False
        self.hiv.save()
        self.assertEqual(self.get('hiv/$lookup', base='/orgs/MOH', code='P1', version='HEAD').content, hidden.content)
        response = self.get(f'{self.ciel_v1.id}/$lookup', code='999', version='HEAD')
        self.assert_outcome(response, 400, 'invalid', 'invalid-data')
        self.assertEqual(response.data['issue'][0]['expression'], ['version'])

    def test_a_deleted_concept_version_is_not_found(self):
        """A concept version deleted (is_active false) is gone, a retired one stays (Codex F1.4)."""
        ConceptFactory(parent=self.ciel, mnemonic='555', sources=[self.ciel_v2], is_active=False,
                       names=[name('Deleted', preferred=True)])
        self.assert_outcome(self.lookup(system=CIEL, code='555'), 404, 'not-found', 'invalid-code')

    def test_a_fixed_number_of_queries(self):
        """Resolution (the toggle, the repository, its release; the registry adds its entry and namespace owner), the
        concept and its names, however many concepts the code system holds."""
        count = 7 if self.registry else 5
        with self.assertNumQueries(count):
            self.lookup(system=CIEL, code='117053')
        for number in range(30):
            ConceptFactory(parent=self.ciel, mnemonic=f'x{number}', sources=[self.ciel_v2], names=[name(f'x {number}')])
        with self.assertNumQueries(count):
            self.lookup(system=CIEL, code='117053')
        with self.assertNumQueries(count - 1):  # no names to read
            self.lookup(system=CIEL, code='nope')


class LookupWithoutRegistryTest(LookupCases, OCLTestCase):
    registry = False


class LookupWithRegistryTest(LookupCases, OCLTestCase):
    registry = True
