from mock import patch, Mock, ANY

from core.common.tests import OCLAPITestCase
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class BulkImportViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.superuser = UserProfile.objects.get(username='ocladmin')
        self.token = self.superuser.get_token()

    def test_get_400(self):
        response = self.client.get(
            '/importers/bulk-import/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception='Required task id'))

    def test_get_403(self):
        user = UserProfileFactory()
        task_id = '1' * 36 + '-ocladmin'
        response = self.client.get(
            '/importers/bulk-import/?task={}'.format(task_id),
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)

    @patch('core.importers.views.AsyncResult')
    def test_get_task_success(self, async_result_klass_mock):
        async_result_mock = Mock(json='json-format', report='report-format', detailed_summary='summary')
        async_result_instance_mock = Mock(successful=Mock(return_value=True), get=Mock(return_value=async_result_mock))
        async_result_klass_mock.return_value = async_result_instance_mock

        task_id = '1'*36 + '-ocladmin'
        response = self.client.get(
            '/importers/bulk-import/?task={}'.format(task_id),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'summary')
        async_result_klass_mock.assert_called_once_with(task_id)
        async_result_instance_mock.successful.assert_called_once()
        async_result_instance_mock.get.assert_called_once()

        response = self.client.get(
            '/importers/bulk-import/?task={}&result=json'.format(task_id),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'json-format')

        response = self.client.get(
            '/importers/bulk-import/?task={}&result=report'.format(task_id),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'report-format')

    @patch('core.importers.views.AsyncResult')
    def test_get_task_failed(self, async_result_klass_mock):
        async_result_instance_mock = Mock(
            successful=Mock(return_value=False), failed=Mock(return_value=True), result='task-failure-result'
        )
        async_result_klass_mock.return_value = async_result_instance_mock

        task_id = '1'*36 + '-ocladmin'
        response = self.client.get(
            '/importers/bulk-import/?task={}'.format(task_id),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception='task-failure-result'))
        async_result_klass_mock.assert_called_once_with(task_id)
        async_result_instance_mock.successful.assert_called_once()
        async_result_instance_mock.failed.assert_called_once()

    @patch('core.importers.views.AsyncResult')
    def test_get_task_pending(self, async_result_klass_mock):
        task_id = '1'*36 + '-ocladmin'
        async_result_instance_mock = Mock(
            successful=Mock(return_value=False), failed=Mock(return_value=False), state='pending', id=task_id
        )
        async_result_klass_mock.return_value = async_result_instance_mock

        response = self.client.get(
            '/importers/bulk-import/?task={}'.format(task_id),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(task=task_id, state='pending'))
        async_result_instance_mock.successful.assert_called_once()
        async_result_instance_mock.failed.assert_called_once()

    def test_post_400(self):
        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=1',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception="update_if_exists must be either 'true' or 'false'"))

    @patch('core.importers.views.bulk_priority_import')
    def test_priority_post_200(self, bulk_priority_import_mock):
        task_mock = Mock(id='task-id', state='pending')
        bulk_priority_import_mock.apply_async = Mock(return_value=task_mock)

        response = self.client.post(
            "/importers/bulk-import/?update_if_exists=true",
            'some-data',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(task='task-id', state='pending'))
        bulk_priority_import_mock.apply_async.assert_called_once_with(
            ('"some-data"', self.superuser.username, True), task_id=ANY
        )

    @patch('core.importers.views.bulk_import')
    def test_post_200(self, bulk_import_mock):
        random_user = UserProfileFactory()
        task_mock = Mock(id='task-id', state='pending')
        bulk_import_mock.apply_async = Mock(return_value=task_mock)

        response = self.client.post(
            "/importers/bulk-import/?update_if_exists=true",
            'some-data',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(task='task-id', state='pending'))
        bulk_import_mock.apply_async.assert_called_once_with(
            ('"some-data"', random_user.username, True), task_id=ANY
        )
