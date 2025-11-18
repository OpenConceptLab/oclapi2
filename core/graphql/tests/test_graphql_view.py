import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token


class TestGraphQLCsrfBehavior(TestCase):
    def setUp(self):
        self.url = reverse('graphql')
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='graphql-test-user', password='testpass')
        self.token = Token.objects.create(user=self.user)

    def _post_graphql(self, headers=None):
        headers = headers or {}
        payload = json.dumps({"query": "{ __typename }"})
        return self.client.post(self.url, data=payload, content_type='application/json', **headers)

    def test_post_without_token_requires_csrf(self):
        response = self._post_graphql()
        self.assertEqual(response.status_code, 403)

    def test_post_with_token_skips_csrf(self):
        headers = {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}
        response = self._post_graphql(headers=headers)
        self.assertEqual(response.status_code, 200)
