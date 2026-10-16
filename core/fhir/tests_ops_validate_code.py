"""
CodeSystem/$validate-code and ValueSet/$validate-code (SVCM ITI-99, FHIR R4 $validate-code) at the global base /fhir/
and at owner bases, GET and POST, at the type and instance levels, with the URL registry toggle off and on: the
answer's code, system, version used and display; FHIRsmith's issues and message ids (unknown system or version, unknown
code, wrong display, inactive, not in the value set); value set membership from the resolved release's default
expansion; a value set that doesn't resolve is a 422 OperationOutcome; whatever the caller isn't shown answers as
missing; a fixed number of queries. OpenConceptLab/ocl_online#451.
"""
from core.collections.tests.factories import ExpansionFactory
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.fhir.outcomes import MESSAGE_ID_EXTENSION
from core.fhir.tests_ops_lookup import (
    OpsCorpusMixin, name, CIEL, ICD10, HIV, REST, UNKNOWN, FEVERS, SHARED)
from core.fhir.tests_search import source, collection
from core.orgs.tests.factories import OrganizationFactory
from core.url_registry.factories import GlobalURLRegistryFactory

DRAFT_ONLY = 'http://example.org/fhir/CodeSystem/head-only'
GONE = 'http://example.org/fhir/CodeSystem/gone'
DRAFT_UNRELEASED = 'http://example.org/fhir/CodeSystem/unreleased'


def issues_of(parameters):
    """The `issues` OperationOutcome's issues as severity|code|tx-issue-type|expression|message id."""
    outcome = parameters.get('issues', {}).get('resource', {'issue': []})
    return sorted(
        '|'.join([item['severity'], item['code'], item['details']['coding'][0]['code'],
                  ','.join(item.get('expression', [])),
                  ','.join(e['valueString'] for e in item.get('extension', []) if e['url'] == MESSAGE_ID_EXTENSION)])
        for item in outcome['issue'])


