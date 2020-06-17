from core.common.tests import OCLTestCase
from core.orgs.models import Organization
from core.users.constants import USER_OBJECT_TYPE
from core.users.models import UserProfile


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

        self.assertEqual(USER_OBJECT_TYPE, user.resource_type())

    def test_mnemonic(self):
        self.assertEqual(UserProfile().mnemonic, '')
        self.assertEqual(UserProfile(username='foo').mnemonic, 'foo')

    def test_user(self):
        self.assertEqual(UserProfile().user, '')
        self.assertEqual(UserProfile(username='foo').user, 'foo')
