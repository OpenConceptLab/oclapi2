import factory
from django.core.exceptions import ValidationError
from django.db import transaction
from mock import patch, Mock, ANY, PropertyMock
from mock.mock import call

from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.constants import HEAD, ACCESS_TYPE_EDIT, ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW, \
    OPENMRS_VALIDATION_SCHEMA
from core.common.tasks import index_source_mappings, index_source_concepts
from core.common.tasks import seed_children_to_new_version
from core.common.tasks import update_source_active_concepts_count
from core.common.tasks import update_source_active_mappings_count
from core.common.tasks import update_validation_schema
from core.common.tests import OCLTestCase
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.mappings.documents import MappingDocument
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.services.storages.postgres import PostgresQL
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.url_registry.factories import OrganizationURLRegistryFactory, GlobalURLRegistryFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class SourceTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.new_source = OrganizationSourceFactory.build(organization=None)
        self.user = UserProfileFactory()

    def test_public_can_view(self):
        self.assertFalse(Source(public_access='none').public_can_view)
        self.assertFalse(Source(public_access='foobar').public_can_view)
        self.assertTrue(Source().public_can_view)  # default access_type is view
        self.assertTrue(Source(public_access='view').public_can_view)
        self.assertTrue(Source(public_access='edit').public_can_view)

    def test_public_can_edit(self):
        self.assertFalse(Source().public_can_edit)
        self.assertFalse(Source(public_access='none').public_can_edit)
        self.assertFalse(Source(public_access='foobar').public_can_edit)
        self.assertFalse(Source(public_access='view').public_can_edit)
        self.assertTrue(Source(public_access='edit').public_can_edit)

    def test_has_edit_access(self):
        admin = UserProfile.objects.get(username='ocladmin')
        source_private = OrganizationSourceFactory(public_access=ACCESS_TYPE_NONE)
        source_public_edit = OrganizationSourceFactory(public_access=ACCESS_TYPE_EDIT)
        source_public_view = OrganizationSourceFactory(public_access=ACCESS_TYPE_VIEW)

        self.assertTrue(source_public_view.has_edit_access(admin))
        self.assertTrue(source_public_edit.has_edit_access(admin))
        self.assertTrue(source_private.has_edit_access(admin))

        self.assertFalse(source_private.has_edit_access(self.user))
        self.assertFalse(source_public_view.has_edit_access(self.user))
        self.assertTrue(source_public_edit.has_edit_access(self.user))

        source_private.organization.members.add(self.user)
        self.assertTrue(source_private.has_edit_access(self.user))

        source_public_edit.organization.members.add(self.user)
        self.assertTrue(source_public_edit.has_edit_access(self.user))

        user_source_private = UserSourceFactory(public_access=ACCESS_TYPE_NONE)
        user_source_public_edit = UserSourceFactory(public_access=ACCESS_TYPE_EDIT)
        user_source_public_view = UserSourceFactory(public_access=ACCESS_TYPE_VIEW)

        self.assertTrue(user_source_private.has_edit_access(admin))
        self.assertTrue(user_source_public_view.has_edit_access(admin))
        self.assertTrue(user_source_public_edit.has_edit_access(admin))

        self.assertFalse(user_source_private.has_edit_access(self.user))
        self.assertFalse(user_source_public_view.has_edit_access(self.user))
        self.assertTrue(user_source_public_edit.has_edit_access(self.user))

        self.assertTrue(user_source_private.has_edit_access(user_source_private.parent))
        self.assertTrue(user_source_public_edit.has_edit_access(user_source_public_edit.parent))
        self.assertTrue(user_source_public_view.has_edit_access(user_source_public_view.parent))

    def test_resource_version_type(self):
        self.assertEqual(Source().resource_version_type, 'Source Version')

    def test_resource_type(self):
        self.assertEqual(Source().resource_type, 'Source')

    def test_source(self):
        self.assertEqual(Source().source, '')
        self.assertEqual(Source(mnemonic='source').source, 'source')

    def test_is_versioned(self):
        self.assertTrue(Source().is_versioned)

    def test_persist_new_positive(self):
        kwargs = {
            'parent_resource': self.user
        }
        errors = Source.persist_new(self.new_source, self.user, **kwargs)

        source = Source.objects.get(name=self.new_source.name)
        self.assertEqual(len(errors), 0)
        self.assertTrue(Source.objects.filter(name=self.new_source.name).exists())
        self.assertEqual(source.num_versions, 1)
        self.assertEqual(source.get_latest_version(), source)
        self.assertEqual(source.version, 'HEAD')
        self.assertFalse(source.released)
        self.assertEqual(source.uri, f'/users/{self.user.username}/sources/{source.mnemonic}/')

    def test_persist_new_negative__no_parent(self):
        errors = Source.persist_new(self.new_source, self.user)

        self.assertEqual(errors, {'parent': 'Parent resource cannot be None.'})
        self.assertFalse(Source.objects.filter(name=self.new_source.name).exists())

    def test_persist_new_negative__no_owner(self):
        kwargs = {
            'parent_resource': self.user
        }

        errors = Source.persist_new(self.new_source, None, **kwargs)

        self.assertEqual(errors, {'created_by': 'Creator cannot be None.'})
        self.assertFalse(Source.objects.filter(name=self.new_source.name).exists())

    def test_persist_new_negative__no_name(self):
        kwargs = {
            'parent_resource': self.user
        }
        self.new_source.name = None

        errors = Source.persist_new(self.new_source, self.user, **kwargs)

        self.assertEqual(errors, {'name': ['This field cannot be null.']})
        self.assertFalse(Source.objects.filter(name=self.new_source.name).exists())

    def test_persist_changes_positive(self):
        kwargs = {
            'parent_resource': self.user
        }
        errors = Source.persist_new(self.new_source, self.user, **kwargs)
        self.assertEqual(len(errors), 0)
        saved_source = Source.objects.get(name=self.new_source.name)

        name = saved_source.name

        self.new_source.name = f"{name}_prime"
        self.new_source.source_type = 'Reference'

        errors = Source.persist_changes(self.new_source, self.user, None, **kwargs)
        updated_source = Source.objects.get(mnemonic=self.new_source.mnemonic)

        self.assertEqual(len(errors), 0)
        self.assertEqual(updated_source.num_versions, 1)
        self.assertEqual(updated_source.head, updated_source)
        self.assertEqual(updated_source.name, self.new_source.name)
        self.assertEqual(updated_source.source_type, 'Reference')
        self.assertEqual(
            updated_source.uri,
            f'/users/{self.user.username}/sources/{updated_source.mnemonic}/'
        )

    def test_persist_changes_negative__repeated_mnemonic(self):
        kwargs = {
            'parent_resource': self.user
        }
        source1 = OrganizationSourceFactory(organization=None, user=self.user, mnemonic='source-1', version=HEAD)
        source2 = OrganizationSourceFactory(organization=None, user=self.user, mnemonic='source-2', version=HEAD)

        source2.mnemonic = source1.mnemonic

        with transaction.atomic():
            errors = Source.persist_changes(source2, self.user, None, **kwargs)
        self.assertEqual(len(errors), 1)
        self.assertTrue('__all__' in errors)

    def test_source_version_create_positive(self):
        source = OrganizationSourceFactory()
        self.assertEqual(source.num_versions, 1)

        source_version = Source(
            name='version1',
            mnemonic=source.mnemonic,
            version='version1',
            released=True,
            created_by=source.created_by,
            updated_by=source.updated_by,
            organization=source.organization
        )
        source_version.full_clean()
        source_version.save()

        self.assertEqual(source.num_versions, 2)
        self.assertEqual(source.organization.mnemonic, source_version.parent_resource)
        self.assertEqual(source.organization.resource_type, source_version.parent_resource_type)
        self.assertEqual(source_version, source.get_latest_version())
        self.assertEqual(
            source_version.uri,
            f'/orgs/{source_version.organization.mnemonic}/sources/{source_version.mnemonic}/{source_version.version}/'
        )

    def test_source_version_create_negative__same_version(self):
        source = OrganizationSourceFactory()
        self.assertEqual(source.num_versions, 1)
        OrganizationSourceFactory(
            name='version1', mnemonic=source.mnemonic, version='version1', organization=source.organization
        )
        self.assertEqual(source.num_versions, 2)

        with transaction.atomic():
            source_version = Source(
                name='version1',
                version='version1',
                mnemonic=source.mnemonic,
                organization=source.organization
            )
            with self.assertRaises(ValidationError):
                source_version.full_clean()
                source_version.save()

        self.assertEqual(source.num_versions, 2)

    def test_source_version_create_positive__same_version(self):
        source = OrganizationSourceFactory()
        self.assertEqual(source.num_versions, 1)
        OrganizationSourceFactory(
            name='version1', mnemonic=source.mnemonic, version='version1', organization=source.organization
        )
        source2 = OrganizationSourceFactory()
        self.assertEqual(source2.num_versions, 1)
        OrganizationSourceFactory(
            name='version1', mnemonic=source2.mnemonic, version='version1', organization=source2.organization
        )
        self.assertEqual(source2.num_versions, 2)

    def test_persist_new_version(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept = ConceptFactory(mnemonic='concept1', parent=source)

        self.assertEqual(source.concepts_set.count(), 2)  # parent-child
        self.assertEqual(source.concepts.count(), 2)
        self.assertEqual(concept.sources.count(), 1)
        self.assertTrue(source.is_latest_version)

        version1 = OrganizationSourceFactory.build(
            name='version1', version='v1', mnemonic=source.mnemonic, organization=source.organization
        )
        Source.persist_new_version(version1, source.created_by)
        source.refresh_from_db()

        self.assertFalse(source.is_latest_version)
        self.assertEqual(source.concepts_set.count(), 2)  # parent-child
        self.assertEqual(source.concepts.count(), 2)
        self.assertTrue(version1.is_latest_version)
        self.assertEqual(version1.concepts.count(), 1)
        self.assertEqual(version1.concepts.first(), source.concepts.filter(is_latest_version=True).first())
        self.assertEqual(version1.concepts_set.count(), 0)  # no direct child

    @patch('core.common.models.delete_s3_objects', Mock())
    def test_source_version_delete(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept = ConceptFactory(
            mnemonic='concept1', version=HEAD, sources=[source], parent=source
        )

        self.assertTrue(source.is_latest_version)
        self.assertEqual(concept.get_latest_version().sources.count(), 1)

        version1 = OrganizationSourceFactory.build(
            name='version1', version='v1', mnemonic=source.mnemonic, organization=source.organization
        )
        Source.persist_new_version(version1, source.created_by)
        source.refresh_from_db()

        self.assertEqual(concept.get_latest_version().sources.count(), 2)
        self.assertTrue(version1.is_latest_version)
        self.assertFalse(source.is_latest_version)

        source_versions = Source.objects.filter(
            mnemonic=source.mnemonic,
            version='v1',
        )
        self.assertTrue(source_versions.exists())
        self.assertEqual(version1.concepts.count(), 1)

        version1.delete()
        source.refresh_from_db()

        self.assertFalse(Source.objects.filter(
            version='v1',
            mnemonic=source.mnemonic,
        ).exists())
        self.assertTrue(source.is_latest_version)
        self.assertEqual(concept.get_latest_version().sources.count(), 1)

    def test_child_count_updates(self):
        source = OrganizationSourceFactory(version=HEAD)
        self.assertEqual(source.active_concepts, None)

        concept = ConceptFactory(sources=[source], parent=source)
        source.save()
        source.update_concepts_count()

        self.assertEqual(source.active_concepts, 1)
        self.assertEqual(source.last_concept_update, concept.updated_at)
        self.assertEqual(source.last_child_update, source.last_concept_update)

    def test_new_version_should_not_affect_last_child_update(self):
        source = OrganizationSourceFactory(version=HEAD)
        source_updated_at = source.updated_at
        source_last_child_update = source.last_child_update

        self.assertIsNotNone(source.id)
        self.assertEqual(source_updated_at, source_last_child_update)

        concept = ConceptFactory(sources=[source], parent=source)
        source.update_concepts_count()
        source.refresh_from_db()

        self.assertEqual(source.updated_at, source_updated_at)
        self.assertEqual(source.last_child_update, concept.updated_at)
        self.assertNotEqual(source.last_child_update, source_updated_at)
        self.assertNotEqual(source.last_child_update, source_last_child_update)
        source_last_child_update = source.last_child_update

        source_v1 = OrganizationSourceFactory.build(version='v1', mnemonic=source.mnemonic, organization=source.parent)
        Source.persist_new_version(source_v1, source.created_by)
        source_v1 = source.versions.filter(version='v1').first()
        source.refresh_from_db()

        self.assertIsNotNone(source_v1.id)
        self.assertEqual(source.last_child_update, source_last_child_update)
        self.assertEqual(source.updated_at, source_updated_at)

        source_v1_updated_at = source_v1.updated_at
        source_v1_last_child_update = source_v1.last_child_update

        source_v2 = OrganizationSourceFactory.build(version='v2', mnemonic=source.mnemonic, organization=source.parent)
        Source.persist_new_version(source_v2, source.created_by)
        source_v2 = source.versions.filter(version='v2').first()
        source.refresh_from_db()
        source_v1.refresh_from_db()

        self.assertIsNotNone(source_v2.id)

        self.assertEqual(source.last_child_update, source_last_child_update)
        self.assertEqual(source.updated_at, source_updated_at)
        self.assertEqual(source_v1.last_child_update, source_v1_last_child_update)
        self.assertEqual(source_v1.updated_at, source_v1_updated_at)

    def test_source_active_inactive_should_affect_children(self):
        source = OrganizationSourceFactory(is_active=True)
        concept = ConceptFactory(parent=source, is_active=True)

        source.is_active = False
        source._should_update_is_active = True  # pylint: disable=protected-access
        source.save()
        concept.refresh_from_db()

        self.assertFalse(source.is_active)
        self.assertFalse(concept.is_active)

        source.is_active = True
        source._should_update_is_active = True  # pylint: disable=protected-access
        source.save()
        concept.refresh_from_db()

        self.assertTrue(source.is_active)
        self.assertTrue(concept.is_active)

    def test_get_search_document(self):
        self.assertEqual(Source.get_search_document(), SourceDocument)

    def test_released_versions(self):
        source = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(mnemonic=source.mnemonic, organization=source.organization, version='v1')

        self.assertEqual(source.released_versions.count(), 0)

        source_v1.released = True
        source_v1.save()
        self.assertEqual(source.released_versions.count(), 1)
        self.assertEqual(source_v1.released_versions.count(), 1)

    def test_get_latest_released_version(self):
        source = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1', released=True
        )

        self.assertEqual(source.get_latest_released_version(), source_v1)

        source_v2 = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v2', released=True
        )

        self.assertEqual(source.get_latest_released_version(), source_v2)

    def test_get_version(self):
        source = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(mnemonic=source.mnemonic, organization=source.organization, version='v1')

        self.assertEqual(Source.get_version(source.mnemonic), source)
        self.assertEqual(Source.get_version(source.mnemonic, 'v1'), source_v1)

    def test_clear_processing(self):
        source = OrganizationSourceFactory(_background_process_ids=[1, 2])

        self.assertEqual(source._background_process_ids, [1, 2])  # pylint: disable=protected-access

        source.clear_processing()

        self.assertEqual(source._background_process_ids, [])  # pylint: disable=protected-access

    @patch('core.common.models.AsyncResult')
    def test_is_processing(self, async_result_klass_mock):
        source = OrganizationSourceFactory()
        self.assertFalse(source.is_processing)

        async_result_instance_mock = Mock(successful=Mock(return_value=True))
        async_result_klass_mock.return_value = async_result_instance_mock

        source._background_process_ids = [None, '']  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_processing)
        self.assertEqual(source._background_process_ids, [])  # pylint: disable=protected-access

        source._background_process_ids = ['1', '2', '3']  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_processing)
        self.assertEqual(source._background_process_ids, [])  # pylint: disable=protected-access

        async_result_instance_mock = Mock(successful=Mock(return_value=False), failed=Mock(return_value=True))
        async_result_klass_mock.return_value = async_result_instance_mock

        source._background_process_ids = [1, 2, 3]  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_processing)
        self.assertEqual(source._background_process_ids, [])  # pylint: disable=protected-access

        async_result_instance_mock = Mock(successful=Mock(return_value=False), failed=Mock(return_value=False))
        async_result_klass_mock.return_value = async_result_instance_mock

        source._background_process_ids = [1, 2, 3]  # pylint: disable=protected-access
        source.save()

        self.assertTrue(source.is_processing)
        self.assertEqual(source._background_process_ids, [1, 2, 3])  # pylint: disable=protected-access

    @patch('core.common.models.AsyncResult')
    def test_is_exporting(self, async_result_klass_mock):
        source = OrganizationSourceFactory()
        self.assertFalse(source.is_exporting)

        async_result_instance_mock = Mock(successful=Mock(return_value=True))
        async_result_klass_mock.return_value = async_result_instance_mock

        source._background_process_ids = [None, '']  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_exporting)

        source._background_process_ids = ['1', '2', '3']  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_exporting)

        async_result_instance_mock = Mock(successful=Mock(return_value=False), failed=Mock(return_value=True))
        async_result_klass_mock.return_value = async_result_instance_mock

        source._background_process_ids = [1, 2, 3]  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_exporting)

        async_result_instance_mock = Mock(successful=Mock(return_value=False), failed=Mock(return_value=False))
        async_result_instance_mock.name = 'core.common.tasks.foobar'
        async_result_klass_mock.return_value = async_result_instance_mock

        source._background_process_ids = [1, 2, 3]  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_exporting)

        async_result_instance_mock = Mock(
            name='core.common.tasks.export_source', successful=Mock(return_value=False), failed=Mock(return_value=False)
        )
        async_result_instance_mock.name = 'core.common.tasks.export_source'
        async_result_klass_mock.return_value = async_result_instance_mock

        source._background_process_ids = [1, 2, 3]  # pylint: disable=protected-access
        source.save()

        self.assertTrue(source.is_exporting)

    def test_add_processing(self):
        source = OrganizationSourceFactory()
        self.assertEqual(source._background_process_ids, [])  # pylint: disable=protected-access

        source.add_processing('123')
        self.assertEqual(source._background_process_ids, ['123'])  # pylint: disable=protected-access

        source.add_processing('123')
        self.assertEqual(source._background_process_ids, ['123', '123'])  # pylint: disable=protected-access

        source.add_processing('abc')
        self.assertEqual(source._background_process_ids, ['123', '123', 'abc'])  # pylint: disable=protected-access

        source.refresh_from_db()
        self.assertEqual(source._background_process_ids, ['123', '123', 'abc'])  # pylint: disable=protected-access

    def test_hierarchy_root(self):
        source = OrganizationSourceFactory()
        source_concept = ConceptFactory(parent=source)
        other_concept = ConceptFactory()

        source.hierarchy_root = other_concept
        with self.assertRaises(ValidationError) as ex:
            source.full_clean()
        self.assertEqual(
            ex.exception.message_dict, {'hierarchy_root': ['Hierarchy Root must belong to the same Source.']}
        )
        source.hierarchy_root = source_concept
        source.full_clean()

    def test_hierarchy_with_hierarchy_root(self):
        source = OrganizationSourceFactory()
        root_concept = ConceptFactory(parent=source, mnemonic='root')
        source.hierarchy_root = root_concept
        source.save()
        child_concept = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'root-kid',
            'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [root_concept.uri]
        })
        parentless_concept = ConceptFactory(parent=source, mnemonic='parentless')
        parentless_concept_child = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'parentless-kid',
            'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parentless_concept.uri]
        })

        hierarchy = source.hierarchy()
        self.assertEqual(hierarchy, {'id': source.mnemonic, 'count': 2, 'children': ANY, 'offset': 0, 'limit': 100})
        hierarchy_children = hierarchy['children']
        self.assertEqual(len(hierarchy_children), 2)
        self.assertEqual(
            hierarchy_children[1],
            {
                'uuid': str(root_concept.id),
                'id': root_concept.mnemonic,
                'url': root_concept.uri,
                'name': root_concept.display_name,
                'children': [child_concept.uri],
                'root': True
            }
        )
        self.assertEqual(
            hierarchy_children[0],
            {
                'uuid': str(parentless_concept.id),
                'id': parentless_concept.mnemonic,
                'url': parentless_concept.uri,
                'name': parentless_concept.display_name,
                'children': [parentless_concept_child.uri]
            }
        )

    def test_hierarchy_without_hierarchy_root(self):
        source = OrganizationSourceFactory()
        parentless_concept = ConceptFactory(parent=source, mnemonic='parentless')
        parentless_concept_child = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'parentless-kid',
            'parent': source,
            'names': [ConceptNameFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parentless_concept.uri]
        })

        hierarchy = source.hierarchy()
        self.assertEqual(hierarchy, {'id': source.mnemonic, 'count': 1, 'children': ANY, 'offset': 0, 'limit': 100})
        hierarchy_children = hierarchy['children']
        self.assertEqual(len(hierarchy_children), 1)
        self.assertEqual(
            hierarchy_children[0],
            {
                'uuid': str(parentless_concept.id),
                'id': parentless_concept.mnemonic,
                'url': parentless_concept.uri,
                'name': parentless_concept.display_name,
                'children': [parentless_concept_child.uri]
            }
        )

    def test_is_validation_necessary(self):
        source = OrganizationSourceFactory()

        self.assertFalse(source.is_validation_necessary())

        source.custom_validation_schema = OPENMRS_VALIDATION_SCHEMA

        self.assertFalse(source.is_validation_necessary())

        source.active_concepts = 1
        self.assertTrue(source.is_validation_necessary())

    @patch('core.sources.models.Source.head', new_callable=PropertyMock)
    def test_is_hierarchy_root_belonging_to_self(self, head_mock):
        root = Concept(id=1, parent_id=100)
        source = Source(id=1, hierarchy_root=root, version='HEAD')
        head_mock.return_value = source
        self.assertFalse(source.is_hierarchy_root_belonging_to_self())
        source_v1 = Source(id=1, hierarchy_root=root, version='v1')
        self.assertFalse(source_v1.is_hierarchy_root_belonging_to_self())

        root.parent_id = 1
        self.assertTrue(source.is_hierarchy_root_belonging_to_self())
        self.assertTrue(source_v1.is_hierarchy_root_belonging_to_self())

    def test_resolve_reference_expression_non_existing(self):
        resolved_source_version, _ = Source.resolve_reference_expression('/some/url/')
        self.assertIsNone(resolved_source_version.id)
        self.assertFalse(resolved_source_version.is_fqdn)

        resolved_source_version, _ = Source.resolve_reference_expression('/some/url/', namespace='/orgs/foo/')
        self.assertIsNone(resolved_source_version.id)
        self.assertFalse(resolved_source_version.is_fqdn)

        resolved_source_version, _ = Source.resolve_reference_expression('https://some/url/')
        self.assertIsNone(resolved_source_version.id)
        self.assertEqual(resolved_source_version.version, '')
        self.assertTrue(resolved_source_version.is_fqdn)

        resolved_source_version, _ = Source.resolve_reference_expression(
            'https://some/url/', namespace='/orgs/foo/')
        self.assertIsNone(resolved_source_version.id)
        self.assertTrue(resolved_source_version.is_fqdn)
        self.assertTrue(isinstance(resolved_source_version, Source))

        org = OrganizationFactory(mnemonic='org')
        OrganizationSourceFactory(
            mnemonic='source', canonical_url='https://source.org.com', organization=org)
        OrganizationSourceFactory(
            mnemonic='source', canonical_url='https://source.org.com', organization=org, version='v1.0')

        resolved_source_version, _ = Source.resolve_reference_expression('https://source.org.com|v2.0')
        self.assertIsNone(resolved_source_version.id)
        self.assertTrue(resolved_source_version.is_fqdn)

        resolved_source_version, _ = Source.resolve_reference_expression('https://source.org.com', version='2.0')
        self.assertIsNone(resolved_source_version.id)
        self.assertTrue(resolved_source_version.is_fqdn)

        resolved_source_version, _ = Source.resolve_reference_expression('https://source.org.com', version='2.0')
        self.assertIsNone(resolved_source_version.id)
        self.assertTrue(resolved_source_version.is_fqdn)

    def test_resolve_reference_expression_existing(self):  # pylint: disable=too-many-statements
        org = OrganizationFactory(mnemonic='org')
        OrganizationSourceFactory(
            id=1, mnemonic='source', canonical_url='https://source.org.com', organization=org)
        OrganizationSourceFactory(
            id=2, mnemonic='source', canonical_url='https://source.org.com', organization=org, version='v1.0',
            released=True
        )
        OrganizationSourceFactory(
            id=3, mnemonic='source', canonical_url='https://source.org.com', organization=org, version='v2.0',
            released=True
        )
        OrganizationSourceFactory(id=4, mnemonic='source', organization=org, version='v3.0',)
        OrganizationCollectionFactory(id=5, mnemonic='collection', organization=org)
        OrganizationCollectionFactory(id=6, mnemonic='collection', organization=org, version='v1.0', released=True)
        OrganizationCollectionFactory(id=7, mnemonic='collection', organization=org, version='v2.0')

        OrganizationCollectionFactory(id=8, mnemonic='collection2', organization=org)
        OrganizationCollectionFactory(id=9, mnemonic='collection2', organization=org, version='v1.0', released=False)

        resolved_version, _ = Source.resolve_reference_expression(
            '/orgs/org/sources/source/', version="v1.0")
        self.assertEqual(resolved_version.id, 2)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v1.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version, _ = Source.resolve_reference_expression('/orgs/org/sources/source/')
        self.assertEqual(resolved_version.id, 3)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v2.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version, _ = Source.resolve_reference_expression(
            '/orgs/org/sources/source/', namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 3)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v2.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version, _ = Source.resolve_reference_expression(
            '/orgs/org/sources/source/v1.0/', namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 2)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v1.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version, _ = Source.resolve_reference_expression(
            'https://source.org.com', version="v3.0")
        self.assertEqual(resolved_version.id, None)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.resolution_url, 'https://source.org.com')
        self.assertTrue(resolved_version.is_fqdn)

        resolved_version, _ = Source.resolve_reference_expression(
            'https://source.org.com', version="v3.0", namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 4)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v3.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertTrue(resolved_version.is_fqdn)

        resolved_version, _ = Source.resolve_reference_expression('https://source.org.com')
        self.assertEqual(resolved_version.id, None)

        resolved_version, _ = Source.resolve_reference_expression('https://source.org.com', namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 3)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v2.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertTrue(resolved_version.is_fqdn)

        resolved_version, _ = Source.resolve_reference_expression(
            'https://source.org.com|v1.0', namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 2)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v1.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertTrue(resolved_version.is_fqdn)

        resolved_version, _ = Source.resolve_reference_expression(
            '/orgs/org/collections/collection/concepts/?q=foobar', namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 6)
        self.assertTrue(isinstance(resolved_version, Collection))
        self.assertEqual(resolved_version.version, 'v1.0')
        self.assertEqual(resolved_version.canonical_url, None)
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version, _ = Source.resolve_reference_expression(
            '/orgs/org/collections/collection/concepts/123/', namespace='/orgs/org/', version='v2.0')
        self.assertEqual(resolved_version.id, 7)
        self.assertTrue(isinstance(resolved_version, Collection))
        self.assertEqual(resolved_version.version, 'v2.0')
        self.assertEqual(resolved_version.canonical_url, None)
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version, _ = Source.resolve_reference_expression(
            '/orgs/org/collections/collection2/', namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 8)
        self.assertTrue(isinstance(resolved_version, Collection))
        self.assertEqual(resolved_version.version, 'HEAD')
        self.assertEqual(resolved_version.canonical_url, None)
        self.assertFalse(resolved_version.is_fqdn)

    def test_resolve_reference_expression_with_canonical_url(self):  # pylint:disable=too-many-statements,too-many-locals
        org1 = OrganizationFactory(mnemonic='org1')
        org2 = OrganizationFactory(mnemonic='org2')
        org1_entry1 = OrganizationURLRegistryFactory(organization=org1, url='https://source1.com', namespace=org1.uri)
        org1_entry2 = OrganizationURLRegistryFactory(organization=org1, url='https://source2.com', namespace=org1.uri)
        org1_entry3 = OrganizationURLRegistryFactory(organization=org1, url='https://source3.com')
        org1_entry_unknown1 = OrganizationURLRegistryFactory(
            organization=org1, url='https://unknown1.com', namespace=org2.uri)
        org1_entry6 = OrganizationURLRegistryFactory(organization=org1, url='https://source6.com', namespace=org1.uri)
        global_entry1 = GlobalURLRegistryFactory(url='https://source1.com', namespace=org1.uri)
        GlobalURLRegistryFactory(url='https://source2.com', namespace=org1.uri)
        GlobalURLRegistryFactory(url='https://source3.com')
        global_entry4 = GlobalURLRegistryFactory(url='https://source4.com', namespace=org2.uri)
        global_entry6 = GlobalURLRegistryFactory(url='https://source6.com', namespace=org2.uri)
        GlobalURLRegistryFactory(url='https://unknown2.com', namespace=org2.uri)
        source1 = OrganizationSourceFactory(organization=org1, canonical_url='https://source1.com')
        source2 = OrganizationSourceFactory(organization=org1, canonical_url='https://source2.com')
        source3 = OrganizationSourceFactory(organization=org2, canonical_url='https://source3.com')
        source4 = OrganizationSourceFactory(organization=org2, canonical_url='https://source4.com')
        source5 = OrganizationSourceFactory(organization=org2, canonical_url='https://source5.com')
        source6 = OrganizationSourceFactory(organization=org2, canonical_url='https://source6.com')

        # should hit owner's url registry
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source1.com', '/orgs/org1/')
        self.assertEqual(resolved_version.id, source1.id)
        self.assertEqual(resolved_entry.relative_uri, f"/orgs/org1/url-registry/{org1_entry1.id}/")

        # should hit global url registry
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source1.com', None)
        self.assertEqual(resolved_version.id, source1.id)
        self.assertEqual(resolved_entry.relative_uri,  f"/url-registry/{global_entry1.id}/")

        # should hit global url registry
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source1.com', '/')
        self.assertEqual(resolved_version.id, source1.id)
        self.assertEqual(resolved_entry.relative_uri,  f"/url-registry/{global_entry1.id}/")

        # should hit org2 registry and then org2 repos and then global url registry
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source1.com', '/orgs/org2/')
        self.assertEqual(resolved_version.id, source1.id)
        self.assertEqual(resolved_entry.relative_uri, f"/url-registry/{global_entry1.id}/")

        # should hit org2 registry and then org2 repos
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source4.com', '/orgs/org2/')
        self.assertEqual(resolved_version.id, source4.id)
        self.assertEqual(resolved_entry, None)

        # should hit org1 registry and then org1 repos and then global url registry
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source4.com', '/orgs/org1/')
        self.assertEqual(resolved_version.id, source4.id)
        self.assertEqual(resolved_entry.relative_uri, f"/url-registry/{global_entry4.id}/")

        # should hit org2 registry and then org2 repos
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source5.com', '/orgs/org2/')
        self.assertEqual(resolved_version.id, source5.id)
        self.assertEqual(resolved_entry, None)

        # should hit org1 registry
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source2.com', '/orgs/org1/')
        self.assertEqual(resolved_version.id, source2.id)
        self.assertEqual(resolved_entry.relative_uri, f"/orgs/org1/url-registry/{org1_entry2.id}/")

        # should hit org1 registry and then org1 repos and then global registry
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source3.com', '/orgs/org1/')
        self.assertIsNone(resolved_version.id)
        self.assertEqual(resolved_entry.relative_uri, f'/orgs/org1/url-registry/{org1_entry3.id}/')

        # should hit org2 registry and then org2 repos
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source3.com', '/orgs/org2/')
        self.assertEqual(resolved_version.id, source3.id)
        self.assertEqual(resolved_entry, None)

        # should hit org2 registry and then org2 repos
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source6.com', '/orgs/org2/')
        self.assertEqual(resolved_version.id, source6.id)
        self.assertEqual(resolved_entry, None)

        # should hit global registry
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source6.com', '/')
        self.assertEqual(resolved_version.id, source6.id)
        self.assertEqual(resolved_entry.relative_uri, f"/url-registry/{global_entry6.id}/")

        # should hit org1 registry only
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source6.com', '/orgs/org1/')
        self.assertIsNone(resolved_version.id)
        self.assertEqual(resolved_entry, org1_entry6)

        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source5.com', '/')
        self.assertIsNone(resolved_version.id)
        self.assertEqual(resolved_entry, None)
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source5.com', '/orgs/org1/')
        self.assertIsNone(resolved_version.id)
        self.assertEqual(resolved_entry, None)
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://source5.com', 'foobar')
        self.assertIsNone(resolved_version.id)
        self.assertEqual(resolved_entry, None)
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://unknown1.com', '/orgs/org2/')
        self.assertIsNone(resolved_version.id)
        self.assertEqual(resolved_entry, None)
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://unknown1.com', '/orgs/org1/')
        self.assertIsNone(resolved_version.id)
        self.assertEqual(resolved_entry.relative_uri, f"/orgs/org1/url-registry/{org1_entry_unknown1.id}/")
        resolved_version, resolved_entry = Source.resolve_reference_expression('https://unknown1.com', '/')
        self.assertIsNone(resolved_version.id)
        self.assertEqual(resolved_entry, None)

    @patch('core.sources.models.Source.batch_index')
    def test_index_children(self, batch_index_mock):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        MappingFactory(parent=source, from_concept=concept1, to_concept=concept2)

        source.index_children()

        self.assertEqual(batch_index_mock.call_count, 2)

    def test_autoid_start_from_validate_non_negative(self):
        for field in [
            'autoid_concept_mnemonic_start_from', 'autoid_mapping_mnemonic_start_from',
            'autoid_concept_external_id_start_from', 'autoid_mapping_external_id_start_from',
        ]:
            with self.assertRaises(ValidationError):
                Source(**{field: -1}, mnemonic='foo', version='HEAD', name='foo').full_clean()

        for field in [
            'autoid_concept_mnemonic_start_from', 'autoid_mapping_mnemonic_start_from',
            'autoid_concept_external_id_start_from', 'autoid_mapping_external_id_start_from',
        ]:
            Source(**{field: 1}, mnemonic='foo', version='HEAD', name='foo').full_clean()

    @patch('core.services.storages.postgres.PostgresQL.create_seq')
    def test_autoid_field_changes(self, create_seq):
        org = OrganizationFactory(mnemonic='org')
        source = OrganizationSourceFactory(mnemonic='sequence', organization=org)
        self.assertEqual(source.autoid_concept_mnemonic, None)

        source.autoid_concept_mnemonic = 'sequential'
        source.autoid_concept_mnemonic_start_from = 100
        source.save()

        self.assertEqual(source.autoid_concept_mnemonic, 'sequential')
        self.assertEqual(source.autoid_concept_mnemonic_start_from, 100)
        create_seq.assert_called_once_with(
            '_orgs_org_sources_sequence__concepts_mnemonic_seq', 'sources.uri', 0, 100
        )

    def test_get_mapped_sources(self):
        source = OrganizationSourceFactory(mnemonic='subject')
        source1 = OrganizationSourceFactory(mnemonic='source1')
        source2 = OrganizationSourceFactory(mnemonic='source2')
        source3 = OrganizationSourceFactory(mnemonic='source3')
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source1)
        concept3 = ConceptFactory(parent=source1)
        concept4 = ConceptFactory(parent=source2)
        concept5 = ConceptFactory(parent=source3)
        # self
        MappingFactory(
            parent=source, from_concept=concept1, to_concept=concept1,
            from_source=concept1.parent, to_source=concept1.parent
        )

        mapped_sources = source.get_mapped_sources()

        self.assertEqual(mapped_sources.count(), 0)

        # direct
        MappingFactory(
            parent=source, from_concept=concept1, to_concept=concept2,
            from_source=concept1.parent, to_source=concept2.parent
        )
        # reverse
        MappingFactory(
            parent=source, from_concept=concept4, to_concept=concept1,
            from_source=concept4.parent, to_source=concept1.parent
        )
        # other source's mapping
        MappingFactory(
            parent=source1, from_concept=concept1, to_concept=concept3,
            from_source=concept1.parent, to_source=concept3.parent
        )
        # other source's mapping
        MappingFactory(
            parent=source3, from_concept=concept5, to_concept=concept1,
            from_source=concept5.parent, to_source=concept1.parent
        )
        # Mapping with unknown source
        MappingFactory(
            parent=source, from_concept=concept1, to_concept=None, to_concept_name='concept-unknown',
            from_source=concept1.parent, to_source=None
        )

        mapped_sources = source.get_mapped_sources()

        self.assertEqual(mapped_sources.count(), 1)
        self.assertEqual(mapped_sources.first().url, source1.url)

    def test_clone_with_cascade(self):  # pylint: disable=too-many-locals,too-many-statements
        """
            test_clone_with_cascade
            source1: cloneFrom
                - concept1
                - concept2
                - concept3
                - concept4
                - mapping -> concept1 -> Q-AND-A -> concept3
                - mapping -> concept2 -> Q-AND-A -> concept1
                - mapping -> concept2 -> NARROWER-THAN -> concept3
                - mapping -> concept2 -> BROADER-THAN -> concept4
            source2: cloneTo
                - concept1
                - concept3
                - mapping -> source2.concept1 -> SAME-AS -> source1.concept1
                - mapping -> source2.concept3 -> SAME-AS -> source1.concept3

            --CLONE source1.concept2 in source2--

            source2:
                - (old) concept1
                - (old) concept3
                - (old) mapping -> source2.concept1 -> SAME-AS -> source1.concept1
                - (old) mapping -> source2.concept3 -> SAME-AS -> source1.concept3

                - (new) concept2 (clone of source1.concept2)
                - (new) mapping -> source2.concept2 -> SAME-AS -> source1.concept2
                - (new) mapping -> source2.concept2 -> Q-AND-A -> source2.concept1
                - (new) mapping -> source2.concept2 -> NARROWER-THAN -> source2.concept3
                - (new) mapping -> source2.concept2 -> BROADER-THAN -> source1.concept4
        """
        source1 = OrganizationSourceFactory(mnemonic='source1')
        source1_concept1 = ConceptFactory(
            mnemonic='concept1', parent=source1, names=[ConceptNameFactory.build(name='concept1')])  # to_concept
        source1_concept2 = ConceptFactory(
            mnemonic='concept2', parent=source1, names=[ConceptNameFactory.build(name='concept2')])  # from_concept
        source1_concept3 = ConceptFactory(
            mnemonic='concept3', parent=source1, names=[ConceptNameFactory.build(name='concept3')])
        source1_concept4 = ConceptFactory(
            mnemonic='concept4', parent=source1, names=[ConceptNameFactory.build(name='concept4')])
        MappingFactory(
            from_concept=source1_concept1, to_concept=source1_concept3, parent=source1, map_type='Q-AND-A')
        MappingFactory(
            from_concept=source1_concept2, to_concept=source1_concept1, parent=source1, map_type='Q-AND-A')
        MappingFactory(
            from_concept=source1_concept2, to_concept=source1_concept3, parent=source1, map_type='NARROWER-THAN')
        MappingFactory(
            from_concept=source1_concept2, to_concept=source1_concept4, parent=source1, map_type='BROADER-THAN')

        source2 = OrganizationSourceFactory(mnemonic='source2')
        # same as source1_concept1 -> to_concept
        source2_concept1 = ConceptFactory(
            mnemonic='concept1', parent=source2, names=[ConceptNameFactory.build(name='concept1')])
        # same as source1_concept3
        source2_concept3 = ConceptFactory(
            mnemonic='concept3', parent=source2, names=[ConceptNameFactory.build(name='concept3')])
        MappingFactory(
            from_concept=source2_concept1, to_concept=source1_concept1, parent=source2, map_type='SAME-AS')
        MappingFactory(
            from_concept=source2_concept3, to_concept=source1_concept3, parent=source2, map_type='SAME-AS')

        self.assertEqual(source2.get_active_concepts().count(), 2)
        self.assertEqual(source2.get_active_mappings().count(), 2)

        added_concepts, added_mappings = source2.clone_with_cascade(
            concept_to_clone=source1_concept2,
            user=source1_concept2.created_by,
            map_types='Q-AND-A,CONCEPT-SET',
            equivalency_map_types='SAME-AS'
        )

        self.assertEqual(len(added_concepts), 1)
        self.assertEqual(len(added_mappings), 4)
        self.assertEqual(source2.get_active_concepts().count(), 3)
        self.assertEqual(source2.get_active_mappings().count(), 6)
        source2_concepts = source2.get_concepts_queryset().order_by('created_at')
        self.assertEqual(
            list(source2_concepts.values_list('mnemonic', flat=True)),
            ['concept1', 'concept3', ANY]
        )
        self.assertNotEqual(source2_concepts.last().mnemonic, 'concept2')
        self.assertEqual(
            [concept.display_name for concept in source2_concepts],
            ['concept1', 'concept3', 'concept2']
        )
        mappings = source2.get_mappings_queryset()
        self.assertEqual(mappings.count(), 6)

        same_as_mapping = mappings.filter(map_type='SAME-AS', to_concept_code='concept2').first()
        self.assertEqual(same_as_mapping.to_concept.uri, source1_concept2.uri)
        new_from_concept = same_as_mapping.from_concept
        self.assertNotEqual(new_from_concept.mnemonic, source1_concept2.mnemonic)
        self.assertTrue(new_from_concept.display_name == source1_concept2.display_name == 'concept2')

        q_and_a_mapping = mappings.filter(map_type='Q-AND-A').first()
        self.assertEqual(q_and_a_mapping.from_concept.uri, new_from_concept.uri)
        self.assertEqual(q_and_a_mapping.to_concept.uri, source2_concept1.uri)

        narrower_than_mapping = mappings.filter(map_type='NARROWER-THAN').first()
        self.assertEqual(narrower_than_mapping.from_concept.uri, new_from_concept.uri)
        self.assertEqual(narrower_than_mapping.to_concept.uri, source2_concept3.uri)

        broader_than_mapping = mappings.filter(map_type='BROADER-THAN').first()
        self.assertEqual(broader_than_mapping.from_concept.uri, new_from_concept.uri)
        self.assertEqual(broader_than_mapping.to_concept.uri, source1_concept4.uri)

        added_concepts, added_mappings = source2.clone_with_cascade(
            concept_to_clone=source1_concept2,
            user=source1_concept2.created_by,
            map_types='Q-AND-A,CONCEPT-SET',
            equivalency_map_types='SAME-AS'
        )

        self.assertEqual(len(added_concepts), 0)
        self.assertEqual(len(added_mappings), 0)
        self.assertEqual(source2.get_active_concepts().count(), 3)
        self.assertEqual(source2.get_active_mappings().count(), 6)

        result = source1_concept2.cascade(
            repo_version=source1, omit_if_exists_in=source2.uri, equivalency_map_types='SAME-AS'
        )
        self.assertEqual(result['concepts'].count(), 1)
        self.assertEqual(result['concepts'].first(), source1_concept2)
        self.assertEqual(result['mappings'].count(), 0)

        result = source1_concept2.cascade_as_hierarchy(
            repo_version=source1, omit_if_exists_in=source2.uri, equivalency_map_types='SAME-AS'
        )
        self.assertEqual(result, source1_concept2)
        self.assertEqual(result.cascaded_entries['concepts'].count(), 0)
        self.assertEqual(result.cascaded_entries['mappings'].count(), 0)

    def test_clone_with_cascade_with_autoid_sequence_manual_set(self):  # pylint: disable=too-many-locals,too-many-statements
        source1 = OrganizationSourceFactory(mnemonic='source1')
        source1_concept1 = ConceptFactory(
            mnemonic='concept1', parent=source1, names=[ConceptNameFactory.build(name='concept1')])  # to_concept
        source1_concept2 = ConceptFactory(
            mnemonic='concept2', parent=source1, names=[ConceptNameFactory.build(name='concept2')])  # from_concept
        source1_concept3 = ConceptFactory(
            mnemonic='concept3', parent=source1, names=[ConceptNameFactory.build(name='concept3')])
        source1_concept4 = ConceptFactory(
            mnemonic='concept4', parent=source1, names=[ConceptNameFactory.build(name='concept4')])
        MappingFactory(
            from_concept=source1_concept1, to_concept=source1_concept3, parent=source1, map_type='Q-AND-A')
        MappingFactory(
            from_concept=source1_concept2, to_concept=source1_concept1, parent=source1, map_type='Q-AND-A')
        MappingFactory(
            from_concept=source1_concept2, to_concept=source1_concept3, parent=source1, map_type='NARROWER-THAN')
        MappingFactory(
            from_concept=source1_concept2, to_concept=source1_concept4, parent=source1, map_type='BROADER-THAN')

        source2 = OrganizationSourceFactory(
            mnemonic='source2', autoid_concept_mnemonic='sequential', autoid_mapping_mnemonic='sequential')
        # same as source1_concept1 -> to_concept
        source2_concept1 = ConceptFactory(
            mnemonic='1', parent=source2, names=[ConceptNameFactory.build(name='concept1')])
        # same as source1_concept3
        source2_concept3 = ConceptFactory(
            mnemonic='concept3', parent=source2, names=[ConceptNameFactory.build(name='concept3')])
        MappingFactory(
            mnemonic='1', from_concept=source2_concept1, to_concept=source1_concept1, parent=source2,
            map_type='SAME-AS')
        MappingFactory(
            mnemonic='2', from_concept=source2_concept3, to_concept=source1_concept3, parent=source2,
            map_type='SAME-AS')

        self.assertEqual(source2.get_active_concepts().count(), 2)
        self.assertEqual(source2.get_active_mappings().count(), 2)
        self.assertEqual(PostgresQL.last_value(source2.concepts_mnemonic_seq_name), 1)  # dint update the sequence since the concept mnemonic was never provided  # pylint: disable=line-too-long
        self.assertEqual(PostgresQL.last_value(source2.mappings_mnemonic_seq_name), 1)  # dint update the sequence since the mapping mnemonic was never provided  # pylint: disable=line-too-long
        self.assertEqual(
            list(source2.get_concepts_queryset().order_by('created_at').values_list('mnemonic', flat=True)),
            ['1', 'concept3']
        )
        self.assertEqual(
            list(source2.get_mappings_queryset().order_by('created_at').values_list('mnemonic', flat=True)),
            ['1', '2']
        )

        added_concepts, added_mappings = source2.clone_with_cascade(
            concept_to_clone=source1_concept2,
            user=source1_concept2.created_by,
            map_types='Q-AND-A,CONCEPT-SET',
            equivalency_map_types='SAME-AS'
        )

        self.assertEqual(len(added_concepts), 1)
        self.assertEqual(len(added_mappings), 4)
        self.assertEqual(source2.get_active_concepts().count(), 3)
        self.assertEqual(source2.get_active_mappings().count(), 6)
        source2_concepts = source2.get_concepts_queryset().order_by('created_at')
        self.assertEqual(
            list(source2_concepts.values_list('mnemonic', flat=True)),
            ['1', 'concept3', '2']
        )
        self.assertNotEqual(source2_concepts.last().mnemonic, 'concept2')
        self.assertEqual(
            [concept.display_name for concept in source2_concepts],
            ['concept1', 'concept3', 'concept2']
        )
        mappings = source2.get_mappings_queryset().order_by('created_at')
        self.assertEqual(mappings.count(), 6)
        self.assertEqual(
            list(mappings.values_list('mnemonic', flat=True)),
            ['1', '2', '3', '4', '5', '6']
        )

        same_as_mapping = mappings.filter(map_type='SAME-AS', to_concept_code='concept2').first()
        self.assertEqual(same_as_mapping.to_concept.uri, source1_concept2.uri)
        new_from_concept = same_as_mapping.from_concept
        self.assertNotEqual(new_from_concept.mnemonic, source1_concept2.mnemonic)
        self.assertTrue(new_from_concept.display_name == source1_concept2.display_name == 'concept2')

        q_and_a_mapping = mappings.filter(map_type='Q-AND-A').first()
        self.assertEqual(q_and_a_mapping.from_concept.uri, new_from_concept.uri)
        self.assertEqual(q_and_a_mapping.to_concept.uri, source2_concept1.uri)

        narrower_than_mapping = mappings.filter(map_type='NARROWER-THAN').first()
        self.assertEqual(narrower_than_mapping.from_concept.uri, new_from_concept.uri)
        self.assertEqual(narrower_than_mapping.to_concept.uri, source2_concept3.uri)

        broader_than_mapping = mappings.filter(map_type='BROADER-THAN').first()
        self.assertEqual(broader_than_mapping.from_concept.uri, new_from_concept.uri)
        self.assertEqual(broader_than_mapping.to_concept.uri, source1_concept4.uri)

        added_concepts, added_mappings = source2.clone_with_cascade(
            concept_to_clone=source1_concept2,
            user=source1_concept2.created_by,
            map_types='Q-AND-A,CONCEPT-SET',
            equivalency_map_types='SAME-AS'
        )

        self.assertEqual(len(added_concepts), 0)
        self.assertEqual(len(added_mappings), 0)
        self.assertEqual(source2.get_active_concepts().count(), 3)
        self.assertEqual(source2.get_active_mappings().count(), 6)

        result = source1_concept2.cascade(
            repo_version=source1, omit_if_exists_in=source2.uri, equivalency_map_types='SAME-AS'
        )
        self.assertEqual(result['concepts'].count(), 1)
        self.assertEqual(result['concepts'].first(), source1_concept2)
        self.assertEqual(result['mappings'].count(), 0)

        result = source1_concept2.cascade_as_hierarchy(
            repo_version=source1, omit_if_exists_in=source2.uri, equivalency_map_types='SAME-AS'
        )
        self.assertEqual(result, source1_concept2)
        self.assertEqual(result.cascaded_entries['concepts'].count(), 0)
        self.assertEqual(result.cascaded_entries['mappings'].count(), 0)


