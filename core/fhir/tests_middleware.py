"""The middleware's answers on FHIR paths are OperationOutcomes (OpenConceptLab/ocl_online#451)."""
import gzip
import json
from unittest.mock import patch

import requests
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse, JsonResponse, HttpResponseNotFound
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from lxml import etree
from rest_framework.test import APIClient

from core.common.exceptions import DeactivatedAccountLoginRefused
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM
from core.middlewares.middlewares import FhirErrorMiddleware, FhirMiddleware, RequireAuthenticationMiddleware

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

        self.assertEqual(self.answer('/orgs/CIEL/sources/').content, b'ok')

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
        self.assertIn(RequireAuthenticationMiddleware.forbidden_response['upgrade_url'], issue['diagnostics'])

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
