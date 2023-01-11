from datetime import datetime

from django.contrib.auth.models import Group
from mock import Mock, patch, ANY
from rest_framework.authtoken.models import Token

from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.constants import ACCESS_TYPE_NONE, HEAD, OCL_ORG_ID
from core.common.tasks import send_user_verification_email, send_user_reset_password_email
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.orgs.models import Organization
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.constants import USER_OBJECT_TYPE, OCL_SERVERS_GROUP
from core.users.documents import UserProfileDocument
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class UserProfileTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.org = Organization.objects.get(id=OCL_ORG_ID)

    def test_create_userprofile_positive(self):
        self.assertFalse(UserProfile.objects.filter(username='user1').exists())
        user = UserProfile(
            username='user1',
            email='user1@test.com',
            last_name='Schindler',
            first_name='Oskar',
            password='password',
        )
        user.full_clean()
        user.save()
        user.organizations.add(self.org)

        self.assertIsNotNone(user.id)
        self.assertEqual(user.username, user.mnemonic)
        self.assertTrue(UserProfile.objects.filter(username='user1').exists())

    def test_name(self):
        self.assertEqual(
            UserProfile(first_name='First', last_name="Last").name,
            "First Last"
        )

    def test_full_name(self):
        self.assertEqual(
            UserProfile(first_name='First', last_name="Last").full_name,
            "First Last"
        )

    def test_resource_type(self):
        user = UserProfile()

        self.assertEqual(user.resource_type, USER_OBJECT_TYPE)

    def test_mnemonic(self):
        self.assertEqual(UserProfile().mnemonic, '')
        self.assertEqual(UserProfile(username='foo').mnemonic, 'foo')

    def test_user(self):
        self.assertEqual(UserProfile().user, '')
        self.assertEqual(UserProfile(username='foo').user, 'foo')

    def test_get_search_document(self):
        self.assertEqual(UserProfile.get_search_document(), UserProfileDocument)

    def test_status(self):
        self.assertEqual(UserProfile(is_active=True, verified=True).status, 'verified')
        self.assertEqual(UserProfile(is_active=True, verified=False).status, 'unverified')
        self.assertEqual(UserProfile(is_active=False, verified=True).status, 'deactivated')
        self.assertEqual(UserProfile(is_active=False, verified=False).status, 'deactivated')

    @patch('core.users.models.UserProfile.source_set')
    def test_public_sources(self, source_set_mock):
        source_set_mock.filter = Mock(return_value=Mock(exclude=Mock(return_value=Mock(count=Mock(return_value=10)))))

        self.assertEqual(UserProfile().public_sources, 10)
        source_set_mock.filter.assert_called_once_with(version=HEAD)
        source_set_mock.filter().exclude.assert_called_once_with(public_access=ACCESS_TYPE_NONE)
        source_set_mock.filter().exclude().count.assert_called_once()

    @patch('core.orgs.models.Organization.collection_set')
    def test_public_collections(self, collection_set_mock):
        collection_set_mock.filter = Mock(
            return_value=Mock(exclude=Mock(return_value=Mock(count=Mock(return_value=10)))))

        self.assertEqual(Organization().public_collections, 10)
        collection_set_mock.filter.assert_called_once_with(version=HEAD)
        collection_set_mock.filter().exclude.assert_called_once_with(public_access=ACCESS_TYPE_NONE)
        collection_set_mock.filter().exclude().count.assert_called_once()

    def test_delete(self):
        user = UserProfileFactory()
        user_id = user.id

        self.assertTrue(user.is_active)
        self.assertTrue(UserProfile.objects.filter(id=user_id).exists())

        user.soft_delete()

        self.assertFalse(user.is_active)
        self.assertTrue(UserProfile.objects.filter(id=user_id).exists())

        user.delete()

        self.assertFalse(UserProfile.objects.filter(id=user_id).exists())

    def test_user_active_inactive_should_affect_children(self):
        user = UserProfileFactory(is_active=True)
        source = OrganizationSourceFactory(user=user, is_active=True)
        collection = OrganizationCollectionFactory(user=user, is_active=True)

        user.is_active = False
        user.save()
        source.refresh_from_db()
        collection.refresh_from_db()

        self.assertFalse(user.is_active)
        self.assertFalse(source.is_active)
        self.assertFalse(collection.is_active)

        user.is_active = True
        user.save()
        source.refresh_from_db()
        collection.refresh_from_db()

        self.assertTrue(user.is_active)
        self.assertTrue(source.is_active)
        self.assertTrue(collection.is_active)

    def test_update_password(self):
        user = UserProfileFactory()
        user.set_password('Password123!')
        user.save()

        user.update_password()
        self.assertTrue(user.check_password('Password123!'))

        self.assertEqual(
            user.update_password(password='newpassword'),
            dict(errors=['This password is too common.', 'This password is not alphanumeric.'])
        )
        self.assertEqual(
            user.update_password(password='short'),
            dict(errors=[
                'This password is too short. It must contain at least 8 characters.',
                'This password is not alphanumeric.'
            ])
        )

        user.verification_token = 'some-token'
        user.save()
        user.update_password(password='Newpassw0rd')
        self.assertIsNone(user.verification_token)
        self.assertFalse(user.check_password('Password123!'))
        self.assertTrue(user.check_password('Newpassw0rd'))

        user.update_password(hashed_password='hashedpassword')
        self.assertFalse(user.check_password('password'))
        self.assertEqual(user.password, 'hashedpassword')

    def test_get_token(self):
        user = UserProfileFactory()

        self.assertFalse(Token.objects.filter(user=user).exists())

        token = user.get_token()

        self.assertIsNotNone(token)
        self.assertEqual(user.auth_token.key, token)
        self.assertEqual(user.get_token(), token)

    def test_set_token(self):
        user = UserProfileFactory()

        self.assertFalse(Token.objects.filter(user=user).exists())

        user.set_token('token')
        self.assertEqual(user.auth_token.key, 'token')

    @patch('core.users.models.send_user_verification_email')
    def test_send_verification_email(self, mail_mock):
        user = UserProfile(id=189)
        user.send_verification_email()

        mail_mock.delay.assert_called_once_with(189)

    @patch('core.users.models.send_user_reset_password_email')
    def test_send_reset_password_email(self, mail_mock):
        user = UserProfile(id=189)
        user.send_reset_password_email()

        mail_mock.delay.assert_called_once_with(189)

    def test_email_verification_url(self):
        user = UserProfile(id=189, username='foobar', verification_token='some-token')
        self.assertEqual(
            user.email_verification_url,
            'http://localhost:4000/#/accounts/foobar/verify/some-token/'
        )

    def test_reset_password_url(self):
        user = UserProfile(id=189, username='foobar', verification_token='some-token')
        self.assertEqual(
            user.reset_password_url,
            'http://localhost:4000/#/accounts/foobar/password/reset/some-token/'
        )

    def test_mark_verified(self):
        user = UserProfileFactory(verified=False, verification_token='some-token')
        self.assertFalse(user.verified)

        self.assertFalse(user.mark_verified(token='wrong-token'))
        user.refresh_from_db()
        self.assertEqual(user.verification_token, 'some-token')
        self.assertFalse(user.verified)

        self.assertTrue(user.mark_verified(token='some-token'))
        user.refresh_from_db()
        self.assertIsNone(user.verification_token)
        self.assertTrue(user.verified)

        user.save = Mock()
        self.assertTrue(user.mark_verified(token='some-token'))
        self.assertIsNone(user.verification_token)
        user.save.assert_not_called()

    def test_auth_groups(self):
        user = UserProfileFactory()
        self.assertEqual(user.auth_groups.count(), 0)

        user.groups.add(Group.objects.get(name=OCL_SERVERS_GROUP))

        self.assertEqual(user.auth_groups.count(), 1)

    def test_is_valid_auth_group(self):
        self.assertFalse(UserProfile.is_valid_auth_group('foobar'))
        self.assertTrue(UserProfile.is_valid_auth_group(OCL_SERVERS_GROUP))

    def test_deactivate(self):
        user = UserProfileFactory(is_active=True, deactivated_at=None, verified=True)

        self.assertEqual(user.status, 'verified')

        user.deactivate()

        self.assertEqual(user.status, 'deactivated')
        self.assertFalse(user.verified)
        self.assertFalse(user.is_active)

    def test_verify(self):
        user = UserProfileFactory(
            is_active=False, deactivated_at=datetime.now(), verified=False, verification_token=None)

        self.assertEqual(user.status, 'deactivated')

        user.send_verification_email = Mock()

        user.verify()

        self.assertEqual(user.status, 'verification_pending')
        self.assertFalse(user.verified)
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.verification_token)
        user.send_verification_email.assert_called_once()