class TasksTest(OCLTestCase):
    @patch('core.sources.models.Source.index_children')
    @patch('core.common.tasks.export_source')
    def test_seed_children_task(self, export_source_task, index_children_mock):
        source = OrganizationSourceFactory()
        ConceptFactory(parent=source)
        MappingFactory(parent=source)

        source_v1 = OrganizationSourceFactory(organization=source.organization, version='v1', mnemonic=source.mnemonic)

        self.assertEqual(source_v1.concepts.count(), 0)
        self.assertEqual(source_v1.mappings.count(), 0)

        seed_children_to_new_version('source', source_v1.id, False)  # pylint: disable=no-value-for-parameter

        self.assertEqual(source_v1.concepts.count(), 1)
        self.assertEqual(source_v1.mappings.count(), 1)
        export_source_task.apply_async.assert_not_called()
        index_children_mock.assert_called_once_with(sync=False, user=source_v1.created_by)

    @patch('core.sources.models.Source.index_children')
    @patch('core.common.tasks.export_source')
    def test_seed_children_task_with_export(self, export_source_task, index_children_mock):
        export_source_task.__name__ = 'export_source'
        source = OrganizationSourceFactory()
        ConceptFactory(parent=source)
        MappingFactory(parent=source)

        source_v1 = OrganizationSourceFactory(organization=source.organization, version='v1', mnemonic=source.mnemonic)

        self.assertEqual(source_v1.concepts.count(), 0)
        self.assertEqual(source_v1.mappings.count(), 0)

        seed_children_to_new_version('source', source_v1.id)  # pylint: disable=no-value-for-parameter

        self.assertEqual(source_v1.concepts.count(), 1)
        self.assertEqual(source_v1.mappings.count(), 1)
        export_source_task.apply_async.assert_called_once_with(
            (source_v1.id,), task_id=ANY, queue='default', persist_args=True)
        index_children_mock.assert_called_once()

    @patch('core.common.tasks.export_source')
    @patch('core.sources.models.index_source_mappings')
    @patch('core.sources.models.index_source_concepts')
    def test_seed_children_to_first_released_version_should_index_children(
            self, index_source_concepts_task_mock, index_source_mappings_task_mock, export_source_task_mock
    ):
        export_source_task_mock.__name__ = 'export_source'
        index_source_concepts_task_mock.__name__ = 'index_source_concepts'
        index_source_mappings_task_mock.__name__ = 'index_source_mappings'

        source = OrganizationSourceFactory()
        ConceptFactory(parent=source)
        MappingFactory(parent=source)

        source_v1 = OrganizationSourceFactory(
            organization=source.organization, version='v1', mnemonic=source.mnemonic, released=True)

        self.assertEqual(source_v1.concepts.count(), 0)
        self.assertEqual(source_v1.mappings.count(), 0)

        seed_children_to_new_version('source', source_v1.id)  # pylint: disable=no-value-for-parameter

        self.assertEqual(source_v1.concepts.count(), 1)
        self.assertEqual(source_v1.mappings.count(), 1)

        export_source_task_mock.apply_async.assert_called_once_with(
            (source_v1.id,), queue='default', persist_args=True, task_id=ANY)
        index_source_concepts_task_mock.apply_async.assert_called_once_with(
            (source_v1.id,), queue='indexing', persist_args=True, task_id=ANY)
        index_source_mappings_task_mock.apply_async.assert_called_once_with(
            (source_v1.id,), queue='indexing', persist_args=True, task_id=ANY)

    @patch('core.common.tasks.export_source')
    @patch('core.sources.models.index_source_mappings')
    @patch('core.sources.models.index_source_concepts')
    def test_seed_children_to_new_second_released_version_should_index_children_of_new_and_prev_released_version(
            self, index_source_concepts_task_mock, index_source_mappings_task_mock, export_source_task_mock
    ):
        export_source_task_mock.__name__ = 'export_source'
        index_source_concepts_task_mock.__name__ = 'index_source_concepts'
        index_source_mappings_task_mock.__name__ = 'index_source_mappings'

        source = OrganizationSourceFactory()
        ConceptFactory(parent=source)
        MappingFactory(parent=source)

        source_v1 = OrganizationSourceFactory(
            organization=source.organization, version='v1', mnemonic=source.mnemonic, released=True)

        source_v2 = OrganizationSourceFactory(
            organization=source.organization, version='v2', mnemonic=source.mnemonic, released=True)

        self.assertEqual(source_v2.concepts.count(), 0)
        self.assertEqual(source_v2.mappings.count(), 0)

        seed_children_to_new_version('source', source_v2.id)  # pylint: disable=no-value-for-parameter

        self.assertEqual(source_v2.concepts.count(), 1)
        self.assertEqual(source_v2.mappings.count(), 1)

        export_source_task_mock.apply_async.assert_called_once_with(
            (source_v2.id,), queue='default', persist_args=True, task_id=ANY)
        self.assertEqual(
            index_source_concepts_task_mock.apply_async.mock_calls,
            [
                call((source_v1.id,), queue='indexing', persist_args=True, task_id=ANY),
                call((source_v2.id,), queue='indexing', persist_args=True, task_id=ANY)
            ])
        self.assertEqual(
            index_source_mappings_task_mock.apply_async.mock_calls,
            [
                call((source_v1.id,), queue='indexing', persist_args=True, task_id=ANY),
                call((source_v2.id,), queue='indexing', persist_args=True, task_id=ANY)
            ])

    def test_update_source_active_mappings_count(self):
        source = OrganizationSourceFactory()
        MappingFactory(parent=source)
        MappingFactory(retired=True, parent=source)

        self.assertEqual(source.active_mappings, None)

        update_source_active_mappings_count(source.id)

        source.refresh_from_db()
        self.assertEqual(source.active_mappings, 1)

    def test_update_source_active_concepts_count(self):
        source = OrganizationSourceFactory()
        ConceptFactory(parent=source)
        ConceptFactory(retired=True, parent=source)

        self.assertEqual(source.active_concepts, None)

        update_source_active_concepts_count(source.id)

        source.refresh_from_db()
        self.assertEqual(source.active_concepts, 1)

    @patch('core.sources.models.Source.mappings')
    @patch('core.sources.models.Source.batch_index')
    def test_index_source_mappings(self, batch_index_mock, source_mappings_mock):
        source = OrganizationSourceFactory()
        index_source_mappings(source.id)
        batch_index_mock.assert_called_once_with(source_mappings_mock, MappingDocument)

    @patch('core.sources.models.Source.concepts')
    @patch('core.sources.models.Source.batch_index')
    def test_index_source_concepts(self, batch_index_mock, source_concepts_mock):
        source = OrganizationSourceFactory()
        index_source_concepts(source.id)
        batch_index_mock.assert_called_once_with(source_concepts_mock, ConceptDocument)

    @patch('core.sources.models.Source.validate_child_concepts')
    def test_update_validation_schema_success(self, validate_child_concepts_mock):
        validate_child_concepts_mock.return_value = None
        source = OrganizationSourceFactory()

        self.assertEqual(source.custom_validation_schema, 'None')

        update_validation_schema('source', source.id, 'OpenMRS')

        source.refresh_from_db()
        self.assertEqual(source.custom_validation_schema, 'OpenMRS')
        validate_child_concepts_mock.assert_called_once()

    @patch('core.sources.models.Source.validate_child_concepts')
    def test_update_validation_schema_failure(self, validate_child_concepts_mock):
        validate_child_concepts_mock.return_value = {'errors': 'Failed'}
        source = OrganizationSourceFactory()

        self.assertEqual(source.custom_validation_schema, 'None')
        self.assertEqual(
            update_validation_schema('source', source.id, 'OpenMRS'),
            {'failed_concept_validations': {'errors': 'Failed'}}
        )

        source.refresh_from_db()
        self.assertEqual(source.custom_validation_schema, 'None')
        validate_child_concepts_mock.assert_called_once()
