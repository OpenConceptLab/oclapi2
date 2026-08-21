import json
import unittest
import uuid
from unittest.mock import patch, Mock, PropertyMock

from celery import Task as CeleryTask
from celery.states import PENDING, STARTED, FAILURE, SUCCESS, RETRY, REVOKED
from celery.worker.request import Request
from celery_once import AlreadyQueued, QueueOnce
from django import db
from django.http import QueryDict
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.common.tasks import rerun_indexing_job
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.tasks.constants import TASK_NOT_COMPLETED
from core.tasks.mixins import TaskMixin, IndexingTaskMixin
from core.tasks.models import Task, AsyncTask, WorkerRequest, QueueOnceCustomTask
from core.tasks.serializers import TaskDetailSerializer
from core.tasks.signals import on_task_done
from core.tasks.utils import wait_until_task_complete
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


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

    def test_result_all_returns_raw_string_when_not_json(self):
        task = Task(result='not-json')
        self.assertEqual(task.result_all, 'not-json')
        self.assertIsNone(Task(result=None).result_all)

    def test_is_success(self):
        self.assertTrue(Task(state=SUCCESS).is_success)
        self.assertFalse(Task(state=PENDING).is_success)

    def test_status(self):
        self.assertEqual(Task(state=PENDING).status, PENDING)

    def test_runtime_in_progress(self):
        started = timezone.now() - timezone.timedelta(seconds=5)
        task = Task(started_at=started)
        self.assertGreaterEqual(task.runtime, 5)

    def test_runtime_finished(self):
        started = timezone.now() - timezone.timedelta(seconds=10)
        finished = timezone.now()
        task = Task(started_at=started, finished_at=finished)
        self.assertAlmostEqual(task.runtime, 10, delta=1)

    def test_runtime_none_without_start(self):
        self.assertIsNone(Task(started_at=None).runtime)

    def test_record_exception(self):
        task = Task(id='rec-exc-task-id')
        task.save()
        try:
            raise ValueError('boom')
        except ValueError as ex:
            task.record_exception(ex)

        task.refresh_from_db()
        self.assertEqual(task.error_message, 'boom')
        self.assertIn('ValueError', task.traceback)

    def test_clean_sets_created_by_from_task_id(self):
        user = UserProfileFactory(username='taskowner')
        task = Task(id=f"{uuid.uuid4()}-taskowner~default", created_by_id=None)
        task.clean()
        self.assertEqual(task.created_by_id, user.id)

    def test_before_start_permanent_false_returns_none(self):
        self.assertIsNone(Task.before_start('temp-task-id', [], {'permanent': False}, name='whatever'))
        self.assertFalse(Task.objects.filter(id='temp-task-id').exists())

    def test_before_start_returns_none_for_unknown_non_bulk_import_task(self):
        result = Task.before_start('unknown-task-id-xyz', [], {}, name='core.common.tasks.some_other_task')
        self.assertIsNone(result)
        self.assertFalse(Task.objects.filter(id='unknown-task-id-xyz').exists())

    def test_after_return_task_not_found(self):
        self.assertIsNone(Task.after_return('SUCCESS', 'ok', 'no-such-task-id', [], {}, None))

    def test_on_failure(self):
        Task.before_start('fail-task-id', [], {}, name='bulk_import_parts_inline')
        try:
            raise ValueError('failure')
        except ValueError as ex:
            Task.on_failure(ex, 'fail-task-id', [], {}, ex)

        task = Task.objects.get(id='fail-task-id')
        self.assertEqual(task.state, FAILURE)
        self.assertIsNotNone(task.finished_at)
        self.assertEqual(task.error_message, 'failure')

    def test_on_failure_task_not_found(self):
        self.assertIsNone(Task.on_failure(ValueError('x'), 'no-such-id', [], {}, None))

    def test_on_failure_excludes_revoked_tasks(self):
        Task.before_start('revoked-task-id', [], {}, name='bulk_import_parts_inline')
        task = Task.objects.get(id='revoked-task-id')
        task.state = REVOKED
        task.save()

        result = Task.on_failure(ValueError('x'), 'revoked-task-id', [], {}, None)

        self.assertIsNone(result)

    def test_on_retry(self):
        Task.before_start('retry-task-id', [], {}, name='bulk_import_parts_inline')

        Task.on_retry(ValueError('retry'), 'retry-task-id', [], {}, None)

        task = Task.objects.get(id='retry-task-id')
        self.assertEqual(task.state, RETRY)
        self.assertEqual(task.retry, 1)

    def test_on_retry_task_not_found(self):
        self.assertIsNone(Task.on_retry(ValueError('x'), 'no-such-id', [], {}, None))

    def test_on_success(self):
        Task.before_start('success-task-id', [], {}, name='bulk_import_parts_inline')

        Task.on_success({'foo': 'bar'}, 'success-task-id', [], {})

        task = Task.objects.get(id='success-task-id')
        self.assertEqual(task.state, SUCCESS)
        self.assertEqual(json.loads(task.result), {'foo': 'bar'})

    def test_on_success_task_not_found(self):
        self.assertIsNone(Task.on_success({}, 'no-such-id', [], {}))

    @patch('core.tasks.models.app')
    @patch('core.importers.importer.ImportTask.import_task_from_async_result')
    @patch.object(Task, 'celery_result', new_callable=PropertyMock)
    @patch.object(Task, 'clear_celery_once_lock')
    @patch.object(Task, 'children_still_playing')
    def test_revoke_revokes_import_task_and_children(  # pylint: disable=too-many-arguments
            self, children_mock, clear_lock_mock, celery_result_mock, import_task_from_async_result_mock, app_mock):
        celery_result_mock.return_value = Mock()
        import_result_mock = Mock()
        import_task_from_async_result_mock.return_value = import_result_mock
        child_mock = Mock()
        children_mock.return_value = [child_mock]

        task = Task(id='revoke-parent-id', name='x', state=STARTED)
        task.save()

        task.revoke()

        import_result_mock.revoke.assert_called_once()
        child_mock.revoke.assert_called_once()
        clear_lock_mock.assert_called_once()
        app_mock.control.revoke.assert_called_once_with('revoke-parent-id', terminate=True, signal='SIGKILL')
        task.refresh_from_db()
        self.assertEqual(task.state, REVOKED)

    def test_celery_result_returns_async_result(self):
        task = Task(id='cr-task-id')
        result = task.celery_result
        self.assertEqual(result.id, 'cr-task-id')

    @patch('core.tasks.models.QueueOnce')
    @patch('core.tasks.models.get_bulk_import_celery_once_lock_key')
    def test_clear_celery_once_lock(self, lock_key_mock, queue_once_cls_mock):
        lock_key_mock.return_value = 'lock-key'
        queue_once_instance = Mock()
        queue_once_cls_mock.return_value = queue_once_instance
        fake_result = Mock()
        fake_result.name = 'core.common.tasks.bulk_import_parts_inline'
        task = Task(id='lock-task-id')

        task.clear_celery_once_lock(fake_result)

        queue_once_instance.once_backend.clear_lock.assert_called_once_with('lock-key')
        self.assertEqual(queue_once_instance.name, fake_result.name)

    def test_new_resolves_user_by_username(self):
        user = UserProfileFactory(username='newtaskuser')
        task = Task.new(username='newtaskuser')
        self.assertEqual(task.created_by_id, user.id)

    def test_find(self):
        Task(id='find-task-id', name='findable-task-name').save()

        self.assertEqual(Task.find(name='findable-task-name').id, 'find-task-id')
        self.assertIsNone(Task.find(name='does-not-exist'))

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

    @patch('core.tasks.models.Task.clear_celery_once_lock')
    @patch('core.tasks.models.app')
    def test_rerun_revokes_still_playing_children(self, app_mock, _clear_lock_mock):
        app_mock.tasks.get = Mock(return_value=Mock(apply_async=Mock(return_value='async-result')))
        child = Task(id='rerun-child-id', name='core.common.tasks.index_source_concepts', state=STARTED)
        child.save()
        task = Task(
            id='rerun-parent-id', name='core.common.tasks.delete_organization', state=FAILURE,
            children=['rerun-child-id']
        )
        task.save()

        with patch.object(Task, 'revoke') as child_revoke_mock:
            task.rerun()

        child_revoke_mock.assert_called_once()


