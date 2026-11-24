import json
import re
import time
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve
from rest_framework.exceptions import AuthenticationFailed

from core.common.exceptions import DeactivatedAccountLoginRefused, SSOIdentityMismatchRefused, \
    SSOUsernameCaseMismatchRefused
from core.middlewares.middlewares import CustomLoggerMiddleware, RequireAuthenticationMiddleware, \
    SlowRequestLogMiddleware


@override_settings(
    REQUIRE_AUTHENTICATION=True,
    APPROVED_ANONYMOUS_CLIENTS=['test-client'],
    APPROVED_ANONYMOUS_API_KEYS=['test-api-key'],
    APPROVED_ANONYMOUS_IPS=['10.0.0.1'],
)
class RequireAuthenticationMiddlewareTest(SimpleTestCase):
    """Verify anonymous authentication enforcement and approved bypasses."""

    def setUp(self):
        """Create a request factory and middleware instance for each test."""
        self.factory = RequestFactory()
        self.middleware = RequireAuthenticationMiddleware(lambda request: HttpResponse('ok'))

    def make_request(self, path='/orgs/', method='get', user=None, **meta):
        """Build a request object with a controllable authenticated user state."""
        request_method = getattr(self.factory, method.lower())
        request = request_method(path, **meta)
        request.user = user or AnonymousUser()
        return request

    def test_allows_authenticated_request(self):
        """Authenticated requests should bypass the anonymous access gate."""
        user = type('AuthenticatedUser', (), {'is_authenticated': True})()

        response = self.middleware(self.make_request(user=user))

        self.assertEqual(response.status_code, 200)

    @patch('core.middlewares.middlewares.OCLAuthentication.authenticate')
    def test_allows_request_authenticated_by_drf_header_auth(self, authenticate_mock):
        """Header auth should be resolved before enforcing the anonymous access gate."""
        user = type('AuthenticatedUser', (), {'is_authenticated': True})()
        authenticate_mock.return_value = (user, 'token-auth')
        request = self.make_request('/orgs/OCL/', HTTP_AUTHORIZATION='Token real-token')

        response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertIs(request.user, user)
        self.assertEqual(request.auth, 'token-auth')

    @patch('core.middlewares.middlewares.OCLAuthentication.authenticate')
    def test_blocks_request_when_drf_header_auth_fails(self, authenticate_mock):
        """Failed DRF header auth should still leave protected paths blocked."""
        authenticate_mock.return_value = None

        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_AUTHORIZATION='Token bad-token'))

        self.assertEqual(response.status_code, 403)

    @patch('core.middlewares.middlewares.OCLAuthentication.authenticate')
    def test_blocks_request_when_drf_header_auth_raises(self, authenticate_mock):
        """An invalid header credential reads as anonymous access."""
        authenticate_mock.side_effect = AuthenticationFailed('Login failed')

        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_AUTHORIZATION='Bearer bad-token'))

        self.assertEqual(response.status_code, 403)

    @patch('core.middlewares.middlewares.OCLAuthentication.authenticate')
    def test_rejects_sign_in_refused_for_deactivated_account(self, authenticate_mock):
        """A sign-in refused for a deactivated account gets a 401 that says why, not the anonymous 403."""
        authenticate_mock.side_effect = DeactivatedAccountLoginRefused()

        response = self.middleware(self.make_request('/user/', HTTP_AUTHORIZATION='Bearer sso-token'))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['WWW-Authenticate'], 'Bearer realm="api"')
        self.assertIn('deactivated OCL account', json.loads(response.content)['detail'])

    @patch('core.middlewares.middlewares.OCLAuthentication.authenticate')
    def test_rejects_sign_in_refused_for_identity_mismatch(self, authenticate_mock):
        """A sign-in refused for a username linked to another Keycloak user gets the same 401 that says why."""
        authenticate_mock.side_effect = SSOIdentityMismatchRefused()

        response = self.middleware(self.make_request('/user/', HTTP_AUTHORIZATION='Bearer sso-token'))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['WWW-Authenticate'], 'Bearer realm="api"')
        self.assertIn('different sign-in identity', json.loads(response.content)['detail'])

    @patch('core.middlewares.middlewares.OCLAuthentication.authenticate')
    def test_rejects_sign_in_refused_for_username_case_mismatch(self, authenticate_mock):
        authenticate_mock.side_effect = SSOUsernameCaseMismatchRefused()

        response = self.middleware(self.make_request('/user/', HTTP_AUTHORIZATION='Bearer sso-token'))

        self.assertEqual(response.status_code, 401)
        self.assertIn('different capitalization', json.loads(response.content)['detail'])

    def test_blocks_anonymous_request_for_protected_path(self):
        """Anonymous traffic to protected API paths should receive a 403 response."""
        response = self.middleware(self.make_request('/orgs/OCL/'))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            json.loads(response.content),
            {
                'detail': 'Authentication required. Anonymous API access is disabled.',
                'upgrade_url': 'https://openconceptlab.org/pricing',
            }
        )

    def test_allows_anonymous_request_for_approved_client_header(self):
        """Approved X-OCL-CLIENT values should retain anonymous access."""
        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_X_OCL_CLIENT='test-client'))

        self.assertEqual(response.status_code, 200)

    def test_blocks_anonymous_request_for_unapproved_client_header(self):
        """Unknown X-OCL-CLIENT values should still be rejected."""
        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_X_OCL_CLIENT='unknown-client'))

        self.assertEqual(response.status_code, 403)

    @override_settings(
        APPROVED_ANONYMOUS_CLIENTS=['oclmap/*', 'oclweb3/2.1.5', 'oclweb2/*']
    )
    def test_allows_anonymous_request_for_wildcard_client_header(self):
        """Wildcard client entries should match any version for the configured client."""
        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_X_OCL_CLIENT='oclmap/4.2.0'))

        self.assertEqual(response.status_code, 200)

    @override_settings(
        APPROVED_ANONYMOUS_CLIENTS=['oclmap/*', 'oclweb3/2.1.5', 'oclweb2/*']
    )
    def test_allows_anonymous_request_for_exact_version_client_header(self):
        """Exact client/version entries should still require an exact match."""
        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_X_OCL_CLIENT='oclweb3/2.1.5'))

        self.assertEqual(response.status_code, 200)

    @override_settings(
        APPROVED_ANONYMOUS_CLIENTS=['oclmap/*', 'oclweb3/2.1.5', 'oclweb2/*']
    )
    def test_blocks_anonymous_request_for_non_matching_exact_version_client_header(self):
        """Exact client/version entries should reject other versions of the same client."""
        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_X_OCL_CLIENT='oclweb3/2.1.6'))

        self.assertEqual(response.status_code, 403)

    @override_settings(
        APPROVED_ANONYMOUS_CLIENTS=['oclmap/*', 'oclweb3/2.1.5', 'oclweb2/*']
    )
    def test_allows_anonymous_request_for_comma_separated_client_string_setting(self):
        """String settings should be normalized before matching approved clients."""
        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_X_OCL_CLIENT='oclweb2/9.9.9'))

        self.assertEqual(response.status_code, 200)

    def test_blocks_anonymous_request_for_whitespace_client_header(self):
        """Whitespace-only client header values should be rejected after normalization."""
        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_X_OCL_CLIENT='   '))

        self.assertEqual(response.status_code, 403)

    def test_allows_anonymous_request_for_approved_api_key_header(self):
        """Allowlisted anonymous API keys should bypass the gate."""
        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_X_API_KEY='test-api-key'))

        self.assertEqual(response.status_code, 200)

    def test_allows_anonymous_request_for_approved_authorization_token(self):
        """Allowlisted bearer or token credentials should bypass the gate."""
        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_AUTHORIZATION='Token test-api-key'))

        self.assertEqual(response.status_code, 200)

    def test_blocks_anonymous_request_for_query_string_api_key(self):
        """Query string API keys should not bypass the authentication gate."""
        response = self.middleware(self.make_request('/orgs/OCL/?api_key=test-api-key'))

        self.assertEqual(response.status_code, 403)

    def test_allows_anonymous_request_for_approved_ip(self):
        """Allowlisted source IPs should keep anonymous access."""
        response = self.middleware(self.make_request('/orgs/OCL/', REMOTE_ADDR='10.0.0.1'))

        self.assertEqual(response.status_code, 200)

    def test_blocks_anonymous_request_for_forwarded_ip_only(self):
        """Forwarded IP headers alone should not bypass the authentication gate."""
        response = self.middleware(
            self.make_request('/orgs/OCL/', HTTP_X_FORWARDED_FOR='10.0.0.1', REMOTE_ADDR='203.0.113.5')
        )

        self.assertEqual(response.status_code, 403)

    def test_allows_options_request(self):
        """CORS preflight requests should not be blocked."""
        response = self.middleware(self.make_request('/orgs/OCL/', method='options'))

        self.assertEqual(response.status_code, 200)

    def test_allows_elb_health_checker_request(self):
        """Infrastructure health checks should bypass the gate."""
        response = self.middleware(
            self.make_request('/orgs/OCL/', HTTP_USER_AGENT='ELB-HealthChecker/2.0')
        )

        self.assertEqual(response.status_code, 200)

    def test_allows_exempt_exact_paths(self):
        """Public root-level utility endpoints should remain anonymous."""
        for path in ['/', '/version/', '/changelog/', '/feedback/', '/toggles/', '/locales/', '/events/']:
            with self.subTest(path=path):
                response = self.middleware(self.make_request(path))
                self.assertEqual(response.status_code, 200)

    def test_allows_exempt_path_prefixes(self):
        """Auth, docs, admin, and FHIR prefixes should remain anonymous."""
        paths = [
            '/healthcheck/',
            '/users/api-token/',
            '/users/login/',
            '/users/logout/',
            '/users/signup/',
            '/users/password/reset/',
            '/users/oidc/code-exchange/',
            '/oidc/authenticate/',
            '/fhir/',
            '/swagger/',
            '/swagger.json',
            '/redoc/',
            '/admin/login/',
        ]

        for path in paths:
            with self.subTest(path=path):
                response = self.middleware(self.make_request(path))
                self.assertEqual(response.status_code, 200)

    def test_allows_exempt_dynamic_user_paths(self):
        """Public user verification and following endpoints should remain anonymous."""
        paths = [
            '/users/alice/verify/token-123/',
            '/users/alice/sso-migrate/',
            '/users/alice/following/',
        ]

        for path in paths:
            with self.subTest(path=path):
                response = self.middleware(self.make_request(path))
                self.assertEqual(response.status_code, 200)


