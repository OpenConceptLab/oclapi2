from django.db import transaction, IntegrityError
from mock import patch, Mock

from core.common.constants import HEAD
from core.common.tasks import seed_children
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.tests.factories import UserProfileFactory


class SourceTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.new_source = OrganizationSourceFactory.build(organization=None)
        self.user = UserProfileFactory()

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
        self.assertEqual(
            source.uri,
            '/users/{username}/sources/{source}/'.format(username=self.user.username, source=source.mnemonic)
        )

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

        self.new_source.name = "%s_prime" % name
        self.new_source.source_type = 'Reference'

        errors = Source.persist_changes(self.new_source, self.user, **kwargs)
        updated_source = Source.objects.get(mnemonic=self.new_source.mnemonic)

        self.assertEqual(len(errors), 0)
        self.assertEqual(updated_source.num_versions, 1)
        self.assertEqual(updated_source.head, updated_source)
        self.assertEqual(updated_source.name, self.new_source.name)
        self.assertEqual(updated_source.source_type, 'Reference')
        self.assertEqual(
            updated_source.uri,
            '/users/{username}/sources/{source}/'.format(username=self.user.username, source=updated_source.mnemonic)
        )

    def test_persist_changes_negative__repeated_mnemonic(self):
        kwargs = {
            'parent_resource': self.user
        }
        source1 = OrganizationSourceFactory(organization=None, user=self.user, mnemonic='source-1', version=HEAD)
        source2 = OrganizationSourceFactory(organization=None, user=self.user, mnemonic='source-2', version=HEAD)

        source2.mnemonic = source1.mnemonic

        with transaction.atomic():
            errors = Source.persist_changes(source2, self.user, **kwargs)
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
            '/orgs/{org}/sources/{source}/{version}/'.format(
                org=source_version.organization.mnemonic,
                source=source_version.mnemonic, version=source_version.version
            )
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

    @patch('core.common.services.S3.delete_objects', Mock())
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
        self.assertEqual(source.active_concepts, 0)

        concept = ConceptFactory(sources=[source], parent=source)
        source.save()

        self.assertEqual(source.num_concepts, 1)
        self.assertEqual(source.active_concepts, 1)
        self.assertEqual(source.last_concept_update, concept.updated_at)
        self.assertEqual(source.last_child_update, source.last_concept_update)

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


class TasksTest(OCLTestCase):
    @patch('core.common.models.ConceptContainerModel.index_children')
    @patch('core.common.tasks.export_source')
    def test_seed_children_task(self, export_source_task, index_children_mock):
        source = OrganizationSourceFactory()
        ConceptFactory(parent=source)
        MappingFactory(parent=source)

        source_v1 = OrganizationSourceFactory(organization=source.organization, version='v1', mnemonic=source.mnemonic)

        self.assertEqual(source_v1.concepts.count(), 0)
        self.assertEqual(source_v1.mappings.count(), 0)

        seed_children('source', source_v1.id, False)  # pylint: disable=no-value-for-parameter

        self.assertEqual(source_v1.concepts.count(), 1)
        self.assertEqual(source_v1.mappings.count(), 1)
        export_source_task.delay.assert_not_called()
        index_children_mock.assert_not_called()

    @patch('core.common.models.ConceptContainerModel.index_children')
    @patch('core.common.tasks.export_source')
    def test_seed_children_task_with_export(self, export_source_task, index_children_mock):
        source = OrganizationSourceFactory()
        ConceptFactory(parent=source)
        MappingFactory(parent=source)

        source_v1 = OrganizationSourceFactory(organization=source.organization, version='v1', mnemonic=source.mnemonic)

        self.assertEqual(source_v1.concepts.count(), 0)
        self.assertEqual(source_v1.mappings.count(), 0)

        seed_children('source', source_v1.id)  # pylint: disable=no-value-for-parameter

        self.assertEqual(source_v1.concepts.count(), 1)
        self.assertEqual(source_v1.mappings.count(), 1)
        export_source_task.delay.assert_called_once_with(source_v1.id)
        index_children_mock.assert_called_once()
