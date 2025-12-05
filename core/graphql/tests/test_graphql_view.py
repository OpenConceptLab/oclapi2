import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth.backends import ModelBackend
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from core.graphql.tests.conftest import bootstrap_super_user, create_user_with_token


@override_settings(
    SESSION_ENGINE='django.contrib.sessions.backends.signed_cookies',
    MESSAGE_STORAGE='django.contrib.messages.storage.cookie.CookieStorage',
    STRAWBERRY_ASYNC=False,
    MIDDLEWARE=[
        mw for mw in __import__('django.conf').conf.settings.MIDDLEWARE
        if 'SessionMiddleware' not in mw
        and 'AuthenticationMiddleware' not in mw
        and 'MessageMiddleware' not in mw
        and 'TokenAuthMiddleWare' not in mw  # type: ignore
    ],
)
class TestGraphQLCsrfBehavior(TestCase):
    def setUp(self):
        self.url = reverse('graphql')
        self.super_user = bootstrap_super_user()
        self.user, self.token = create_user_with_token(
            username='graphql-test-user',
            password='testpass',
            super_user=self.super_user,
        )
        self.client.force_login(self.user)

    def _post_graphql(self, headers=None, query="{ __typename }"):
        headers = headers or {}
        payload = json.dumps({"query": query})
        return self.client.post(self.url, data=payload, content_type='application/json', **headers)

    def test_post_without_token_requires_csrf(self):
        response = self._post_graphql()
        self.assertEqual(response.status_code, 200)

    def test_post_with_token_skips_csrf(self):
        headers = {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}
        response = self._post_graphql(headers=headers)
        self.assertEqual(response.status_code, 200)

    def test_post_with_oidc_bearer_uses_authentication_service(self):
        class DummyOIDCAuthentication(BaseAuthentication):
            user = None

            def authenticate(self, request):
                auth_header = get_authorization_header(request).split()
                if len(auth_header) == 2 and auth_header[1] == b'valid-oidc-token':
                    return self.user, None
                raise AuthenticationFailed('Invalid token')

        DummyOIDCAuthentication.user = self.user
        query = "query { concepts(conceptIds:[\"abc\"]) { totalCount } }"

        with self.settings(TEST_MODE=False), patch(
            'core.services.auth.core.AuthService.get',
            return_value=SimpleNamespace(
                authentication_class=DummyOIDCAuthentication,
                token_type='Bearer',
                authentication_backend_class=ModelBackend,
            )
        ):
            response = self._post_graphql(
                headers={"HTTP_AUTHORIZATION": "Bearer valid-oidc-token"},
                query=query
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertIn('data', payload)
        self.assertNotIn('errors', payload)

    def test_post_with_invalid_oidc_bearer_fails_authentication(self):
        class DummyOIDCAuthentication(BaseAuthentication):
            def authenticate(self, request):
                raise AuthenticationFailed('Invalid token')

        query = "query { concepts(conceptIds:[\"abc\"]) { totalCount } }"

        with self.settings(TEST_MODE=False), patch(
            'core.services.auth.core.AuthService.get',
            return_value=SimpleNamespace(
                authentication_class=DummyOIDCAuthentication,
                token_type='Bearer',
                authentication_backend_class=ModelBackend,
            )
        ):
            response = self._post_graphql(
                headers={"HTTP_AUTHORIZATION": "Bearer invalid-oidc-token"},
                query=query
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertIn('errors', payload)
        self.assertIn('Authentication failure', payload['errors'][0]['message'])
