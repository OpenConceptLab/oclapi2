import importlib
import json

from django.apps import apps
from mock import patch

from core.common.constants import RETIRED_ACCESS_TYPE_EDIT, ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW
from core.map_projects.models import MapProject
from core.map_projects.tests.factories import MapProjectFactory
from core.map_projects.tests.tests import MapProjectAbstractViewTest
from core.users.tests.factories import UserProfileFactory

COLUMNS = json.dumps([{'label': 'name', 'hidden': False, 'dataKey': 'name', 'original': 'name'}])


class MapProjectPrivacyTest(MapProjectAbstractViewTest):
    """Map projects are private unless shared explicitly."""

    def get_list(self, url, user):
        return self.client.get(url, HTTP_AUTHORIZATION='Token ' + user.get_token())

    def test_public_access_defaults_to_private(self):
        self.assertEqual(MapProject().public_access, ACCESS_TYPE_NONE)
        self.assertEqual(MapProjectFactory().public_access, ACCESS_TYPE_NONE)

    def test_org_project_is_listed_for_members_only(self):
        MapProjectFactory(organization=self.org)

        self.assertEqual(len(self.get_list('/orgs/CIEL/map-projects/', UserProfileFactory()).data), 0)
        self.assertEqual(len(self.get_list('/orgs/CIEL/map-projects/', self.user).data), 1)

    @patch('core.services.storages.cloud.aws.S3.upload')
    def test_post_is_private_by_default(self, _upload_mock):
        url = f'/users/{self.user.username}/map-projects/'
        response = self.client.post(
            url, data={'name': 'Private Project', 'file': self.file, 'columns': COLUMNS},
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(MapProject.objects.get(id=response.data['id']).public_access, ACCESS_TYPE_NONE)
        self.assertEqual(len(self.get_list(url, UserProfileFactory()).data), 0)
        self.assertEqual(len(self.get_list(url, self.user).data), 1)

    @patch('core.services.storages.cloud.aws.S3.upload')
    def test_post_keeps_explicit_public_access(self, _upload_mock):
        response = self.client.post(
            f'/users/{self.user.username}/map-projects/',
            data={'name': 'Shared Project', 'file': self.file, 'columns': COLUMNS, 'public_access': ACCESS_TYPE_VIEW},
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(MapProject.objects.get(id=response.data['id']).public_access, ACCESS_TYPE_VIEW)

    def test_migration_makes_existing_projects_private(self):
        migration = importlib.import_module('core.map_projects.migrations.0039_mapproject_private_by_default')
        viewable = MapProjectFactory(public_access=ACCESS_TYPE_VIEW)
        editable = MapProjectFactory(public_access=RETIRED_ACCESS_TYPE_EDIT)

        migration.make_existing_projects_private(apps, None)

        viewable.refresh_from_db()
        editable.refresh_from_db()
        self.assertEqual(viewable.public_access, ACCESS_TYPE_NONE)
        self.assertEqual(editable.public_access, ACCESS_TYPE_NONE)
