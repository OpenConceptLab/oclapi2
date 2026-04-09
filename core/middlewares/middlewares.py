import logging
import time

import requests
from django.conf import settings
from django.http import HttpResponseNotFound, HttpResponse
from django.http.response import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from request_logging.middleware import LoggingMiddleware
from rest_framework.views import APIView

from core.common.constants import VERSION_HEADER, REQUEST_USER_HEADER, RESPONSE_TIME_HEADER, REQUEST_URL_HEADER, \
    REQUEST_METHOD_HEADER
from core.common.throttling import ThrottleUtil
from core.common.utils import set_current_user, set_request_url
from core.services.analytics_event_emitter import AnalyticsEventEmitter
from core.services.auth.core import AuthService

request_logger = logging.getLogger('request_logger')
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
        '',
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
        'upgrade_url': 'https://app.openconceptlab.org/pricing',
    }

    def __call__(self, request):
        """Allow exempt and approved anonymous traffic, otherwise return 403."""
        if self.is_request_allowed(request):
            return self.get_response(request)

        return JsonResponse(self.forbidden_response, status=403)

    def is_request_allowed(self, request):
        """Return whether the current request can bypass authentication enforcement."""
        if request.method == 'OPTIONS' or request.META.get('HTTP_USER_AGENT', '').startswith('ELB-HealthChecker'):
            return True

        user = getattr(request, 'user', None)
        return any((
            getattr(user, 'is_authenticated', False),
            self.is_exempt_path(request.path),
            self.has_approved_client_header(request),
            self.has_approved_api_key(request),
            self.has_approved_ip(request),
        ))

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
        return bool(client_name and client_name in settings.APPROVED_ANONYMOUS_CLIENTS)

    @staticmethod
    def has_approved_api_key(request):
        """Match allowlisted anonymous API keys from common request locations."""
        approved_keys = settings.APPROVED_ANONYMOUS_API_KEYS
        if not approved_keys:
            return False

        authorization = request.META.get('HTTP_AUTHORIZATION', '').strip()
        x_api_key = request.META.get('HTTP_X_API_KEY', '').strip()
        query_api_key = request.GET.get('api_key', '').strip()
        tokens = [authorization, x_api_key, query_api_key]
        bearer_token = authorization.split(None, 1)[1].strip() if ' ' in authorization else ''
        if bearer_token:
            tokens.append(bearer_token)

        return any(token and token in approved_keys for token in tokens)

    @staticmethod
    def has_approved_ip(request):
        """Match source IPs using forwarded addresses first, then the socket address."""
        approved_ips = settings.APPROVED_ANONYMOUS_IPS
        if not approved_ips:
            return False

        forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
        remote_addr = request.META.get('REMOTE_ADDR', '')
        ip_candidates = [ip.strip() for ip in forwarded_for.split(',') if ip.strip()]
        if remote_addr:
            ip_candidates.append(remote_addr.strip())

        return any(ip in approved_ips for ip in ip_candidates)


class FhirMiddleware(BaseMiddleware):
    """
    It is used to expose FHIR endpoints under FHIR subdomain only and convert content from xml to json.
    If FHIR is not deployed under a dedicated subdomain then FHIR_SUBDOMAIN environment variable should be empty.
    """

    def __call__(self, request):
        absolute_uri = request.build_absolute_uri()

        if settings.FHIR_SUBDOMAIN:
            uri = absolute_uri.split('/')
            domain = uri[2] if len(uri) > 2 else ''
            is_fhir_domain = domain.startswith(settings.FHIR_SUBDOMAIN + '.')
            resource_type = uri[5] if len(uri) > 5 else None
            global_space = uri[3] if len(uri) > 3 else None
            is_fhir_resource = (global_space == 'fhir' or resource_type == 'CodeSystem' or
                                resource_type == 'ValueSet' or resource_type == 'ConceptMap')

            if is_fhir_domain:
                if global_space != 'version' and not is_fhir_resource:
                    return HttpResponseNotFound()
            elif is_fhir_resource:
                return HttpResponseNotFound()

        if settings.FHIR_VALIDATOR_URL and ('/fhir/' in absolute_uri or '/CodeSystem/' in absolute_uri or
                                            '/ValueSet/' in absolute_uri or '/ConceptMap' in absolute_uri):
            accept_content_type = request.headers.get('Accept', '')
            content_type = request.headers.get('Content-Type', '')

            if content_type.startswith('application/xml') or content_type.startswith('application/fhir+xml'):
                request.META['CONTENT_TYPE'] = "application/json"
                if request.method in ['POST', 'PUT']:
                    json_request = requests.post(settings.FHIR_VALIDATOR_URL +
                                                 '/convert?version=4.0&type=xml&toType=json', data=request.body)
                    request._body = json_request

            if accept_content_type.startswith(
                    'application/xml') or accept_content_type.startswith('application/fhir+xml'):
                request.META['HTTP_ACCEPT'] = "application/json"

            response = self.get_response(request)

            if accept_content_type.startswith(
                    'application/xml') or accept_content_type.startswith('application/fhir+xml'):
                xml_response = requests.post(settings.FHIR_VALIDATOR_URL + '/convert?version=4.0&type=json&toType=xml',
                                             data=response.content)
                response = HttpResponse(xml_response, content_type=accept_content_type)
        else:
            response = self.get_response(request)

        return response


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

            if minute_limit := ThrottleUtil.get_limit_remaining(throttles[0], request, view):
                response['X-LimitRemaining-Minute'] = minute_limit
                response['X-LimitRemaining-Day'] = ThrottleUtil.get_limit_remaining(throttles[1], request, view)

        return response


class AnalyticsMiddleware(BaseMiddleware):
    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        path = request.path

        ignore_any_under_paths = ['/users/logout/', '/users/signup/']
        ignore_paths = [
            '', '/swagger', '/redoc', '/version', '/toggles', '/users/oidc/code-exchange', '/favicon.ico',
            '/users/api-token', '/users/password/reset', '/user',
            *[p.rstrip('/') for p in ignore_any_under_paths]
        ]
        if path.rstrip("/") not in ignore_paths and not any(path.startswith(p) for p in ignore_any_under_paths):
            duration_ms = int((time.monotonic() - start) * 1000)
            AnalyticsEventEmitter(request, response, duration_ms).emit()
        return response
