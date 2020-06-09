from django.core.management import call_command
from django.test import TestCase

from core.orgs.models import Organization
from .constants import USER_OBJECT_TYPE
from .models import UserProfile


class UserProfileTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")

    def setUp(self):
        self.org = Organization.objects.get(id=1)

    def tearDown(self):
        Organization.objects.exclude(id=1).all().delete()
        UserProfile.objects.exclude(id=1).all().delete()

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

    def test_user_id(self):
        self.assertEqual(UserProfile().user_id, None)
        self.assertEqual(UserProfile(id=1).user_id, 1)
