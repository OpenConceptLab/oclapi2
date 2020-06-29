from mock import Mock, patch

from core.common.constants import ACCESS_TYPE_NONE, HEAD
from core.common.tests import OCLTestCase
from core.orgs.models import Organization
from core.users.constants import USER_OBJECT_TYPE
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class UserProfileTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.org = Organization.objects.get(id=1)

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
