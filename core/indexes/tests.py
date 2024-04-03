from mock import patch, Mock
from mock.mock import ANY

from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class PopulateESIndexViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token_header = 'Token ' + self.user.get_token()

    def test_post_unauthorised(self):
        response = self.client.post(
            '/indexes/apps/populate/'
        )
        self.assertEqual(response.status_code, 401)

        random_user = UserProfileFactory()
        response = self.client.post(
            '/indexes/apps/populate/',
            {},
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
        )
        self.assertEqual(response.status_code, 403)

    @patch('core.indexes.views.PopulateESIndexView.task')
    def test_post_202(self, task_mock):
        task_mock.__name__ = 'populate_es_index'
        task_mock.apply_async = Mock(return_value=Mock(state='state', task_id='task-id'))
        response = self.client.post(
            '/indexes/apps/populate/',
            {'apps': 'concepts,sources,users'},
            HTTP_AUTHORIZATION=self.token_header,
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data,
            {
                'state': 'state',
                'username': self.user.username,
                'task': 'task-id',
                'queue': 'default'
            }
        )
        task_mock.apply_async.assert_called_once_with(
            (['concepts', 'sources', 'users'],), queue='indexing', task_id=ANY)


class RebuildESIndexViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token_header = 'Token ' + self.user.get_token()

    def test_post_unauthorised(self):
        response = self.client.post(
            '/indexes/apps/rebuild/'
        )
        self.assertEqual(response.status_code, 401)

        random_user = UserProfileFactory()
        response = self.client.post(
            '/indexes/apps/rebuild/',
            {},
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
        )
        self.assertEqual(response.status_code, 403)

    @patch('core.indexes.views.RebuildESIndexView.task')
    def test_post_202(self, task_mock):
        task_mock.__name__ = 'rebuild_es_index'
        task_mock.apply_async = Mock(return_value=Mock(state='state', task_id='task-id'))
        response = self.client.post(
            '/indexes/apps/rebuild/',
            HTTP_AUTHORIZATION=self.token_header,
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data,
            {
                'state': 'state',
                'username': self.user.username,
                'task': 'task-id',
                'queue': 'default'
            }
        )
        task_mock.apply_async.assert_called_once_with((None,), queue='indexing', task_id=ANY)


class ResourceIndexViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.token_header = 'Token ' + self.token

    def test_post_unauthorised(self):
        response = self.client.post(
            '/indexes/resources/foobar/'
        )
        self.assertEqual(response.status_code, 401)

        random_user = UserProfileFactory()
        response = self.client.post(
            '/indexes/resources/foobar/',
            {},
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
        )
        self.assertEqual(response.status_code, 403)

    def test_post_404(self):
        response = self.client.post(
            '/indexes/resources/foobar/',
            {},
            HTTP_AUTHORIZATION=self.token_header,
        )
        self.assertEqual(response.status_code, 404)

    def test_post_400(self):
        url = '/indexes/resources/concepts/'
        response = self.client.post(url, {'ids': ''}, HTTP_AUTHORIZATION=self.token_header)
        self.assertEqual(response.status_code, 400)

        response = self.client.post(url, {'ids': []}, HTTP_AUTHORIZATION=self.token_header)
        self.assertEqual(response.status_code, 400)

        response = self.client.post(url, {'ids': ', '}, HTTP_AUTHORIZATION=self.token_header)
        self.assertEqual(response.status_code, 400)

    def test_post_202(self):
        concept = ConceptFactory()
        url = '/indexes/resources/concepts/'

        response = self.client.post(url, {'ids': f'{concept.mnemonic}'}, HTTP_AUTHORIZATION=self.token_header)

        self.assertEqual(response.status_code, 202)
