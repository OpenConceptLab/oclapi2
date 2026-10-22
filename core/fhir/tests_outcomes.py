from django.core.exceptions import ValidationError as DjangoValidationError, PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.test import SimpleTestCase
from fhir.resources.R4B.operationoutcome import OperationOutcome
from rest_framework import exceptions

from core.common.exceptions import Http400
from core.fhir import outcomes
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM


def issue_summary(outcome):
    """severity|code|tx-issue-type|expression for each issue: what the parity suite compares."""
    summary = []
    for issue in outcome['issue']:
        coding = [c['code'] for c in issue['details']['coding'] if c['system'] == TX_ISSUE_TYPE_SYSTEM]
        expression = ','.join(issue.get('expression', []))
        summary.append(f"{issue['severity']}|{issue['code']}|{','.join(coding)}|{expression}")
    return summary


class TypedErrorTest(SimpleTestCase):
    def test_each_typed_error_has_its_code_tx_issue_type_and_status(self):
        cases = [
            (outcomes.NotFoundError, 404, 'not-found', 'not-found'),
            (outcomes.InvalidError, 400, 'invalid', 'invalid-data'),
            (outcomes.NotSupportedError, 400, 'not-supported', 'not-supported'),
            (outcomes.MultipleMatchesError, 400, 'multiple-matches', 'invalid-data'),
            (outcomes.TooCostlyError, 422, 'too-costly', 'too-costly'),
            (outcomes.ProcessingError, 422, 'processing', 'invalid-data'),
        ]
        for klass, status, code, tx_issue_type in cases:
            error = klass('Something is wrong', expression='system')
            self.assertEqual(error.status_code, status, klass)
            outcome = error.outcome()
            self.assertEqual(issue_summary(outcome), [f'error|{code}|{tx_issue_type}|system'], klass)
            self.assertEqual(outcome['issue'][0]['details']['text'], 'Something is wrong')
            OperationOutcome.model_validate(outcome)

    def test_a_typed_error_can_name_its_tx_issue_type_status_and_message_id(self):
        error = outcomes.NotFoundError(
            "Unknown code 'x'", expression=['code', 'coding.code'], tx_issue_type='invalid-code',
            message_id='Unknown_Code_in_Version', status_code=422)

        outcome = error.outcome()

        self.assertEqual(error.status_code, 422)
        self.assertEqual(issue_summary(outcome), ['error|not-found|invalid-code|code,coding.code'])
        self.assertEqual(outcome['issue'][0]['extension'], [
            {'url': outcomes.MESSAGE_ID_EXTENSION, 'valueString': 'Unknown_Code_in_Version'}])
        OperationOutcome.model_validate(outcome)

    def test_a_typed_error_without_a_message_or_expression(self):
        outcome = outcomes.NotSupportedError().outcome()

        self.assertEqual(issue_summary(outcome), ['error|not-supported|not-supported|'])
        self.assertNotIn('expression', outcome['issue'][0])
        self.assertTrue(outcome['issue'][0]['details']['text'])

    def test_issue_codes_without_a_tx_issue_type_of_their_own_get_invalid_data(self):
        self.assertEqual(outcomes.tx_issue_type_for('forbidden'), 'invalid-data')
        self.assertEqual(outcomes.tx_issue_type_for('code-invalid'), 'invalid-code')
        self.assertEqual(outcomes.tx_issue_type_for('throttled'), 'too-costly')


