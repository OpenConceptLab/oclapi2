import factory
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from mock import patch, Mock, ANY, PropertyMock

from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.constants import HEAD, ACCESS_TYPE_EDIT, ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW, \
    CUSTOM_VALIDATION_SCHEMA_OPENMRS
from core.common.tasks import seed_children_to_new_version
from core.common.tasks import update_source_active_concepts_count
from core.common.tasks import update_source_active_mappings_count
from core.common.tasks import index_source_mappings, index_source_concepts
from core.common.tasks import update_validation_schema
from core.common.tests import OCLTestCase
from core.mappings.documents import MappingDocument
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, LocalizedTextFactory
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
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
            with self.assertRaises(IntegrityError):
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
        source.save()
        concept.refresh_from_db()

        self.assertFalse(source.is_active)
        self.assertFalse(concept.is_active)

        source.is_active = True
        source.save()
        concept.refresh_from_db()

        self.assertTrue(source.is_active)
        self.assertTrue(concept.is_active)

    def test_get_search_document(self):
        self.assertEqual(Source.get_search_document(), SourceDocument)

    def test_head_from_uri(self):
        source = OrganizationSourceFactory(version='HEAD')
        self.assertEqual(Source.head_from_uri('').count(), 0)
        self.assertEqual(Source.head_from_uri('foobar').count(), 0)

        queryset = Source.head_from_uri(source.uri)
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first(), source)

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
            ex.exception.message_dict, dict(hierarchy_root=['Hierarchy Root must belong to the same Source.'])
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
            'names': [LocalizedTextFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [root_concept.uri]
        })
        parentless_concept = ConceptFactory(parent=source, mnemonic='parentless')
        parentless_concept_child = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'parentless-kid',
            'parent': source,
            'names': [LocalizedTextFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parentless_concept.uri]
        })

        hierarchy = source.hierarchy()
        self.assertEqual(hierarchy, dict(id=source.mnemonic, count=2, children=ANY, offset=0, limit=100))
        hierarchy_children = hierarchy['children']
        self.assertEqual(len(hierarchy_children), 2)
        self.assertEqual(
            hierarchy_children[1],
            dict(uuid=str(root_concept.id), id=root_concept.mnemonic, url=root_concept.uri,
                 name=root_concept.display_name, children=[child_concept.uri], root=True)
        )
        self.assertEqual(
            hierarchy_children[0],
            dict(uuid=str(parentless_concept.id), id=parentless_concept.mnemonic, url=parentless_concept.uri,
                 name=parentless_concept.display_name, children=[parentless_concept_child.uri])
        )

    def test_hierarchy_without_hierarchy_root(self):
        source = OrganizationSourceFactory()
        parentless_concept = ConceptFactory(parent=source, mnemonic='parentless')
        parentless_concept_child = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': 'parentless-kid',
            'parent': source,
            'names': [LocalizedTextFactory.build(locale='en', name='English', locale_preferred=True)],
            'parent_concept_urls': [parentless_concept.uri]
        })

        hierarchy = source.hierarchy()
        self.assertEqual(hierarchy, dict(id=source.mnemonic, count=1, children=ANY, offset=0, limit=100))
        hierarchy_children = hierarchy['children']
        self.assertEqual(len(hierarchy_children), 1)
        self.assertEqual(
            hierarchy_children[0],
            dict(uuid=str(parentless_concept.id), id=parentless_concept.mnemonic, url=parentless_concept.uri,
                 name=parentless_concept.display_name, children=[parentless_concept_child.uri])
        )

    def test_is_validation_necessary(self):
        source = OrganizationSourceFactory()

        self.assertFalse(source.is_validation_necessary())

        source.custom_validation_schema = CUSTOM_VALIDATION_SCHEMA_OPENMRS

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
        resolved_source_version = Source.resolve_reference_expression('/some/url/')
        self.assertIsNone(resolved_source_version.id)
        self.assertFalse(resolved_source_version.is_fqdn)

        resolved_source_version = Source.resolve_reference_expression('/some/url/', namespace='/orgs/foo/')
        self.assertIsNone(resolved_source_version.id)
        self.assertFalse(resolved_source_version.is_fqdn)

        resolved_source_version = Source.resolve_reference_expression('https://some/url/')
        self.assertIsNone(resolved_source_version.id)
        self.assertEqual(resolved_source_version.version, '')
        self.assertTrue(resolved_source_version.is_fqdn)

        resolved_source_version = Source.resolve_reference_expression(
            'https://some/url/', namespace='/orgs/foo/')
        self.assertIsNone(resolved_source_version.id)
        self.assertTrue(resolved_source_version.is_fqdn)
        self.assertTrue(isinstance(resolved_source_version, Source))

        org = OrganizationFactory(mnemonic='org')
        OrganizationSourceFactory(
            mnemonic='source', canonical_url='https://source.org.com', organization=org)
        OrganizationSourceFactory(
            mnemonic='source', canonical_url='https://source.org.com', organization=org, version='v1.0')

        resolved_source_version = Source.resolve_reference_expression('https://source.org.com|v2.0')
        self.assertIsNone(resolved_source_version.id)
        self.assertTrue(resolved_source_version.is_fqdn)

        resolved_source_version = Source.resolve_reference_expression('https://source.org.com', version='2.0')
        self.assertIsNone(resolved_source_version.id)
        self.assertTrue(resolved_source_version.is_fqdn)

        resolved_source_version = Source.resolve_reference_expression('https://source.org.com', version='2.0')
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

        resolved_version = Source.resolve_reference_expression(
            '/orgs/org/sources/source/', version="v1.0")
        self.assertEqual(resolved_version.id, 2)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v1.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version = Source.resolve_reference_expression('/orgs/org/sources/source/')
        self.assertEqual(resolved_version.id, 3)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v2.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version = Source.resolve_reference_expression(
            '/orgs/org/sources/source/', namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 3)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v2.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version = Source.resolve_reference_expression(
            '/orgs/org/sources/source/v1.0/', namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 2)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v1.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version = Source.resolve_reference_expression(
            'https://source.org.com', version="v3.0")
        self.assertEqual(resolved_version.id, 4)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v3.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertTrue(resolved_version.is_fqdn)

        resolved_version = Source.resolve_reference_expression('https://source.org.com')
        self.assertEqual(resolved_version.id, 3)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v2.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertTrue(resolved_version.is_fqdn)

        resolved_version = Source.resolve_reference_expression(
            'https://source.org.com|v1.0', namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 2)
        self.assertTrue(isinstance(resolved_version, Source))
        self.assertEqual(resolved_version.version, 'v1.0')
        self.assertEqual(resolved_version.canonical_url, 'https://source.org.com')
        self.assertTrue(resolved_version.is_fqdn)

        resolved_version = Source.resolve_reference_expression(
            '/orgs/org/collections/collection/concepts/?q=foobar', namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 6)
        self.assertTrue(isinstance(resolved_version, Collection))
        self.assertEqual(resolved_version.version, 'v1.0')
        self.assertEqual(resolved_version.canonical_url, None)
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version = Source.resolve_reference_expression(
            '/orgs/org/collections/collection/concepts/123/', namespace='/orgs/org/', version='v2.0')
        self.assertEqual(resolved_version.id, 7)
        self.assertTrue(isinstance(resolved_version, Collection))
        self.assertEqual(resolved_version.version, 'v2.0')
        self.assertEqual(resolved_version.canonical_url, None)
        self.assertFalse(resolved_version.is_fqdn)

        resolved_version = Source.resolve_reference_expression(
            '/orgs/org/collections/collection2/', namespace='/orgs/org/')
        self.assertEqual(resolved_version.id, 8)
        self.assertTrue(isinstance(resolved_version, Collection))
        self.assertEqual(resolved_version.version, 'HEAD')
        self.assertEqual(resolved_version.canonical_url, None)
        self.assertFalse(resolved_version.is_fqdn)

    @patch('core.mappings.documents.MappingDocument.update')
    @patch('core.concepts.documents.ConceptDocument.update')
    def test_index_children(self, concept_document_update, mapping_document_update):  # pylint: disable=no-self-use
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        MappingFactory(parent=source, from_concept=concept1, to_concept=concept2)

        source.index_children()

        concept_document_update.assert_called_once_with(ANY, parallel=True)
        mapping_document_update.assert_called_once_with(ANY, parallel=True)


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
        export_source_task.delay.assert_not_called()
        index_children_mock.assert_not_called()

    @patch('core.sources.models.Source.index_children')
    @patch('core.common.tasks.export_source')
    def test_seed_children_task_with_export(self, export_source_task, index_children_mock):
        source = OrganizationSourceFactory()
        ConceptFactory(parent=source)
        MappingFactory(parent=source)

        source_v1 = OrganizationSourceFactory(organization=source.organization, version='v1', mnemonic=source.mnemonic)

        self.assertEqual(source_v1.concepts.count(), 0)
        self.assertEqual(source_v1.mappings.count(), 0)

        seed_children_to_new_version('source', source_v1.id)  # pylint: disable=no-value-for-parameter

        self.assertEqual(source_v1.concepts.count(), 1)
        self.assertEqual(source_v1.mappings.count(), 1)
        export_source_task.delay.assert_called_once_with(source_v1.id)
        index_children_mock.assert_called_once()

    def test_update_source_active_mappings_count(self):
        source = OrganizationSourceFactory()
        mapping1 = MappingFactory(parent=source)
        mapping2 = MappingFactory(retired=True, parent=source)

        self.assertEqual(source.active_mappings, None)

        update_source_active_mappings_count(source.id)

        source.refresh_from_db()
        self.assertEqual(source.active_mappings, 1)

    def test_update_source_active_concepts_count(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(retired=True, parent=source)

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
    def test_index_source_mappings(self, batch_index_mock, source_concepts_mock):
        source = OrganizationSourceFactory()
        index_source_concepts(source.id)
        batch_index_mock.assert_called_once_with(source_concepts_mock, ConceptDocument)

    @patch('core.sources.models.Source.validate_child_concepts')
    def test_update_validation_schema_success(self, validate_child_concepts_mock):
        validate_child_concepts_mock.return_value = None
        source = OrganizationSourceFactory()

        self.assertEqual(source.custom_validation_schema, None)

        update_validation_schema('source', source.id, 'OpenMRS')

        source.refresh_from_db()
        self.assertEqual(source.custom_validation_schema, 'OpenMRS')
        validate_child_concepts_mock.assert_called_once()

    @patch('core.sources.models.Source.validate_child_concepts')
    def test_update_validation_schema_failure(self, validate_child_concepts_mock):
        validate_child_concepts_mock.return_value = dict(errors='Failed')
        source = OrganizationSourceFactory()

        self.assertEqual(source.custom_validation_schema, None)
        self.assertEqual(
            update_validation_schema('source', source.id, 'OpenMRS'),
            {'failed_concept_validations': {'errors': 'Failed'}}
        )

        source.refresh_from_db()
        self.assertEqual(source.custom_validation_schema, None)
        validate_child_concepts_mock.assert_called_once()