class AsyncTaskTest(unittest.TestCase):
    def test_on_failure_delegates_to_task_model(self):
        mock_self = Mock(spec=AsyncTask)
        with patch.object(CeleryTask, 'on_failure') as super_mock:
            with patch.object(Task, 'on_failure') as task_on_failure_mock:
                AsyncTask.on_failure(mock_self, 'exc', 'tid', [1], {'a': 1}, 'einfo')

        super_mock.assert_called_once_with('exc', 'tid', [1], {'a': 1}, 'einfo')
        task_on_failure_mock.assert_called_once_with('exc', 'tid', [1], {'a': 1}, 'einfo')

    def test_on_success_delegates_to_task_model(self):
        mock_self = Mock(spec=AsyncTask)
        with patch.object(CeleryTask, 'on_success') as super_mock:
            with patch.object(Task, 'on_success') as task_on_success_mock:
                AsyncTask.on_success(mock_self, {'r': 1}, 'tid', [1], {'a': 1})

        super_mock.assert_called_once_with({'r': 1}, 'tid', [1], {'a': 1})
        task_on_success_mock.assert_called_once_with({'r': 1}, 'tid', [1], {'a': 1})

    def test_on_retry_delegates_to_task_model(self):
        mock_self = Mock(spec=AsyncTask)
        with patch.object(CeleryTask, 'on_retry') as super_mock:
            with patch.object(Task, 'on_retry') as task_on_retry_mock:
                AsyncTask.on_retry(mock_self, 'exc', 'tid', [1], {'a': 1}, 'einfo')

        super_mock.assert_called_once_with('exc', 'tid', [1], {'a': 1}, 'einfo')
        task_on_retry_mock.assert_called_once_with('exc', 'tid', [1], {'a': 1}, 'einfo')

    def test_after_return_delegates_to_task_model(self):
        mock_self = Mock(spec=AsyncTask)
        with patch.object(CeleryTask, 'after_return') as super_mock:
            with patch.object(Task, 'after_return') as task_after_return_mock:
                AsyncTask.after_return(mock_self, 'SUCCESS', 'ret', 'tid', [1], {'a': 1}, 'einfo')

        super_mock.assert_called_once_with('SUCCESS', 'ret', 'tid', [1], {'a': 1}, 'einfo')
        task_after_return_mock.assert_called_once_with('SUCCESS', 'ret', 'tid', [1], {'a': 1}, 'einfo')

    def test_before_start_delegates_to_task_model(self):
        mock_self = Mock(spec=AsyncTask)
        mock_self.name = 'core.common.tasks.some_task'
        with patch.object(CeleryTask, 'before_start') as super_mock:
            with patch.object(Task, 'before_start') as task_before_start_mock:
                AsyncTask.before_start(mock_self, 'tid', [1], {'a': 1})

        super_mock.assert_called_once_with('tid', [1], {'a': 1})
        task_before_start_mock.assert_called_once_with('tid', [1], {'a': 1}, mock_self.name)