@override_settings(REQUIRE_AUTHENTICATION=False)
class RequireAuthenticationSettingsTest(SimpleTestCase):
    """Verify auth-enforcement middleware configuration toggles cleanly."""

    def test_authentication_middleware_not_inserted_when_disabled(self):
        """RequireAuthenticationMiddleware should be absent when the feature is disabled."""
        self.assertNotIn('core.middlewares.middlewares.RequireAuthenticationMiddleware', settings.MIDDLEWARE)

    def test_cors_allows_analytics_attribution_headers(self):
        """CORS should allow the analytics attribution headers introduced for #109."""
        for header in (
            'X-OCL-REQUEST-SOURCE',
            'X-OCL-EVENT-METADATA',
            'X-OCL-REQUEST-IDEMPOTENCY-KEY',
            'X-OCL-PROMPT-TEMPLATE-KEY',
            'X-OCL-PROMPT-TEMPLATE-VERSION',
        ):
            with self.subTest(header=header):
                self.assertIn(header, settings.CORS_ALLOW_HEADERS)


REQUEST_MARKER = 'request-body-marker'
RESPONSE_MARKER = 'response-body-marker'


@override_settings(LOG_REQUEST_BODIES=False)
class CustomLoggerMiddlewareTest(SimpleTestCase):
    """Request logging writes each request's lines but, unless LOG_REQUEST_BODIES, no request or response body."""

    def setUp(self):
        self.factory = RequestFactory()

    def json_post(self):
        return self.factory.post('/orgs/', data=json.dumps({'name': REQUEST_MARKER}), content_type='application/json')

    def log_lines(self, request, status=400, response=None):
        """Pass the request through the middleware to a view that answers with a marker; return what was logged."""
        middleware = CustomLoggerMiddleware(
            lambda req: response or JsonResponse({'name': RESPONSE_MARKER}, status=status))
        with self.assertLogs('django.request', level='DEBUG') as logs:
            middleware(request)
        return [re.sub(r'\x1b\[[0-9;]*m', '', record.getMessage()) for record in logs.records]

    def assert_no_marker(self, lines):
        for marker in (REQUEST_MARKER, RESPONSE_MARKER):
            self.assertEqual([line for line in lines if marker in line], [])

    def test_logs_request_lines_but_no_body_at_any_status(self):
        for status in (201, 400, 500):
            with self.subTest(status=status):
                lines = self.log_lines(self.json_post(), status)

                self.assert_no_marker(lines)
                self.assertIn('POST /orgs/', lines)
                self.assertIn(f'POST /orgs/ - {status}', lines)

    def test_logs_no_multipart_body(self):
        lines = self.log_lines(self.factory.post('/orgs/', data={'name': REQUEST_MARKER}), 400)

        self.assert_no_marker(lines)

    @override_settings(REQUEST_LOGGING_SENSITIVE_HEADERS=['X-Deployment-Token'])
    def test_masks_credential_headers_whether_or_not_bodies_are_logged(self):
        for log_bodies in (False, True):
            with self.subTest(log_bodies=log_bodies), override_settings(LOG_REQUEST_BODIES=log_bodies):
                request = self.factory.get(
                    '/orgs/', HTTP_AUTHORIZATION=f'Token {REQUEST_MARKER}', HTTP_X_API_KEY=REQUEST_MARKER,
                    HTTP_COOKIE=f'sessionid={REQUEST_MARKER}', HTTP_X_CSRFTOKEN=REQUEST_MARKER,
                    HTTP_X_AUTH_TOKEN=REQUEST_MARKER, HTTP_X_DEPLOYMENT_TOKEN=REQUEST_MARKER)
                lines = self.log_lines(request, response=self.json_response_with_cookies())

                self.assert_no_marker(lines)
                self.assertTrue(any('X-Csrftoken' in line and 'Cookie' in line for line in lines))
                self.assertTrue(any('Set-Cookie' in line for line in lines))

    @staticmethod
    def json_response_with_cookies():
        response = JsonResponse({}, status=400)
        response.set_cookie('sessionid', RESPONSE_MARKER)
        response['Set-Cookie'] = f'token={RESPONSE_MARKER}'
        response['X-Deployment-Token'] = RESPONSE_MARKER
        return response

    @override_settings(LOG_REQUEST_BODIES=True)
    def test_logs_bodies_with_log_request_bodies(self):
        """Shows the capture above would see a body: with LOG_REQUEST_BODIES both bodies are logged."""
        lines = self.log_lines(self.json_post(), 400)

        self.assertTrue(any(REQUEST_MARKER in line for line in lines))
        self.assertTrue(any(RESPONSE_MARKER in line for line in lines))