class OutcomeNormalizationTest(SimpleTestCase):
    """Returned outcomes are normalized throughout the response without changing neighboring resources."""

    def test_nested_outcomes_preserve_existing_codings_and_other_resources(self):
        """An outcome inside another outcome still gains its missing code and structured details."""
        coding = {'system': TX_ISSUE_TYPE_SYSTEM, 'code': 'invalid-code'}
        nested = {'resourceType': 'OperationOutcome', 'id': 'nested', 'issue': [{'details': 'Nested failure'}]}
        outer = {'resourceType': 'OperationOutcome', 'contained': [nested], 'issue': [
            {'severity': 'error', 'code': 'code-invalid', 'details': {'coding': [coding], 'text': 'Unknown code'}}]}
        code_system = {'resourceType': 'CodeSystem', 'status': 'active', 'content': 'not-present'}
        bundle = {'resourceType': 'Bundle', 'type': 'collection', 'entry': [
            {'resource': outer}, {'resource': code_system}]}

        normalized = outcomes.normalize_outcomes(bundle)

        result = normalized['entry'][0]['resource']
        OperationOutcome.model_validate(result)
        self.assertEqual(result['issue'], outer['issue'])
        self.assertEqual(issue_summary(result['contained'][0]), ['error|processing|invalid-data|'])
        self.assertEqual(result['contained'][0]['issue'][0]['details']['text'], 'Nested failure')
        self.assertEqual(normalized['entry'][1]['resource'], code_system)
        self.assertEqual(nested['issue'], [{'details': 'Nested failure'}])