class AsyncTaskApplyAsyncTest(OCLTestCase):
    def test_persists_task_name_and_args_when_permanent(self):
        task = Task(id='persist-task-id', name='old-name')
        task.save()
        mock_self = Mock(spec=AsyncTask)
        mock_self.name = 'core.common.tasks.some_task'

        with patch.object(CeleryTask, 'apply_async', return_value='ok') as super_mock:
            result = AsyncTask.apply_async(
                mock_self, args=[1, 2], kwargs={'permanent': True}, task_id='persist-task-id', persist_args=True
            )

        self.assertEqual(result, 'ok')
        super_mock.assert_called_once()
        task.refresh_from_db()
        self.assertEqual(task.name, 'core.common.tasks.some_task')
        self.assertEqual(task.args, [1, 2])

    def test_skips_persisting_args_when_marked_temporary(self):
        task = Task(id='temp-task-id', name='old-name')
        task.save()
        mock_self = Mock(spec=AsyncTask)
        mock_self.name = 'core.common.tasks.some_task'

        with patch.object(CeleryTask, 'apply_async', return_value='ok'):
            AsyncTask.apply_async(
                mock_self, args=[1, 2], kwargs={'permanent': False}, task_id='temp-task-id', persist_args=True
            )

        task.refresh_from_db()
        self.assertEqual(task.name, 'old-name')


