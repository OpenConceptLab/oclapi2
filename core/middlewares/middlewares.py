import logging
import time

from django.conf import settings
from django.http.response import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from django.core.exceptions import SuspiciousOperation
from pydash import get
from request_logging.middleware import LoggingMiddleware
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request
from rest_framework.views import APIView

from core.common.authentication import OCLAuthentication
from core.common.constants import VERSION_HEADER, REQUEST_USER_HEADER, RESPONSE_TIME_HEADER, REQUEST_URL_HEADER, \
    REQUEST_METHOD_HEADER
from core.common.exceptions import DeactivatedAccountLoginRefused, SSOIdentityMismatchRefused
from core.common.throttling import ThrottleUtil
from core.common.utils import set_current_user, set_request_url
from core.fhir.base import FHIR_JSON, is_fhir_path, is_outcome_response, outcome_response, response_json
from core.fhir.outcomes import issue, operation_outcome, outcome_for_status
from core.services.analytics_event_emitter import AnalyticsEventEmitter
from core.services.auth.core import AuthService

request_logger = logging.getLogger('request_logger')
fhir_logger = logging.getLogger(__name__)
MAX_BODY_LENGTH = 50000


class BaseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response


class CustomLoggerMiddleware(LoggingMiddleware):
    def __call__(self, request):
        if request.META.get('HTTP_USER_AGENT', '').startswith('ELB-HealthChecker'):
            return self.get_response(request)
        return super().__call__(request)


class FixMalformedLimitParamMiddleware(BaseMiddleware):
    """
    Why this was necessary: https://github.com/OpenConceptLab/ocl_issues/issues/151
    """

    def __call__(self, request):
        to_remove = '?limit=100'
        if request.get_full_path()[-10:] == to_remove and request.method == 'GET':
            query_dict_copy = request.GET.copy()
            for key, value in query_dict_copy.copy().items():
                query_dict_copy[key] = value.replace(to_remove, '')
            if 'limit' not in query_dict_copy:
                query_dict_copy['limit'] = 100
                request.GET = query_dict_copy

        return self.get_response(request)


class ResponseHeadersMiddleware(BaseMiddleware):
    def __call__(self, request):
        start_time = time.time()
        response = self.get_response(request)
        response[VERSION_HEADER] = settings.VERSION
        try:
            response[REQUEST_USER_HEADER] = str(getattr(request, 'user', None))
        except Exception:  # noqa: BLE001 - skip user header when session unavailable (e.g., async context)
            response[REQUEST_USER_HEADER] = ''
        response[RESPONSE_TIME_HEADER] = time.time() - start_time
        response[REQUEST_URL_HEADER] = request.get_full_path() or request.path
        response[REQUEST_METHOD_HEADER] = request.method
        return response


class CurrentUserMiddleware(BaseMiddleware):
    def __call__(self, request):
        set_current_user(lambda self: getattr(request, 'user', None))
        set_request_url(lambda self: request.build_absolute_uri())
        response = self.get_response(request)
        return response


class TokenAuthMiddleWare(BaseMiddleware):
    def __call__(self, request):
        if not AuthService.is_valid_django_token(request):
            authorization_header = request.META.get('HTTP_AUTHORIZATION')
            token = request.session.get("oidc_access_token")
            token_type = AuthService.get().token_type  # Bearer or Token
            if authorization_header:
                if token_type not in authorization_header:
                    request.META['HTTP_AUTHORIZATION'] = authorization_header.replace(
                        'Token', token_type).replace('Bearer', token_type)
            elif token:
                request.META['HTTP_AUTHORIZATION'] = f'{token_type} {token}'

        response = self.get_response(request)
        return response


