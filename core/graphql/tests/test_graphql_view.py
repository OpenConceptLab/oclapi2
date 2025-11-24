import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed


class TestGraphQLCsrfBehavior(TestCase):
    def setUp(self):
        self.url = reverse('graphql')
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='graphql-test-user', password='testpass')
        self.token = Token.objects.create(user=self.user)

    def _post_graphql(self, headers=None, query="{ __typename }"):
        headers = headers or {}
        payload = json.dumps({"query": query})
        return self.client.post(self.url, data=payload, content_type='application/json', **headers)

    def test_post_without_token_requires_csrf(self):
        response = self._post_graphql()
        self.assertEqual(response.status_code, 403)

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

        with patch(
            'core.services.auth.core.AuthService.get',
            return_value=SimpleNamespace(authentication_class=DummyOIDCAuthentication)
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

        with patch(
            'core.services.auth.core.AuthService.get',
            return_value=SimpleNamespace(authentication_class=DummyOIDCAuthentication)
        ):
            response = self._post_graphql(
                headers={"HTTP_AUTHORIZATION": "Bearer invalid-oidc-token"},
                query=query
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertIn('errors', payload)
        self.assertIn('Authentication failure', payload['errors'][0]['message'])
