from rest_framework.test import APITestCase

from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT, OCL_ORG_ID, SUPER_ADMIN_USER_ID
from core.common.tests import PauseElasticSearchIndex
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class OrganizationListViewTest(APITestCase, PauseElasticSearchIndex):
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

    def tearDown(self):
        Organization.objects.exclude(id=OCL_ORG_ID).all().delete()
        UserProfile.objects.exclude(id=SUPER_ADMIN_USER_ID).all().delete()

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
            '/orgs/',
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

    def test_get_200_with_username(self):
        response = self.client.get(
            '/users/{}/orgs/'.format(self.user.username),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-private-org', 'user-public-view-org']
        )

        random_user = UserProfileFactory()
        response = self.client.get(
            '/users/{}/orgs/'.format(random_user.username),
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)
