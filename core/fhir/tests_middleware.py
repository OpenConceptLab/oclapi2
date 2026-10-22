"""The middleware's answers on FHIR paths are OperationOutcomes (OpenConceptLab/ocl_online#451)."""
import gzip
import json
from unittest.mock import patch

import requests
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse, JsonResponse, HttpResponseNotFound
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from fhir.resources.R4B.operationoutcome import OperationOutcome
from lxml import etree
from rest_framework.test import APIClient

from core.common.exceptions import DeactivatedAccountLoginRefused
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM
from core.middlewares.middlewares import FhirErrorMiddleware, FhirMiddleware, RequireAuthenticationMiddleware
from core.wsgi import application

REQUIRE_AUTHENTICATION = 'core.middlewares.middlewares.RequireAuthenticationMiddleware'


def outcome_issue(response):
    """The first issue of an OperationOutcome response, as severity|code|tx-issue-type."""
    outcome = json.loads(response.content)
    issue = outcome['issue'][0]
    tx_issue_type = next(c['code'] for c in issue['details']['coding'] if c['system'] == TX_ISSUE_TYPE_SYSTEM)
    return f"{issue['severity']}|{issue['code']}|{tx_issue_type}"


class FhirMiddlewareTest(SimpleTestCase):
    factory = RequestFactory()

    def answer(self, path, response=None, host='api.example.org', **headers):
        middleware = FhirMiddleware(lambda request: response or HttpResponse('ok'))
        return middleware(self.factory.get(path, HTTP_HOST=host, **headers))

    @override_settings(FHIR_SUBDOMAIN='fhir')
    def test_fhir_paths_off_the_fhir_host_are_a_404_outcome(self):
        for path in ('/fhir/CodeSystem/', '/orgs/CIEL/CodeSystem/$lookup', '/users/jo/ValueSet/x/$expand/'):
            response = self.answer(path)

            self.assertEqual(response.status_code, 404, path)
            self.assertEqual(response['Content-Type'], 'application/fhir+json', path)
            self.assertEqual(outcome_issue(response), 'error|not-found|not-found', path)

        for path in ('/orgs/CIEL/sources/', '/user/sources/CodeSystem/'):
            self.assertEqual(self.answer(path).content, b'ok', path)

    @override_settings(FHIR_SUBDOMAIN='fhir')
    def test_other_paths_on_the_fhir_host_are_a_404_outcome(self):
        response = self.answer('/orgs/CIEL/sources/?q=x', host='fhir.example.org', HTTP_ACCEPT='application/fhir+xml')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(etree.fromstring(response.content).tag, '{http://hl7.org/fhir}OperationOutcome')

        for path in ('/fhir/CodeSystem/?url=http://x', '/orgs/CIEL/CodeSystem?url=http://x', '/version/'):
            self.assertEqual(self.answer(path, host='fhir.example.org').content, b'ok', path)



class FhirErrorMiddlewareTest(SimpleTestCase):
    """The outermost middleware: an error anywhere in the stack on a FHIR path goes out as an OperationOutcome."""
    factory = RequestFactory()

    def answer(self, path, response, **headers):
        return FhirErrorMiddleware(lambda request: response)(self.factory.get(path, **headers))

    def test_is_the_outermost_middleware(self):
        self.assertEqual(settings.MIDDLEWARE[0], 'core.middlewares.middlewares.FhirErrorMiddleware')

    def test_an_error_no_fhir_view_answered_becomes_an_outcome(self):
        response = self.answer('/fhir/Patient/', HttpResponseNotFound(b'<h1>Not Found</h1>'))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(outcome_issue(response), 'error|not-found|not-found')
        self.assertEqual(
            json.loads(response.content)['issue'][0]['details']['text'], 'Unknown FHIR path: GET /fhir/Patient/')

        server_error = JsonResponse({'error': 'Server Error (500)'}, status=500)
        server_error['Access-Control-Allow-Origin'] = '*'
        response = self.answer('/fhir/CodeSystem/', server_error, HTTP_ACCEPT='application/fhir+xml')

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response['Access-Control-Allow-Origin'], '*')  # the stack's headers are kept
        issue = etree.fromstring(response.content).find('{http://hl7.org/fhir}issue/{http://hl7.org/fhir}code')
        self.assertEqual(issue.get('value'), 'exception')

    def test_a_json_error_keeps_its_message(self):
        response = self.answer('/orgs/CIEL/CodeSystem/', JsonResponse({'detail': 'Slow down.'}, status=429))

        self.assertEqual(response.status_code, 429)
        self.assertEqual(json.loads(response.content)['issue'][0]['details']['text'], 'Slow down.')

    def test_answers_left_alone(self):
        outcome = JsonResponse({'resourceType': 'OperationOutcome', 'issue': []}, status=404)
        self.assertIs(self.answer('/fhir/CodeSystem/x', outcome), outcome)

        compressed = HttpResponse(gzip.compress(json.dumps({'resourceType': 'OperationOutcome', 'issue': []}).encode()),
                                  status=404, content_type='application/json')
        compressed['Content-Encoding'] = 'gzip'
        self.assertIs(self.answer('/fhir/CodeSystem/x', compressed), compressed)

        not_found = HttpResponseNotFound(b'<h1>Not Found</h1>')
        self.assertIs(self.answer('/orgs/CIEL/sources/x/', not_found), not_found)
        ok = HttpResponse('ok')
        self.assertIs(self.answer('/fhir/CodeSystem/', ok), ok)


