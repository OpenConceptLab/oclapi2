import unittest
from unittest.mock import patch, Mock

from core.common.tests import OCLAPITestCase
from core.tasks.utils import wait_until_task_complete
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
        flower_get_mock.assert_called_once_with('api/task/info/123')

    @patch('core.tasks.views.flower_get')
    def test_get_404(self, flower_get_mock):
        flower_get_mock.return_value = Mock(status_code=404)

        response = self.client.get(
            '/tasks/123/',
            HTTP_AUTHORIZATION=f'Token {self.token}',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    @patch('core.tasks.views.flower_get')
    def test_get_400(self, flower_get_mock):
        flower_get_mock.side_effect = [Exception('service down')]

        response = self.client.get(
            '/tasks/123/',
            HTTP_AUTHORIZATION=f'Token {self.token}',
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, 'service down')


class UtilsTest(unittest.TestCase):
    @patch('core.tasks.utils.AsyncResult')
    def test_wait_until_task_complete_pending(self, async_result_mock):
        async_mock = Mock(get=Mock(return_value='task-result'))
        async_mock.ready.side_effect = [False, False, False, True]
        async_result_mock.return_value = async_mock

        self.assertEqual(wait_until_task_complete('task-id', 1), 'TASK_NOT_COMPLETED')
        self.assertEqual(async_mock.ready.call_count, 3)
        self.assertEqual(async_mock.get.call_count, 0)

    @patch('core.tasks.utils.AsyncResult')
    def test_wait_until_task_complete_finished(self, async_result_mock):
        async_mock = Mock(get=Mock(return_value='task-result'))
        async_mock.ready.side_effect = [False, False, True]
        async_result_mock.return_value = async_mock

        self.assertEqual(wait_until_task_complete('task-id', 1), 'task-result')
        self.assertEqual(async_mock.ready.call_count, 3)
        self.assertEqual(async_mock.get.call_count, 1)
