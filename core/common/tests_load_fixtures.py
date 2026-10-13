from django.core.management import call_command

from core.capabilities.models import Capability
from core.common.tests import OCLTestCase
from core.orgs.models import Organization
from core.users.models import UserProfile


class LoadFixturesCommandTest(OCLTestCase):
    def test_keeps_an_existing_ocl_org(self):
        org = Organization.objects.get(id=1)
        org.name = 'Open Concept Lab'
        org.text = '<p>About OCL</p>'
        org.description = 'Shared reference content'
        org.overview = {'background': {'image': 'https://example.org/banner.png'}}
        org.save()

        call_command('load_fixtures', verbosity=0)

        org.refresh_from_db()
        self.assertEqual(org.name, 'Open Concept Lab')
        self.assertEqual(org.text, '<p>About OCL</p>')
        self.assertEqual(org.description, 'Shared reference content')
        self.assertEqual(org.overview, {'background': {'image': 'https://example.org/banner.png'}})

    def test_reloads_the_other_fixtures(self):
        Capability.objects.filter(id=1).update(description='changed')

        call_command('load_fixtures', verbosity=0)

        self.assertEqual(Capability.objects.get(id=1).description, 'Number of map projects a user may create')

    def test_startup_restores_a_demoted_or_deactivated_ocladmin(self):
        UserProfile.objects.filter(username='ocladmin').update(
            is_superuser=False, is_staff=False, is_active=False, verified=False)

        call_command('load_fixtures', verbosity=0)
        call_command('setup_superuser')

        ocladmin = UserProfile.objects.get(username='ocladmin')
        self.assertTrue(ocladmin.is_superuser)
        self.assertTrue(ocladmin.is_staff)
        self.assertTrue(ocladmin.is_active)
        self.assertTrue(ocladmin.verified)

    def test_seeds_the_ocl_org_and_ocladmin_on_a_new_database(self):
        Organization.objects.filter(id=1).delete()
        UserProfile.objects.filter(id=1).delete()

        call_command('load_fixtures', verbosity=0)

        self.assertEqual(Organization.objects.get(id=1).mnemonic, 'OCL')
        self.assertTrue(UserProfile.objects.get(id=1, username='ocladmin').is_superuser)