@override_settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=1000, FHIR_SUBDOMAIN=None, REQUIRE_AUTHENTICATION=False,
                   DEBUG=False, DEBUG_PROPAGATE_EXCEPTIONS=False)
class QueryParsingFailureTest(TestCase):
    """Malformed query strings remain FHIR errors through the complete middleware stack."""

    def test_too_many_query_fields_return_a_400_json_outcome(self):
        """Both routed and unknown paths use safe JSON when the query cannot be parsed."""
        query = '&'.join(['_format=xml'] + ['x='] * 1001)
        client = APIClient(raise_request_exception=False)
        for path in ('/fhir/missing', '/fhir/metadata'):
            with self.subTest(path=path):
                response = client.get(f'{path}?{query}', HTTP_ACCEPT='application/fhir+xml')

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response['Content-Type'], 'application/fhir+json')
                OperationOutcome.model_validate_json(response.content)
                self.assertEqual(outcome_issue(response), 'error|invalid|invalid-data')


@override_settings(FHIR_SUBDOMAIN=None, REQUIRE_AUTHENTICATION=False,
                   DEBUG=False, DEBUG_PROPAGATE_EXCEPTIONS=False)
class MediaTypeParsingFailureTest(TestCase):
    """Malformed MIME parameters remain client errors through the complete middleware stack."""

    def assert_bad_request_outcome(self, response):
        """Malformed media types always use safe FHIR JSON, including when XML was requested."""
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Content-Type'], 'application/fhir+json')
        OperationOutcome.model_validate_json(response.content)
        self.assertEqual(outcome_issue(response), 'error|invalid|invalid-data')

    def test_unknown_accept_encoding_on_unknown_path_is_a_400_json_outcome(self):
        """An unrouted path must not fail again while negotiating its error response."""
        response = APIClient(raise_request_exception=False).get(
            '/fhir/missing', HTTP_ACCEPT="application/fhir+xml; foo*=bad''%41")

        self.assert_bad_request_outcome(response)

    def test_unknown_accept_encoding_on_routed_path_is_a_400_json_outcome(self):
        """Initial view negotiation rejects the same malformed Accept as middleware negotiation."""
        client = APIClient(raise_request_exception=False)
        for path in ('/fhir/metadata', '/fhir/metadata?_format=xml'):
            for accept in ("application/fhir+xml; foo*=bad''%41", "text/html; foo*=bad''%41, application/xml"):
                with self.subTest(path=path, accept=accept):
                    response = client.get(path, HTTP_ACCEPT=accept)

                    self.assert_bad_request_outcome(response)

    def test_unknown_content_type_encoding_on_post_is_a_400_json_outcome(self):
        """Use the production request class so Content-Type parsing reaches the middleware stack."""
        client = APIClient(raise_request_exception=False)
        with patch('django.test.client.WSGIRequest', application.request_class):
            for path in ('/fhir/missing', '/fhir/CodeSystem/$lookup'):
                with self.subTest(path=path):
                    response = client.post(
                        path, b'{"resourceType":"Parameters"}',
                        content_type="application/fhir+json; foo*=bad''%41", HTTP_ACCEPT='application/fhir+xml')

                    self.assert_bad_request_outcome(response)

    @override_settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=1000)
    def test_too_many_query_fields_with_charset_are_a_400_json_outcome(self):
        """Query parsing in the production request's encoding setter remains a client error."""
        query = '&'.join(['x='] * 1001)
        client = APIClient(raise_request_exception=False)
        with patch('django.test.client.WSGIRequest', application.request_class):
            response = client.get(
                f'/fhir/metadata?{query}', content_type='application/fhir+json; charset=utf-8',
                HTTP_ACCEPT='application/fhir+xml')

        self.assert_bad_request_outcome(response)

    def test_unknown_charset_name_is_ignored_as_django_does(self):
        """An unknown charset name was never an error: Django ignores it and decodes with the default."""
        client = APIClient(raise_request_exception=False)
        with patch('django.test.client.WSGIRequest', application.request_class):
            response = client.get('/fhir/metadata', content_type='application/fhir+json; charset=not-a-charset')

        self.assertEqual(response.status_code, 200)


    def test_non_text_charset_with_query_is_a_400_json_outcome(self):
        """A failed charset is cleared and cannot break error rendering on an unknown path."""
        client = APIClient(raise_request_exception=False)
        with patch('django.test.client.WSGIRequest', application.request_class):
            response = client.get(
                '/fhir/missing?x=1', content_type='application/fhir+json; charset=base64',
                HTTP_ACCEPT='application/fhir+xml')

        self.assert_bad_request_outcome(response)
        self.assertIsNone(response.wsgi_request.encoding)


