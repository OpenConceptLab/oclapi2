import importlib

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import connection
from mock import patch, Mock

from core.collections.models import Collection, CollectionReference, Expansion
from core.common.constants import ACCESS_TYPE_NONE, HEAD, ACCESS_TYPE_VIEW, RETIRED_ACCESS_TYPE_EDIT, ROLE_ADMIN, \
    ROLE_ID_BY_NAME
from core.common.models import Role
from core.common.tasks import delete_organization
from core.common.tests import OCLTestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.constants import ORG_OBJECT_TYPE
from core.orgs.documents import OrganizationDocument
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.users.tests.factories import UserProfileFactory


class RoleTest(OCLTestCase):
    def test_roles(self):
        self.assertEqual(Role._meta.db_table, 'roles')  # pylint: disable=protected-access
        self.assertEqual(dict(Role.objects.values_list('name', 'id')), ROLE_ID_BY_NAME)

    def test_name_is_one_of_the_roles(self):
        Role(name=ROLE_ADMIN).clean_fields()

        with self.assertRaises(ValidationError) as error:
            Role(name='owner').clean_fields()
        self.assertEqual(list(error.exception.message_dict), ['name'])


class OrganizationTest(OCLTestCase):
    def test_get_search_document(self):
        self.assertEqual(Organization.get_search_document(), OrganizationDocument)

    def test_resource_type(self):
        self.assertEqual(Organization().resource_type, ORG_OBJECT_TYPE)

    def test_org(self):
        self.assertEqual(Organization().org, '')
        self.assertEqual(Organization(mnemonic='blah').org, 'blah')

    def test_is_versioned(self):
        self.assertFalse(Organization().is_versioned)

    def test_has_edit_access(self):
        org = OrganizationFactory()
        member, outsider = UserProfileFactory(), UserProfileFactory()
        org.members.add(member)

        self.assertTrue(org.has_edit_access(member))
        self.assertTrue(org.has_edit_access(UserProfileFactory(is_staff=True)))
        self.assertFalse(org.has_edit_access(outsider))
        self.assertFalse(org.has_edit_access(AnonymousUser()))
        self.assertFalse(org.has_edit_access(None))
        # what a repository of the org asks its owner
        source = OrganizationSourceFactory(organization=org)
        for user in (member, outsider):
            self.assertEqual(source.has_parent_edit_access(user), org.has_edit_access(user))

    def test_has_admin_access(self):
        org = OrganizationFactory()
        member, admin, outsider = UserProfileFactory(), UserProfileFactory(), UserProfileFactory()
        org.members.add(member)
        org.set_member_role(admin, ROLE_ADMIN)

        self.assertTrue(org.has_admin_access(admin))
        self.assertTrue(org.has_admin_access(UserProfileFactory(is_staff=True)))
        self.assertFalse(org.has_admin_access(member))
        self.assertFalse(org.has_admin_access(outsider))
        self.assertFalse(org.has_admin_access(AnonymousUser()))
        self.assertFalse(org.has_admin_access(None))

    def test_is_last_admin(self):
        org = OrganizationFactory()
        admin, member = UserProfileFactory(), UserProfileFactory()
        org.set_member_role(admin, ROLE_ADMIN)
        org.members.add(member)
        org.members.remove(org.created_by)

        self.assertTrue(org.is_last_admin(admin))
        self.assertFalse(org.is_last_admin(member))
        org.set_member_role(UserProfileFactory(is_active=False), ROLE_ADMIN)
        self.assertTrue(org.is_last_admin(admin))
        org.set_member_role(UserProfileFactory(), ROLE_ADMIN)
        self.assertFalse(org.is_last_admin(admin))

    def test_is_last_admin_counts_the_user_even_when_deactivated(self):
        """So a deactivated last admin can't be removed, or hard deleted, leaving the org with no admin."""
        org = OrganizationFactory()
        admin = UserProfileFactory(is_active=False)
        org.set_member_role(admin, ROLE_ADMIN)
        org.members.remove(org.created_by)

        self.assertTrue(org.is_last_admin(admin))

    def test_is_last_admin_makes_one_query(self):
        org = OrganizationFactory()

        with self.assertNumQueries(1):
            org.is_last_admin(org.created_by)

    def test_an_insert_that_names_no_role_is_an_admin(self):
        """Code older than the role column inserts this way, during a rollout or after a rollback."""
        org = OrganizationFactory()
        user = UserProfileFactory()

        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO user_profiles_organizations (userprofile_id, organization_id) VALUES (%s, %s)',
                [user.id, org.id]
            )

        self.assertTrue(org.is_admin(user))

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

        self.assertEqual(org.members.count(), 1)
        self.assertEqual(list(org.members.values_list('username', flat=True)), ['creator'])

        org.members.remove(creator)
        org.save()

        self.assertEqual(org.members.count(), 0)

    def test_is_only_member(self):
        creator = UserProfileFactory()
        other = UserProfileFactory()
        org = OrganizationFactory(created_by=creator, updated_by=creator)

        self.assertTrue(org.is_only_member(creator))
        self.assertFalse(org.is_only_member(other))
        self.assertFalse(org.is_only_member(None))

        org.members.add(other)

        self.assertFalse(org.is_only_member(creator))
        self.assertFalse(org.is_only_member(other))

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
        source_set_mock.filter = Mock(return_value=Mock(exclude=Mock(return_value=Mock(count=Mock(return_value=10)))))

        self.assertEqual(Organization().public_sources, 10)
        source_set_mock.filter.assert_called_once_with(version=HEAD)
        source_set_mock.filter().exclude.assert_called_once_with(public_access=ACCESS_TYPE_NONE)
        source_set_mock.filter().exclude().count.assert_called_once()

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

    @patch('core.common.models.delete_s3_objects')
    def test_delete_organization_task(self, delete_s3_objects_mock):
        org = OrganizationFactory(mnemonic='to-be-deleted-org')
        source = OrganizationSourceFactory(mnemonic='to-be-deleted-source', organization=org)
        collection = OrganizationCollectionFactory(mnemonic='to-be-deleted-coll', organization=org)
        concept = ConceptFactory(mnemonic='to-be-deleted-concept', parent=source)
        mapping = MappingFactory(mnemonic='to-be-deleted-mapping', parent=source)
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        collection.add_expressions({'expressions': [concept.uri, mapping.uri]}, collection.created_by)

        self.assertEqual(org.source_set.count(), 1)
        self.assertEqual(org.collection_set.count(), 1)
        self.assertEqual(source.concepts_set.count(), 2)
        self.assertEqual(source.mappings_set.count(), 2)
        self.assertEqual(collection.references.count(), 2)
        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.expansion.mappings.count(), 1)

        delete_organization(0)

        self.assertTrue(Organization.objects.filter(id=org.id).exists())

        delete_organization(org.id)

        self.assertFalse(Organization.objects.filter(id=org.id).exists())
        self.assertFalse(Source.objects.filter(mnemonic='to-be-deleted-source').exists())
        self.assertFalse(Collection.objects.filter(mnemonic='to-be-deleted-coll').exists())
        self.assertFalse(Concept.objects.filter(mnemonic='to-be-deleted-concept').exists())
        self.assertFalse(Mapping.objects.filter(mnemonic='to-be-deleted-mapping').exists())
        self.assertEqual(CollectionReference.objects.count(), 0)
        self.assertEqual(Expansion.objects.count(), 0)
        delete_s3_objects_mock.assert_called()

    def test_logo_url(self):
        self.assertIsNone(Organization(logo_path=None).logo_url)
        self.assertEqual(
            Organization(logo_path='path/foo.png').logo_url.replace('https://', 'http://'),
            'http://oclapi2-dev.s3.amazonaws.com/path/foo.png'
        )


class RetirePublicEditMigrationTest(OCLTestCase):
    def test_migration_moves_public_edit_orgs_to_view(self):
        migration = importlib.import_module('core.orgs.migrations.0022_retire_public_edit_access')
        editable = OrganizationFactory()
        private = OrganizationFactory(public_access=ACCESS_TYPE_NONE)
        Organization.objects.filter(id=editable.id).update(public_access=RETIRED_ACCESS_TYPE_EDIT)

        migration.retire_public_edit_access(apps, None)

        editable.refresh_from_db()
        private.refresh_from_db()
        self.assertEqual(editable.public_access, ACCESS_TYPE_VIEW)
        self.assertEqual(private.public_access, ACCESS_TYPE_NONE)
