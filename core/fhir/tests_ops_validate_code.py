"""
CodeSystem/$validate-code and ValueSet/$validate-code (SVCM ITI-99, FHIR R4 $validate-code) at the global base /fhir/
and at owner bases, GET and POST, at the type and instance levels, with the URL registry toggle off and on: the
answer's code, system, version used and display; FHIRsmith's issues and message ids (unknown system or version, unknown
code, wrong display, inactive, not in the value set); value set membership from the resolved release's default
expansion; a value set that doesn't resolve is a 422 OperationOutcome; whatever the caller isn't shown answers as
missing; a fixed number of queries. OpenConceptLab/ocl_online#451.
"""
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.fhir.outcomes import MESSAGE_ID_EXTENSION
from core.fhir.tests_ops_lookup import (
    OpsCorpusMixin, name, CIEL, ICD10, HIV, REST, UNKNOWN, FEVERS, SHARED)


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
