"""
OperationOutcome for the FHIR API: typed errors, and the exception handler that answers every error a FHIR view
raises with an OperationOutcome.

Every issue carries a tx-issue-type coding, as FHIRsmith and tx.fhir.org do, so clients and the parity suite can
compare errors by kind rather than by wording. No FHIR response is a 5xx: an error OCL can't attribute to the
request is answered with a 422 and the issue code `exception`, logged at ERROR and sent to Errbit
(report_server_error), so monitoring still counts it.
"""
import logging
from http import HTTPStatus

from django.core.exceptions import BadRequest, SuspiciousOperation, ValidationError as DjangoValidationError, \
    PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import set_rollback

from core.common import ERRBIT_LOGGER

logger = logging.getLogger('oclapi')

TX_ISSUE_TYPE_SYSTEM = 'http://hl7.org/fhir/tools/CodeSystem/tx-issue-type'
MESSAGE_ID_EXTENSION = 'http://hl7.org/fhir/StructureDefinition/operationoutcome-message-id'

# The tx-issue-type of an issue that names none, by its FHIR issue type. This is FHIRsmith's table
# (tx/library/operation-outcome.js), plus `throttled`; every other issue type gets `invalid-data`, as there.
DEFAULT_TX_ISSUE_TYPES = {
    'not-found': 'not-found',
    'code-invalid': 'invalid-code',
    'business-rule': 'business-rule',
    'too-costly': 'too-costly',
    'not-supported': 'not-supported',
    'throttled': 'too-costly',
}
FALLBACK_TX_ISSUE_TYPE = 'invalid-data'

# The issue type of an error that has only an HTTP status
STATUS_ISSUE_CODES = {
    400: 'invalid',
    401: 'login',
    403: 'forbidden',
    404: 'not-found',
    405: 'not-supported',
    406: 'not-supported',
    410: 'deleted',
    415: 'not-supported',
    422: 'processing',
    429: 'throttled',
}
NON_FIELD_ERROR_KEYS = ('non_field_errors', '__all__')
UNEXPECTED_ERROR_STATUS = status.HTTP_422_UNPROCESSABLE_ENTITY
UNEXPECTED_ERROR_MESSAGE = 'An unexpected error occurred while processing the request.'


def tx_issue_type_for(issue_code):
    """The tx-issue-type for an issue that doesn't name one."""
    return DEFAULT_TX_ISSUE_TYPES.get(issue_code, FALLBACK_TX_ISSUE_TYPE)


def issue(  # pylint: disable=too-many-arguments
        code, text, severity='error', expression=None, tx_issue_type=None, message_id=None, diagnostics=None):
    """One OperationOutcome issue. `expression` is a FHIRPath (the parameter or element at fault) or a list of them."""
    result = {
        'severity': severity,
        'code': code,
        'details': {
            'coding': [{'system': TX_ISSUE_TYPE_SYSTEM, 'code': tx_issue_type or tx_issue_type_for(code)}],
            'text': text
        }
    }
    if expression:
        result['expression'] = [expression] if isinstance(expression, str) else list(expression)
    if message_id:
        result['extension'] = [{'url': MESSAGE_ID_EXTENSION, 'valueString': message_id}]
    if diagnostics:
        result['diagnostics'] = diagnostics
    return result


def operation_outcome(*issues):
    return {'resourceType': 'OperationOutcome', 'issue': list(issues)}


def is_outcome(data):
    return isinstance(data, dict) and data.get('resourceType') == 'OperationOutcome'


def response_status(status_code):
    """The status a FHIR error goes out with: a 5xx becomes a 422."""
    return UNEXPECTED_ERROR_STATUS if status_code >= 500 else status_code


def report_server_error(status_code, exc=None):
    """
    A 5xx a FHIR answer goes out as a 422 is still OCL's error: logged at ERROR, with the exception's traceback when
    there is one (Errbit gets a logged traceback), else with a notice to Errbit of its own.
    """
    message = f'FHIR answered a {status_code} server error as a {UNEXPECTED_ERROR_STATUS}'
    if exc is not None:
        message = f'{message}: {type(exc).__name__}'
    logger.error(message, exc_info=exc)
    if exc is None:
        ERRBIT_LOGGER.raise_errbit(message)


def status_issue_code(status_code):
    if status_code >= 500:
        return 'exception'
    return STATUS_ISSUE_CODES.get(status_code, 'invalid')


def status_text(status_code):
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return UNEXPECTED_ERROR_MESSAGE


def to_plain(value):
    """DRF error details (ErrorDetail strings, nested) as plain JSON values."""
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    return str(value) if isinstance(value, str) else value


def detail_issues(detail, code='invalid', field=None):
    """Issues for a serializer-style error detail: one per message, naming the field it belongs to."""
    if isinstance(detail, dict):
        issues = []
        for key, value in detail.items():
            issues += detail_issues(value, code, field or (None if key in NON_FIELD_ERROR_KEYS else str(key)))
        return issues
    if isinstance(detail, (list, tuple)):
        return [item for value in detail for item in detail_issues(value, code, field)]
    return [issue(code, str(detail), expression=field)]


