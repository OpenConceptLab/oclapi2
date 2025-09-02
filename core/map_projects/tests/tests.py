import json

from django.core.files.uploadedfile import SimpleUploadedFile
from mock import patch, ANY

from core.common.tests import OCLAPITestCase
from core.map_projects.tests.factories import MapProjectFactory
from core.orgs.tests.factories import OrganizationFactory
from core.users.tests.factories import UserProfileFactory


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
        }
        response = self.client.post(
            '/orgs/CIEL/map-projects/',
            data=data,
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['id'])
        self.assertEqual(self.org.map_projects.count(), 1)
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
        upload_mock.assert_called_once_with(
            key=f"map_projects/{response.data['id']}/input.csv", file_content=ANY)