class RequireAuthenticationMiddleware(BaseMiddleware):
    """Block anonymous API access unless the request matches an approved bypass."""

    exempt_path_prefixes = (
        '/healthcheck/',
        '/users/api-token/',
        '/users/login/',
        '/users/logout/',
        '/users/signup/',
        '/users/password/reset/',
        '/users/oidc/',
        '/oidc/',
        '/fhir/',
        '/swagger',
        '/redoc/',
        '/admin/',
    )
    exempt_exact_paths = {
        '/',
        '/version',
        '/changelog',
        '/feedback',
        '/toggles',
        '/locales',
        '/events',
    }
    forbidden_response = {
        'detail': 'Authentication required. Anonymous API access is disabled.',
        'upgrade_url': 'https://openconceptlab.org/pricing',
    }

    def __call__(self, request):
        """Allow exempt and approved anonymous traffic, otherwise return 403."""
        try:
            is_allowed = self.is_request_allowed(request)
        except (DeactivatedAccountLoginRefused, SSOIdentityMismatchRefused) as ex:
            # a refused sign-in says why, rather than reading as anonymous access (ocl_online#339)
            headers = {'WWW-Authenticate': 'Bearer realm="api"'}
            if is_fhir_path(request.path):
                return outcome_response(
                    request, operation_outcome(issue('login', str(ex.detail))), ex.status_code, headers)
            response = JsonResponse({'detail': str(ex.detail)}, status=ex.status_code)
            response['WWW-Authenticate'] = headers['WWW-Authenticate']
            return response

        if is_allowed:
            return self.get_response(request)

        if is_fhir_path(request.path):
            return outcome_response(request, operation_outcome(issue(
                'login', self.forbidden_response['detail'],
                diagnostics=f"Upgrade: {self.forbidden_response['upgrade_url']}")), 403)
        return JsonResponse(self.forbidden_response, status=403)

    def is_request_allowed(self, request):
        """Return whether the current request can bypass authentication enforcement."""
        if request.method == 'OPTIONS' or request.META.get('HTTP_USER_AGENT', '').startswith('ELB-HealthChecker'):
            return True

        return self.is_exempt_path(request.path) or self.has_approved_client_header(request) or \
            self.has_approved_api_key(request) or self.has_approved_ip(request) or get(
                self.get_authenticated_user(request), 'is_authenticated', False
            )

        # user = self.get_authenticated_user(request)
        # return getattr(user, 'is_authenticated', False)

    @staticmethod
    def get_authenticated_user(request):
        """
        Resolve the authenticated user from either the Django session or the DRF auth stack.

        Django's AuthenticationMiddleware only populates session-backed users. API token and
        OIDC bearer authentication happen later in DRF views, so this middleware must run that
        authentication early when header credentials are present.
        """
        user = getattr(request, 'user', None)
        if getattr(user, 'is_authenticated', False):
            return user

        try:
            auth_result = OCLAuthentication().authenticate(Request(request))
        except (DeactivatedAccountLoginRefused, SSOIdentityMismatchRefused):
            raise
        except AuthenticationFailed:
            return user

        if not auth_result:
            return user

        authenticated_user, auth = auth_result
        request.user = authenticated_user
        request.auth = auth
        return authenticated_user

    @classmethod
    def is_exempt_path(cls, path):
        """Return whether a request path must remain accessible to anonymous users."""
        normalized_path = path.rstrip('/') or '/'
        if normalized_path in cls.exempt_exact_paths:
            return True

        if any(path.startswith(prefix) for prefix in cls.exempt_path_prefixes):
            return True

        return (
            path.startswith('/users/')
            and (
                '/verify/' in path
                or path.endswith('/sso-migrate/')
                or path.endswith('/following/')
            )
        )

    @staticmethod
    def has_approved_client_header(request):
        """Match the configured anonymous allowlist against the X-OCL-CLIENT header."""
        client_name = request.META.get('HTTP_X_OCL_CLIENT', '').strip()
        if not client_name:
            return False

        approved_clients = settings.APPROVED_ANONYMOUS_CLIENTS or set()
        for approved_client in approved_clients:
            if approved_client.endswith('/*'):
                if client_name.startswith(approved_client[:-1]):
                    return True
                continue

            if client_name == approved_client:
                return True

        return False

    @staticmethod
    def has_approved_api_key(request):
        """Match allowlisted anonymous API keys from common request locations."""
        approved_keys = settings.APPROVED_ANONYMOUS_API_KEYS
        if not approved_keys:
            return False

        authorization = request.META.get('HTTP_AUTHORIZATION', '').strip()
        x_api_key = request.META.get('HTTP_X_API_KEY', '').strip()
        tokens = [authorization, x_api_key]
        bearer_token = authorization.split(None, 1)[1].strip() if ' ' in authorization else ''
        if bearer_token:
            tokens.append(bearer_token)

        return any(token and token in approved_keys for token in tokens)

    @staticmethod
    def has_approved_ip(request):
        """Match source IPs using the socket address only."""
        approved_ips = settings.APPROVED_ANONYMOUS_IPS
        if not approved_ips:
            return False

        remote_addr = request.META.get('REMOTE_ADDR', '').strip()
        if not remote_addr:
            return False

        return remote_addr in approved_ips