def with_tx_issue_types(outcome):
    """An OperationOutcome built elsewhere, with a tx-issue-type coding and a code on every issue."""
    outcome = to_plain(outcome)
    for item in outcome.get('issue') or []:
        item.setdefault('severity', 'error')
        item.setdefault('code', 'processing')
        details = item.get('details')
        if not isinstance(details, dict):
            details = item['details'] = {'text': str(details)} if details else {}
        codings = details.setdefault('coding', [])
        if not any(coding.get('system') == TX_ISSUE_TYPE_SYSTEM for coding in codings):
            codings.append({'system': TX_ISSUE_TYPE_SYSTEM, 'code': tx_issue_type_for(item['code'])})
    return outcome


def normalize_outcomes(data):
    """Normalize every returned OperationOutcome, including resources nested in Bundle entries."""
    if isinstance(data, dict):
        data = {key: normalize_outcomes(value) for key, value in data.items()}
        return with_tx_issue_types(data) if is_outcome(data) else data
    if isinstance(data, (list, tuple)):
        return [normalize_outcomes(value) for value in data]
    return data


def outcome_for_status(status_code, data=None):
    """An error response that isn't an OperationOutcome (an empty 404, a {'detail': ...} body) as one."""
    code = status_issue_code(status_code)
    if isinstance(data, dict) and 'detail' in data:
        return operation_outcome(issue(code, str(data['detail'])))
    if data and isinstance(data, (dict, list)):
        return operation_outcome(*detail_issues(data, code))
    return operation_outcome(issue(code, status_text(status_code)))


class FhirError(exceptions.APIException):
    """An error a FHIR view raises on purpose. Each subclass names its issue type and HTTP status."""
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    issue_code = 'processing'
    default_detail = 'The request could not be processed.'

    def __init__(  # pylint: disable=too-many-arguments
            self, message=None, expression=None, tx_issue_type=None, message_id=None, status_code=None):
        super().__init__(message or self.default_detail)
        self.expression = expression
        self.tx_issue_type = tx_issue_type
        self.message_id = message_id
        if status_code:
            self.status_code = status_code

    def outcome(self):
        return operation_outcome(issue(
            self.issue_code, str(self.detail), expression=self.expression, tx_issue_type=self.tx_issue_type,
            message_id=self.message_id))


class NotFoundError(FhirError):
    status_code = status.HTTP_404_NOT_FOUND
    issue_code = 'not-found'
    default_detail = 'The resource was not found.'


class InvalidError(FhirError):
    status_code = status.HTTP_400_BAD_REQUEST
    issue_code = 'invalid'
    default_detail = 'The request is not valid.'


class NotSupportedError(FhirError):
    status_code = status.HTTP_400_BAD_REQUEST
    issue_code = 'not-supported'
    default_detail = 'The request uses something this server does not support.'


class TooCostlyError(FhirError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    issue_code = 'too-costly'
    default_detail = 'The request is too costly to process.'


class ProcessingError(FhirError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    issue_code = 'processing'


def outcome_for_exception(exc):
    """(status, OperationOutcome, headers) for an exception raised while answering a FHIR request."""
    headers = {}
    if isinstance(exc, FhirError):
        status_code, outcome = exc.status_code, exc.outcome()
    elif isinstance(exc, (Http404, exceptions.NotFound)):
        status_code = 404
        outcome = operation_outcome(issue('not-found', str(getattr(exc, 'detail', exc)) or status_text(404)))
    elif isinstance(exc, DjangoPermissionDenied):
        status_code, outcome = 403, operation_outcome(issue('forbidden', str(exc) or status_text(403)))
    elif isinstance(exc, (BadRequest, SuspiciousOperation)):
        status_code, outcome = 400, operation_outcome(issue('invalid', 'The request could not be parsed.'))
    elif isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, 'error_dict') else exc.messages
        status_code, outcome = 400, operation_outcome(*detail_issues(detail))
    elif isinstance(exc, exceptions.ValidationError):
        status_code = exc.status_code
        outcome = with_tx_issue_types(exc.detail) if is_outcome(exc.detail) else operation_outcome(
            *detail_issues(exc.detail))
    elif isinstance(exc, exceptions.APIException):
        if getattr(exc, 'auth_header', None):
            headers['WWW-Authenticate'] = exc.auth_header
        if getattr(exc, 'wait', None):
            headers['Retry-After'] = str(int(exc.wait))
        status_code = exc.status_code
        outcome = operation_outcome(*detail_issues(exc.detail, status_issue_code(exc.status_code)))
        if status_code >= 500:
            report_server_error(status_code, exc)
    else:
        report_server_error(status.HTTP_500_INTERNAL_SERVER_ERROR, exc)
        status_code, outcome = UNEXPECTED_ERROR_STATUS, operation_outcome(issue('exception', UNEXPECTED_ERROR_MESSAGE))
    return status_code, outcome, headers


def exception_handler(exc, context):  # pylint: disable=unused-argument
    """The exception handler of FHIR views (FhirViewMixin): every error is an OperationOutcome, never a 5xx."""
    status_code, outcome, headers = outcome_for_exception(exc)
    set_rollback()
    return Response(outcome, status=response_status(status_code), headers=headers)
