import unittest
import uuid
from unittest.mock import patch, Mock

from rest_framework.test import APIClient

from core.common.tests import OCLTestCase, OCLAPITestCase
from core.tasks.models import Task
from core.tasks.utils import wait_until_task_complete
from core.users.models import UserProfile


class TaskTest(OCLTestCase):
    def test_parse_bulk_import_task_id(self):
        task_uuid = str(uuid.uuid4())

        task_id = f"{task_uuid}-username~queue"
        self.assertEqual(
            Task.parse_bulk_import_task_id(task_id),
            {'uuid': task_uuid + '-', 'username': 'username', 'queue': 'queue'}
        )

        task_id = f"{task_uuid}-username"
        self.assertEqual(
            Task.parse_bulk_import_task_id(task_id),
            {'uuid': task_uuid + '-', 'username': 'username', 'queue': 'default'}
        )

    def test_create_new_task(self):
        task = Task()
        task.save()

        self.assertIsNotNone(task.id)

    def test_before_start(self):
        Task.before_start('new_task_id', [], {}, name='bulk_import_parts_inline')

        new_task = Task.objects.filter(id='new_task_id').first()
        self.assertEqual(new_task.id, 'new_task_id')
        self.assertIsNotNone(new_task.started_at)
        self.assertIsNone(new_task.finished_at)

    def test_after_return(self):
        Task.before_start('new_task_id', [], {}, name='bulk_import_parts_inline')
        Task.after_return('SUCCESS', 'Ok!', 'new_task_id', [], {}, None)

        new_task = Task.objects.filter(id='new_task_id').first()
        self.assertEqual(new_task.id, 'new_task_id')
        self.assertIsNotNone(new_task.started_at)
        self.assertIsNotNone(new_task.finished_at)
        self.assertEqual(new_task.result, 'Ok!')
        self.assertEqual(new_task.state, 'SUCCESS')


class TaskAPITest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.client = APIClient()

    def test_get_all_tasks(self):
        response = self.client.get('/tasks/', HTTP_AUTHORIZATION='Token ' + self.token)

        self.assertEqual(response.status_code, 200)

    def test_get_user_tasks(self):
        response = self.client.get(f'/users/{self.user.username}/tasks/', HTTP_AUTHORIZATION='Token ' + self.token)

        self.assertEqual(response.status_code, 200)


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
