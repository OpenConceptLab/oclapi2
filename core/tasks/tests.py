import unittest
import uuid
from unittest.mock import patch, Mock

from core.common.tests import OCLTestCase
from core.tasks.models import Task
from core.tasks.utils import wait_until_task_complete


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