class TokenAuthenticationViewTest(OCLAPITestCase):
    def test_login(self):
        response = self.client.post('/users/login/', {})

        self.assertEqual(response.status_code, 400)

        response = self.client.post('/users/login/', dict(username='foo', password='bar'))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            dict(non_field_errors=["Unable to log in with provided credentials."])
        )

        user = UserProfileFactory()
        user.set_password('password')
        user.save()
        self.assertIsNone(user.last_login)

        response = self.client.post('/users/login/', dict(username=user.username, password='password'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(token=ANY))
        user.refresh_from_db()
        self.assertIsNotNone(user.last_login)


class UserLogoViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserProfileFactory(username='username1')
        self.token = self.user.get_token()

    @patch('core.common.services.S3.upload_base64')
    def test_post_200(self, upload_base64_mock):
        upload_base64_mock.return_value = 'users/username1/logo.png'
        self.assertIsNone(self.user.logo_url)
        self.assertIsNone(self.user.logo_path)

        response = self.client.post(
            self.user.uri + 'logo/',
            dict(base64='base64-data'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        expected_logo_url = 'http://oclapi2-dev.s3.amazonaws.com/users/username1/logo.png'
        self.assertEqual(response.data['logo_url'].replace('https://', 'http://'), expected_logo_url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.logo_url.replace('https://', 'http://'), expected_logo_url)
        self.assertEqual(self.user.logo_path, 'users/username1/logo.png')
        upload_base64_mock.assert_called_once_with('base64-data', 'users/username1/logo.png', False, True)


class TasksTest(OCLTestCase):
    @patch('core.common.tasks.EmailMessage.send')
    def test_send_user_verification_email(self, send_mail_mock):
        send_mail_mock.return_value = 1
        self.assertIsNone(send_user_verification_email(404))
        send_mail_mock.assert_not_called()

        user = UserProfileFactory()
        mail = send_user_verification_email(user.id)

        self.assertEqual(mail.content_subtype, 'html')
        self.assertEqual(mail.subject, 'Confirm E-mail Address')
        self.assertEqual(mail.to, [user.email])
        self.assertTrue(user.email_verification_url in mail.body)
        self.assertTrue(f'Hi {user.username},' in mail.body)
        send_mail_mock.assert_called_once()

    @patch('core.common.tasks.EmailMessage.send')
    def test_send_user_reset_password_email(self, send_mail_mock):
        send_mail_mock.return_value = 1
        self.assertIsNone(send_user_reset_password_email(404))
        send_mail_mock.assert_not_called()

        user = UserProfileFactory()
        mail = send_user_reset_password_email(user.id)

        self.assertEqual(mail.content_subtype, 'html')
        self.assertEqual(mail.subject, 'Password Reset E-mail')
        self.assertEqual(mail.to, [user.email])
        self.assertTrue(user.reset_password_url in mail.body)
        self.assertTrue(f'Hi {user.username},' in mail.body)
        send_mail_mock.assert_called_once()
