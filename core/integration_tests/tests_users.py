from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT
from core.common.tests import OCLAPITestCase
from core.orgs.tests.factories import OrganizationFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class UserOrganizationListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory(username='user')
        self.org_private = OrganizationFactory(mnemonic='private-org', public_access=ACCESS_TYPE_NONE)
        self.org_public_view = OrganizationFactory(mnemonic='public-view-org', public_access=ACCESS_TYPE_VIEW)
        self.org_public_edit = OrganizationFactory(mnemonic='public-edit-org', public_access=ACCESS_TYPE_EDIT)
        self.user_org_public = OrganizationFactory(mnemonic='user-public-view-org', public_access=ACCESS_TYPE_VIEW)
        self.user_org_private = OrganizationFactory(mnemonic='user-private-org', public_access=ACCESS_TYPE_NONE)
        self.user.organizations.set([self.user_org_private, self.user_org_public])
        self.token = self.user.get_token()

    def test_get_200(self):
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

    def test_head_200(self):
        response = self.client.head(
            '/users/{}/orgs/'.format(self.user.username),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['num_found'], '2')

    def test_post_405(self):
        response = self.client.post(
            '/users/{}/orgs/'.format(self.user.username),
            dict(id='test-org-1', name='Test Org 1'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 405)
        self.assertIsNone(response.data)


class UserLoginViewTest(OCLAPITestCase):
    def test_login(self):
        user = UserProfileFactory(username='marty')
        user.set_password('boogeyman')
        user.save()

        response = self.client.post(
            '/users/login/',
            dict(username='marty', password='boogeyman'),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(token=user.get_token()))

        response = self.client.post(
            '/users/login/',
            dict(username='marty', password='wuss'),
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(non_field_errors=["Unable to log in with provided credentials."]))


class UserListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.superuser = UserProfile.objects.get(username='ocladmin')

    def test_get_200(self):
        response = self.client.get(
            '/users/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['username'], 'ocladmin')

        response = self.client.get(
            '/users/?verbose=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['username'], 'ocladmin')
        self.assertEqual(response.data[0]['email'], self.superuser.email)

    def test_post_201(self):
        response = self.client.post(
            '/users/',
            dict(username='charles', name='Charles Dickens', password='scrooge', email='charles@fiction.com'),
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['uuid'])
        self.assertIsNotNone(response.data['token'])

        response = self.client.post(
            '/users/',
            dict(username='charles', name='Charles Dickens', password='scrooge', email='charles@fiction.com'),
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['token'])

        response = self.client.post(
            '/users/login/',
            dict(username='charles', password='scrooge'),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data['token'])

    def test_post_403(self):
        random_user = UserProfileFactory()
        response = self.client.post(
            '/users/',
            dict(username='johndoe', name='John Doe', password='unknown', email='john@doe.com'),
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)


class UserDetailViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory()
        self.token = self.user.get_token()
        self.superuser = UserProfile.objects.get(username='ocladmin')

    def test_get_200(self):
        response = self.client.get(
            '/users/{}/'.format(self.user.username),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], self.user.username)
        self.assertEqual(response.data['name'], self.user.name)
        self.assertEqual(response.data['url'], self.user.uri)

    def test_get_404(self):
        response = self.client.get(
            '/users/foobar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        self.user.set_password('password')
        self.user.email = 'user1@user.com'
        self.user.save()
        self.assertTrue(self.user.check_password('password'))

        response = self.client.put(
            '/users/{}/'.format(self.user.username),
            dict(password='newpassword', email='user@user.com'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], self.user.username)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('newpassword'))
        self.assertEqual(self.user.email, 'user@user.com')

    def test_delete_self_405(self):
        response = self.client.delete(
            '/users/{}/'.format(self.superuser.username),
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    def test_delete_403(self):
        random_user = UserProfileFactory()
        response = self.client.delete(
            '/users/{}/'.format(self.user.username),
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)

    def test_delete_204(self):
        response = self.client.delete(
            '/users/{}/'.format(self.user.username),
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)


class UserReactivateViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.superuser = UserProfile.objects.get(username='ocladmin')

    def test_put_bad_request(self):
        inactive_user = UserProfileFactory(is_active=False)
        response = self.client.put(
            '/users/{}/reactivate/'.format(inactive_user.username),
            HTTP_AUTHORIZATION='Token ' + inactive_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 401)

        random_user = UserProfileFactory()
        response = self.client.put(
            '/users/{}/reactivate/'.format(inactive_user.username),
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 403)

    def test_put_204(self):
        inactive_user = UserProfileFactory(is_active=False)
        response = self.client.put(
            '/users/{}/reactivate/'.format(inactive_user.username),
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 204)
        inactive_user.refresh_from_db()
        self.assertTrue(inactive_user.is_active)