class ExceptionHandlerTest(SimpleTestCase):
    def handle(self, exc):
        response = outcomes.exception_handler(exc, {})
        OperationOutcome.model_validate(response.data)
        return response

    def test_typed_error(self):
        response = self.handle(outcomes.NotFoundError('No such system', expression='system'))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(issue_summary(response.data), ['error|not-found|not-found|system'])

    def test_not_found_exceptions(self):
        for exc in (Http404('nope'), exceptions.NotFound()):
            response = self.handle(exc)

            self.assertEqual(response.status_code, 404, exc)
            self.assertEqual(issue_summary(response.data), ['error|not-found|not-found|'], exc)

    def test_drf_exceptions_keep_their_status(self):
        cases = [
            (exceptions.NotAuthenticated(), 401, 'login'),
            (exceptions.AuthenticationFailed(), 401, 'login'),
            (exceptions.PermissionDenied(), 403, 'forbidden'),
            (DjangoPermissionDenied(), 403, 'forbidden'),
            (exceptions.MethodNotAllowed('DELETE'), 405, 'not-supported'),
            (exceptions.NotAcceptable(), 406, 'not-supported'),
            (exceptions.UnsupportedMediaType('text/plain'), 415, 'not-supported'),
            (exceptions.ParseError(), 400, 'invalid'),
            (Http400('The following query params are not supported: x'), 400, 'invalid'),
            (exceptions.Throttled(wait=3), 429, 'throttled'),
        ]
        for exc, status, code in cases:
            response = self.handle(exc)

            self.assertEqual(response.status_code, status, exc)
            self.assertEqual(response.data['issue'][0]['code'], code, exc)
            self.assertEqual(
                response.data['issue'][0]['details']['coding'][0]['code'], outcomes.tx_issue_type_for(code), exc)

    def test_throttled_and_unauthenticated_keep_their_headers(self):
        self.assertEqual(self.handle(exceptions.Throttled(wait=3))['Retry-After'], '3')

        exc = exceptions.NotAuthenticated()
        exc.auth_header = 'Token'
        self.assertEqual(self.handle(exc)['WWW-Authenticate'], 'Token')

    def test_validation_error_dict_names_each_field(self):
        response = self.handle(exceptions.ValidationError(
            {'version': ['Version 1.0 already exists.'], 'non_field_errors': ['Bad.'], 'url': 'Required.'}))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(issue_summary(response.data), [
            'error|invalid|invalid-data|version', 'error|invalid|invalid-data|', 'error|invalid|invalid-data|url'])
        self.assertEqual(
            [issue['details']['text'] for issue in response.data['issue']],
            ['Version 1.0 already exists.', 'Bad.', 'Required.'])

    def test_validation_error_list_and_nested_dict(self):
        response = self.handle(exceptions.ValidationError(['First.', 'Second.']))
        self.assertEqual(issue_summary(response.data), ['error|invalid|invalid-data|', 'error|invalid|invalid-data|'])

        response = self.handle(exceptions.ValidationError({'parameter': [{'name': ['Required.']}]}))
        self.assertEqual(issue_summary(response.data), ['error|invalid|invalid-data|parameter'])
        self.assertEqual(response.data['issue'][0]['details']['text'], 'Required.')

    def test_validation_error_carrying_an_operation_outcome_is_kept_and_gets_a_tx_issue_type(self):
        detail = {'resourceType': 'OperationOutcome', 'issue': [
            {'severity': 'error', 'code': 'not-found', 'details': {'text': "Code 'x' not found"}}]}

        response = self.handle(exceptions.ValidationError(detail=detail))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(issue_summary(response.data), ['error|not-found|not-found|'])
        self.assertEqual(response.data['issue'][0]['details']['text'], "Code 'x' not found")

    def test_an_outcome_without_codes_gets_them(self):
        detail = {'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'details': 'Failed'}, {}]}

        response = self.handle(exceptions.ValidationError(detail=detail))

        self.assertEqual(issue_summary(response.data), ['error|processing|invalid-data|'] * 2)
        self.assertEqual(response.data['issue'][0]['details']['text'], 'Failed')

    def test_django_validation_error(self):
        response = self.handle(DjangoValidationError({'url': ['Enter a valid URL.']}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(issue_summary(response.data), ['error|invalid|invalid-data|url'])

        response = self.handle(DjangoValidationError('Bad parameters'))
        self.assertEqual(issue_summary(response.data), ['error|invalid|invalid-data|'])
        self.assertEqual(response.data['issue'][0]['details']['text'], 'Bad parameters')

    def test_server_side_api_exceptions_never_return_a_5xx(self):
        class Unavailable(exceptions.APIException):
            status_code = 503

        for exc in (exceptions.APIException(), Unavailable()):
            response = self.handle(exc)

            self.assertEqual(response.status_code, 422, exc)
            self.assertEqual(issue_summary(response.data), ['error|exception|invalid-data|'], exc)

    def test_unexpected_exception_is_logged_and_answered_with_a_4xx_outcome(self):
        with self.assertLogs('oclapi', level='ERROR') as logs:
            response = self.handle(TypeError('get_serializer() got multiple values for argument'))

        self.assertEqual(response.status_code, 422)
        self.assertEqual(issue_summary(response.data), ['error|exception|invalid-data|'])
        self.assertNotIn('get_serializer', response.data['issue'][0]['details']['text'])
        self.assertIn('TypeError', logs.output[0])


class OutcomeForStatusTest(SimpleTestCase):
    def test_an_error_status_without_a_body(self):
        self.assertEqual(issue_summary(outcomes.outcome_for_status(404)), ['error|not-found|not-found|'])
        self.assertEqual(issue_summary(outcomes.outcome_for_status(405)), ['error|not-supported|not-supported|'])
        self.assertEqual(issue_summary(outcomes.outcome_for_status(409)), ['error|invalid|invalid-data|'])
        self.assertEqual(issue_summary(outcomes.outcome_for_status(500)), ['error|exception|invalid-data|'])
        self.assertEqual(outcomes.outcome_for_status(499)['issue'][0]['details']['text'],
                         outcomes.UNEXPECTED_ERROR_MESSAGE)

    def test_a_body_with_a_detail_keeps_its_message(self):
        outcome = outcomes.outcome_for_status(403, {'detail': 'You do not have permission.'})

        self.assertEqual(issue_summary(outcome), ['error|forbidden|invalid-data|'])
        self.assertEqual(outcome['issue'][0]['details']['text'], 'You do not have permission.')

    def test_a_field_error_body_names_its_fields(self):
        outcome = outcomes.outcome_for_status(400, {'version': ['Version 1.0 already exists.']})

        self.assertEqual(issue_summary(outcome), ['error|invalid|invalid-data|version'])

    def test_is_outcome(self):
        self.assertTrue(outcomes.is_outcome({'resourceType': 'OperationOutcome', 'issue': []}))
        self.assertFalse(outcomes.is_outcome({'detail': 'x'}))
        self.assertFalse(outcomes.is_outcome(None))

    def test_response_status_maps_5xx_to_422(self):
        self.assertEqual(outcomes.response_status(503), 422)
        self.assertEqual(outcomes.response_status(404), 404)
