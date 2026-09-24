from django.core.exceptions import ValidationError
from mock import patch

from core.map_projects.models import MapProject
from core.map_projects.tests.factories import MapProjectFactory
from core.map_projects.tests.tests import MapProjectAbstractViewTest
from core.users.tests.factories import UserProfileFactory


class MapProjectUpdateTest(MapProjectAbstractViewTest):
    """A PUT without a file keeps the stored input file, and a save that fails validation returns 400."""

    def setUp(self):
        super().setUp()
        self.project = MapProjectFactory(organization=self.org, name='Project 1', input_file_name='input.csv')
        self.url = f'/orgs/CIEL/map-projects/{self.project.id}/'

    def test_persist_changes_without_input_file_keeps_input_file_name(self):
        self.project.name = 'Renamed'

        errors = MapProject.persist_changes(self.project, UserProfileFactory())

        self.assertEqual(errors, {})
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Renamed')
        self.assertEqual(self.project.input_file_name, 'input.csv')

    def test_put_without_file(self):
        response = self.client.put(
            self.url, {'name': 'Renamed Project'}, HTTP_AUTHORIZATION='Token ' + self.user.get_token(), format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'Renamed Project')
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Renamed Project')
        self.assertEqual(self.project.input_file_name, 'input.csv')

    @patch('core.map_projects.models.MapProject.full_clean', side_effect=ValidationError({'name': ['Invalid name.']}))
    def test_put_400_when_changes_fail_validation(self, _full_clean_mock):
        response = self.client.put(
            self.url, {'name': 'Renamed Project'}, HTTP_AUTHORIZATION='Token ' + self.user.get_token(), format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'name': ['Invalid name.']})
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Project 1')