class WorkerRequestTest(unittest.TestCase):
    def test_on_failure_delegates_to_task_model(self):
        mock_self = Mock(spec=WorkerRequest, task_id='wr-task-id', args=[1], kwargs={'a': 1})
        exc_info = Mock(exception=ValueError('boom'))

        with patch.object(Request, 'on_failure') as super_mock:
            with patch.object(Task, 'on_failure') as task_on_failure_mock:
                WorkerRequest.on_failure(mock_self, exc_info, send_failed_event=True, return_ok=False)

        super_mock.assert_called_once_with(exc_info, send_failed_event=True, return_ok=False)
        task_on_failure_mock.assert_called_once_with(exc_info.exception, 'wr-task-id', [1], {'a': 1}, exc_info)


class QueueOnceCustomTaskTest(OCLTestCase):
    def test_deletes_task_when_response_rejected(self):
        task = Task(id='reject-task-id', name='x')
        task.save()
        mock_self = Mock(spec=QueueOnceCustomTask)
        mock_self.once = {}

        with patch.object(QueueOnce, 'apply_async', return_value=Mock(state='REJECTED')) as super_mock:
            QueueOnceCustomTask.apply_async(mock_self, [], {}, once={'task_id': 'reject-task-id'})

        super_mock.assert_called_once()
        self.assertFalse(Task.objects.filter(id='reject-task-id').exists())

    def test_reraises_and_deletes_task_when_already_queued(self):
        task = Task(id='already-queued-task-id', name='x')
        task.save()
        mock_self = Mock(spec=QueueOnceCustomTask)
        mock_self.once = {}

        with patch.object(QueueOnce, 'apply_async', side_effect=AlreadyQueued(5)):
            with self.assertRaises(AlreadyQueued):
                QueueOnceCustomTask.apply_async(mock_self, [], {}, once={'task_id': 'already-queued-task-id'})

        self.assertFalse(Task.objects.filter(id='already-queued-task-id').exists())


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

    def test_get_all_tasks_with_result_param(self):
        Task(id='result-task-id', name='x', state=SUCCESS, result='{"a": 1}', created_by=self.user).save()

        response = self.client.get('/tasks/?result=true', HTTP_AUTHORIZATION='Token ' + self.token)

        self.assertEqual(response.status_code, 200)

    def test_get_single_task_with_result_param_uses_result_serializer(self):
        task = Task(id='detail-result-task-id', name='x', state=SUCCESS, result='{"a": 1}', created_by=self.user)
        task.save()

        response = self.client.get(f'/tasks/{task.id}/?result=true', HTTP_AUTHORIZATION='Token ' + self.token)

        self.assertEqual(response.status_code, 200)

    def test_get_single_task(self):
        task = Task(id='plain-detail-task-id', name='x', state=SUCCESS, created_by=self.user)
        task.save()

        response = self.client.get(f'/tasks/{task.id}/', HTTP_AUTHORIZATION='Token ' + self.token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], task.id)

    def test_get_all_tasks_filter_by_state_and_search(self):
        Task(id='filtered-task-id', name='special-name', state=SUCCESS, created_by=self.user).save()
        Task(id='other-task-id', name='other-name', state=FAILURE, created_by=self.user).save()

        response = self.client.get('/tasks/?state=SUCCESS&q=special', HTTP_AUTHORIZATION='Token ' + self.token)

        self.assertEqual(response.status_code, 200)
        ids = [t['id'] for t in response.data]
        self.assertIn('filtered-task-id', ids)
        self.assertNotIn('other-task-id', ids)

    def test_delete_task_success(self):
        task = Task(id='delete-task-id', name='x', state=STARTED, created_by=self.user)
        task.save()

        with patch.object(Task, 'revoke'):
            response = self.client.delete(f'/tasks/{task.id}/', HTTP_AUTHORIZATION='Token ' + self.token)

        self.assertEqual(response.status_code, 204)

    def test_delete_task_permission_denied(self):
        task = Task(id='denied-task-id', name='x', state=FAILURE, created_by=self.user)
        task.save()
        random_user = UserProfileFactory()

        response = self.client.delete(
            f'/tasks/{task.id}/', HTTP_AUTHORIZATION='Token ' + random_user.get_token())

        self.assertEqual(response.status_code, 403)

    def test_delete_task_already_finished(self):
        task = Task(id='finished-task-id', name='x', state=SUCCESS, created_by=self.user)
        task.save()

        response = self.client.delete(f'/tasks/{task.id}/', HTTP_AUTHORIZATION='Token ' + self.token)

        self.assertEqual(response.status_code, 400)

    def test_delete_task_revoke_exception(self):
        task = Task(id='revoke-exc-task-id', name='x', state=STARTED, created_by=self.user)
        task.save()

        with patch.object(Task, 'revoke', side_effect=Exception('revoke boom')):
            response = self.client.delete(f'/tasks/{task.id}/', HTTP_AUTHORIZATION='Token ' + self.token)

        self.assertEqual(response.status_code, 400)


