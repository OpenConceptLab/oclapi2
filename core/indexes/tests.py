import json

from mock import patch, Mock
from mock.mock import ANY

from core.common.tests import OCLAPITestCase
from core.concepts.models import Concept
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

    @patch('core.indexes.views.batch_index_resources')
    def test_post_400(self, batch_index_resources_mock):
        url = '/indexes/resources/concepts/'
        for data in [
                {}, {'ids': ''}, {'ids': []}, {'ids': ', '}, {'ids': 'concept1'}, {'filter': '{}'},
                {'filter': '[1]'}, {'filter': 'id=1'}, {'filter': '{"foobar": 1}'}, {'filter': '{"id__in": ["a"]}'},
                {'ids': '1', 'uri': '/orgs/CIEL/'}, {'ids': '1', 'filter': '{"id": 1}'},
        ]:
            response = self.client.post(url, data, HTTP_AUTHORIZATION=self.token_header)
            self.assertEqual(response.status_code, 400, data)

        batch_index_resources_mock.assert_not_called()

    @patch('core.indexes.views.batch_index_resources')
    def test_post_202_filters(self, batch_index_resources_mock):
        url = '/indexes/resources/concepts/'

        response = self.client.post(url, {'ids': ' 1, 2,,'}, HTTP_AUTHORIZATION=self.token_header)
        self.assertEqual(response.status_code, 202)
        batch_index_resources_mock.assert_called_with('concepts', {'id__in': [1, 2]}, False)

        response = self.client.post(url, {'uri': '/orgs/CIEL/sources/CIEL/'}, HTTP_AUTHORIZATION=self.token_header)
        self.assertEqual(response.status_code, 202)
        batch_index_resources_mock.assert_called_with('concepts', {'uri__icontains': '/orgs/CIEL/sources/CIEL/'}, False)

        response = self.client.post(
            url, {'filter': '{"updated_at__gte": "2026-10-01T00:00:00Z"}', 'update_indexed': 'true'},
            HTTP_AUTHORIZATION=self.token_header
        )
        self.assertEqual(response.status_code, 202)
        batch_index_resources_mock.assert_called_with('concepts', {'updated_at__gte': '2026-10-01T00:00:00Z'}, True)

    def test_post_202_ids_are_internal_ids(self):
        concept = ConceptFactory()
        same_mnemonic_concept = ConceptFactory(mnemonic=concept.mnemonic)
        self.assertNotEqual(concept.parent_id, same_mnemonic_concept.parent_id)
        Concept.objects.filter(mnemonic=concept.mnemonic).update(_index=False)

        response = self.client.post(
            '/indexes/resources/concepts/', {'ids': f'{concept.id}', 'update_indexed': 'true'},
            HTTP_AUTHORIZATION=self.token_header
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            list(Concept.objects.filter(mnemonic=concept.mnemonic, _index=True).values_list('id', flat=True)),
            [concept.id]
        )

    def test_post_202_update_indexed_false(self):
        concept = ConceptFactory()
        Concept.objects.filter(id=concept.id).update(_index=False)
        url = '/indexes/resources/concepts/'
        _filter = json.dumps({'id': concept.id})

        for value in ['false', 'False', '0', '']:
            response = self.client.post(
                url, {'filter': _filter, 'update_indexed': value}, HTTP_AUTHORIZATION=self.token_header)
            self.assertEqual(response.status_code, 202)
            concept.refresh_from_db()
            self.assertFalse(concept._index, value)  # pylint: disable=protected-access

        response = self.client.post(
            url, {'filter': _filter, 'update_indexed': 'true'}, HTTP_AUTHORIZATION=self.token_header)
        self.assertEqual(response.status_code, 202)
        concept.refresh_from_db()
        self.assertTrue(concept._index)  # pylint: disable=protected-access
