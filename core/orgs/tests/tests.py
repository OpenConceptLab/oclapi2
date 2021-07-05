from django.core.exceptions import ValidationError
from mock import patch, Mock

from core.common.constants import ACCESS_TYPE_NONE, HEAD
from core.common.tasks import delete_organization
from core.common.tests import OCLTestCase
from core.orgs.constants import ORG_OBJECT_TYPE
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.collections.tests.factories import OrganizationCollectionFactory
from core.users.tests.factories import UserProfileFactory


class OrganizationTest(OCLTestCase):
    def test_resource_type(self):
        self.assertEqual(Organization().resource_type, ORG_OBJECT_TYPE)

    def test_org(self):
        self.assertEqual(Organization().org, '')
        self.assertEqual(Organization(mnemonic='blah').org, 'blah')

    def test_is_versioned(self):
        self.assertFalse(Organization().is_versioned)

    def test_members(self):
        org = Organization(id=123)
        self.assertEqual(org.members.count(), 0)

        creator = UserProfileFactory(username='creator')
        org = OrganizationFactory.build(created_by=creator, updated_by=creator)
        org.save()

        self.assertEqual(org.members.count(), 1)
        self.assertEqual(list(org.members.values_list('username', flat=True)), ['creator'])

        updater = UserProfileFactory(username='updater')
        org.updated_by = updater
        org.save()

        self.assertEqual(org.members.count(), 2)
        self.assertEqual(sorted(list(org.members.values_list('username', flat=True))), sorted(['creator', 'updater']))

    def test_create_organization_negative__no_name(self):
        with self.assertRaises(ValidationError):
            org = Organization(mnemonic='org1')
            org.full_clean()
            org.save()

    def test_create_organization_negative__no_mnemonic(self):
        with self.assertRaises(ValidationError):
            org = Organization(name='My Organization')
            org.full_clean()
            org.save()

    def test_organization_delete(self):
        org = OrganizationFactory()
        org_id = org.id

        self.assertTrue(org.is_active)
        self.assertTrue(Organization.objects.filter(id=org_id).exists())
        org.soft_delete()
        self.assertFalse(org.is_active)
        self.assertTrue(Organization.objects.filter(id=org_id).exists())
        org.delete()
        self.assertFalse(Organization.objects.filter(id=org_id).exists())

    @patch('core.orgs.models.Organization.source_set')
    def test_public_sources(self, source_set_mock):
        source_set_mock.exclude = Mock(return_value=Mock(filter=Mock(return_value=Mock(count=Mock(return_value=10)))))

        self.assertEqual(Organization().public_sources, 10)
        source_set_mock.exclude.assert_called_once_with(public_access=ACCESS_TYPE_NONE)
        source_set_mock.exclude().filter.assert_called_once_with(version=HEAD)
        source_set_mock.exclude().filter().count.assert_called_once()

    def test_create_org_special_characters(self):
        # period in mnemonic
        org = OrganizationFactory(name='test', mnemonic='org.1')
        self.assertTrue(org.id)
        self.assertEqual(org.mnemonic, 'org.1')

        # hyphen in mnemonic
        org = OrganizationFactory(name='test', mnemonic='org-1')
        self.assertTrue(org.id)
        self.assertEqual(org.mnemonic, 'org-1')

        # underscore in mnemonic
        org = OrganizationFactory(name='test', mnemonic='org_1')
        self.assertTrue(org.id)
        self.assertEqual(org.mnemonic, 'org_1')

        # all characters in mnemonic
        org = OrganizationFactory(name='test', mnemonic='org.1_2-3')
        self.assertTrue(org.id)
        self.assertEqual(org.mnemonic, 'org.1_2-3')

        # @ characters in mnemonic
        org = OrganizationFactory(name='test', mnemonic='org@1')
        self.assertTrue(org.id)
        self.assertEqual(org.mnemonic, 'org@1')

    def test_org_active_inactive_should_affect_children(self):
        org = OrganizationFactory(is_active=True)
        source = OrganizationSourceFactory(organization=org, is_active=True)
        collection = OrganizationCollectionFactory(organization=org, is_active=True)

        org.is_active = False
        org.save()
        source.refresh_from_db()
        collection.refresh_from_db()

        self.assertFalse(org.is_active)
        self.assertFalse(source.is_active)
        self.assertFalse(collection.is_active)

        org.is_active = True
        org.save()
        source.refresh_from_db()
        collection.refresh_from_db()

        self.assertTrue(org.is_active)
        self.assertTrue(source.is_active)
        self.assertTrue(collection.is_active)

    def test_delete_organization_task(self):
        org = OrganizationFactory()

        delete_organization(0)

        self.assertTrue(Organization.objects.filter(id=org.id).exists())

        delete_organization(org.id)

        self.assertFalse(Organization.objects.filter(id=org.id).exists())

    def test_logo_url(self):
        self.assertIsNone(Organization(logo_path=None).logo_url)
        self.assertEqual(
            Organization(logo_path='path/foo.png').logo_url.replace('https://', 'http://'),
            'http://oclapi2-dev.s3.amazonaws.com/path/foo.png'
        )