@override_settings(REQUIRE_AUTHENTICATION=True, APPROVED_ANONYMOUS_CLIENTS=[], APPROVED_ANONYMOUS_API_KEYS=[],
                   APPROVED_ANONYMOUS_IPS=[], MIDDLEWARE=[
                       *settings.MIDDLEWARE[:settings.MIDDLEWARE.index(
                           'django.contrib.auth.middleware.AuthenticationMiddleware') + 1],
                       REQUIRE_AUTHENTICATION,
                       *[name for name in settings.MIDDLEWARE[settings.MIDDLEWARE.index(
                           'django.contrib.auth.middleware.AuthenticationMiddleware') + 1:]
                         if name != REQUIRE_AUTHENTICATION]])
class OuterMiddlewareFailureTest(TestCase):
    """An outer middleware failing on a FHIR path (the sign-in provider down) is not a 5xx."""

    @patch('core.middlewares.middlewares.OCLAuthentication.authenticate',
           side_effect=requests.exceptions.ConnectionError('sign-in provider unreachable'))
    def test_an_outer_middleware_error_on_a_fhir_path_is_a_4xx_outcome(self, _authenticate):
        client = APIClient(raise_request_exception=False)
        with self.assertLogs('django.request', level='ERROR'):
            response = client.get('/orgs/CIEL/CodeSystem/$lookup?system=http://x&code=1', HTTP_AUTHORIZATION='Bearer x')

        self.assertEqual(response.status_code, 422)
        self.assertEqual(outcome_issue(response), 'error|exception|invalid-data')

        with self.assertLogs('django.request', level='ERROR'):
            response = client.get('/orgs/CIEL/sources/', HTTP_AUTHORIZATION='Bearer x')

        self.assertEqual(response.status_code, 500)  # other paths keep Django's answer
        self.assertEqual(json.loads(response.content), {'error': 'Server Error (500)'})


@override_settings(REQUIRE_AUTHENTICATION=True, APPROVED_ANONYMOUS_CLIENTS=[], APPROVED_ANONYMOUS_API_KEYS=[],
                   APPROVED_ANONYMOUS_IPS=[])
class RequireAuthenticationOnFhirPathsTest(SimpleTestCase):
    factory = RequestFactory()

    def answer(self, path, **headers):
        request = self.factory.get(path, **headers)
        request.user = AnonymousUser()
        return RequireAuthenticationMiddleware(lambda request: HttpResponse('ok'))(request)

    def test_anonymous_access_to_an_owner_base_is_a_403_outcome(self):
        response = self.answer('/orgs/CIEL/CodeSystem/$lookup?system=x&code=1')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(outcome_issue(response), 'error|login|invalid-data')
        issue = json.loads(response.content)['issue'][0]
        self.assertEqual(issue['details']['text'], RequireAuthenticationMiddleware.forbidden_response['detail'])
        self.assertNotIn('diagnostics', issue)

    def test_other_paths_keep_their_json_403(self):
        response = self.answer('/orgs/CIEL/sources/')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content), RequireAuthenticationMiddleware.forbidden_response)

    def test_the_global_fhir_base_stays_open(self):
        self.assertEqual(self.answer('/fhir/CodeSystem/$lookup?system=x&code=1').content, b'ok')

    def test_a_refused_sign_in_on_a_fhir_path_is_an_outcome(self):
        middleware = RequireAuthenticationMiddleware(lambda request: HttpResponse('ok'))
        middleware.is_request_allowed = lambda request: (_ for _ in ()).throw(DeactivatedAccountLoginRefused())

        request = self.factory.get('/orgs/CIEL/CodeSystem/', HTTP_AUTHORIZATION='Bearer x')
        response = middleware(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['WWW-Authenticate'], 'Bearer realm="api"')
        self.assertEqual(outcome_issue(response), 'error|login|invalid-data')

        response = middleware(self.factory.get('/orgs/CIEL/sources/', HTTP_AUTHORIZATION='Bearer x'))
        self.assertEqual(response.status_code, 401)
        self.assertIn('detail', json.loads(response.content))