class TaskMixinTest(OCLTestCase):
    class DummyView(TaskMixin):
        def __init__(self, user, is_async=False, is_inline=False):
            self.request = Mock(user=user)
            self._is_async = is_async
            self._is_inline = is_inline

        def is_async_requested(self):
            return self._is_async

        def is_inline_requested(self):
            return self._is_inline

    def setUp(self):
        super().setUp()
        self.user = UserProfile.objects.filter(is_superuser=True).first()

    def test_perform_task_already_queued(self):
        # is_async=True is needed so perform_task doesn't take its TEST_MODE inline shortcut, since
        # that shortcut calls task_func directly and never reaches the apply_async/AlreadyQueued path.
        view = self.DummyView(self.user, is_async=True)
        task_func = Mock(__name__='my_already_queued_task')
        task_func.apply_async = Mock(side_effect=AlreadyQueued(5))

        response = view.perform_task(task_func, [1, 2])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data, {'detail': 'Already Queued'})
        self.assertFalse(Task.objects.filter(name='my_already_queued_task').exists())

    @patch('core.tasks.mixins.wait_until_task_complete')
    def test_perform_task_returns_task_response_when_not_completed(self, wait_mock):
        # The wait-for-result branch is only reachable when TEST_MODE is False: with it True and
        # is_async False, perform_task always takes its inline shortcut instead.
        wait_mock.return_value = TASK_NOT_COMPLETED
        view = self.DummyView(self.user)
        task_func = Mock(__name__='my_slow_task')
        task_func.apply_async = Mock(return_value=None)

        with override_settings(TEST_MODE=False):
            response = view.perform_task(task_func, [1, 2])

        self.assertEqual(response.status_code, 202)
        self.assertTrue(Task.objects.filter(name='my_slow_task').exists())

    def test_perform_task_runs_inline_in_test_mode(self):
        view = self.DummyView(self.user)  # TEST_MODE True (default) and is_async False -> inline shortcut
        task_func = Mock(return_value='inline-result')

        result = view.perform_task(task_func, [1, 2])

        self.assertEqual(result, 'inline-result')
        task_func.assert_called_once_with(1, 2)

    def test_perform_task_returns_task_response_when_async(self):
        view = self.DummyView(self.user, is_async=True)
        task_func = Mock(__name__='async_success_task')
        task_func.apply_async = Mock(return_value=None)

        response = view.perform_task(task_func, [1, 2])

        self.assertEqual(response.status_code, 202)
        self.assertTrue(Task.objects.filter(name='async_success_task').exists())

    @patch('core.tasks.mixins.wait_until_task_complete')
    def test_perform_task_returns_result_when_wait_completes(self, wait_mock):
        wait_mock.return_value = 'final-result'
        view = self.DummyView(self.user)
        task_func = Mock(__name__='wait_success_task')
        task_func.apply_async = Mock(return_value=None)

        with override_settings(TEST_MODE=False):
            result = view.perform_task(task_func, [1, 2])

        self.assertEqual(result, 'final-result')


