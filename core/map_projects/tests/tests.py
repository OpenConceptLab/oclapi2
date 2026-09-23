import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from mock import patch, ANY, Mock
from rest_framework.test import APIRequestFactory

from core.common.constants import PERSIST_NEW_ERROR_MESSAGE
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.map_projects.models import MapProject
from core.map_projects.views import AutomatchRunListView
from core.map_projects.tests.factories import MapProjectFactory, AutomatchRunFactory
from core.orgs.tests.factories import OrganizationFactory
from core.users.tests.factories import UserProfileFactory


class MapProjectModelTest(OCLTestCase):
    def test_mnemonic(self):
        self.assertEqual(MapProject(id=5).mnemonic, 5)

    def test_matches_summary(self):
        project = MapProject(matches=[
            {'state': 'matched'}, {'state': 'matched'}, {'state': 'unmatched'}, {'no_state': 1}
        ])
        self.assertEqual(project.matches_summary, {'matched': 2, 'unmatched': 1})

    def test_visible_columns(self):
        project = MapProject(columns=[
            {'label': 'no-hidden-key'},
            {'label': 'visible', 'hidden': False},
            {'label': 'hidden-column', 'hidden': True},
            {'hidden': False},
        ])
        self.assertEqual(
            project.visible_columns,
            [{'label': 'no-hidden-key'}, {'label': 'visible', 'hidden': False}]
        )

    def test_summary(self):
        project = MapProject(
            matches=[{'state': 'matched'}, {'state': 'unmatched'}],
            columns=[{'label': 'name', 'hidden': False}]
        )
        self.assertEqual(
            project.summary,
            {'matches': {'matched': 1, 'unmatched': 1}, 'columns': ['name']}
        )

    @patch('core.map_projects.models.get_export_service')
    def test_file_url_with_input_file_name(self, get_export_service_mock):
        export_service_mock = Mock()
        export_service_mock.url_for.return_value = 'http://export.url/file.csv'
        get_export_service_mock.return_value = export_service_mock

        project = MapProjectFactory(input_file_name='file.csv')

        self.assertEqual(project.file_url, 'http://export.url/file.csv')
        export_service_mock.url_for.assert_called_once_with(project.file_path)

    @patch('core.map_projects.models.get_export_service')
    def test_update_input_file_success(self, get_export_service_mock):
        export_service_mock = Mock()
        export_service_mock.upload.return_value = 204
        get_export_service_mock.return_value = export_service_mock

        project = MapProjectFactory()
        input_file = SimpleUploadedFile('new-input.csv', b'content', 'application/csv')

        project.update_input_file(input_file)

        self.assertEqual(project.input_file_name, 'new-input.csv')
        project.refresh_from_db()
        self.assertEqual(project.input_file_name, 'new-input.csv')

    def test_persist_new_validation_error(self):
        project = MapProject(name='', organization_id=None, user_id=None)
        user = UserProfileFactory()

        errors = MapProject.persist_new(project, user)

        self.assertTrue(errors)
        self.assertIsNone(project.id)

    def test_persist_new_integrity_error(self):
        org = OrganizationFactory()
        user = UserProfileFactory()
        project = MapProject(name='Some Project', organization=org)
        project.full_clean = Mock()
        project.save = Mock(side_effect=IntegrityError('duplicate'))

        errors = MapProject.persist_new(project, user)

        self.assertIn('__all__', errors)

    def test_persist_new_no_errors_no_id_sets_non_field_error(self):
        org = OrganizationFactory()
        user = UserProfileFactory()
        project = MapProject(name='Some Project', organization=org)
        project.full_clean = Mock()
        project.save = Mock()

        errors = MapProject.persist_new(project, user)

        self.assertEqual(errors, {'non_field_errors': PERSIST_NEW_ERROR_MESSAGE.format('MapProject')})

    def test_persist_changes_validation_error(self):
        project = MapProjectFactory()
        user = UserProfileFactory()
        project.name = ''

        errors = MapProject.persist_changes(project, user)

        self.assertTrue(errors)

    def test_persist_changes_integrity_error(self):
        project = MapProjectFactory()
        user = UserProfileFactory()
        project.full_clean = Mock()
        project.save = Mock(side_effect=IntegrityError('duplicate'))

        errors = MapProject.persist_changes(project, user)

        self.assertIn('__all__', errors)

    def test_format_json_invalid_json_kept_as_is(self):
        data = {'matches': 'not-json{'}
        MapProject.format_json(data, 'matches')
        self.assertEqual(data['matches'], 'not-json{')

    def test_clean_matches_parses_json_string(self):
        project = MapProject(matches='["a", "b"]')
        project.clean_matches()
        self.assertEqual(project.matches, ['a', 'b'])

    def test_clean_matches_leaves_non_json_serializable_as_is(self):
        project = MapProject(matches=[{'state': 'matched'}])
        project.clean_matches()
        self.assertEqual(project.matches, [{'state': 'matched'}])

    def test_clean_filters_removes_falsy_values(self):
        project = MapProject(filters={'a': 'value', 'b': None, 'c': ''})
        project.clean_filters()
        self.assertEqual(project.filters, {'a': 'value'})

    @patch('core.sources.models.Source.resolve_reference_expression')
    def test_target_repo(self, resolve_reference_expression_mock):
        repo_mock = Mock(id=1)
        resolve_reference_expression_mock.return_value = (repo_mock, None)

        project = MapProject(target_repo_url='/orgs/CIEL/sources/CIEL/')

        self.assertEqual(project.target_repo, repo_mock)

    def test_fields_mapped(self):
        project = MapProject(columns=[
            {'label': 'ID', 'hidden': False},
            {'label': 'Property: foo', 'hidden': False},
            {'label': 'Not Mapped', 'hidden': False},
        ])
        self.assertEqual(project.fields_mapped, ['ID', 'Property: foo'])


class MapProjectAbstractViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()

        self.user = UserProfileFactory()
        self.org = OrganizationFactory(mnemonic='CIEL')
        self.org.members.add(self.user)

        self.file = SimpleUploadedFile('input.csv', b'content', "application/csv")


class MapProjectListViewTest(MapProjectAbstractViewTest):
    @patch('core.services.storages.cloud.aws.S3.upload')
    def test_post(self, upload_mock):
        data = {
            'name': 'Test Project',
            'file': self.file,
            'columns': json.dumps([
                {'label': 'itemid', 'hidden': False, 'dataKey': 'itemid', 'original': 'itemid'},
                {'label': 'name', 'hidden': False, 'dataKey': 'name', 'original': 'name'},
                {'label': 'fluid', 'hidden': False, 'dataKey': 'fluid', 'original': 'fluid'},
                {'label': 'category', 'hidden': False, 'dataKey': 'category', 'original': 'category'},
                {'label': 'loinc_code', 'hidden': False, 'dataKey': 'loinc_code', 'original': 'loinc_code'}
            ]),
            # Multipart-shaped wire format used by oclmap — input_locales is
            # JSON-stringified so format_request_data can json.loads it back
            # into the list that the ArrayField expects.
            'input_locales': json.dumps(['pt-BR']),
        }
        response = self.client.post(
            '/orgs/CIEL/map-projects/',
            data=data,
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['id'])
        self.assertEqual(self.org.map_projects.count(), 1)
        self.assertEqual(response.data.get('input_locales'), ['pt-BR'])
        upload_mock.assert_called_once_with(
            key=f"map_projects/{response.data['id']}/input.csv", file_content=ANY)

    def test_get(self):
        response = self.client.get(
            '/orgs/CIEL/map-projects/',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        project = MapProjectFactory(organization=self.org, name="Project 1")
        project.save()
        self.assertEqual(self.org.map_projects.count(), 1)

        response = self.client.get(
            '/orgs/CIEL/map-projects/',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], project.id)
        self.assertEqual(response.data[0]['url'], f'/orgs/CIEL/map-projects/{project.id}/')

    def test_get_verbose(self):
        project = MapProjectFactory(
            organization=self.org,
            name="Verbose Project",
            matches=[{'state': 'matched', 'id': 1}],
            candidates={'1': ['a', 'b']},
            analysis={'score': 0.9},
        )
        project.save()

        response = self.client.get(
            '/orgs/CIEL/map-projects/?verbose=true',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        result = response.data[0]
        self.assertEqual(result['id'], project.id)
        self.assertIn('matches', result)
        self.assertIn('columns', result)
        self.assertIn('candidates', result)
        self.assertIn('analysis', result)
        self.assertEqual(result['matches'], project.matches)
        self.assertEqual(result['candidates'], project.candidates)


class MapProjectViewTest(MapProjectAbstractViewTest):
    def setUp(self):
        super().setUp()
        self.project = MapProjectFactory(organization=self.org, name="Project 1")
        self.project.save()
        self.assertEqual(self.org.map_projects.count(), 1)

    def test_get(self):
        response = self.client.get(
            f'/orgs/CIEL/map-projects/{self.project.id}/',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.project.id)
        self.assertEqual(response.data['url'], f'/orgs/CIEL/map-projects/{self.project.id}/')

    @patch('core.common.tasks.delete_s3_objects.apply_async')
    def test_delete(self, delete_s3_objects_mock):
        response = self.client.delete(
            f'/orgs/CIEL/map-projects/{self.project.id}/',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.org.map_projects.count(), 0)
        delete_s3_objects_mock.assert_called_once_with(
            (f"map_projects/{self.project.id}/input.csv",), queue='default', permanent=False)

    @patch('core.services.storages.cloud.aws.S3.upload')
    def test_put(self, upload_mock):
        data = {
            'name': 'Test Project',
            'file': self.file,
            'columns': json.dumps([
                {
                    'label': 'itemid',
                    'hidden': False,
                    'dataKey': 'itemid',
                    'original': 'itemid'
                }
            ]),
            # Multipart clients send locale arrays as JSON strings, so the
            # view must decode them before serializer validation.
            'input_locales': json.dumps(['en']),
        }
        response = self.client.put(
            f'/orgs/CIEL/map-projects/{self.project.id}/',
            data=data,
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data['id'])
        self.assertEqual(self.org.map_projects.count(), 1)
        self.assertEqual(response.data['name'], 'Test Project')
        self.assertEqual(len(response.data['columns']), 1)
        self.assertEqual(response.data['input_locales'], ['en'])
        upload_mock.assert_called_once_with(
            key=f"map_projects/{response.data['id']}/input.csv", file_content=ANY)


class MapProjectConfigurationsViewTest(MapProjectAbstractViewTest):
    def test_get_200(self):
        project = MapProjectFactory(
            organization=self.org,
            algorithms=[{'name': 'exact-match', 'enabled': True}],
            encoder_model='snowflake-arctic-embed-l-v2.0',
            filters={'retired': False, 'class': ['LabSet']},
            include_retired=True,
            lookup_config={'concepts': {'limit': 20}},
            score_configuration={'recommended': 95, 'available': 75},
            target_repo_url='/orgs/CIEL/sources/CIEL/',
            prompt_template_key='match-recommend',
            prompt_output_locale='pt-BR',
            input_locales=['pt-BR'],
            use_lexical_variants=True
        )
        project.save()

        response = self.client.get(
            f'/orgs/CIEL/map-projects/{project.id}/configurations/',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], project.id)
        self.assertIsNotNone(response.data['id'])
        self.assertIsNotNone(response.data['name'])
        self.assertEqual(response.data['url'], f'/orgs/CIEL/map-projects/{project.id}/')
        self.assertEqual(response.data['algorithms'], [{'name': 'exact-match', 'enabled': True}])
        self.assertEqual(response.data['encoder_model'], 'snowflake-arctic-embed-l-v2.0')
        self.assertEqual(response.data['filters'], {'retired': False, 'class': ['LabSet']})
        self.assertTrue(response.data['include_retired'])
        self.assertEqual(response.data['lookup_config'], {'concepts': {'limit': 20}})
        self.assertEqual(response.data['score_configuration'], {'recommended': 95, 'available': 75})
        self.assertEqual(response.data['target_repo_url'], '/orgs/CIEL/sources/CIEL/')
        self.assertEqual(response.data['prompt_template_key'], 'match-recommend')
        self.assertEqual(response.data['prompt_output_locale'], 'pt-BR')
        self.assertEqual(response.data['input_locales'], ['pt-BR'])
        self.assertTrue(response.data['use_lexical_variants'])
        for field in ['analysis', 'input_file_name', 'candidates', 'matches', 'columns', 'created_by', 'updated_by']:
            self.assertNotIn(field, response.data)


