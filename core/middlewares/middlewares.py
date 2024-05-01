import logging
import time

import requests
from django.http import HttpResponseNotFound, HttpRequest, HttpResponse
from request_logging.middleware import LoggingMiddleware

from core.common.constants import VERSION_HEADER, REQUEST_USER_HEADER, RESPONSE_TIME_HEADER, REQUEST_URL_HEADER, \
    REQUEST_METHOD_HEADER
from core.common.utils import set_current_user, set_request_url
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
        from django.conf import settings
        response[VERSION_HEADER] = settings.VERSION
        response[REQUEST_USER_HEADER] = str(getattr(request, 'user', None))
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


class FhirMiddleware(BaseMiddleware):
    """
    It is used to expose FHIR endpoints under FHIR subdomain only and convert content from xml to json.
    If FHIR is not deployed under a dedicated subdomain then FHIR_SUBDOMAIN environment variable should be empty.
    """

    def __call__(self, request):
        absolute_uri = request.build_absolute_uri()

        from django.conf import settings
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

        if settings.FHIR_VALIDATOR_URL and ('/CodeSystem/' in absolute_uri or '/ValueSet/' in absolute_uri or
                                            '/ConceptMap' in absolute_uri):
            accept_content_type = request.headers.get('Accept')
            content_type = request.headers.get('Content-Type')

            if content_type.startswith('application/xml') or content_type.startswith('application/fhir+xml'):
                request.META['CONTENT_TYPE'] = "application/json"
                if request.method == 'POST' or request.method == 'PUT':
                    json_request = requests.post(settings.FHIR_VALIDATOR_URL +
                                                 '/convert?version=4.0&type=xml&toType=json', data=request.body)
                    request._body = json_request

            if accept_content_type.startswith('application/xml') or \
                    accept_content_type.startswith('application/fhir+xml'):
                request.META['HTTP_ACCEPT'] = "application/json"

            response = self.get_response(request)

            if accept_content_type.startswith('application/xml') or \
                    accept_content_type.startswith('application/fhir+xml'):
                xml_response = requests.post(settings.FHIR_VALIDATOR_URL + '/convert?version=4.0&type=json&toType=xml',
                                             data=response.content)
                response = HttpResponse(xml_response, content_type=accept_content_type)
        else:
            response = self.get_response(request)

        return response
