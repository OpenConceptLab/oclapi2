from mock import Mock, patch
from rest_framework.authtoken.models import Token

from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.constants import ACCESS_TYPE_NONE, HEAD, OCL_ORG_ID
from core.common.tests import OCLTestCase
from core.orgs.models import Organization
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.constants import USER_OBJECT_TYPE
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

    @patch('core.users.models.UserProfile.source_set')
    def test_public_sources(self, source_set_mock):
        source_set_mock.exclude = Mock(return_value=Mock(filter=Mock(return_value=Mock(count=Mock(return_value=10)))))

        self.assertEqual(UserProfile().public_sources, 10)
        source_set_mock.exclude.assert_called_once_with(public_access=ACCESS_TYPE_NONE)
        source_set_mock.exclude().filter.assert_called_once_with(version=HEAD)
        source_set_mock.exclude().filter().count.assert_called_once()

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

    def test_internal_reference_id(self):
        user = UserProfileFactory.build(id=123)

        self.assertIsNotNone(user.id)
        self.assertIsNone(user.internal_reference_id)

        user.save()

        self.assertIsNotNone(user.id)
        self.assertEqual(user.internal_reference_id, str(user.id))

    def test_update_password(self):
        user = UserProfileFactory()
        user.set_password('password')
        user.save()

        user.update_password()
        self.assertTrue(user.check_password('password'))

        user.update_password(password='newpassword')
        self.assertFalse(user.check_password('password'))
        self.assertTrue(user.check_password('newpassword'))

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