class AutomatchRunListViewTest(MapProjectAbstractViewTest):
    def setUp(self):
        super().setUp()
        self.project = MapProjectFactory(organization=self.org, name="Run Project")
        self.url = f'/orgs/CIEL/map-projects/{self.project.id}/auto-match-runs/'

    def test_post_creates_run(self):
        response = self.client.post(
            self.url,
            data={
                'intended_rows': 200,
                'trigger_source': 'ui-auto-match',
                'config_snapshot': {'encoder_model': 'snowflake', 'algorithms': ['ocl-semantic']},
            },
            format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['id'])
        self.assertEqual(response.data['map_project_id'], self.project.id)
        self.assertEqual(response.data['intended_rows'], 200)
        self.assertEqual(response.data['completed_rows'], 0)
        self.assertEqual(response.data['failed_rows'], 0)
        self.assertEqual(response.data['completion_status'], 'running')
        self.assertEqual(response.data['trigger_source'], 'ui-auto-match')
        self.assertIsNone(response.data['completed_at'])
        self.assertIsNone(response.data['parent_run_id'])
        self.assertEqual(response.data['started_by'], self.user.username)
        self.assertEqual(
            response.data['config_snapshot'], {'encoder_model': 'snowflake', 'algorithms': ['ocl-semantic']})
        self.assertEqual(response.data['url'], f"/auto-match-runs/{response.data['id']}/")
        self.assertEqual(self.project.auto_match_runs.count(), 1)
        # client metadata is captured server-side from the request, not the payload
        self.assertEqual(self.project.auto_match_runs.first().client_ip, '127.0.0.1')

    def test_post_captures_user_agent(self):
        response = self.client.post(
            self.url,
            data={'intended_rows': 5, 'trigger_source': 'api'},
            format='json',
            HTTP_USER_AGENT='oclmap/0.0.1-alpha',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            self.project.auto_match_runs.get(id=response.data['id']).client_user_agent, 'oclmap/0.0.1-alpha')

    def test_post_missing_required_400(self):
        # trigger_source is required (no model default)
        response = self.client.post(
            self.url, data={'intended_rows': 10}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 400)
        self.assertIn('trigger_source', response.data)
        # intended_rows is required (no model default)
        response = self.client.post(
            self.url, data={'trigger_source': 'api'}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 400)
        self.assertIn('intended_rows', response.data)

    def test_post_invalid_trigger_source_400(self):
        response = self.client.post(
            self.url, data={'intended_rows': 10, 'trigger_source': 'bogus'}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 400)
        self.assertIn('trigger_source', response.data)

    def test_post_rerun_links_parent_and_leaves_parent_immutable(self):
        # A failed-row re-run is a NEW run pointing at the parent; the parent's
        # failure snapshot must never be mutated (ocl_online#105 OQ3).
        parent = AutomatchRunFactory(
            map_project=self.project, intended_rows=200, completed_rows=190,
            failed_rows=10, completion_status='partial')
        response = self.client.post(
            self.url,
            data={'intended_rows': 10, 'trigger_source': 'ui-rerun-row', 'parent_run': parent.id},
            format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['parent_run_id'], parent.id)
        self.assertEqual(response.data['intended_rows'], 10)  # only the failed rows, not the original total
        self.assertEqual(response.data['trigger_source'], 'ui-rerun-row')
        parent.refresh_from_db()
        self.assertEqual(parent.failed_rows, 10)
        self.assertEqual(parent.completion_status, 'partial')
        self.assertEqual(parent.retry_runs.count(), 1)

    def test_post_rerun_cross_project_parent_400(self):
        other_project = MapProjectFactory(organization=self.org, name="Other")
        foreign_parent = AutomatchRunFactory(map_project=other_project)
        response = self.client.post(
            self.url,
            data={'intended_rows': 3, 'trigger_source': 'ui-rerun-row', 'parent_run': foreign_parent.id},
            format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 400)
        self.assertIn('parent_run', response.data)

    def test_get_lists_only_this_projects_runs(self):
        run1 = AutomatchRunFactory(map_project=self.project)
        run2 = AutomatchRunFactory(map_project=self.project)
        other_project = MapProjectFactory(organization=self.org, name="Other")
        AutomatchRunFactory(map_project=other_project)  # must not leak into this project's list

        response = self.client.get(self.url, HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 200)
        self.assertEqual({r['id'] for r in response.data}, {run1.id, run2.id})

    def test_non_member_cannot_create_or_list(self):
        token = UserProfileFactory().get_token()
        self.assertEqual(
            self.client.get(self.url, HTTP_AUTHORIZATION='Token ' + token).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.url, data={'intended_rows': 1, 'trigger_source': 'api'}, format='json',
                HTTP_AUTHORIZATION='Token ' + token).status_code, 403)

    def test_post_serializer_context_skips_project_lookup_for_swagger_schema(self):
        factory = APIRequestFactory()
        view = AutomatchRunListView()
        view.request = view.initialize_request(factory.post('/schema/auto-match-runs/'))
        view.kwargs = {}
        view.format_kwarg = None
        view.swagger_fake_view = True

        context = view.get_serializer_context()

        self.assertEqual(context['request'].method, 'POST')
        self.assertNotIn('map_project', context)


