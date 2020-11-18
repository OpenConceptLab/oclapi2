import uuid

from celery_once import AlreadyQueued
from mock import patch, Mock, ANY

from core.common.tests import OCLAPITestCase, OCLTestCase
from core.importers.models import BulkImport
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class BulkImportTest(OCLTestCase):
    @patch('core.importers.models.OclFlexImporter')
    def test_run(self, flex_importer_mock):
        user = UserProfile.objects.get(username='ocladmin')
        import_results = Mock(
            to_json=Mock(return_value='{"all": "200"}'),
            get_detailed_summary=Mock(return_value='summary'),
            display_report=Mock(return_value='report')
        )
        flex_importer_instance_mock = Mock(process=Mock(return_value=None), import_results=import_results)
        flex_importer_mock.return_value = flex_importer_instance_mock
        content = '{"foo": "bar"}\n{"foobar": "foo"}'

        bulk_import = BulkImport(content=content, username='ocladmin', update_if_exists=True)
        bulk_import.run()

        self.assertEqual(bulk_import.result.json, {"all": "200"})
        self.assertEqual(bulk_import.result.detailed_summary, 'summary')
        self.assertEqual(bulk_import.result.report, 'report')

        flex_importer_mock.assert_called_once_with(
            input_list=[{"foo": "bar"}, {"foobar": "foo"}],
            api_url_root=ANY,
            api_token=user.get_token(),
            do_update_if_exists=True
        )
        flex_importer_instance_mock.process.assert_called_once()


class BulkImportViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.superuser = UserProfile.objects.get(username='ocladmin')
        self.token = self.superuser.get_token()

    @patch('core.importers.views.flower_get')
    def test_get_without_task_id(self, flower_get_mock):
        task_id1 = "{}-{}~{}".format(str(uuid.uuid4()), 'ocladmin', 'priority')
        task_id2 = "{}-{}~{}".format(str(uuid.uuid4()), 'ocladmin', 'priority')
        task_id3 = "{}-{}~{}".format(str(uuid.uuid4()), 'foobar', 'normal')
        task_id4 = "{}-{}".format(str(uuid.uuid4()), 'foobar')
        flower_tasks = {
            task_id1: dict(name='tasks.bulk_import', state='success'),
            task_id2: dict(name='foo-task', state='failed'),
            task_id3: dict(name='tasks.bulk_import', state='failed'),
            task_id4: dict(name='foo-task', state='pending')
        }
        flower_get_mock.return_value = Mock(json=Mock(return_value=flower_tasks))

        response = self.client.get(
            '/importers/bulk-import/?username=ocladmin',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [dict(queue='priority', state='success', task=task_id1, username='ocladmin')])

        response = self.client.get(
            '/importers/bulk-import/?username=foobar',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [dict(queue='normal', state='failed', task=task_id3, username='foobar')])

        response = self.client.get(
            '/importers/bulk-import/priority/?username=ocladmin',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [dict(queue='priority', state='success', task=task_id1, username='ocladmin')])

        response = self.client.get(
            '/importers/bulk-import/normal/?username=ocladmin',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
        flower_get_mock.assert_called_with('api/tasks')

    @patch('core.importers.views.AsyncResult')
    def test_get_with_task_id_success(self, async_result_klass_mock):
        task_id1 = "{}-{}~{}".format(str(uuid.uuid4()), 'ocladmin', 'priority')
        task_id2 = "{}-{}~{}".format(str(uuid.uuid4()), 'foobar', 'normal')
        foobar_user = UserProfileFactory(username='foobar')

        response = self.client.get(
            '/importers/bulk-import/?task={}'.format(task_id1),
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 403)

        async_result_mock = dict(json='json-format', report='report-format', detailed_summary='summary')
        async_result_instance_mock = Mock(successful=Mock(return_value=True), get=Mock(return_value=async_result_mock))
        async_result_klass_mock.return_value = async_result_instance_mock

        response = self.client.get(
            '/importers/bulk-import/?task={}'.format(task_id2),
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'summary')

        response = self.client.get(
            '/importers/bulk-import/?task={}&result=json'.format(task_id2),
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'json-format')

        response = self.client.get(
            '/importers/bulk-import/?task={}&result=report'.format(task_id2),
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'report-format')

        async_result_instance_mock.successful.assert_called()

    @patch('core.importers.views.AsyncResult')
    def test_get_with_task_id_failed(self, async_result_klass_mock):
        task_id = "{}-{}~{}".format(str(uuid.uuid4()), 'foobar', 'normal')
        foobar_user = UserProfileFactory(username='foobar')

        async_result_instance_mock = Mock(
            successful=Mock(return_value=False), failed=Mock(return_value=True), result='task-failure-result'
        )
        async_result_klass_mock.return_value = async_result_instance_mock

        response = self.client.get(
            '/importers/bulk-import/?task={}'.format(task_id),
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception='task-failure-result'))
        async_result_instance_mock.successful.assert_called()
        async_result_instance_mock.failed.assert_called()

    @patch('core.importers.views.task_exists')
    @patch('core.importers.views.AsyncResult')
    def test_get_task_pending(self, async_result_klass_mock, task_exists_mock):
        task_exists_mock.return_value = False
        task_id = "{}-{}~{}".format(str(uuid.uuid4()), 'foobar', 'normal')
        foobar_user = UserProfileFactory(username='foobar')

        async_result_instance_mock = Mock(
            successful=Mock(return_value=False), failed=Mock(return_value=False), state='PENDING', id=task_id
        )
        async_result_klass_mock.return_value = async_result_instance_mock

        response = self.client.get(
            '/importers/bulk-import/?task={}'.format(task_id),
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, dict(exception="task {} not found".format(task_id)))

        async_result_instance_mock.successful.assert_called_once()
        async_result_instance_mock.failed.assert_called_once()
        task_exists_mock.assert_called_once()

        task_exists_mock.return_value = True
        response = self.client.get(
            '/importers/bulk-import/?task={}'.format(task_id),
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, dict(task=task_id, state='PENDING', username='foobar', queue='normal'))

    def test_post_400(self):
        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=1',
            'some-data',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception="update_if_exists must be either 'true' or 'false'"))

        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception="No content to import"))

    @patch('core.importers.views.queue_bulk_import')
    def test_post_409(self, queue_bulk_import_mock):
        queue_bulk_import_mock.side_effect = AlreadyQueued('already-queued')

        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=true',
            'some-data',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data, dict(exception="The same import has been already queued"))

    @patch('core.common.tasks.bulk_import')
    def test_post_202(self, bulk_import_mock):
        task_mock = Mock(id='task-id', state='pending')
        bulk_import_mock.apply_async = Mock(return_value=task_mock)

        response = self.client.post(
            "/importers/bulk-import/?update_if_exists=true",
            'some-data',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, dict(task='task-id', state='pending', queue='default', username='ocladmin'))
        self.assertEqual(bulk_import_mock.apply_async.call_count, 1)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], (('"some-data"', 'ocladmin', True),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][37:], 'ocladmin~priority')
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['queue'], 'bulk_import_root')

        random_user = UserProfileFactory(username='oswell')

        response = self.client.post(
            "/importers/bulk-import/?update_if_exists=true",
            'some-data',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, dict(task='task-id', state='pending', queue='default', username='oswell'))
        self.assertEqual(bulk_import_mock.apply_async.call_count, 2)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], (('"some-data"', 'oswell', True),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][37:], 'oswell~default')
        self.assertTrue(bulk_import_mock.apply_async.call_args[1]['queue'].startswith('bulk_import_'))

        response = self.client.post(
            "/importers/bulk-import/foobar-queue/?update_if_exists=true",
            'some-data',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, dict(task='task-id', state='pending', queue='default', username='oswell'))
        self.assertEqual(bulk_import_mock.apply_async.call_count, 3)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], (('"some-data"', 'oswell', True),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][37:], 'oswell~foobar-queue')
        self.assertTrue(bulk_import_mock.apply_async.call_args[1]['queue'].startswith('bulk_import_'))

    def test_post_file_upload_400(self):
        response = self.client.post(
            "/importers/bulk-import/upload/?update_if_exists=true",
            {'file': ''},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception='No content to import'))
