from unittest.mock import Mock, ANY

from mock import patch
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ErrorDetail

from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT
from core.common.tests import OCLAPITestCase
from core.orgs.documents import OrganizationDocument
from core.orgs.tests.factories import OrganizationFactory
from core.users.constants import VERIFY_EMAIL_MESSAGE, VERIFICATION_TOKEN_MISMATCH
from core.users.documents import UserProfileDocument
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class UserSignupVerificationViewTest(OCLAPITestCase):
    @patch('core.users.models.UserProfile.send_verification_email')
    def test_signup_unverified_to_verified(self, send_mail_mock):
        response = self.client.post(
            '/users/signup/',
            {
                'username': 'charles',
                'name': 'Charles Dickens',
                'password': 'short1',
                'email': 'charles@fiction.com'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {
                'password': [
                    'This password is too short. It must contain at least 8 characters.', 'This password is too common.'
                ]
            }
        )

        response = self.client.post(
            '/users/signup/',
            {
                'username': 'charles',
                'name': 'Charles Dickens',
                'password': 'scroooge1',
                'email': 'charles@fiction.com'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data, {'detail': VERIFY_EMAIL_MESSAGE})
        send_mail_mock.assert_called_once()

        created_user = UserProfile.objects.get(username='charles')
        self.assertFalse(created_user.verified)
        self.assertIsNotNone(created_user.verification_token)

        response = self.client.post(
            '/users/login/',
            {
                'username': 'charles',
                'password': 'scroooge1'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data, {'detail': VERIFY_EMAIL_MESSAGE, 'email': 'charles@fiction.com'})

        response = self.client.get(
            '/users/charles/verify/random-token/',
            format='json'
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data, {'detail': VERIFICATION_TOKEN_MISMATCH})

        response = self.client.get(
            f'/users/unknown/verify/{created_user.verification_token}/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            f'/users/charles/verify/{created_user.verification_token}/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        created_user.refresh_from_db()
        self.assertTrue(created_user.verified)

        response = self.client.post(
            '/users/login/',
            {
                'username': 'charles',
                'password': 'scroooge1'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'token': created_user.get_token()})

    @patch('core.users.views.AuthService.is_sso_enabled')
    def test_get_405(self, is_sso_enabled_mock):
        is_sso_enabled_mock.return_value = False

        response = self.client.get(
            '/users/signup/?client_id=client-id&redirect_uri=http://post-login-url&state=state&nonce=nonce'
        )

        self.assertEqual(response.status_code, 405)

    @patch('core.users.views.OpenIDAuthService.get_registration_redirect_url')
    @patch('core.users.views.AuthService.is_sso_enabled')
    def test_get_200(self, is_sso_enabled_mock, get_registration_url_mock):
        is_sso_enabled_mock.return_value = True
        get_registration_url_mock.return_value = 'http://registration-redirect.com'

        response = self.client.get(
            '/users/signup/?client_id=client-id&redirect_uri=http://post-registration-url&state=state&nonce=nonce'
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], 'http://registration-redirect.com')
        get_registration_url_mock.assert_called_once_with(
            'client-id', 'http://post-registration-url', 'state', 'nonce'
        )


class UserPasswordResetViewTest(OCLAPITestCase):
    @patch('core.users.models.UserProfile.send_reset_password_email')
    def test_request_and_reset(self, send_mail_mock):
        user = UserProfileFactory(username='foo-user', email='foo@user.com')
        self.assertIsNone(user.verification_token)

        response = self.client.post(
            '/users/password/reset/',
            {},
            format='json'
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            '/users/password/reset/',
            {'email': 'bad@user.com'},
            format='json'
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            '/users/password/reset/',
            {'email': 'foo@user.com'},
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertIsNotNone(user.verification_token)
        send_mail_mock.assert_called_once()

        response = self.client.put(
            '/users/password/reset/',
            {},
            format='json'
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.put(
            '/users/password/reset/',
            {'token': 'bad-token'},
            format='json'
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.put(
            '/users/password/reset/',
            {'new_password': 'new-password123'},
            format='json'
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.put(
            '/users/password/reset/',
            {
                'token': 'bad-token',
                'new_password': 'new-password123'
            },
            format='json'
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.put(
            '/users/password/reset/',
            {
                'token': user.verification_token,
                'new_password': 'new-password123'
            },
            format='json'
        )
        self.assertEqual(response.status_code, 200)

        user.refresh_from_db()
        self.assertIsNone(user.verification_token)
        self.assertTrue(user.check_password('new-password123'))

        response = self.client.post(
            '/users/login/',
            {
                'username': 'foo-user',
                'password': 'new-password123'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'token': user.get_token()})


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
        OrganizationDocument().update([
            self.org_private, self.org_public_view, self.org_public_edit,
            self.user_org_public, self.user_org_private
        ])

    def test_get_200(self):
        response = self.client.get(
            f'/users/{self.user.username}/orgs/',
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
            f'/users/{random_user.username}/orgs/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        response = self.client.get(
            f'/users/{self.user.username}/orgs/?q=private',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-private-org']
        )

        response = self.client.get(
            '/user/orgs/?q=private',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-private-org']
        )

        response = self.client.get(
            '/user/orgs/?q=private&updatedSince=2021-01-01',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-private-org']
        )

        response = self.client.get(
            '/user/orgs/?q=private&updatedSince=3022-01-01',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        response = self.client.get(
            '/user/orgs/?q=',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([org['id'] for org in response.data]),
            sorted(['user-private-org', 'user-public-view-org'])
        )

        response = self.client.get(
            '/user/orgs/?q=foobar',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_head_200(self):
        response = self.client.head(
            f'/users/{self.user.username}/orgs/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get('num_found'), '2')

    def test_post_405(self):
        response = self.client.post(
            f'/users/{self.user.username}/orgs/',
            {
                'id': 'test-org-1',
                'name': 'Test Org 1'
            },
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

        self.assertIsNone(user.last_login)

        response = self.client.post(
            '/users/login/',
            {
                'username': 'marty',
                'password': 'boogeyman'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'token': user.get_token()})
        user.refresh_from_db()
        self.assertIsNotNone(user.last_login)

        response = self.client.post(
            '/users/login/',
            {
                'username': 'marty',
                'password': 'wuss'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'non_field_errors': ["Unable to log in with provided credentials."]})

    @patch('core.users.models.UserProfile.verify')
    def test_login_inactive_user(self, verify_mock):
        user = UserProfileFactory(username='marty', is_active=False)
        user.set_password('boogeyman')
        user.save()

        response = self.client.post(
            '/users/login/',
            {
                'username': 'marty',
                'password': 'boogeyman'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.data,
            {
                'detail': 'A verification email has been sent to the address on record. Verify your email address'
                          ' to re-activate your account.',
                'email': user.email
            }
        )
        verify_mock.assert_called_once()

    @patch('core.users.models.UserProfile.send_verification_email')
    def test_login_unverified_user(self, send_verification_email_mock):
        user = UserProfileFactory(username='marty', is_active=True, verified=False)
        user.set_password('boogeyman')
        user.save()

        response = self.client.post(
            '/users/login/',
            {
                'username': 'marty',
                'password': 'boogeyman'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.data,
            {
                'detail': 'A verification email has been sent to the address on record. Verify your email address to '
                          'activate your account.',
                'email': user.email
            }
        )
        send_verification_email_mock.assert_called_once()


class UserListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.superuser = UserProfile.objects.get(username='ocladmin')
        UserProfileDocument().update([self.superuser])

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

        response = self.client.get(
            '/users/?q=ocl',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        response = self.client.get(
            '/users/?q=foobar',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_get_200_with_inactive_user(self):
        inactive_user = UserProfileFactory(is_active=False, username='inactive')
        UserProfileDocument().update([inactive_user])

        response = self.client.get(
            '/users/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['username'], 'ocladmin')

        response = self.client.get(
            '/users/?includeInactive=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(sorted(user['username'] for user in response.data), sorted(['ocladmin', 'inactive']))

        response = self.client.get(
            '/users/?includeInactive=true&q=',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(sorted(user['username'] for user in response.data), sorted(['ocladmin', 'inactive']))

        response = self.client.get(
            '/users/?includeInactive=true&q=inactive',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['username'], 'inactive')

    def test_get_summary_200(self):
        response = self.client.get(
            '/users/?summary=true',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['username'], 'ocladmin')
        self.assertEqual(response.data[0]['organizations'], 1)
        self.assertEqual(response.data[0]['sources'], 0)
        self.assertEqual(response.data[0]['collections'], 0)
        self.assertEqual(response.data[0]['logo_url'], None)
        self.assertEqual(response.data[0]['url'], self.superuser.url)

    @patch('core.users.models.send_user_verification_email')
    def test_post_201(self, job_mock):
        response = self.client.post(
            '/users/',
            {
                'username': 'charles',
                'name': 'Charles Dickens',
                'password': 'scroooge1',
                'email': 'charles@fiction.com'
            },
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['uuid'])
        self.assertIsNotNone(response.data['token'])
        job_mock.apply_async.assert_called_once_with((int(response.data['uuid']),), queue='default', permanent=False)

        response = self.client.post(
            '/users/',
            {
                'username': 'charles',
                'name': 'Charles Dickens',
                'password': 'scroooge1',
                'email': 'charles@fiction.com'
            },
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {'username': [ErrorDetail(string='A user with this username already exists', code='unique')],
             'email': [ErrorDetail(string='A user with this email already exists', code='unique')]}
        )

        response = self.client.post(
            '/users/login/',
            {
                'username': 'charles',
                'password': 'scroooge1'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data['token'])

    def test_post_403(self):
        random_user = UserProfileFactory()
        response = self.client.post(
            '/users/',
            {
                'username': 'johndoe',
                'name': 'John Doe',
                'password': 'unknown',
                'email': 'john@doe.com'
            },
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
            f'/users/{self.user.username}/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], self.user.username)
        self.assertEqual(response.data['name'], self.user.name)
        self.assertEqual(response.data['url'], self.user.uri)

        random_user = UserProfileFactory()

        response = self.client.get(
            f'/users/{self.user.username}/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], self.user.username)
        self.assertEqual(response.data['name'], self.user.name)
        self.assertEqual(response.data['url'], self.user.uri)

    def test_get_200_with_subscribed_orgs(self):
        response = self.client.get(
            f'/users/{self.user.username}/?includeSubscribedOrgs=false',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], self.user.username)
        self.assertEqual(response.data['name'], self.user.name)
        self.assertEqual(response.data['url'], self.user.uri)
        self.assertFalse('subscribed_orgs' in response.data)

        response = self.client.get(
            f'/users/{self.user.username}/?includeSubscribedOrgs=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], self.user.username)
        self.assertEqual(len(response.data['subscribed_orgs']), 0)

        org = OrganizationFactory()
        self.user.organizations.add(org)

        response = self.client.get(
            f'/users/{self.user.username}/?includeSubscribedOrgs=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], self.user.username)
        self.assertEqual(len(response.data['subscribed_orgs']), 1)

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
            f'/users/{self.user.username}/',
            {
                'password': 'newpassword123',
                'email': 'user@user.com'
            },
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], self.user.username)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('newpassword123'))
        self.assertEqual(self.user.email, 'user@user.com')

    def test_delete_self_405(self):
        response = self.client.delete(
            f'/users/{self.superuser.username}/',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 405)

    def test_delete_403(self):
        random_user = UserProfileFactory()
        response = self.client.delete(
            f'/users/{self.user.username}/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)

    def test_soft_delete_204(self):
        response = self.client.delete(
            f'/users/{self.user.username}/',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.verified)
        self.assertIsNotNone(self.user.deactivated_at)
        self.assertFalse(Token.objects.filter(user_id=self.user.id).exists())

    def test_hard_delete_204(self):
        random_user = UserProfileFactory(username='random_user', is_active=True, verified=True)
        self.assertTrue(UserProfile.objects.filter(username=random_user.username).exists())
        response = self.client.delete(
            f'/users/{random_user.username}/?hardDelete=true',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(UserProfile.objects.filter(username=random_user.username).exists())


class UserReactivateViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.superuser = UserProfile.objects.get(username='ocladmin')

    def test_put_bad_request(self):
        inactive_user = UserProfileFactory(is_active=False)
        response = self.client.put(
            f'/users/{inactive_user.username}/reactivate/',
            HTTP_AUTHORIZATION='Token ' + inactive_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 401)

        random_user = UserProfileFactory()
        response = self.client.put(
            f'/users/{inactive_user.username}/reactivate/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 403)

    def test_put_204(self):
        inactive_user = UserProfileFactory(is_active=False)
        response = self.client.put(
            f'/users/{inactive_user.username}/reactivate/',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 204)
        inactive_user.refresh_from_db()
        self.assertTrue(inactive_user.is_active)


class UserStaffToggleViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.superuser = UserProfile.objects.get(username='ocladmin')
        self.user = UserProfileFactory(username='randomuser')
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)

    def test_put_204(self):
        response = self.client.put(
            f'/users/{self.user.username}/staff/',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 204)

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)

        response = self.client.put(
            f'/users/{self.user.username}/staff/',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 204)

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)

    def test_put_400(self):
        response = self.client.put(
            f'/users/{self.superuser.username}/staff/',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 400)


class UserExtrasViewTest(OCLAPITestCase):
    def setUp(self):
        self.user = UserProfileFactory(extras={})
        self.token = self.user.get_token()

    def test_get(self):
        response = self.client.get(
            f'/users/{self.user.username}/extras/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {})

        extras = {'foo': 'bar'}
        self.user.extras = extras
        self.user.save()

        response = self.client.get(
            f'/users/{self.user.username}/extras/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, extras)

        response = self.client.get(
            '/user/extras/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, extras)


class UserExtraRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        self.user = UserProfileFactory(extras={})
        self.token = self.user.get_token()

    def test_put(self):
        self.assertEqual(self.user.extras, {})

        response = self.client.put(
            f'/users/{self.user.username}/extras/foo/',
            {'foo': 'bar'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'foo': 'bar'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.extras, {'foo': 'bar'})

        response = self.client.put(
            f'/users/{self.user.username}/extras/bar/',
            {'foo': 'bar'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, ['Must specify bar param in body.'])
        self.user.refresh_from_db()
        self.assertEqual(self.user.extras, {'foo': 'bar'})

        response = self.client.put(
            '/users/random/extras/foo/',
            {
                'foo': 'bar'
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_get(self):
        response = self.client.get(
            f'/users/{self.user.username}/extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 404)

        self.user.extras = {'foo': 'bar'}
        self.user.save()

        response = self.client.get(
            f'/users/{self.user.username}/extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'foo': 'bar'})

    def test_delete(self):
        response = self.client.delete(
            f'/users/{self.user.username}/extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 404)

        self.user.extras = {'foo': 'bar', 'tao': 'ching'}
        self.user.save()

        response = self.client.delete(
            f'/users/{self.user.username}/extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertEqual(self.user.extras, {'tao': 'ching'})


class OIDCodeExchangeViewTest(OCLAPITestCase):
    @patch('core.users.views.OpenIDAuthService')
    def test_post_200(self, service_mock):
        service_mock.exchange_code_for_token = Mock(return_value='response')
        response = self.client.post(
            '/users/oidc/code-exchange/',
            {
                'client_id': 'client-id', 'client_secret': 'client-secret',
                'redirect_uri': 'http://app.com', 'code': 'code'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'response')
        service_mock.exchange_code_for_token.assert_called_once_with(
            'code', 'http://app.com', 'client-id', 'client-secret'
        )

    def test_post_400(self):
        response = self.client.post(
            '/users/oidc/code-exchange/',
            {},
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {'error': 'code, redirect_uri, client_id and client_secret are mandatory to exchange for token'}
        )

        response = self.client.post(
            '/users/oidc/code-exchange/',
            {'redirect_uri': 'foobar'},
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {'error': 'code, redirect_uri, client_id and client_secret are mandatory to exchange for token'}
        )

        response = self.client.post(
            '/users/oidc/code-exchange/',
            {
                'client_id': 'client-id',
                'client_secret': None,
                'redirect_uri': 'http://app.com',
                'code': 'code'
            },
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {'error': 'code, redirect_uri, client_id and client_secret are mandatory to exchange for token'}
        )


class TokenExchangeViewTest(OCLAPITestCase):
    def test_get(self):
        random_user = UserProfileFactory()
        token = random_user.get_token()

        response = self.client.get(
            '/users/api-token/',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'token': token})


class OIDCLogoutViewTest(OCLAPITestCase):
    @patch('core.users.views.AuthService.is_sso_enabled')
    def test_get_405(self, is_sso_enabled_mock):
        is_sso_enabled_mock.return_value = False

        response = self.client.get(
            '/users/logout/?id_token_hint=id-token-hint&post_logout_redirect_uri=http://post-logout-url')

        self.assertEqual(response.status_code, 405)

    @patch('core.users.views.OpenIDAuthService.get_logout_redirect_url')
    @patch('core.users.views.AuthService.is_sso_enabled')
    def test_get_200(self, is_sso_enabled_mock, get_logout_url_mock):
        is_sso_enabled_mock.return_value = True
        get_logout_url_mock.return_value = 'http://logout-redirect.com'

        response = self.client.get(
            '/users/logout/?id_token_hint=id-token-hint&post_logout_redirect_uri=http://post-logout-url')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], 'http://logout-redirect.com')
        get_logout_url_mock.assert_called_once_with('id-token-hint', 'http://post-logout-url')


class UserFollowersViewTest(OCLAPITestCase):
    def test_get(self):
        follower = UserProfileFactory(username='follower')
        followed = UserProfileFactory(username='followed')
        follower_token = follower.get_token()
        followed_token = followed.get_token()
        followed.followers.add(follower)

        response = self.client.get(
            f'/users/{follower.username}/followers/',
            HTTP_AUTHORIZATION='Token ' + follower_token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        response = self.client.get(
            f'/users/{followed.username}/followers/',
            HTTP_AUTHORIZATION='Token ' + followed_token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            dict(response.data[0]),
            {
                'username': follower.user,
                'name': follower.name,
                'url': follower.url,
                'logo_url': None,
                'follow_date': ANY
            }
        )

        response = self.client.get(
            f'/users/{followed.username}/followers/',
            HTTP_AUTHORIZATION='Token ' + follower_token,  #anyone can see anyone's followers/followed
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            dict(response.data[0]),
            {
                'username': follower.user,
                'name': follower.name,
                'url': follower.url,
                'logo_url': None,
                'follow_date': ANY
            }
        )


class UserFollowingViewTest(OCLAPITestCase):
    def test_get(self):
        follower = UserProfileFactory(username='follower')
        followed = UserProfileFactory(username='followed')
        follower_token = follower.get_token()
        followed_token = followed.get_token()
        followed.followers.add(follower)

        response = self.client.get(
            f'/users/{follower.username}/following/',
            HTTP_AUTHORIZATION='Token ' + follower_token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            dict(response.data[0]),
            {
                'username': followed.user,
                'name': followed.name,
                'url': followed.url,
                'logo_url': None,
                'follow_date': ANY
            }
        )

        response = self.client.get(
            f'/users/{follower.username}/following/',
            HTTP_AUTHORIZATION='Token ' + followed_token,  #anyone can see anyone's followers/followed
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            dict(response.data[0]),
            {
                'username': followed.user,
                'name': followed.name,
                'url': followed.url,
                'logo_url': None,
                'follow_date': ANY
            }
        )

        response = self.client.get(
            f'/users/{followed.username}/following/',  # not to-way following
            HTTP_AUTHORIZATION='Token ' + followed_token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_post(self):
        follower = UserProfileFactory(username='follower')
        followed = UserProfileFactory(username='followed')
        follower_token = follower.get_token()
        followed_token = followed.get_token()

        response = self.client.post(
            f'/users/{follower.username}/following/',
            {},
            HTTP_AUTHORIZATION='Token ' + follower_token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(followed.followers.count(), 0)

        response = self.client.post(
            f'/users/{follower.username}/following/',
            {'follow': 'foobar'},
            HTTP_AUTHORIZATION='Token ' + follower_token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(followed.followers.count(), 0)

        response = self.client.post(
            f'/users/{follower.username}/following/',
            {'follow': follower.username},
            HTTP_AUTHORIZATION='Token ' + followed_token,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(followed.followers.count(), 0)

        response = self.client.post(
            f'/users/{follower.username}/following/',
            {'follow': follower.username},
            HTTP_AUTHORIZATION='Token ' + follower_token,
        )

        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            f'/users/{follower.username}/following/',
            {'follow': followed.username},
            HTTP_AUTHORIZATION='Token ' + follower_token,
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(followed.followers.count(), 1)
        self.assertEqual(followed.followers.first(), follower)
