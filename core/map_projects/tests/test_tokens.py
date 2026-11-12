import importlib

from mock import patch

from django.apps import apps
from django.test import override_settings, SimpleTestCase

from core.common.utils import drop_key_deep, get_url_origin
from core.map_projects.models import MapProject, AutomatchRun
from core.map_projects.tests.factories import MapProjectFactory, AutomatchRunFactory
from core.map_projects.tests.tests import MapProjectAbstractViewTest

CUSTOM_URL = 'https://match.example.org/match/'
LOOKUP_URL = 'https://terms.example.org/orgs/X/sources/Y/'


def find_token_keys(value, path=''):
    if isinstance(value, dict):
        return [f'{path}.{key}' for key in value if key == 'token'] + [
            hit for key, val in value.items() for hit in find_token_keys(val, f'{path}.{key}')]
    if isinstance(value, list):
        return [hit for index, item in enumerate(value) for hit in find_token_keys(item, f'{path}[{index}]')]
    return []


@override_settings(API_BASE_URL='https://api.example.org')
class MapProjectTokensTest(MapProjectAbstractViewTest):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Permission
        self.user.user_permissions.add(Permission.objects.get(codename='mapper_custom_algorithms'))
        self.project = MapProjectFactory(organization=self.org, name='Tokens')
        self.url = f'/orgs/CIEL/map-projects/{self.project.id}/'
        self.auth = {'HTTP_AUTHORIZATION': 'Token ' + self.user.get_token()}

    def put(self, data):
        return self.client.put(self.url, {'name': 'Tokens', **data}, format='json', **self.auth)

    def put_tokens(self):
        return self.put({
            'name': 'Tokens',
            'algorithms': [
                {'id': 'custom-1', 'type': 'custom', 'url': CUSTOM_URL, 'token': 'algo-key'},
                {'id': 'ocl-semantic', 'type': 'ocl-semantic'},
            ],
            'lookup_config': {'url': LOOKUP_URL, 'token': 'lookup-key'},
        })

    def test_put_stores_tokens_and_returns_has_token(self):
        response = self.put_tokens()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(find_token_keys(response.data), [])
        self.assertEqual(
            response.data['algorithms'],
            [{'id': 'custom-1', 'type': 'custom', 'url': CUSTOM_URL, 'has_token': True},
             {'id': 'ocl-semantic', 'type': 'ocl-semantic'}]
        )
        self.assertEqual(response.data['lookup_config'], {'url': LOOKUP_URL, 'has_token': True})
        self.project.refresh_from_db()
        self.assertEqual(find_token_keys(self.project.algorithms), [])
        self.assertEqual(find_token_keys(self.project.lookup_config), [])
        self.assertEqual(
            self.project.credentials, {'algorithms': {CUSTOM_URL: 'algo-key'}, 'lookup': {LOOKUP_URL: 'lookup-key'}})

    def test_no_response_returns_tokens(self):
        self.put_tokens()
        AutomatchRunFactory(
            map_project=self.project, config_snapshot={'algorithms': [{'url': CUSTOM_URL, 'token': 'algo-key'}]})
        run = self.project.auto_match_runs.first()

        for url in [
            self.url,
            f'{self.url}configurations/',
            '/orgs/CIEL/map-projects/?verbose=true',
            '/orgs/CIEL/map-projects/',
            f'/users/{self.user.username}/orgs/map-projects/?verbose=true',
            f'/auto-match-runs/{run.id}/',
        ]:
            response = self.client.get(url, **self.auth)
            self.assertEqual(response.status_code, 200, url)
            self.assertEqual(find_token_keys(response.data), [], url)
            self.assertNotIn('algo-key', str(response.data), url)
            self.assertNotIn('lookup-key', str(response.data), url)
            self.assertNotIn('credentials', str(response.data), url)

    def test_put_without_token_keeps_it(self):
        self.put_tokens()

        response = self.put({
            'algorithms': [{'id': 'custom-1', 'type': 'custom', 'url': CUSTOM_URL, 'has_token': True}],
            'lookup_config': {'url': LOOKUP_URL, 'has_token': True},
        })

        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(
            self.project.credentials, {'algorithms': {CUSTOM_URL: 'algo-key'}, 'lookup': {LOOKUP_URL: 'lookup-key'}})

    def test_put_without_algorithms_keeps_tokens(self):
        self.put_tokens()

        self.assertEqual(self.put({'name': 'Renamed'}).status_code, 200)

        self.project.refresh_from_db()
        self.assertEqual(self.project.credentials['algorithms'], {CUSTOM_URL: 'algo-key'})

    def test_put_empty_token_clears_it(self):
        self.put_tokens()

        self.put({
            'algorithms': [{'id': 'custom-1', 'type': 'custom', 'url': CUSTOM_URL, 'token': ''}],
            'lookup_config': {'url': LOOKUP_URL, 'token': None},
        })

        self.project.refresh_from_db()
        self.assertEqual(self.project.credentials, {})

    def test_changed_url_drops_token(self):
        self.put_tokens()

        response = self.put({
            'algorithms': [{'id': 'custom-1', 'type': 'custom', 'url': 'https://elsewhere.example.com/'}],
            'lookup_config': {'url': 'https://elsewhere.example.com/orgs/X/'},
        })

        self.assertNotIn('has_token', response.data['algorithms'][0])
        self.assertNotIn('has_token', response.data['lookup_config'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.credentials, {})

    def test_removed_algorithm_drops_token(self):
        self.put_tokens()

        self.put({'algorithms': [{'id': 'ocl-semantic', 'type': 'ocl-semantic'}]})

        self.project.refresh_from_db()
        self.assertNotIn('algorithms', self.project.credentials)

    def test_ocl_api_lookup_token_is_not_stored(self):
        for lookup_url in ['/orgs/CIEL/sources/CIEL/', 'https://api.example.org/orgs/CIEL/', '']:
            response = self.put({'lookup_config': {'url': lookup_url, 'token': 'ocl-token'}})

            self.assertEqual(response.status_code, 200, lookup_url)
            self.assertNotIn('has_token', response.data['lookup_config'], lookup_url)
            self.project.refresh_from_db()
            self.assertNotIn('lookup', self.project.credentials, lookup_url)
            self.assertEqual(find_token_keys(self.project.lookup_config), [], lookup_url)

    def test_algorithm_without_url_keeps_no_token(self):
        self.put({'algorithms': [{'id': 'ocl-semantic', 'token': 'stray'}]})

        self.project.refresh_from_db()
        self.assertEqual(self.project.algorithms, [{'id': 'ocl-semantic'}])
        self.assertEqual(self.project.credentials, {})

    def test_nested_tokens_are_not_stored(self):
        self.put({
            'algorithms': [{'id': 'custom-1', 'type': 'custom', 'url': CUSTOM_URL, 'auth': {'token': 'nested'}}],
            'lookup_config': {'url': LOOKUP_URL, 'headers': [{'token': 'nested'}]},
        })

        self.project.refresh_from_db()
        self.assertEqual(self.project.algorithms, [{'id': 'custom-1', 'type': 'custom', 'url': CUSTOM_URL, 'auth': {}}])
        self.assertEqual(self.project.lookup_config, {'url': LOOKUP_URL, 'headers': [{}]})
        self.assertEqual(self.project.credentials, {})

    def test_migrated_ocl_lookup_token_is_kept_but_not_shown(self):
        MapProject.objects.filter(id=self.project.id).update(
            lookup_config={'url': '/orgs/CIEL/'}, credentials={'lookup': {'/orgs/CIEL/': 'old-ocl-token'}})

        self.assertNotIn('has_token', self.client.get(self.url, **self.auth).data['lookup_config'])
        response = self.put({'lookup_config': {'url': '/orgs/CIEL/', 'token': 'new-ocl-token'}})

        self.assertNotIn('has_token', response.data['lookup_config'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.credentials, {'lookup': {'/orgs/CIEL/': 'old-ocl-token'}})

    def test_algorithms_sharing_a_url_share_one_token(self):
        self.put_tokens()

        response = self.put({'algorithms': [
            {'id': 'custom-1', 'url': CUSTOM_URL, 'token': 'k1'},
            {'id': 'custom-2', 'url': CUSTOM_URL, 'token': 'k2'},
        ]})
        self.assertEqual(response.status_code, 400)
        self.assertIn('algorithms', response.data)

        response = self.put({'algorithms': [
            {'id': 'custom-1', 'url': CUSTOM_URL, 'has_token': True},
            {'id': 'custom-2', 'url': CUSTOM_URL, 'token': 'k2'},
        ]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([algo.get('has_token') for algo in response.data['algorithms']], [True, True])
        self.project.refresh_from_db()
        self.assertEqual(self.project.credentials['algorithms'], {CUSTOM_URL: 'k2'})

    def test_stale_save_keeps_newer_token(self):
        self.put_tokens()
        stale = MapProject.objects.get(id=self.project.id)
        MapProject.objects.filter(id=self.project.id).update(
            credentials={'algorithms': {CUSTOM_URL: 'newer'}, 'lookup': {LOOKUP_URL: 'lookup-key'}})
        stale.name = 'Renamed'

        self.assertEqual(MapProject.persist_changes(stale, self.user), {})

        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Renamed')
        self.assertEqual(self.project.credentials['algorithms'], {CUSTOM_URL: 'newer'})

    def test_save_of_deleted_project_is_an_error(self):
        stale = MapProject.objects.get(id=self.project.id)
        MapProject.objects.filter(id=self.project.id).delete()

        self.assertEqual(
            MapProject.persist_changes(stale, self.user), {'__all__': ['This map project no longer exists.']})

    def test_credentials_cannot_be_written_directly(self):
        self.put({'credentials': {'algorithms': {CUSTOM_URL: 'injected'}}})

        self.project.refresh_from_db()
        self.assertEqual(self.project.credentials, {})

    @patch('core.common.mixins.write_csv_to_s3', return_value='https://csv-url')
    def test_csv_exports_no_tokens(self, write_csv_mock):
        self.put_tokens()

        for url in [
            '/orgs/CIEL/map-projects/?csv=true',
            '/orgs/CIEL/map-projects/?csv=true&verbose=true',
            f'/users/{self.user.username}/orgs/map-projects/?csv=true&verbose=true',
        ]:
            write_csv_mock.reset_mock()
            response = self.client.get(url, **self.auth)

            self.assertEqual(response.status_code, 200, url)
            write_csv_mock.assert_called_once()
            rows = list(write_csv_mock.call_args.args[0])
            self.assertEqual([row['id'] for row in rows], [self.project.id], url)
            self.assertNotIn('credentials', rows[0], url)
            self.assertNotIn('algo-key', str(rows), url)
            self.assertNotIn('lookup-key', str(rows), url)

    def test_run_snapshot_drops_tokens(self):
        response = self.client.post(
            f'{self.url}auto-match-runs/',
            data={
                'intended_rows': 1, 'trigger_source': 'ui-auto-match',
                'config_snapshot': {
                    'algorithms': [{'url': CUSTOM_URL, 'token': 'algo-key'}],
                    'lookup_config': {'url': LOOKUP_URL, 'token': 'lookup-key'},
                },
            },
            format='json', **self.auth,
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(find_token_keys(response.data), [])
        self.assertEqual(find_token_keys(AutomatchRun.objects.get(id=response.data['id']).config_snapshot), [])


class MapProjectTokensMigrationTest(MapProjectAbstractViewTest):
    def setUp(self):
        super().setUp()
        self.migration = importlib.import_module('core.map_projects.migrations.0041_mapproject_credentials')

    def test_moves_tokens_to_credentials(self):
        project = MapProjectFactory(organization=self.org)
        MapProject.objects.filter(id=project.id).update(
            algorithms=[
                {'type': 'custom', 'url': CUSTOM_URL, 'token': 'algo-key', 'auth': {'token': 'nested'}},
                {'type': 'ocl-semantic', 'token': 'stray'},
            ],
            lookup_config={'url': '/orgs/CIEL/', 'token': 'ocl-token'},
        )
        run = AutomatchRunFactory(map_project=project, config_snapshot={'a': [{'token': 'algo-key', 'x': 1}]})

        self.migration.move_tokens_to_credentials(apps, None)

        project.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(
            project.algorithms, [{'type': 'custom', 'url': CUSTOM_URL, 'auth': {}}, {'type': 'ocl-semantic'}])
        self.assertEqual(project.lookup_config, {'url': '/orgs/CIEL/'})
        self.assertEqual(
            project.credentials, {'algorithms': {CUSTOM_URL: 'algo-key'}, 'lookup': {'/orgs/CIEL/': 'ocl-token'}})
        self.assertEqual(run.config_snapshot, {'a': [{'x': 1}]})

        self.migration.move_tokens_back(apps, None)

        project.refresh_from_db()
        self.assertEqual(project.algorithms[0]['token'], 'algo-key')
        self.assertEqual(project.lookup_config['token'], 'ocl-token')

    def test_stops_on_different_tokens_for_one_url(self):
        project = MapProjectFactory(organization=self.org)
        algorithms = [
            {'id': 'a', 'url': CUSTOM_URL, 'token': 'k1'},
            {'id': 'b', 'url': CUSTOM_URL, 'token': 'k2'},
            {'id': 'c', 'url': CUSTOM_URL, 'token': 'k2'},
        ]
        MapProject.objects.filter(id=project.id).update(algorithms=algorithms)

        with self.assertRaisesMessage(RuntimeError, f'project {project.id} ({CUSTOM_URL}).'):
            self.migration.move_tokens_to_credentials(apps, None)

        project.refresh_from_db()
        self.assertEqual(project.algorithms, algorithms)
        self.assertEqual(project.credentials, {})

    def test_same_token_on_one_url_migrates(self):
        project = MapProjectFactory(organization=self.org)
        MapProject.objects.filter(id=project.id).update(algorithms=[
            {'id': 'a', 'url': CUSTOM_URL, 'token': 'k1'}, {'id': 'b', 'url': CUSTOM_URL, 'token': 'k1'}])

        self.migration.move_tokens_to_credentials(apps, None)

        project.refresh_from_db()
        self.assertEqual(project.credentials, {'algorithms': {CUSTOM_URL: 'k1'}})

    def test_leaves_projects_without_tokens_alone(self):
        project = MapProjectFactory(organization=self.org, algorithms=[{'type': 'ocl-semantic'}])
        updated_at = MapProject.objects.get(id=project.id).updated_at

        self.migration.move_tokens_to_credentials(apps, None)

        project.refresh_from_db()
        self.assertEqual(project.updated_at, updated_at)
        self.assertEqual(project.credentials, {})


class DropKeyDeepTest(SimpleTestCase):
    def test_drops_key_at_any_depth(self):
        self.assertEqual(
            drop_key_deep({'token': 1, 'a': [{'token': 2, 'b': {'token': 3, 'c': 4}}]}, 'token'),
            {'a': [{'b': {'c': 4}}]}
        )
        self.assertEqual(drop_key_deep('token', 'token'), 'token')


class GetUrlOriginTest(SimpleTestCase):
    def test_origin(self):
        self.assertEqual(get_url_origin('HTTPS://API.Example.org:443/orgs/'), ('https', 'api.example.org'))
        self.assertEqual(get_url_origin('http://api.example.org:8000/'), ('http', 'api.example.org:8000'))
        self.assertEqual(get_url_origin('//api.example.org/orgs/'), ('', 'api.example.org'))
        self.assertIsNone(get_url_origin('/orgs/CIEL/'))
        self.assertIsNone(get_url_origin('http://[bad'))
        self.assertIsNone(get_url_origin(None))


@override_settings(API_BASE_URL='https://api.example.org')
class IsOCLAPIURLTest(SimpleTestCase):
    def test_is_ocl_api_url(self):
        for url in ['', None, '/orgs/CIEL/', '//terms.example.org/', 'https://api.example.org/orgs/',
                    'HTTPS://api.example.org:443/orgs/']:
            self.assertTrue(MapProject.is_ocl_api_url(url), url)
        for url in [
                LOOKUP_URL, 'HTTPS://terms.example.org/', 'http://api.example.org/', 'https://api.example.org:8443/']:
            self.assertFalse(MapProject.is_ocl_api_url(url), url)