class FhirErrorMiddleware(BaseMiddleware):
    """
    The outermost middleware (first in MIDDLEWARE). On a FHIR path, an error response that isn't FHIR, from anywhere
    in the stack (an unrouted path, a crash in a view or in another middleware, such as a sign-in provider that
    can't be reached), goes out as an OperationOutcome, and a 5xx as a 422. The stack's headers are kept. Other
    paths are left as they are.
    """
    replaced_headers = ('content-type', 'content-length', 'content-encoding')

    def __call__(self, request):
        if getattr(request, 'fhir_content_type_error', False):
            # Request construction failed: render safe JSON without parsing GET/POST or negotiating again.
            return JsonResponse(outcome_for_status(400), status=400, content_type=FHIR_JSON)
        try:
            response = self.get_response(request)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            # An error that escapes every inner converter (e.g. a logging handler re-reading an over-long query
            # while reporting it): on a FHIR path it still goes out as an OperationOutcome, never a 5xx.
            if not is_fhir_path(request.path):
                raise
            fhir_logger.exception('Unexpected %s answering a FHIR request', type(ex).__name__)
            status = 400 if isinstance(ex, SuspiciousOperation) else 500
            return outcome_response(request, outcome_for_status(status, None), 400 if status == 400 else 422)
        if response.status_code < 400 or not is_fhir_path(request.path) or is_outcome_response(response):
            return response
        if response.status_code == 404:  # nothing is routed there
            outcome = operation_outcome(issue('not-found', f'Unknown FHIR path: {request.method} {request.path}'))
        else:  # a server error's own message isn't passed on
            outcome = outcome_for_status(
                response.status_code, response_json(response) if response.status_code < 500 else None)
        headers = {key: value for key, value in response.items() if key.lower() not in self.replaced_headers}
        return outcome_response(request, outcome, response.status_code, headers)


class FhirMiddleware(BaseMiddleware):
    """
    Keeps FHIR on the FHIR host when FHIR_SUBDOMAIN is set: FHIR resource paths 404 elsewhere, and other paths 404 on
    the FHIR host, each with an OperationOutcome.
    """

    def __call__(self, request):
        fhir_path = is_fhir_path(request.path)
        if settings.FHIR_SUBDOMAIN:
            on_fhir_host = request.get_host().startswith(settings.FHIR_SUBDOMAIN + '.')
            if on_fhir_host and not fhir_path and request.path.strip('/').split('/')[0] != 'version':
                return self.not_found(request, 'This path is not part of the FHIR API.')
            if not on_fhir_host and fhir_path:
                return self.not_found(request, 'The FHIR API is served on its own host.')

        return self.get_response(request)

    @staticmethod
    def not_found(request, message):
        return outcome_response(request, operation_outcome(issue('not-found', message)), 404)


class ThrottleHeadersMiddleware(MiddlewareMixin):
    match_throttled_paths = ['$match', '/concepts/$match/']

    def is_match_throttled_path(self, path):
        for match_path in self.match_throttled_paths:
            if match_path in path:
                return True
        return False

    def process_response(self, request, response):
        if request.path.rstrip("/") not in ['', '/swagger', '/redoc', '/version']:
            view = APIView()
            throttles = ThrottleUtil.get_match_throttles_by_user_plan(request.user) if self.is_match_throttled_path(
                request.path
            ) else ThrottleUtil.get_throttles_by_user_plan(request.user)
            if not throttles:
                return response

            if minute_limit := ThrottleUtil.get_limit_remaining(throttles[0], request, view):
                response['X-LimitRemaining-Minute'] = minute_limit
                response['X-LimitRemaining-Day'] = ThrottleUtil.get_limit_remaining(throttles[1], request, view)

        return response


class AnalyticsMiddleware(BaseMiddleware):
    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        path = request.path

        # /users/signup/ and /users/logout/ are tracked — they're real
        # user-intent events (top-of-funnel + session-end signal). Login
        # tracking was added previously via ocl_online#55.
        ignore_any_under_paths = []
        ignore_paths = [
            '', '/swagger', '/redoc', '/version', '/toggles', '/users/oidc/code-exchange', '/favicon.ico',
            '/users/api-token', '/users/password/reset', '/user',
        ]
        if path.rstrip("/") not in ignore_paths and not any(path.startswith(p) for p in ignore_any_under_paths):
            duration_ms = int((time.monotonic() - start) * 1000)
            AnalyticsEventEmitter(request, response, duration_ms).emit()
        return response
