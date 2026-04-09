import json

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.middlewares.middlewares import RequireAuthenticationMiddleware


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

    def test_blocks_anonymous_request_for_protected_path(self):
        """Anonymous traffic to protected API paths should receive a 403 response."""
        response = self.middleware(self.make_request('/orgs/OCL/'))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            json.loads(response.content),
            {
                'detail': 'Authentication required. Anonymous API access is disabled.',
                'upgrade_url': 'https://app.openconceptlab.org/pricing',
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

    def test_allows_anonymous_request_for_approved_api_key_header(self):
        """Allowlisted anonymous API keys should bypass the gate."""
        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_X_API_KEY='test-api-key'))

        self.assertEqual(response.status_code, 200)

    def test_allows_anonymous_request_for_approved_authorization_token(self):
        """Allowlisted bearer or token credentials should bypass the gate."""
        response = self.middleware(self.make_request('/orgs/OCL/', HTTP_AUTHORIZATION='Token test-api-key'))

        self.assertEqual(response.status_code, 200)

    def test_allows_anonymous_request_for_approved_ip(self):
        """Allowlisted source IPs should keep anonymous access."""
        response = self.middleware(self.make_request('/orgs/OCL/', REMOTE_ADDR='10.0.0.1'))

        self.assertEqual(response.status_code, 200)

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