class AutomatchRunViewTest(MapProjectAbstractViewTest):
    def setUp(self):
        super().setUp()
        self.project = MapProjectFactory(organization=self.org, name="Run Project")
        self.run = AutomatchRunFactory(map_project=self.project, started_by=self.user, intended_rows=200)
        self.url = f'/auto-match-runs/{self.run.id}/'

    def test_get_single(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.run.id)
        self.assertEqual(response.data['map_project_id'], self.project.id)
        self.assertEqual(response.data['intended_rows'], 200)
        self.assertEqual(response.data['started_by'], self.user.username)
        self.assertIn('config_snapshot', response.data)

    def test_patch_progress(self):
        response = self.client.patch(
            self.url, data={'completed_rows': 150, 'failed_rows': 5}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 200)
        self.run.refresh_from_db()
        self.assertEqual(self.run.completed_rows, 150)
        self.assertEqual(self.run.failed_rows, 5)
        self.assertEqual(self.run.completion_status, 'running')
        self.assertIsNone(self.run.completed_at)

    def test_patch_completion_stamps_completed_at(self):
        response = self.client.patch(
            self.url,
            data={'completed_rows': 195, 'failed_rows': 5, 'completion_status': 'partial'},
            format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 200)
        self.run.refresh_from_db()
        self.assertEqual(self.run.completion_status, 'partial')
        self.assertIsNotNone(self.run.completed_at)

    def test_patch_invalid_status_400(self):
        response = self.client.patch(
            self.url, data={'completion_status': 'bogus'}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 400)
        self.assertIn('completion_status', response.data)

    def test_patch_cannot_mutate_snapshot_fields(self):
        response = self.client.patch(
            self.url,
            data={
                'intended_rows': 9999, 'trigger_source': 'cli',
                'config_snapshot': {'x': 1}, 'completed_rows': 10,
            },
            format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 200)
        self.run.refresh_from_db()
        self.assertEqual(self.run.completed_rows, 10)  # mutable lifecycle field applied
        self.assertEqual(self.run.intended_rows, 200)  # run-start snapshot untouched
        self.assertEqual(self.run.trigger_source, 'ui-auto-match')
        self.assertEqual(self.run.config_snapshot, {})

    def test_non_member_cannot_get_or_patch(self):
        token = UserProfileFactory().get_token()
        self.assertEqual(
            self.client.get(self.url, HTTP_AUTHORIZATION='Token ' + token).status_code, 403)
        self.assertEqual(
            self.client.patch(
                self.url, data={'completed_rows': 1}, format='json',
                HTTP_AUTHORIZATION='Token ' + token).status_code, 403)
        self.run.refresh_from_db()
        self.assertEqual(self.run.completed_rows, 0)  # the rejected PATCH changed nothing

    def test_put_not_allowed(self):
        # Updates are PATCH-only; a whole-object PUT must be rejected (405).
        response = self.client.put(
            self.url, data={'completed_rows': 1}, format='json',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 405)

    def test_get_404_for_missing_run(self):
        response = self.client.get(
            '/auto-match-runs/99999999/', HTTP_AUTHORIZATION='Token ' + self.user.get_token())
        self.assertEqual(response.status_code, 404)
