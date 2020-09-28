from mock import patch
from rest_framework.exceptions import ErrorDetail

from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT
from core.common.tests import OCLAPITestCase
from core.orgs.tests.factories import OrganizationFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class OrganizationListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.superuser = UserProfile.objects.get(is_superuser=True)
        self.user = UserProfileFactory(username='user')
        self.org_private = OrganizationFactory(mnemonic='private-org', public_access=ACCESS_TYPE_NONE)
        self.org_public_view = OrganizationFactory(mnemonic='public-view-org', public_access=ACCESS_TYPE_VIEW)
        self.org_public_edit = OrganizationFactory(mnemonic='public-edit-org', public_access=ACCESS_TYPE_EDIT)
        self.user_org_public = OrganizationFactory(mnemonic='user-public-view-org', public_access=ACCESS_TYPE_VIEW)
        self.user_org_private = OrganizationFactory(mnemonic='user-private-org', public_access=ACCESS_TYPE_NONE)
        self.user.organizations.set([self.user_org_private, self.user_org_public])
        self.token = self.user.get_token()

    def test_get_200_anonymous_user(self):
        response = self.client.get('/orgs/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-public-view-org', 'public-edit-org', 'public-view-org', 'OCL']
        )

    def test_get_200_auth_user(self):
        response = self.client.get(
            '/orgs/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 5)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-private-org', 'user-public-view-org', 'public-edit-org', 'public-view-org', 'OCL']
        )

        random_user = UserProfileFactory()
        response = self.client.get(
            '/orgs/?verbose=true',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-public-view-org', 'public-edit-org', 'public-view-org', 'OCL']
        )

    def test_get_200_superuser(self):
        response = self.client.get(
            '/orgs/',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 6)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-private-org', 'user-public-view-org', 'public-edit-org', 'public-view-org', 'private-org', 'OCL']
        )

    def test_get_200_staff_user(self):
        staff_user = UserProfileFactory(is_staff=True)
        response = self.client.get(
            '/orgs/',
            HTTP_AUTHORIZATION='Token ' + staff_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 6)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-private-org', 'user-public-view-org', 'public-edit-org', 'public-view-org', 'private-org', 'OCL']
        )

    def test_post_201(self):
        response = self.client.post(
            '/orgs/',
            dict(id='test-org-1', name='Test Org 1'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['id'], 'test-org-1')
        self.assertEqual(response.data['name'], 'Test Org 1')
        self.assertIsNotNone(response.data['uuid'])
        self.assertTrue(self.user.organizations.filter(mnemonic='test-org-1').exists())

    def test_post_400(self):
        response = self.client.post(
            '/orgs/',
            dict(id='test-org-1'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(name=[ErrorDetail(string='This field is required.', code='required')]))
        self.assertFalse(self.user.organizations.filter(mnemonic='test-org-1').exists())

        response = self.client.post(
            '/orgs/',
            dict(id='OCL', name='another ocl'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(mnemonic='Organization with mnemonic OCL already exists.'))


class OrganizationDetailViewTest(OCLAPITestCase):
    def setUp(self):
        self.org = OrganizationFactory(name='Stark Enterprises')
        self.user = UserProfileFactory(organizations=[self.org])
        self.superuser = UserProfile.objects.get(is_superuser=True)
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(
            '/orgs/{}/'.format(self.org.mnemonic),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.org.id))
        self.assertEqual(response.data['id'], self.org.mnemonic)
        self.assertEqual(response.data['name'], 'Stark Enterprises')

    def test_get_404(self):
        response = self.client.get(
            '/orgs/foobar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        response = self.client.put(
            '/orgs/{}/'.format(self.org.mnemonic),
            dict(name='Wayne Corporation'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.org.id))
        self.assertEqual(response.data['id'], self.org.mnemonic)
        self.assertEqual(response.data['name'], 'Wayne Corporation')

    def test_delete_403(self):
        stranger = UserProfileFactory()
        response = self.client.delete(
            '/orgs/{}/'.format(self.org.mnemonic),
            HTTP_AUTHORIZATION='Token ' + stranger.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)

    @patch('core.orgs.views.delete_organization')
    def test_delete_204_by_superuser(self, delete_organization_mock):
        response = self.client.delete(
            '/orgs/{}/'.format(self.org.mnemonic),
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        delete_organization_mock.delay.assert_called_once_with(self.org.id)

    @patch('core.orgs.views.delete_organization')
    def test_delete_204_by_owner(self, delete_organization_mock):
        response = self.client.delete(
            '/orgs/{}/'.format(self.org.mnemonic),
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        delete_organization_mock.delay.assert_called_once_with(self.org.id)


class OrganizationUserListViewTest(OCLAPITestCase):
    def test_get_200(self):
        response = self.client.get(
            '/orgs/OCL/members/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['username'], 'ocladmin')

    def test_get_404(self):
        response = self.client.get(
            '/orgs/OCL1/members/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_get_403(self):
        private_org = OrganizationFactory(public_access=ACCESS_TYPE_NONE)

        response = self.client.get(
            '/orgs/{}/members/'.format(private_org.mnemonic),
            format='json'
        )

        self.assertEqual(response.status_code, 403)


class OrganizationMemberViewTest(OCLAPITestCase):
    def setUp(self):
        self.org = OrganizationFactory(name='Stark Enterprises')
        self.user = UserProfileFactory(organizations=[self.org])
        self.superuser = UserProfile.objects.get(is_superuser=True)
        self.token = self.user.get_token()

    def test_get_204(self):
        response = self.client.get(
            '/orgs/{}/members/{}/'.format(self.org.mnemonic, self.user.username),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)

    def test_get_403(self):
        random_user = UserProfileFactory()
        response = self.client.get(
            '/orgs/{}/members/{}/'.format(self.org.mnemonic, random_user.username),
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)

    def test_get_404(self):
        response = self.client.get(
            '/orgs/foobar/members/{}/'.format(self.user.username),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        self.assertEqual(self.user.organizations.count(), 1)

        new_org = OrganizationFactory()
        response = self.client.put(
            '/orgs/{}/members/{}/'.format(new_org.mnemonic, self.user.username),
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.user.organizations.count(), 2)

    def test_put_403(self):
        self.assertEqual(self.user.organizations.count(), 1)

        new_org = OrganizationFactory()
        response = self.client.put(
            '/orgs/{}/members/{}/'.format(new_org.mnemonic, self.user.username),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.user.organizations.count(), 1)

    def test_put_404(self):
        new_org = OrganizationFactory()
        response = self.client.put(
            '/orgs/{}/members/foobar/'.format(new_org.mnemonic),
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_403(self):
        self.assertEqual(self.user.organizations.count(), 1)
        random_user = UserProfileFactory()
        response = self.client.delete(
            '/orgs/{}/members/{}/'.format(self.org.mnemonic, random_user.username),
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.user.organizations.count(), 1)

    def test_delete_204(self):
        self.assertEqual(self.user.organizations.count(), 1)

        response = self.client.delete(
            '/orgs/{}/members/{}/'.format(self.org.mnemonic, self.user.username),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.user.organizations.count(), 0)


class OrganizationExtrasViewTest(OCLAPITestCase):
    def test_get_200(self):
        org = OrganizationFactory(extras=dict())
        user = UserProfileFactory(organizations=[org])
        token = user.get_token()

        response = self.client.get(
            org.uri + 'extras/',
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict())

        org = OrganizationFactory(extras=dict(foo='bar'))

        response = self.client.get(
            org.uri + 'extras/',
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='bar'))


class OrganizationExtraRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.extras = dict(foo='bar', tao='ching')
        self.organization = OrganizationFactory(extras=self.extras)
        self.user = UserProfileFactory(organizations=[self.organization])
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(
            self.organization.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='bar'))

    def test_get_404(self):
        response = self.client.get(
            self.organization.uri + 'extras/bar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            '/orgs/org-foo/extras/bar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        response = self.client.put(
            self.organization.uri + 'extras/foo/',
            dict(foo='foobar'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(foo='foobar'))
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.extras, dict(foo='foobar', tao='ching'))

    def test_put_400(self):
        response = self.client.put(
            self.organization.uri + 'extras/foo/',
            dict(tao='te-ching'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, ['Must specify foo param in body.'])

    def test_delete(self):
        response = self.client.delete(
            self.organization.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.extras, dict(tao='ching'))

        response = self.client.delete(
            self.organization.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.extras, dict(tao='ching'))