class ValidateCodeCases(OpsCorpusMixin):
    def validate(self, resource='CodeSystem', base='/fhir', user=None, **params):
        return self.get('$validate-code', base=base, user=user, resource=resource, **params)

    def answer(self, response):
        """{name: value} of a Parameters answer, the issues as issues_of has them."""
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['parameter'][0]['name'], 'result')
        parameters = self.by_name(response)
        values = {key: next(value for name_, value in parameter.items() if name_ not in ('name',))
                  for key, parameter in parameters.items() if key != 'issues'}
        if 'issues' in parameters:
            values['issues'] = issues_of(parameters)
            self.assertIn('message', values)
        return values

    # CodeSystem

    def test_a_valid_code_answers_its_code_system_version_and_display(self):
        self.assertEqual(self.answer(self.validate(url=CIEL, code='117053')), {
            'result': True, 'system': CIEL, 'code': '117053', 'version': 'v2', 'display': 'Infectious enteritis'})
        self.assertEqual(self.answer(self.validate(url=CIEL, code='999', version='v1'))['version'], 'v1')
        self.assertEqual(self.answer(self.validate(url=ICD10, code='S20.4', version='ICD102019'))['result'], True)

    def test_a_display_is_one_of_the_concepts_names_in_any_case(self):
        for display in ('Infectious enteritis', 'INFECTIOUS ENTERITIS', 'Entérite infectieuse',
                        'Enteritis of infectious origin'):
            self.assertTrue(self.answer(self.validate(url=CIEL, code='117053', display=display))['result'], display)
        self.assertEqual(self.answer(self.validate(url=CIEL, code='117053', display='Not it')), {
            'result': False, 'system': CIEL, 'code': '117053', 'version': 'v2', 'display': 'Infectious enteritis',
            'message': self.answer(self.validate(url=CIEL, code='117053', display='Not it'))['message'],
            'issues': ['error|invalid|invalid-display|display|Display_Name_for__should_be_one_of__instead_of']})
        # a concept without names has none to check a display against
        self.assertEqual(self.answer(self.validate(url=REST, code='batch', display='Anything')), {
            'result': True, 'system': REST, 'code': 'batch', 'version': '4.0.1'})

    def test_a_retired_code_is_valid_and_inactive_with_a_warning(self):
        answer = self.answer(self.validate(url=CIEL, code='103'))
        self.assertEqual((answer['result'], answer['inactive'], answer['issues']),
                         (True, True, ['warning|business-rule|code-comment|code|INACTIVE_CONCEPT_FOUND']))

    def test_an_unknown_code(self):
        answer = self.answer(self.validate(url=CIEL, code='nope'))
        self.assertEqual((answer['result'], answer['version'], answer['issues']),
                         (False, 'v2', ['error|code-invalid|invalid-code|code|Unknown_Code_in_Version']))

    def test_an_unknown_system_or_version_with_or_without_a_code(self):
        self.assertEqual(self.answer(self.validate(url=UNKNOWN, code='1')), {
            'result': False, 'system': UNKNOWN, 'code': '1', 'x-caused-by-unknown-system': UNKNOWN,
            'message': f"A definition for CodeSystem '{UNKNOWN}' could not be found, so the code cannot be validated",
            'issues': ['error|not-found|not-found|system|UNKNOWN_CODESYSTEM']})
        answer = self.answer(self.validate(url=CIEL, code='117053', version='nope'))
        self.assertEqual((answer['version'], answer['x-caused-by-unknown-system'], answer['issues']),
                         ('nope', f'{CIEL}|nope', ['error|not-found|not-found|system|UNKNOWN_CODESYSTEM_VERSION']))
        self.assertNotIn('code', self.answer(self.validate(url=UNKNOWN)))
        answer = self.answer(self.validate(url=f'{CIEL}|nope', code='117053'))
        self.assertEqual((answer['x-caused-by-unknown-system'], answer['issues']),
                         (f'{CIEL}|nope', ['error|not-found|not-found|system|UNKNOWN_CODESYSTEM_VERSION']))

    def test_a_code_system_the_caller_is_not_shown_answers_as_a_missing_one(self):
        member = self.validate(base='/orgs/MOH', user=self.moh_member, url=HIV, code='P1')
        self.assertTrue(self.answer(member)['result'])
        hidden = [self.validate(base=base, user=user, url=HIV, **code)
                  for base, user in (('/fhir', None), ('/fhir', self.moh_member), ('/orgs/MOH', None),
                                     ('/orgs/MOH', self.outsider))
                  for code in ({'code': 'P1'}, {'code': 'nope'}, {})]
        self.hiv.is_active = False
        self.hiv.save()
        missing = {code: self.validate(url=HIV, **({'code': code} if code else {})).content
                   for code in ('P1', 'nope', None)}
        for response, code in zip(hidden, ['P1', 'nope', None] * 4):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, missing[code])

    def test_a_private_copy_of_a_canonical_does_not_hide_the_public_one(self):
        self.assertEqual(self.answer(self.validate(url=SHARED, code='S1'))['display'], 'Public copy')

    def test_post_and_a_coding(self):
        response = self.post('$validate-code', [
            {'name': 'url', 'valueUri': CIEL}, {'name': 'code', 'valueString': '117053'},
            {'name': 'display', 'valueString': 'Infectious enteritis'}])
        self.assertTrue(self.answer(response)['result'])
        response = self.post('$validate-code', [{'name': 'coding', 'valueCoding': {
            'system': CIEL, 'code': '117053', 'version': 'v1', 'display': 'Wrong'}}])
        answer = self.answer(response)
        self.assertEqual((answer['version'], answer['issues']), ('v1', [
            'error|invalid|invalid-display|Coding.display|Display_Name_for__should_be_one_of__instead_of']))
        response = self.validate(coding=f'{CIEL}|103')
        self.assertTrue(self.answer(response)['inactive'])

    def test_a_codeable_concept_and_inputs_that_are_not_text(self):
        response = self.post('$validate-code', [{'name': 'codeableConcept', 'valueCodeableConcept': {
            'coding': [{'system': CIEL, 'code': '117053', 'display': 'Infectious enteritis'}]}}])
        self.assertTrue(self.answer(response)['result'])
        response = self.post('$validate-code', [{'name': 'codeableConcept', 'valueCodeableConcept': {
            'coding': [{'system': CIEL, 'code': '117053'}, {'system': ICD10, 'code': 'S20.4'}]}}])
        self.assert_outcome(response, 400, 'not-supported', 'not-supported')
        self.assertEqual(response.data['issue'][0]['expression'], ['codeableConcept'])
        for parameter, expression in (({'name': 'url', 'valueBoolean': True}, 'url'),
                                      ({'name': 'coding', 'valueCoding': {'system': CIEL, 'code': 7}}, 'coding')):
            response = self.post('$validate-code', [parameter, {'name': 'code', 'valueCode': '117053'}])
            self.assert_outcome(response, 400, 'invalid', 'invalid-data')
            self.assertEqual(response.data['issue'][0]['expression'], [expression])

    def test_a_code_stored_percent_encoded(self):
        """OCL stores a code with reserved characters percent-encoded (ICD-11's ND53.Y&XK70); FHIR names it as it is."""
        ConceptFactory(parent=self.ciel, mnemonic='ND53.Y%26XK70', sources=[self.ciel_v2],
                       names=[name('Encoded', preferred=True)])
        self.assertEqual(self.answer(self.validate(url=CIEL, code='ND53.Y&XK70')), {
            'result': True, 'system': CIEL, 'code': 'ND53.Y&XK70', 'version': 'v2', 'display': 'Encoded'})

    def test_the_instance_level(self):
        response = self.get(f'{self.ciel_v1.id}/$validate-code', code='999')
        self.assertEqual(self.answer(response)['version'], 'v1')
        response = self.get('CIEL/$validate-code', base='/orgs/CIEL', code='117053', display='Nope')
        self.assertFalse(self.answer(response)['result'])

    def test_xml(self):
        response = self.client_for().get(
            f'/fhir/CodeSystem/$validate-code?url={UNKNOWN}&code=1&_format=xml')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<valueCanonical value="' + UNKNOWN.encode() + b'"/>', response.content)
        self.assertIn(b'<OperationOutcome', response.content)

    def test_a_hidden_version_in_a_path_answers_as_a_missing_one(self):
        """A private version named in an OCL path answers as an unknown version, exactly as a missing one (F1.1)."""
        path = '/orgs/HL7/CodeSystem/newest/v2/'
        hidden = self.validate(url=path, code='N1')
        self.assertIn('x-caused-by-unknown-system', self.answer(hidden))
        self.newest_private_v2.is_active = False
        self.newest_private_v2.save()
        self.assertEqual(self.validate(url=path, code='N1').content, hidden.content)

    def test_a_coding_of_another_system_is_not_valid_in_this_one(self):
        """The code system validated against is the url's (or the instance); a coding of another system is result
        false, never that system's code taken for this one's (Codex F1.2)."""
        mismatch = ['error|invalid|invalid-data|Coding.system|']
        for base, operation, url in (('/orgs/CIEL', 'CIEL/$validate-code', None), ('/fhir', '$validate-code', CIEL)):
            parameters = [{'name': 'coding', 'valueCoding': {'system': ICD10, 'code': '117053'}}]
            if url:
                parameters.append({'name': 'url', 'valueUri': url})
            answer = self.answer(self.post(operation, parameters, base=base))
            self.assertEqual((answer['result'], answer['system'], answer['issues']), (False, ICD10, mismatch), base)
            parameters[0]['valueCoding']['system'] = CIEL
            self.assertTrue(self.answer(self.post(operation, parameters, base=base))['result'], base)
        response = self.get('CIEL/$validate-code', base='/orgs/CIEL', url=ICD10, code='117053')
        self.assertEqual(self.answer(response)['issues'], ['error|invalid|invalid-data|url|'])
        # an instance's url naming another system is a mismatch whatever the coding says (Codex F2.2)
        answer = self.answer(self.post('CIEL/$validate-code', [
            {'name': 'url', 'valueUri': ICD10}, {'name': 'coding', 'valueCoding': {'system': CIEL, 'code': '117053'}}],
            base='/orgs/CIEL'))
        self.assertEqual((answer['result'], answer['issues']), (False, ['error|invalid|invalid-data|url|']))
        # a coding without a system is of the url's code system
        answer = self.answer(self.post('$validate-code', [
            {'name': 'url', 'valueUri': CIEL}, {'name': 'coding', 'valueCoding': {'code': '117053'}}]))
        self.assertEqual((answer['result'], answer['system']), (True, CIEL))

    def test_an_instance_and_another_version_named(self):
        """
        At the instance level, the id names the code system version: another version named (the version parameter, or
        a coding's) is result false with an issue on it, never validated in the instance's version (Codex F2.1).
        """
        mismatch = 'error|invalid|invalid-data|{}|'
        global_ciel_v1 = f'{self.ciel_v1.id}/$validate-code'
        answer = self.answer(self.get(global_ciel_v1, code='999', version='v2'))
        self.assertEqual((answer['result'], answer['issues']), (False, [mismatch.format('version')]))
        owner_extra = [{'name': 'version', 'valueString': 'v1'}]
        for base, operation, extra in (
                ('/fhir', global_ciel_v1, []), ('/orgs/CIEL', 'CIEL/$validate-code', owner_extra)):
            coding = {'name': 'coding', 'valueCoding': {'system': CIEL, 'code': '999', 'version': 'v2'}}
            answer = self.answer(self.post(operation, [coding, *extra], base=base))
            self.assertEqual((answer['result'], answer['issues']), (False, [mismatch.format('Coding.version')]), base)
        # at the type level, a coding's version is the version it is validated in
        answer = self.answer(self.post('$validate-code', [
            {'name': 'url', 'valueUri': CIEL}, {'name': 'version', 'valueString': 'v1'},
            {'name': 'coding', 'valueCoding': {'system': CIEL, 'code': '999', 'version': 'v2'}}]))
        self.assertEqual((answer['result'], answer['issues']), (False, [mismatch.format('Coding.version')]))
        # the instance's own version, named any of these ways, is validated
        self.assertTrue(self.answer(self.get(global_ciel_v1, code='999', version='v1', url=f'{CIEL}|v1'))['result'])
        self.assertTrue(self.answer(self.post('CIEL/$validate-code', [
            {'name': 'coding', 'valueCoding': {'system': CIEL, 'code': '999', 'version': 'v1'}}],
            base='/orgs/CIEL'))['result'])

    def test_a_deleted_code_system_release(self):
        """A deactivated release (is_active false) whose concept and expansion links remain is no version a member is
        in, nor one to answer: the member is answered from what remains (Codex F2.3)."""
        gone = source(OrganizationFactory(mnemonic='Gone'), 'gone', canonical_url=GONE)
        gone_v1 = source(gone.organization, 'gone', 'v1', released=True, canonical_url=GONE)
        self.fevers_v1.expansion.concepts.add(ConceptFactory(
            parent=gone, mnemonic='G1', sources=[gone_v1], names=[name('G one', preferred=True)]))
        self.assertEqual(self.answer(self.validate_in(url=FEVERS, system=GONE, code='G1'))['version'], 'v1')
        gone_v1.is_active = False
        gone_v1.save()
        self.assertEqual(self.answer(self.validate_in(url=FEVERS, system=GONE, code='G1')), {
            'result': True, 'system': GONE, 'code': 'G1', 'display': 'G one'})
        answer = self.answer(self.validate_in(url=FEVERS, system=GONE, code='G1', systemVersion='v1'))
        self.assertEqual((answer['result'], answer['issues']), (False, [
            'error|code-invalid|not-in-vs|code|None_of_the_provided_codes_are_in_the_value_set_one',
            'error|not-found|not-found|system|UNKNOWN_CODESYSTEM_VERSION']))

    def test_a_deleted_concept_version_is_not_valid(self):
        """Deleted (is_active false) concept versions are gone from code systems and value sets (Codex F1.4)."""
        deleted = ConceptFactory(parent=self.ciel, mnemonic='555', sources=[self.ciel_v2], is_active=False,
                                 names=[name('Deleted', preferred=True)])
        self.fevers_v1.expansion.concepts.add(deleted)
        self.assertEqual(self.answer(self.validate(url=CIEL, code='555'))['issues'],
                         ['error|code-invalid|invalid-code|code|Unknown_Code_in_Version'])
        self.assertFalse(self.answer(self.validate_in(url=FEVERS, system=CIEL, code='555'))['result'])

    def test_the_version_answered_is_the_one_the_expansion_pins(self):
        """An expansion's explicit source version is the version answered, though a newer release holds the same
        concept (Codex F1.3); then the version it evaluated; never a HEAD or unreleased version (F1.5)."""
        pinned = collection(self.acme, 'pinned', 'v1', released=True, canonical_url='https://example.org/pinned')
        collection(self.acme, 'pinned', canonical_url='https://example.org/pinned')
        expansion = ExpansionFactory(mnemonic='autoexpand-v1', collection_version=pinned)
        expansion.concepts.add(self.infectious)  # in CIEL v1 and v2
        expansion.explicit_source_versions.add(self.ciel_v1)
        expansion.evaluated_source_versions.add(self.ciel_v2)
        pinned.expansion_uri = expansion.uri
        pinned.save()
        if self.registry:
            GlobalURLRegistryFactory(url='https://example.org/pinned', namespace=self.acme.uri)
        self.assertEqual(self.answer(self.validate_in(
            url='https://example.org/pinned', system=CIEL, code='117053'))['version'], 'v1')

    def test_a_member_from_a_code_system_with_no_release(self):
        """A released value set's expansion defines its members, whatever the release state of their code systems;
        the version is answered only when a release the caller is shown holds the concept (Codex F1.5)."""
        drafts = OrganizationFactory(mnemonic='Drafts')
        head_only = source(drafts, 'head-only', canonical_url=DRAFT_ONLY)
        unreleased = source(drafts, 'unreleased', canonical_url=DRAFT_UNRELEASED)
        unreleased_v1 = source(drafts, 'unreleased', 'v1', canonical_url=DRAFT_UNRELEASED)
        expansion = self.fevers_v1.expansion
        expansion.concepts.add(
            ConceptFactory(parent=head_only, mnemonic='D1', names=[name('D one', preferred=True)]),
            ConceptFactory(parent=unreleased, mnemonic='U1', sources=[unreleased_v1],
                           names=[name('U one', preferred=True)]))
        expansion.explicit_source_versions.add(unreleased_v1)
        self.assertEqual(self.answer(self.validate_in(url=FEVERS, system=DRAFT_ONLY, code='D1')), {
            'result': True, 'system': DRAFT_ONLY, 'code': 'D1', 'display': 'D one'})
        self.assertEqual(self.answer(self.validate_in(url=FEVERS, system=DRAFT_UNRELEASED, code='U1')), {
            'result': True, 'system': DRAFT_UNRELEASED, 'code': 'U1', 'display': 'U one'})

    def test_code_system_query_counts(self):
        count = 7 if self.registry else 5
        with self.assertNumQueries(count):
            self.validate(url=CIEL, code='117053', display='Infectious enteritis')
        for number in range(30):
            ConceptFactory(parent=self.ciel, mnemonic=f'x{number}', sources=[self.ciel_v2], names=[name(f'x {number}')])
        with self.assertNumQueries(count):
            self.validate(url=CIEL, code='117053', display='Infectious enteritis')

    # ValueSet

    def validate_in(self, base='/fhir', user=None, **params):
        return self.validate(resource='ValueSet', base=base, user=user, **params)

    def test_membership_is_the_default_expansion(self):
        """A code in the release's expansion is a member, whether or not a reference names it."""
        self.assertEqual(self.answer(self.validate_in(url=FEVERS, system=CIEL, code='117053')), {
            'result': True, 'system': CIEL, 'code': '117053', 'version': 'v2', 'display': 'Infectious enteritis'})
        answer = self.answer(self.validate_in(url=FEVERS, system=CIEL, code='103'))
        self.assertEqual((answer['result'], answer['inactive']), (True, True))
        answer = self.answer(self.validate_in(url=FEVERS, system=REST, code='batch', display='Whatever'))
        self.assertEqual(answer, {'result': True, 'system': REST, 'code': 'batch', 'version': '4.0.1'})

    def test_a_code_of_another_system_is_not_a_member(self):
        answer = self.answer(self.validate_in(url=FEVERS, system=ICD10, code='117053'))
        self.assertEqual(answer['issues'], [
            'error|code-invalid|invalid-code|code|Unknown_Code_in_Version',
            'error|code-invalid|not-in-vs|code|None_of_the_provided_codes_are_in_the_value_set_one'])
        self.assertNotIn('version', answer)

    def test_a_code_its_system_holds_outside_the_value_set(self):
        self.assertEqual(self.answer(self.validate_in(url=FEVERS, system=CIEL, code='999', systemVersion='v1')), {
            'result': False, 'system': CIEL, 'code': '999', 'version': 'v1', 'display': 'Older code',
            'message': f"The provided code '{CIEL}#999' was not found in the value set '{FEVERS}|v1'",
            'issues': ['error|code-invalid|not-in-vs|code|None_of_the_provided_codes_are_in_the_value_set_one']})

    def test_an_unknown_system_in_a_value_set(self):
        answer = self.answer(self.validate_in(url=FEVERS, system=UNKNOWN, code='117053'))
        self.assertEqual((answer['x-unknown-system'], answer['issues']), (UNKNOWN, [
            'error|code-invalid|not-in-vs|code|None_of_the_provided_codes_are_in_the_value_set_one',
            'error|not-found|not-found|system|UNKNOWN_CODESYSTEM']))

    def test_a_wrong_display_in_a_value_set(self):
        answer = self.answer(self.validate_in(url=FEVERS, system=CIEL, code='117053', display='Not it'))
        self.assertEqual((answer['result'], answer['display'], answer['issues']), (False, 'Infectious enteritis', [
            'error|invalid|invalid-display|display|Display_Name_for__should_be_one_of__instead_of']))

    def test_a_member_from_a_code_system_the_caller_is_not_shown_answers_as_an_unknown_system(self):
        self.assertTrue(self.answer(self.validate_in(
            base='/orgs/Acme', user=self.moh_member, url=FEVERS, system=HIV, code='P1'))['result'])
        hidden = self.validate_in(url=FEVERS, system=HIV, code='P1', user=self.moh_member)
        self.hiv.is_active = False
        self.hiv.save()
        self.assertEqual(hidden.content, self.validate_in(url=FEVERS, system=HIV, code='P1').content)
        self.assertIn('x-unknown-system', self.answer(hidden))

    def test_a_value_set_that_does_not_resolve_is_a_422(self):
        response = self.validate_in(url='http://example.org/no-such-value-set', system=CIEL, code='117053')
        self.assert_outcome(response, 422, 'not-found', 'not-found')
        self.assertEqual(response.data['issue'][0]['extension'][0]['valueString'], 'Unable_to_resolve_value_Set_')
        self.assertNotIn('expression', response.data['issue'][0])
        self.assert_outcome(self.validate_in(url=FEVERS, valueSetVersion='nope', system=CIEL, code='117053'),
                            422, 'not-found', 'not-found')

    def test_a_value_set_the_caller_is_not_shown_answers_as_a_missing_one(self):
        self.fevers.public_access = self.fevers_v1.public_access = 'None'
        self.fevers.save()
        self.fevers_v1.save()
        self.acme.members.add(self.outsider)
        self.assertTrue(self.answer(self.validate_in(
            base='/orgs/Acme', user=self.outsider, url=FEVERS, system=CIEL, code='117053'))['result'])
        hidden = [self.validate_in(base=base, user=user, url=FEVERS, system=CIEL, code='117053')
                  for base, user in (('/fhir', None), ('/fhir', self.outsider), ('/orgs/Acme', self.jdoe))]
        self.fevers.is_active = False
        self.fevers.save()
        missing = self.validate_in(url=FEVERS, system=CIEL, code='117053')
        for response in hidden:
            self.assertEqual((response.status_code, response.content), (422, missing.content))

    def test_value_set_post_and_instance_level(self):
        response = self.post('$validate-code', [
            {'name': 'url', 'valueUri': FEVERS}, {'name': 'code', 'valueString': '117053'},
            {'name': 'system', 'valueString': CIEL}], resource='ValueSet')
        self.assertTrue(self.answer(response)['result'])
        response = self.get('fevers/$validate-code', base='/orgs/Acme', resource='ValueSet', system=CIEL, code='117053')
        self.assertTrue(self.answer(response)['result'])
        response = self.get(f'{self.fevers_v1.id}/$validate-code', resource='ValueSet', system=CIEL, code='999')
        self.assertFalse(self.answer(response)['result'])
        self.assert_outcome(self.get('fevers/$validate-code', resource='ValueSet', system=CIEL, code='117053'),
                            404, 'not-found', 'not-found')

    def test_a_value_set_instance_and_another_version_or_url_named(self):
        """At the instance level the id names the value set version: a valueSetVersion or url naming another one is a
        400, never answered from the instance (the ValueSet side of Codex F2.1 and F2.2)."""
        global_fevers = f'{self.fevers_v1.id}/$validate-code'
        for base, operation, params, expression in (
                ('/fhir', global_fevers, {'valueSetVersion': 'v2'}, 'valueSetVersion'),
                ('/fhir', global_fevers, {'url': 'https://example.org/another'}, 'url'),
                ('/orgs/Acme', 'fevers/$validate-code', {'url': f'{FEVERS}|v2'}, 'url')):
            response = self.get(operation, base=base, resource='ValueSet', system=CIEL, code='117053', **params)
            self.assert_outcome(response, 400, 'invalid', 'invalid-data')
            self.assertEqual(response.data['issue'][0]['expression'], [expression], (base, params))
        response = self.get(global_fevers, resource='ValueSet', system=CIEL, code='117053', valueSetVersion='v1',
                            url=FEVERS)
        self.assertTrue(self.answer(response)['result'])

    def test_without_a_value_set_the_code_system_alone_answers(self):
        self.assertEqual(self.answer(self.validate_in(system=CIEL, code='999', systemVersion='v1'))['result'], True)
        self.assertIn('x-caused-by-unknown-system', self.answer(self.validate_in(system=UNKNOWN, code='1')))

    def test_value_set_query_counts(self):
        """Resolving the value set, the code's concepts, those in the expansion, the code system version of the member
        (with whether the caller is shown it), the concept and its names, whatever the size of the value set; a code
        outside it adds its code system's resolution."""
        resolution = 5 if self.registry else 3  # the toggle, the repository, its release (+ the registry's two)
        with self.assertNumQueries(resolution + 5):
            self.validate_in(url=FEVERS, system=CIEL, code='117053')
        for number in range(30):
            concept = ConceptFactory(parent=self.ciel, mnemonic=f'x{number}', sources=[self.ciel_v2])
            self.fevers_v1.expansion.concepts.add(concept)
        with self.assertNumQueries(resolution + 5):
            self.validate_in(url=FEVERS, system=CIEL, code='117053')
        with self.assertNumQueries(2 * resolution + 4):  # its concepts, none in the expansion; the code system's
            self.validate_in(url=FEVERS, system=CIEL, code='999', systemVersion='v1')
        with self.assertNumQueries(2 * resolution + 2):  # no concept with the code: no membership to read
            self.validate_in(url=FEVERS, system=CIEL, code='nope')


class ValidateCodeWithoutRegistryTest(ValidateCodeCases, OCLTestCase):
    registry = False


class ValidateCodeWithRegistryTest(ValidateCodeCases, OCLTestCase):
    registry = True