class IndexingTaskMixinTest(OCLTestCase):
    class DummyIndexingView(IndexingTaskMixin):
        def __init__(self, instance, task_func):
            self.instance = instance
            self.task_func = task_func

        def get_object(self):
            return self.instance

        def get_task_function(self):
            return self.task_func

        def get_task_args(self, instance):
            return [instance.id]

    def setUp(self):
        super().setUp()
        self.user = UserProfile.objects.filter(is_superuser=True).first()

    def test_get_task_function_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            IndexingTaskMixin().get_task_function()

    def test_get_task_args_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            IndexingTaskMixin().get_task_args(None)

    def test_post_already_queued(self):
        task_func = Mock(__name__='index_already_queued_task')
        task_func.apply_async = Mock(side_effect=AlreadyQueued(5))
        view = self.DummyIndexingView(Mock(id=1), task_func)
        request = Mock(user=self.user)

        response = view.post(request)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data, {'detail': 'Already Queued'})
        self.assertFalse(Task.objects.filter(name='index_already_queued_task').exists())

    def test_post_queues_task_successfully(self):
        task_func = Mock(__name__='index_success_task')
        task_func.apply_async = Mock(return_value=None)
        view = self.DummyIndexingView(Mock(id=1), task_func)
        request = Mock(user=self.user)

        response = view.post(request)

        self.assertEqual(response.status_code, 202)
        self.assertTrue(Task.objects.filter(name='index_success_task').exists())


class TaskSerializerTest(OCLTestCase):
    def test_to_representation_adjusts_result_for_import_task(self):
        user = UserProfile.objects.filter(is_superuser=True).first()
        mock_async_result = Mock()
        mock_async_result.state = PENDING
        mock_async_result.ready.return_value = False
        mock_async_result.result = {'time_finished': timezone.now().isoformat()}

        result_all = {
            'import_task': [['real-task-id', None], None],
            'time_started': timezone.now().isoformat(),
            'dependencies': [],
            'subtask_ids': [],
            'initial_summary': {
                'total': 5, 'processed': 5, 'created': 5, 'updated': 0, 'deleted': 0, 'existing': 0,
                'failed': 0, 'permission_denied': 0, 'unchanged': 0, 'failures': [], 'dependencies': [],
            },
        }
        task = Task(
            id='serializer-task-id', name='x', state=SUCCESS, result=json.dumps(result_all), created_by=user
        )
        task.save()

        request = Mock()
        request.query_params = QueryDict('result=all')
        with patch('core.importers.importer.result_from_tuple', return_value=mock_async_result):
            data = TaskDetailSerializer(task, context={'request': request}).data

        self.assertEqual(data['state'], STARTED)  # reassigned from the import task's PENDING state
        self.assertEqual(data['summary']['total'], 5)

    def test_get_result_json_type(self):
        user = UserProfile.objects.filter(is_superuser=True).first()
        task = Task(id='result-json-task-id', name='x', state=SUCCESS, result='{"a": 1}', created_by=user)
        task.save()
        request = Mock()
        request.query_params = QueryDict('result=json')

        data = TaskDetailSerializer(task, context={'request': request}).data

        self.assertEqual(data['result'], {'a': 1})

    def test_get_result_defaults_to_none_and_is_dropped(self):
        user = UserProfile.objects.filter(is_superuser=True).first()
        task = Task(id='result-none-task-id', name='x', state=SUCCESS, result='{"a": 1}', created_by=user)
        task.save()
        request = Mock()
        request.query_params = QueryDict('')

        data = TaskDetailSerializer(task, context={'request': request}).data

        self.assertNotIn('result', data)


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
