from unittest.mock import patch, Mock, ANY

from core.services.auth.django import DjangoAuthService
from core.services.auth.openid import OpenIDAuthService
from core.common.tests import OCLTestCase
from core.users.tests.factories import UserProfileFactory


class DjangoAuthServiceTest(OCLTestCase):
    def test_get_token(self):
        user = UserProfileFactory(username='foobar')

        token = DjangoAuthService(user=user, password='foobar').get_token(True)
        self.assertEqual(token, False)

        user.set_password('foobar')
        user.save()

        token = DjangoAuthService(username='foobar', password='foobar').get_token(True)
        self.assertTrue('Token ' in token)
        self.assertTrue(len(token), 64)


class OpenIDAuthServiceTest(OCLTestCase):
    def test_get_login_redirect_url(self):
        self.assertEqual(
            OpenIDAuthService.get_login_redirect_url('client-id', 'http://localhost:4000', 'state', 'nonce'),
            '/realms/ocl/protocol/openid-connect/auth?response_type=code id_token&client_id=client-id&'
            'state=state&nonce=nonce&redirect_uri=http://localhost:4000'
        )

    def test_get_logout_redirect_url(self):
        self.assertEqual(
            OpenIDAuthService.get_logout_redirect_url('id-token-hint', 'http://localhost:4000'),
            '/realms/ocl/protocol/openid-connect/logout?id_token_hint=id-token-hint&'
            'post_logout_redirect_uri=http://localhost:4000'
        )

    @patch('requests.post')
    def test_exchange_code_for_token(self, post_mock):
        post_mock.return_value = Mock(json=Mock(return_value={'token': 'token', 'foo': 'bar'}))

        result = OpenIDAuthService.exchange_code_for_token(
            'code', 'http://localhost:4000', 'client-id', 'client-secret'
        )

        self.assertEqual(result, {'token': 'token', 'foo': 'bar'})
        post_mock.assert_called_once_with(
            '/realms/ocl/protocol/openid-connect/token',
            data={
                'grant_type': 'authorization_code',
                'client_id': 'client-id',
                'client_secret': 'client-secret',
                'code': 'code',
                'redirect_uri': 'http://localhost:4000'
            }
        )

    @patch('requests.post')
    def test_get_admin_token(self, post_mock):
        post_mock.return_value = Mock(json=Mock(return_value={'access_token': 'token', 'foo': 'bar'}))

        result = OpenIDAuthService.get_admin_token('username', 'password')

        self.assertEqual(result, 'token')
        post_mock.assert_called_once_with(
            '/realms/master/protocol/openid-connect/token',
            data={
                'grant_type': 'password',
                'username': 'username',
                'password': 'password',
                'client_id': 'admin-cli'
            },
            verify=False
        )

    @patch('core.services.auth.openid.OpenIDAuthService.get_admin_token')
    @patch('requests.post')
    def test_add_user(self, post_mock, get_admin_token_mock):
        post_mock.return_value = Mock(status_code=201, json=Mock(return_value={'foo': 'bar'}))
        get_admin_token_mock.return_value = 'token'
        user = UserProfileFactory(username='username')
        user.set_password('password')
        user.save()

        result = OpenIDAuthService.add_user(user, 'username', 'password')

        self.assertEqual(result, True)
        get_admin_token_mock.assert_called_once_with(username='username', password='password')
        post_mock.assert_called_once_with(
            '/admin/realms/ocl/users',
            json={
                'enabled': True,
                'emailVerified': user.verified,
                'firstName': user.first_name,
                'lastName': user.last_name,
                'email': user.email,
                'username': user.username,
                'credentials': ANY
            },
            verify=False,
            headers={'Authorization': 'Bearer token'}
        )
