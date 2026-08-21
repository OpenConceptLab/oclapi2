import unittest
import uuid
from unittest.mock import patch, Mock

from celery.states import PENDING, STARTED, FAILURE
from django import db
from django.utils import timezone
from rest_framework.test import APIClient

from core.common.tasks import rerun_indexing_job
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.tasks.models import Task
from core.tasks.signals import on_task_done
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

    def test_rerun_needs_a_finished_task(self):
        task = Task(id='task-id', name='core.common.tasks.delete_organization', state=STARTED)
        task.save()

        with self.assertRaises(ValueError) as ex:
            task.rerun()

        self.assertEqual(ex.exception.args[0], 'Task is not finished yet.')

    def test_rerun_needs_a_registered_task(self):
        task = Task(id='task-id', name='core.common.tasks.no_such_task', state=FAILURE)
        task.save()

        with self.assertRaises(ValueError) as ex:
            task.rerun()

        self.assertEqual(ex.exception.args[0], 'Task core.common.tasks.no_such_task is not registered.')

    @patch('core.tasks.models.Task.clear_celery_once_lock')
    @patch('core.tasks.models.app')
    def test_rerun_resets_previous_run_and_requeues(self, app_mock, clear_lock_mock):
        apply_async_mock = Mock(return_value='async-result')
        app_mock.tasks.get = Mock(return_value=Mock(apply_async=apply_async_mock))
        task = Task(
            id='task-id', name='core.common.tasks.delete_organization', state=FAILURE, queue='default',
            args=[1], kwargs={'foo': 'bar'}, retry=1, result='old-result', summary={'processed': 3},
            error_message='boom', traceback='Traceback...', started_at=timezone.now(),
            finished_at=timezone.now(), children=['child-id']
        )
        task.save()

        self.assertEqual(task.rerun(), 'async-result')

        clear_lock_mock.assert_called_once()
        apply_async_mock.assert_called_once_with(
            args=[1], kwargs={'foo': 'bar'}, queue='default', task_id='task-id', persist_args=True
        )

        task.refresh_from_db()
        self.assertEqual(task.retry, 2)
        self.assertEqual(task.state, PENDING)
        self.assertIsNone(task.result)
        self.assertIsNone(task.summary)
        self.assertIsNone(task.error_message)
        self.assertIsNone(task.traceback)
        self.assertIsNone(task.started_at)
        self.assertIsNone(task.finished_at)
        self.assertEqual(task.children, [])

    @patch('core.tasks.models.Task.clear_celery_once_lock')
    @patch('core.tasks.models.app')
    def test_rerun_forced_on_started_task(self, app_mock, clear_lock_mock):  # pylint: disable=unused-argument
        app_mock.tasks.get = Mock(return_value=Mock(apply_async=Mock(return_value='async-result')))
        task = Task(id='task-id', name='core.common.tasks.delete_organization', state=STARTED)
        task.save()

        self.assertEqual(task.rerun(force=True), 'async-result')

        task.refresh_from_db()
        self.assertEqual(task.state, PENDING)
        self.assertEqual(task.retry, 1)


class RerunIndexingJobTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.stranded = self.stranded_job('stranded-id', hours_ago=1)

    @staticmethod
    def stranded_job(task_id, hours_ago=1, queue='indexing'):
        task = Task(
            id=task_id, name='core.common.tasks.index_source_concepts', state=STARTED, queue=queue,
            started_at=timezone.now() - timezone.timedelta(hours=hours_ago)
        )
        task.save()
        return task

    @staticmethod
    def inspector_mock(active=None, reserved=None, scheduled=None, active_queues=None):
        if active_queues is None:
            active_queues = {'worker1': [{'name': 'indexing'}]}
        return Mock(
            active_queues=Mock(return_value=active_queues),
            active=Mock(return_value=active or {}),
            reserved=Mock(return_value=reserved or {}),
            scheduled=Mock(return_value=scheduled or {}),
        )

    @patch('core.tasks.models.Task.rerun')
    @patch('core.common.tasks.app')
    def test_reruns_job_no_indexing_worker_holds(self, app_mock, rerun_mock):
        app_mock.control.inspect = Mock(return_value=self.inspector_mock())

        rerun_indexing_job()

        rerun_mock.assert_called_once_with(force=True)

    @patch('core.tasks.models.Task.rerun')
    @patch('core.common.tasks.app')
    def test_reruns_job_whose_queue_is_only_on_the_task_id(self, app_mock, rerun_mock):
        app_mock.control.inspect = Mock(return_value=self.inspector_mock())
        self.stranded.delete()
        self.stranded_job('some-uuid-admin~indexing', hours_ago=2, queue='default')

        rerun_indexing_job()

        rerun_mock.assert_called_once_with(force=True)

    @patch('core.tasks.models.Task.rerun')
    @patch('core.common.tasks.app')
    def test_ignores_jobs_on_other_queues(self, app_mock, rerun_mock):
        app_mock.control.inspect = Mock(return_value=self.inspector_mock())
        self.stranded.delete()
        self.stranded_job('default-queue-id', hours_ago=2, queue='default')

        rerun_indexing_job()

        rerun_mock.assert_not_called()

    @patch('core.tasks.models.Task.rerun')
    @patch('core.common.tasks.app')
    def test_skips_job_a_worker_is_executing(self, app_mock, rerun_mock):
        app_mock.control.inspect = Mock(
            return_value=self.inspector_mock(active={'worker1': [{'id': 'stranded-id'}]}))

        rerun_indexing_job()

        rerun_mock.assert_not_called()

    @patch('core.tasks.models.Task.rerun')
    @patch('core.common.tasks.app')
    def test_skips_job_a_worker_has_prefetched(self, app_mock, rerun_mock):
        app_mock.control.inspect = Mock(
            return_value=self.inspector_mock(reserved={'worker1': [{'id': 'stranded-id'}]}))

        rerun_indexing_job()

        rerun_mock.assert_not_called()

    @patch('core.tasks.models.Task.rerun')
    @patch('core.common.tasks.app')
    def test_skips_job_a_worker_has_scheduled(self, app_mock, rerun_mock):
        app_mock.control.inspect = Mock(
            return_value=self.inspector_mock(scheduled={'worker1': [{'request': {'id': 'stranded-id'}}]}))

        rerun_indexing_job()

        rerun_mock.assert_not_called()

    @patch('core.tasks.models.Task.rerun')
    @patch('core.common.tasks.app')
    def test_skips_sweep_when_no_indexing_worker_answers(self, app_mock, rerun_mock):
        app_mock.control.inspect = Mock(return_value=self.inspector_mock(active_queues={}))

        rerun_indexing_job()

        rerun_mock.assert_not_called()

    @patch('core.tasks.models.Task.rerun')
    @patch('core.common.tasks.app')
    def test_skips_sweep_when_only_non_indexing_workers_answer(self, app_mock, rerun_mock):
        app_mock.control.inspect = Mock(
            return_value=self.inspector_mock(active_queues={'worker1': [{'name': 'default'}]}))

        rerun_indexing_job()

        rerun_mock.assert_not_called()

    @patch('core.tasks.models.Task.rerun')
    @patch('core.common.tasks.app')
    def test_skips_job_within_grace_period(self, app_mock, rerun_mock):
        app_mock.control.inspect = Mock(return_value=self.inspector_mock())
        self.stranded.started_at = timezone.now()
        self.stranded.save()

        rerun_indexing_job()

        rerun_mock.assert_not_called()

    @patch('core.tasks.models.Task.rerun')
    @patch('core.common.tasks.app')
    def test_reruns_five_oldest_at_most(self, app_mock, rerun_mock):
        app_mock.control.inspect = Mock(return_value=self.inspector_mock())
        for i in range(10):
            self.stranded_job(f'stranded-{i}', hours_ago=i + 2)

        rerun_indexing_job()

        self.assertEqual(rerun_mock.call_count, 5)

    @patch('core.tasks.models.Task.rerun')
    @patch('core.common.tasks.app')
    def test_one_failure_does_not_stop_the_sweep(self, app_mock, rerun_mock):
        app_mock.control.inspect = Mock(return_value=self.inspector_mock())
        rerun_mock.side_effect = [ValueError('nope'), None]
        self.stranded_job('stranded-id-2', hours_ago=2)

        rerun_indexing_job()

        self.assertEqual(rerun_mock.call_count, 2)


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


class SignalsTest(unittest.TestCase):
    @patch('core.tasks.signals.db.connections')
    def test_on_task_done_closes_unusable_connections(self, connections_mock):
        conn = Mock()
        connections_mock.all.return_value = [conn]

        on_task_done()

        conn.close_if_unusable_or_obsolete.assert_called_once()

    @patch('core.tasks.signals.db.connections')
    def test_on_task_done_ignores_interface_error(self, connections_mock):
        conn = Mock()
        conn.close_if_unusable_or_obsolete.side_effect = db.utils.InterfaceError('gone')
        connections_mock.all.return_value = [conn]

        on_task_done()  # should not raise

    @patch('core.tasks.signals.db.connections')
    def test_on_task_done_ignores_closed_database_error(self, connections_mock):
        conn = Mock()
        conn.close_if_unusable_or_obsolete.side_effect = db.DatabaseError('connection already closed')
        connections_mock.all.return_value = [conn]

        on_task_done()  # should not raise

    @patch('core.tasks.signals.db.connections')
    def test_on_task_done_ignores_not_connected_database_error(self, connections_mock):
        conn = Mock()
        conn.close_if_unusable_or_obsolete.side_effect = db.DatabaseError('server not connected')
        connections_mock.all.return_value = [conn]

        on_task_done()  # should not raise

    @patch('core.tasks.signals.db.connections')
    def test_on_task_done_reraises_other_database_error(self, connections_mock):
        conn = Mock()
        conn.close_if_unusable_or_obsolete.side_effect = db.DatabaseError('something else broke')
        connections_mock.all.return_value = [conn]

        with self.assertRaises(db.DatabaseError):
            on_task_done()