class SlowRequestLogMiddlewareTest(SimpleTestCase):
    """A request taking SLOW_REQUEST_LOG_MS or more writes one slow_request line, for CloudWatch metric filters."""
    path = '/orgs/CIEL/sources/CIEL/v2026-10-23/mapped-sources/'

    def records(self, request, delay=0, status=200, resolved=True):
        """Pass the request through the middleware to a view taking `delay` seconds; return the records logged."""
        def view(req):
            if resolved:
                req.resolver_match = resolve(req.path)
            time.sleep(delay)
            return HttpResponse(status=status)

        with patch('core.middlewares.middlewares.emit') as emit:
            response = SlowRequestLogMiddleware(view)(request)

        self.assertEqual(response.status_code, status)
        return [logged.args[0] for logged in emit.call_args_list]

    def test_wraps_every_middleware_but_fhir_errors_so_it_times_the_whole_request(self):
        self.assertEqual(settings.MIDDLEWARE[:2], [
            'core.middlewares.middlewares.FhirErrorMiddleware',
            'core.middlewares.middlewares.SlowRequestLogMiddleware',
        ])

    @override_settings(SLOW_REQUEST_LOG_MS=20)
    def test_a_slow_request_logs_its_view_and_route(self):
        request = RequestFactory().get(self.path + '?excludeSelf=false&brief=true', HTTP_AUTHORIZATION='Token secret')

        records = self.records(request, delay=0.03, status=503)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertGreaterEqual(record.pop('ms'), 20)
        self.assertEqual(record, {
            'event': 'slow_request', 'view': 'SourceVersionMappedSourcesListView',
            'route': 'orgs/<str:org>/sources/<str:source>/<str:version>/mapped-sources/', 'method': 'GET',
            'status': 503})

    @override_settings(SLOW_REQUEST_LOG_MS=1)
    def test_logs_no_value_from_the_path(self):
        """A path can carry a user's name and a token, as email verification's does."""
        records = self.records(RequestFactory().get('/users/someone/verify/a-secret-token/'), delay=0.01)

        self.assertEqual(records[0]['route'], 'users/<str:user>/verify/<str:verification_token>/')
        self.assertNotIn('someone', json.dumps(records))
        self.assertNotIn('a-secret-token', json.dumps(records))

    @override_settings(SLOW_REQUEST_LOG_MS=60000)
    def test_a_request_under_the_threshold_logs_nothing(self):
        self.assertEqual(self.records(RequestFactory().get(self.path)), [])

    @override_settings(SLOW_REQUEST_LOG_MS=0)
    def test_zero_turns_it_off(self):
        self.assertEqual(self.records(RequestFactory().get(self.path), delay=0.01), [])

    @override_settings(SLOW_REQUEST_LOG_MS=1)
    def test_a_request_answered_before_url_resolution_still_names_its_view(self):
        """A slow failure in authentication, before Django resolves the URL, still counts for its view."""
        records = self.records(RequestFactory().get(self.path), delay=0.01, status=500, resolved=False)

        self.assertEqual([(record['view'], record['route']) for record in records], [(
            'SourceVersionMappedSourcesListView', 'orgs/<str:org>/sources/<str:source>/<str:version>/mapped-sources/')])

    @override_settings(SLOW_REQUEST_LOG_MS=1, FHIR_SUBDOMAIN='fhir', ALLOWED_HOSTS=['*'])
    def test_a_request_the_fhir_host_answered_before_url_resolution_names_no_view(self):
        """The FHIR host refuses API paths before resolution: such a request isn't a read of the API view."""
        records = self.records(
            RequestFactory().get(self.path, HTTP_HOST='fhir.example.org'), delay=0.01, status=404, resolved=False)

        self.assertEqual([(record['view'], record['route']) for record in records], [(None, None)])

    @override_settings(SLOW_REQUEST_LOG_MS=1, FHIR_SUBDOMAIN='fhir', ALLOWED_HOSTS=['*'])
    def test_a_fhir_request_on_the_fhir_host_answered_before_url_resolution_names_its_view(self):
        records = self.records(
            RequestFactory().get('/fhir/metadata/', HTTP_HOST='fhir.example.org'), delay=0.01, status=500,
            resolved=False)

        self.assertEqual(
            [(record['view'], record['route']) for record in records], [('CapabilityStatementView', 'fhir/metadata/')])

    @override_settings(SLOW_REQUEST_LOG_MS=1, FHIR_SUBDOMAIN='fhir', ALLOWED_HOSTS=['api.example.org'])
    def test_a_disallowed_host_still_logs_the_slow_request(self):
        records = self.records(
            RequestFactory().get(self.path, HTTP_HOST='elsewhere.example.org'), delay=0.01, status=400, resolved=False)

        self.assertEqual([(record['view'], record['status']) for record in records], [(None, 400)])

    @override_settings(SLOW_REQUEST_LOG_MS=1)
    def test_an_unresolved_path_has_no_view_or_route(self):
        records = self.records(RequestFactory().get('/no/such/path/'), delay=0.01, status=404, resolved=False)

        self.assertEqual([(record['view'], record['route']) for record in records], [(None, None)])

    @override_settings(SLOW_REQUEST_LOG_MS=1)
    def test_a_log_that_fails_never_fails_the_request(self):
        def view(_):
            time.sleep(0.01)
            return HttpResponse(status=201)

        with patch('core.middlewares.middlewares.emit', side_effect=RuntimeError("can't start new thread")):
            response = SlowRequestLogMiddleware(view)(RequestFactory().post('/orgs/'))

        self.assertEqual(response.status_code, 201)
