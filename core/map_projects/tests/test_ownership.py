import json

from django.contrib.auth.models import Permission
from mock import patch

from core.map_projects.models import MapProject
from core.map_projects.tests.factories import MapProjectFactory
from core.map_projects.tests.tests import MapProjectAbstractViewTest
from core.orgs.tests.factories import OrganizationFactory
from core.users.tests.factories import UserProfileFactory

COLUMNS = json.dumps([{'label': 'name', 'hidden': False, 'dataKey': 'name', 'original': 'name'}])


class MapProjectOwnershipTest(MapProjectAbstractViewTest):
    """A map project's owner comes from the URL only. Owner ids in the request body are ignored."""

    def allow_org_projects(self):
        # Where creating org-owned projects is gated by the mapper_org_projects permission, grant it.
        self.user.user_permissions.add(*Permission.objects.filter(codename='mapper_org_projects'))

    @patch('core.services.storages.cloud.aws.S3.upload')
    def test_post_to_user_ignores_organization_id_in_body(self, _upload_mock):
        other_org = OrganizationFactory()
        response = self.client.post(
            f'/users/{self.user.username}/map-projects/',
            data={'name': 'My Project', 'file': self.file, 'columns': COLUMNS, 'organization_id': other_org.id},
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )

        self.assertEqual(response.status_code, 201)
        project = MapProject.objects.get(id=response.data['id'])
        self.assertEqual(project.user_id, self.user.id)
        self.assertIsNone(project.organization_id)
        self.assertEqual(other_org.map_projects.count(), 0)

    @patch('core.services.storages.cloud.aws.S3.upload')
    def test_post_to_org_ignores_owner_ids_in_body(self, _upload_mock):
        self.allow_org_projects()
        response = self.client.post(
            '/orgs/CIEL/map-projects/',
            data={
                'name': 'Org Project', 'file': self.file, 'columns': COLUMNS,
                'organization_id': OrganizationFactory().id, 'user_id': UserProfileFactory().id,
            },
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )

        self.assertEqual(response.status_code, 201)
        project = MapProject.objects.get(id=response.data['id'])
        self.assertEqual(project.organization_id, self.org.id)
        self.assertIsNone(project.user_id)

    def test_post_to_org_by_non_member_403(self):
        response = self.client.post(
            '/orgs/CIEL/map-projects/',
            data={'name': 'Not My Org', 'file': self.file, 'columns': COLUMNS},
            HTTP_AUTHORIZATION='Token ' + UserProfileFactory().get_token(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.org.map_projects.count(), 0)

    @patch('core.services.storages.cloud.aws.S3.upload')
    def test_put_ignores_owner_ids_in_body(self, _upload_mock):
        project = MapProjectFactory(organization=self.org, name='Project 1')
        response = self.client.put(
            f'/orgs/CIEL/map-projects/{project.id}/',
            data={
                'name': 'Renamed Project', 'file': self.file,
                'organization_id': OrganizationFactory().id, 'user_id': UserProfileFactory().id,
            },
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )

        self.assertEqual(response.status_code, 200)
        project.refresh_from_db()
        self.assertEqual(project.name, 'Renamed Project')
        self.assertEqual(project.organization_id, self.org.id)
        self.assertIsNone(project.user_id)
