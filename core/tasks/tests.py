import unittest
from unittest.mock import patch, Mock

from django.conf import settings

from core.common.tests import OCLAPITestCase
from core.users.models import UserProfile


class TaskViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.token = UserProfile.objects.last().get_token()

    @patch('core.tasks.views.flower_get')
    def test_get_200(self, flower_get_mock):
        flower_get_mock.return_value = Mock(
            status_code=200, json=Mock(return_value={"task-id": "123", "state": "PENDING"}))

        response = self.client.get(
            '/tasks/123/',
            HTTP_AUTHORIZATION=f'Token {self.token}',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"task-id": "123", "state": "PENDING"})

    @unittest.skipIf(settings.ENV == 'ci', 'this test fails on CI.')
    @patch('core.common.utils.flower_get')
    def test_get_404(self, flower_get_mock):
        flower_get_mock.return_value = Mock(status_code=404)

        response = self.client.get(
            '/tasks/123/',
            HTTP_AUTHORIZATION=f'Token {self.token}',
            format='json'
        )

        self.assertEqual(response.status_code, 404)
