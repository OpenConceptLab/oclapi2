from types import SimpleNamespace

import factory
from celery_once import AlreadyQueued
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import override_settings
from mock import patch, Mock, ANY, PropertyMock, call
from rest_framework import status
from rest_framework.response import Response

from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.constants import HEAD, ACCESS_TYPE_EDIT, ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW, \
    OPENMRS_VALIDATION_SCHEMA
from core.common.tasks import index_source_mappings, index_source_concepts
from core.common.tasks import seed_children_to_new_version
from core.common.tasks import update_source_active_concepts_count
from core.common.tasks import update_source_active_mappings_count
from core.common.tasks import update_validation_schema
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.services.storages.postgres import PostgresQL
from core.sources.constants import AUTO_ID_SEQUENTIAL
from core.sources.documents import SourceDocument
from core.sources.models import Source, CloneError
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.tasks.models import Task
from core.url_registry.factories import OrganizationURLRegistryFactory, GlobalURLRegistryFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class SourceViewsAPITest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.admin_token = self.admin.get_token()

    def test_verify_scope_no_kwargs_non_get_raises_404(self):
        response = self.client.post('/sources/', {}, format='json')
        self.assertEqual(response.status_code, 404)

    def test_verify_scope_no_owner_scope_raises_404(self):
        response = self.client.get('/sources/some-source/')
        self.assertEqual(response.status_code, 404)

    def test_logo_view_get_permission(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get(f'{source.uri}logo/')
        self.assertIn(response.status_code, [200, 400, 404, 405])

    @patch('core.common.tasks.update_source_active_mappings_count.apply_async')
    @patch('core.common.tasks.update_source_active_concepts_count.apply_async')
    def test_get_object_updates_active_counts_when_not_test_mode(
            self, update_concepts_apply_async_mock, update_mappings_apply_async_mock):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        with override_settings(TEST_MODE=False):
            response = self.client.get(source.uri, HTTP_AUTHORIZATION=f"Token {self.admin_token}")
        self.assertEqual(response.status_code, 200)
        update_concepts_apply_async_mock.assert_called()
        update_mappings_apply_async_mock.assert_called()

    def test_delete_source_failure(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        with patch('core.sources.views.delete_source') as delete_source_mock:
            delete_source_mock.return_value = False
            response = self.client.delete(source.uri, HTTP_AUTHORIZATION=f"Token {self.admin_token}")
        self.assertEqual(response.status_code, 400)

    def test_versions_list_brief(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get(
            f'{source.uri}versions/?brief=true', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_versions_list_released_filter(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get(
            f'{source.uri}versions/?released=true', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_latest_version_not_found_404(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get(
            f'{source.uri}latest/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    @patch('core.common.tasks.update_source_active_mappings_count.apply_async')
    @patch('core.common.tasks.update_source_active_concepts_count.apply_async')
    def test_version_get_object_updates_active_counts_when_not_test_mode(
            self, update_concepts_apply_async_mock, update_mappings_apply_async_mock):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        with override_settings(TEST_MODE=False):
            response = self.client.get(version.uri, HTTP_AUTHORIZATION=f"Token {self.admin_token}")
        self.assertEqual(response.status_code, 200)
        update_concepts_apply_async_mock.assert_called()
        update_mappings_apply_async_mock.assert_called()

    def test_version_update_sets_external_id_from_version_external_id(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        response = self.client.put(
            version.uri, {'version_external_id': 'ext-1'}, format='json',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)
        version.refresh_from_db()
        self.assertEqual(version.external_id, 'ext-1')

    def test_version_delete_validation_error(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        with patch('core.sources.models.Source.delete') as delete_mock:
            delete_mock.side_effect = ValidationError({'__all__': ['cannot delete']})
            response = self.client.delete(version.uri, HTTP_AUTHORIZATION=f"Token {self.admin_token}")
        self.assertEqual(response.status_code, 400)

    def test_properties_and_filters_list(self):
        source = OrganizationSourceFactory(
            created_by=self.admin, updated_by=self.admin, properties=[{'code': 'p1'}], filters=[{'code': 'f1'}]
        )
        response = self.client.get(
            f'{source.uri}properties/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [{'code': 'p1'}])

        response = self.client.get(
            f'{source.uri}filters/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [{'code': 'f1'}])

    def test_version_properties_and_filters_list(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1',
            properties=[{'code': 'p1'}], filters=[{'code': 'f1'}],
            created_by=self.admin, updated_by=self.admin
        )
        response = self.client.get(
            f'{version.uri}properties/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [{'code': 'p1'}])

        response = self.client.get(
            f'{version.uri}filters/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [{'code': 'f1'}])

    def test_source_summary_distribution(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get(
            f'{source.uri}summary/?verbose=true&distribution=datatype',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_source_version_summary_distribution(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        response = self.client.get(
            f'{version.uri}summary/?verbose=true&distribution=datatype',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_source_latest_version_summary_distribution(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1', released=True,
            created_by=self.admin, updated_by=self.admin
        )
        response = self.client.get(
            f'{source.uri}latest/summary/?verbose=true&distribution=datatype',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_source_latest_version_summary_not_found_404(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get(
            f'{source.uri}latest/summary/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_versions_diff_auto_swaps_older_and_newer(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        newer = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v-newer',
            created_by=self.admin, updated_by=self.admin
        )
        older = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v-older',
            created_by=self.admin, updated_by=self.admin
        )
        from django.utils import timezone
        import datetime
        Source.objects.filter(id=newer.id).update(created_at=timezone.now())
        Source.objects.filter(id=older.id).update(created_at=timezone.now() - datetime.timedelta(days=1))

        response = self.client.post(
            '/sources/$compare/', {'version1': newer.uri, 'version2': older.uri}, format='json',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_versions_diff_invalid_verbosity_defaults_to_zero(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        response = self.client.post(
            '/sources/$compare/?verbosity=not-a-number', {'version1': source.uri, 'version2': version.uri},
            format='json', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_summary_put_permission_denied_without_edit_access(self):
        source = OrganizationSourceFactory(
            created_by=self.admin, updated_by=self.admin, public_access=ACCESS_TYPE_VIEW
        )
        user = UserProfileFactory()

        response = self.client.put(
            f'{source.uri}summary/', {}, format='json', HTTP_AUTHORIZATION=f"Token {user.get_token()}"
        )

        self.assertEqual(response.status_code, 403)

    def test_versions_diff_already_queued_response(self):
        source = OrganizationSourceFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        with patch(
                'core.tasks.mixins.TaskMixin.perform_task',
                return_value=Response({'detail': 'Already Queued'}, status=status.HTTP_409_CONFLICT)):
            response = self.client.post(
                '/sources/$compare/', {'version1': source.uri, 'version2': version.uri}, format='json',
                HTTP_AUTHORIZATION=f"Token {self.admin_token}"
            )
        self.assertEqual(response.status_code, 409)


class SourceSerializersTest(OCLTestCase):
    @staticmethod
    def _request(query_string=''):
        from django.http import QueryDict
        request = Mock()
        request.query_params = QueryDict(query_string)
        request.path = '/sources/'
        return request

    def test_source_list_get_summary(self):
        from core.sources.serializers import SourceListSerializer
        source = OrganizationSourceFactory()

        serializer_included = SourceListSerializer(context={'request': self._request('includeSummary=true')})
        self.assertIsNotNone(serializer_included.get_summary(source))

        serializer_excluded = SourceListSerializer()
        self.assertIsNone(serializer_excluded.get_summary(source))

    def test_source_version_list_init_and_external_exports(self):
        from core.sources.serializers import SourceVersionListSerializer
        source = OrganizationSourceFactory()

        serializer = SourceVersionListSerializer(context={'request': self._request('includeExternalExports=true')})
        self.assertEqual(serializer.get_external_exports(source), [])

    def test_prepare_object_supported_locales_as_comma_separated_string(self):
        from core.sources.serializers import SourceCreateSerializer
        serializer = SourceCreateSerializer()
        source = serializer.prepare_object(
            {'mnemonic': 'source-locales', 'name': 'Source Locales', 'supported_locales': 'en,es,fr'})
        self.assertEqual(source.supported_locales, ['en', 'es', 'fr'])

    def test_create_serializer_validate_invalid_released_value(self):
        from core.sources.serializers import SourceCreateSerializer
        serializer = SourceCreateSerializer(data={
            'id': 'source-invalid-released', 'name': 'Source Invalid Released', 'released': 'notabool'
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('released', serializer.errors)

    def test_get_client_configs(self):
        from core.sources.serializers import SourceDetailSerializer
        source = OrganizationSourceFactory()

        serializer_included = SourceDetailSerializer(
            context={'request': self._request('includeClientConfigs=true')})
        self.assertEqual(serializer_included.get_client_configs(source), [])

        serializer_excluded = SourceDetailSerializer()
        self.assertIsNone(serializer_excluded.get_client_configs(source))

    def test_get_hierarchy_root(self):
        from core.sources.serializers import SourceDetailSerializer
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source)
        source.hierarchy_root = concept

        serializer_included = SourceDetailSerializer(
            context={'request': self._request('includeHierarchyRoot=true')})
        self.assertIsNotNone(serializer_included.get_hierarchy_root(source))

        serializer_excluded = SourceDetailSerializer()
        self.assertIsNone(serializer_excluded.get_hierarchy_root(source))

    def test_version_get_states_included(self):
        from core.sources.serializers import SourceVersionDetailSerializer
        source = OrganizationSourceFactory()

        serializer = SourceVersionDetailSerializer(context={'request': self._request('includeStates=true')})
        self.assertEqual(serializer.get_states(source), source.states)

    def test_version_get_tasks_included(self):
        from core.sources.serializers import SourceVersionDetailSerializer
        source = OrganizationSourceFactory()

        serializer = SourceVersionDetailSerializer(context={'request': self._request('includeTasks=true')})
        self.assertEqual(serializer.get_tasks(source), source.get_tasks_info())


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

    @patch('core.common.models.cache')
    def test_clear_concepts_cache(self, cache_mock):
        source = OrganizationSourceFactory()
        cache_mock.make_key.side_effect = lambda key: key

        source.clear_concepts_cache()

        cache_mock.client.get_client.return_value.delete.assert_called_once_with(*source.get_concepts_cache_keys())

    @patch('core.common.models.cache')
    def test_clear_mappings_cache(self, cache_mock):
        source = OrganizationSourceFactory()
        cache_mock.make_key.side_effect = lambda key: key

        source.clear_mappings_cache()

        cache_mock.client.get_client.return_value.delete.assert_called_once_with(*source.get_mappings_cache_keys())

    @patch('core.sources.models.Source.clear_mappings_cache')
    @patch('core.sources.models.Source.clear_concepts_cache')
    def test_clear_cache(self, clear_concepts_cache_mock, clear_mappings_cache_mock):
        source = OrganizationSourceFactory()

        source.clear_cache()

        clear_concepts_cache_mock.assert_called_once()
        clear_mappings_cache_mock.assert_called_once()

    @patch('core.common.models.delete_s3_objects', Mock())
    @patch('core.sources.models.Source.clear_cache')
    def test_delete_head_clears_cache_for_all_versions(self, clear_cache_mock):
        head = OrganizationSourceFactory()
        OrganizationSourceFactory(mnemonic=head.mnemonic, organization=head.organization, version='v1')
        OrganizationSourceFactory(mnemonic=head.mnemonic, organization=head.organization, version='v2')

        head.delete(force=True)

        # once for HEAD itself (post_delete_actions) and once each for v1 and v2
        self.assertEqual(clear_cache_mock.call_count, 3)

    @patch('core.common.models.delete_s3_objects', Mock())
    @patch('core.sources.models.Source.clear_cache')
    def test_delete_non_head_version_clears_its_own_cache(self, clear_cache_mock):
        head = OrganizationSourceFactory()
        v1 = OrganizationSourceFactory(mnemonic=head.mnemonic, organization=head.organization, version='v1')

        v1.delete(force=True)

        clear_cache_mock.assert_called_once()

    @patch('core.common.models.delete_s3_objects')
    def test_delete_clears_export_and_external_export_s3_objects(self, delete_s3_objects_mock):
        from core.repos.models import RepoExternalExport
        head = OrganizationSourceFactory()
        v1 = OrganizationSourceFactory(mnemonic=head.mnemonic, organization=head.organization, version='v1')
        RepoExternalExport.objects.create(resource=v1, key='ext1', file_path='v1/external/report.zip')

        expected_export_path = v1.get_version_export_path(suffix=None)

        v1.delete(force=True, sync=True)

        self.assertEqual(delete_s3_objects_mock.call_count, 2)
        called_paths = sorted(call.args[0] for call in delete_s3_objects_mock.call_args_list)
        self.assertEqual(called_paths, sorted([expected_export_path, 'v1/external/report.zip']))

    @patch('core.common.models.delete_s3_objects')
    def test_delete_head_clears_export_and_external_export_s3_objects_for_all_versions(
            self, delete_s3_objects_mock
    ):
        from core.repos.models import RepoExternalExport
        head = OrganizationSourceFactory()
        v1 = OrganizationSourceFactory(mnemonic=head.mnemonic, organization=head.organization, version='v1')
        v2 = OrganizationSourceFactory(mnemonic=head.mnemonic, organization=head.organization, version='v2')
        RepoExternalExport.objects.create(resource=v1, key='ext1', file_path='v1/external/report.zip')
        RepoExternalExport.objects.create(resource=v2, key='ext2', file_path='v2/external/report.zip')

        expected_paths = sorted([
            head.get_version_export_path(suffix=None),
            v1.get_version_export_path(suffix=None),
            v2.get_version_export_path(suffix=None),
            'v1/external/report.zip',
            'v2/external/report.zip',
        ])

        head.delete(force=True, sync=True)

        called_paths = sorted(call.args[0] for call in delete_s3_objects_mock.call_args_list)
        self.assertEqual(called_paths, expected_paths)

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

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
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

    @override_settings(TEST_MODE=False)
    @patch('core.common.models.seed_children_to_new_version.apply_async')
    def test_persist_new_version_registers_seed_task_before_enqueue(self, apply_async):
        source = OrganizationSourceFactory(version=HEAD)
        source_version = OrganizationSourceFactory.build(
            version='v1',
            mnemonic=source.mnemonic,
            organization=source.organization,
        )

        with self.captureOnCommitCallbacks(execute=True):
            Source.persist_new_version(source_version, source.created_by)

        task = Task.objects.get(name='seed_children_to_new_version')
        self.assertEqual(task.args, ['source', source_version.id, True, False])
        apply_async.assert_called_once_with(
            ('source', source_version.id, True, False),
            task_id=task.id,
            queue='default',
            persist_args=True,
        )

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
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

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
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

    @patch('core.common.models.celery_app')
    def test_is_processing(self, celery_app_mock):
        source = OrganizationSourceFactory()
        self.assertFalse(source.is_processing)

        celery_app_mock.backend.get_many.return_value = iter([('1', {}), ('2', {}), ('3', {})])

        source._background_process_ids = [None, '']  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_processing)
        self.assertEqual(source._background_process_ids, [])  # pylint: disable=protected-access

        source._background_process_ids = ['1', '2', '3']  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_processing)
        self.assertEqual(source._background_process_ids, [])  # pylint: disable=protected-access

        celery_app_mock.backend.get_many.return_value = iter([('1', {}), ('2', {}), ('3', {})])

        source._background_process_ids = ['1', '2', '3']  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_processing)
        self.assertEqual(source._background_process_ids, [])  # pylint: disable=protected-access

        celery_app_mock.backend.get_many.return_value = iter([])

        source._background_process_ids = ['1', '2', '3']  # pylint: disable=protected-access
        source.save()

        self.assertTrue(source.is_processing)
        self.assertEqual(source._background_process_ids, ['1', '2', '3'])  # pylint: disable=protected-access

    @patch('core.common.models.AsyncResult')
    @patch('core.common.models.celery_app')
    def test_is_exporting(self, celery_app_mock, async_result_klass_mock):
        source = OrganizationSourceFactory()
        self.assertFalse(source.is_exporting)

        celery_app_mock.backend.get_many.return_value = iter([('1', {}), ('2', {}), ('3', {})])

        source._background_process_ids = [None, '']  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_exporting)

        source._background_process_ids = ['1', '2', '3']  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_exporting)

        celery_app_mock.backend.get_many.return_value = iter([('1', {}), ('2', {}), ('3', {})])

        source._background_process_ids = ['1', '2', '3']  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_exporting)

        # still processing (not yet finished) - is_processing True, but no export task among them
        celery_app_mock.backend.get_many.return_value = iter([])
        async_result_instance_mock = Mock(successful=Mock(return_value=False), failed=Mock(return_value=False))
        async_result_instance_mock.name = 'core.common.tasks.foobar'
        async_result_klass_mock.return_value = async_result_instance_mock

        source._background_process_ids = ['1', '2', '3']  # pylint: disable=protected-access
        source.save()

        self.assertFalse(source.is_exporting)

        celery_app_mock.backend.get_many.return_value = iter([])
        async_result_instance_mock = Mock(
            name='core.common.tasks.export_source', successful=Mock(return_value=False), failed=Mock(return_value=False)
        )
        async_result_instance_mock.name = 'core.common.tasks.export_source'
        async_result_klass_mock.return_value = async_result_instance_mock

        source._background_process_ids = ['1', '2', '3']  # pylint: disable=protected-access
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

        mapped_sources = source.get_mapped_sources(exclude_self=False)

        self.assertEqual(mapped_sources.count(), 2)

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

    def test_clone_with_cascade_rolls_back_when_mapping_clone_fails(self):
        source1 = OrganizationSourceFactory(mnemonic='source1')
        source1_concept1 = ConceptFactory(
            mnemonic='concept1', parent=source1, names=[ConceptNameFactory.build(name='concept1')])
        source1_concept2 = ConceptFactory(
            mnemonic='concept2', parent=source1, names=[ConceptNameFactory.build(name='concept2')])
        MappingFactory(
            from_concept=source1_concept2, to_concept=source1_concept1, parent=source1, map_type='Q-AND-A')

        source2 = OrganizationSourceFactory(mnemonic='source2')

        with patch('core.mappings.models.Mapping.save_cloned', autospec=True) as save_cloned_mock:
            def fail_save_cloned(mapping):
                mapping.errors = {'__all__': ['mapping clone failed']}

            save_cloned_mock.side_effect = fail_save_cloned

            with self.assertRaisesMessage(CloneError, 'Clone failed.'):
                source2.clone_with_cascade(
                    concept_to_clone=source1_concept2,
                    user=source1_concept2.created_by,
                    map_types='Q-AND-A',
                    equivalency_map_types='SAME-AS'
                )

        self.assertEqual(source2.get_active_concepts().count(), 0)
        self.assertEqual(source2.get_active_mappings().count(), 0)

    def test_clone_with_cascade_returns_concept_object_errors_in_clone_error(self):
        source1 = OrganizationSourceFactory(mnemonic='source1')
        source1_concept = ConceptFactory(
            mnemonic='concept1', parent=source1, names=[ConceptNameFactory.build(name='concept1')])
        source2 = OrganizationSourceFactory(mnemonic='source2')

        with patch('core.concepts.models.Concept.save_cloned', autospec=True) as save_cloned_mock:
            def fail_save_cloned(concept):
                concept.errors = {'external_id': ['duplicate external id']}

            save_cloned_mock.side_effect = fail_save_cloned

            with self.assertRaises(CloneError) as raised:
                source2.clone_with_cascade(
                    concept_to_clone=source1_concept,
                    user=source1_concept.created_by,
                    equivalency_map_types='SAME-AS'
                )

        self.assertEqual(
            raised.exception.errors['concepts'],
            [{
                'mnemonic': source1_concept.mnemonic,
                'source_url': source1_concept.uri,
                'errors': {'external_id': ['duplicate external id']}
            }]
        )
        self.assertEqual(source2.get_active_concepts().count(), 0)

    def test_clone_with_cascade_returns_mapping_object_errors_in_clone_error(self):
        source1 = OrganizationSourceFactory(mnemonic='source1')
        source1_concept1 = ConceptFactory(
            mnemonic='concept1', parent=source1, names=[ConceptNameFactory.build(name='concept1')])
        source1_concept2 = ConceptFactory(
            mnemonic='concept2', parent=source1, names=[ConceptNameFactory.build(name='concept2')])
        failed_mapping = MappingFactory(
            from_concept=source1_concept2, to_concept=source1_concept1, parent=source1, map_type='Q-AND-A',
            from_source=source1, to_source=source1
        )
        source2 = OrganizationSourceFactory(mnemonic='source2')

        with patch('core.mappings.models.Mapping.save_cloned', autospec=True) as save_cloned_mock:
            def fail_save_cloned(mapping):
                mapping.errors = {'map_type': ['unsupported in target source']}

            save_cloned_mock.side_effect = fail_save_cloned

            with self.assertRaises(CloneError) as raised:
                source2.clone_with_cascade(
                    concept_to_clone=source1_concept2,
                    user=source1_concept2.created_by,
                    map_types='Q-AND-A'
                )

        self.assertIn(
            {
                'map_type': failed_mapping.map_type,
                'from_concept_code': failed_mapping.from_concept.mnemonic,
                'to_concept_code': failed_mapping.to_concept.mnemonic,
                'from_source_url': failed_mapping.from_source.uri,
                'to_source_url': failed_mapping.to_source.uri,
                'errors': {'map_type': ['unsupported in target source']}
            },
            raised.exception.errors['mappings']
        )
        self.assertEqual(source2.get_active_concepts().count(), 0)
        self.assertEqual(source2.get_active_mappings().count(), 0)

    def test_mapping_mnemonic_next_returns_none_on_exception(self):
        source = OrganizationSourceFactory(autoid_mapping_mnemonic=AUTO_ID_SEQUENTIAL)
        with patch.object(Source, 'get_resource_next_attr_id', side_effect=Exception('boom')):
            self.assertIsNone(source.mapping_mnemonic_next)

    def test_get_first_or_head_returns_head_when_multiple_match(self):
        source1 = OrganizationSourceFactory(canonical_url='https://dup.com', version=HEAD)
        OrganizationSourceFactory(canonical_url='https://dup.com', version='v1')
        result = Source.get_first_or_head('https://dup.com')
        self.assertEqual(result.id, source1.id)

    def test_get_first_or_head_single_match(self):
        source = OrganizationSourceFactory(canonical_url='https://single.com')
        result = Source.get_first_or_head('https://single.com')
        self.assertEqual(result.id, source.id)

    def test_clean_match_algorithms_invalid(self):
        source = Source(match_algorithms=['bogus'])
        with self.assertRaises(ValidationError):
            source.clean_match_algorithms()

    def test_concept_filter_default(self):
        source = Source(meta={'display': {'default_filter': 'status'}})
        self.assertEqual(source.concept_filter_default, 'status')

    def test_clean_properties_not_a_dict(self):
        source = Source(properties=['not-a-dict'])
        with self.assertRaises(ValidationError):
            source.clean_properties()

    def test_clean_properties_invalid_uri_type(self):
        source = Source(properties=[{'code': 'x', 'uri': 123}])
        with self.assertRaises(ValidationError):
            source.clean_properties()

    def test_clean_properties_invalid_description_type(self):
        source = Source(properties=[{'code': 'x', 'description': 123}])
        with self.assertRaises(ValidationError):
            source.clean_properties()

    def test_clean_filters_not_a_dict(self):
        source = Source(filters=['nope'])
        with self.assertRaises(ValidationError):
            source.clean_filters()

    def test_clean_filters_invalid_description_type(self):
        source = Source(filters=[{'code': 'x', 'operator': ['='], 'value': 'y', 'description': 123}])
        with self.assertRaises(ValidationError):
            source.clean_filters()

    @patch('core.common.models.BaseModel.batch_index')
    def test_seed_concepts_indexes_when_index_true(self, batch_index_mock):
        head = OrganizationSourceFactory(version=HEAD)
        ConceptFactory(parent=head)
        v1 = OrganizationSourceFactory(organization=head.organization, mnemonic=head.mnemonic, version='v1')
        v1.seed_concepts()
        batch_index_mock.assert_called_once()

    @patch('core.common.models.BaseModel.batch_index')
    def test_seed_mappings_indexes_when_index_true(self, batch_index_mock):
        head = OrganizationSourceFactory(version=HEAD)
        MappingFactory(parent=head)
        v1 = OrganizationSourceFactory(organization=head.organization, mnemonic=head.mnemonic, version='v1')
        v1.seed_mappings()
        batch_index_mock.assert_called_once()

    @patch('core.sources.models.index_source_mappings')
    def test_index_mappings_async_swallows_already_queued(self, mock_task):
        mock_task.__name__ = 'index_source_mappings'
        mock_task.apply_async.side_effect = AlreadyQueued('x')
        source = OrganizationSourceFactory()
        source.index_mappings_async(source.created_by)

    @patch('core.sources.models.index_source_concepts')
    def test_index_concepts_async_swallows_already_queued(self, mock_task):
        mock_task.__name__ = 'index_source_concepts'
        mock_task.apply_async.side_effect = AlreadyQueued('x')
        source = OrganizationSourceFactory()
        source.index_concepts_async(source.created_by)

    def test_get_export_task(self):
        source = OrganizationSourceFactory()
        task = Task.new(queue='default', user=source.created_by, name='export_source', args=[source.id])
        self.assertEqual(source.get_export_task().id, task.id)

    def test_get_index_concepts_task(self):
        source = OrganizationSourceFactory()
        task = Task.new(queue='indexing', user=source.created_by, name='index_source_concepts', args=[source.id])
        self.assertEqual(source.get_index_concepts_task().id, task.id)

    def test_get_index_mappings_task(self):
        source = OrganizationSourceFactory()
        task = Task.new(queue='indexing', user=source.created_by, name='index_source_mappings', args=[source.id])
        self.assertEqual(source.get_index_mappings_task().id, task.id)

    @patch('core.sources.models.resolve_url_registry_entries')
    def test_save_queues_resolve_url_registry_entries_when_canonical_url_changes(self, resolve_mock):
        source = OrganizationSourceFactory(version=HEAD, canonical_url='https://old.com')
        OrganizationURLRegistryFactory(organization=source.organization, repo=source, url='https://foo.bar.com')

        source.canonical_url = 'https://new.com'
        source.save()

        resolve_mock.apply_async.assert_called_once_with(
            (source.id, source.resource_type), queue='default', permanent=False)

    @patch('core.sources.models.update_mappings_source')
    def test_post_create_actions_queues_when_not_test_mode(self, update_mappings_source_mock):
        source = OrganizationSourceFactory()
        with patch('core.sources.models.settings.TEST_MODE', False):
            source.post_create_actions()
        update_mappings_source_mock.apply_async.assert_called_once_with(
            (source.id,), queue='default', permanent=False)

    def test_update_sequences_creates_and_updates_all_variants(self):
        source = OrganizationSourceFactory(version=HEAD)

        source.autoid_mapping_mnemonic = AUTO_ID_SEQUENTIAL
        source.autoid_mapping_external_id = AUTO_ID_SEQUENTIAL
        source.autoid_concept_external_id = AUTO_ID_SEQUENTIAL
        source.save()

        source.autoid_mapping_external_id_start_from = 50
        source.autoid_concept_external_id_start_from = 50
        source.save()

    def test_clone_resources_skips_existing_equivalent_concept(self):
        source = OrganizationSourceFactory(version=HEAD)
        target = OrganizationSourceFactory(version=HEAD)
        concept = ConceptFactory(
            parent=source, names=[ConceptNameFactory.build(locale_preferred=True)])
        cloned_concept = ConceptFactory(
            parent=target, names=[ConceptNameFactory.build(locale_preferred=True)])
        MappingFactory(from_concept=cloned_concept, to_concept=concept, parent=target, map_type='Same As')

        added_concepts, _ = target.clone_resources(
            concept.created_by, Concept.objects.filter(id=concept.id), Mapping.objects.none(),
            equivalency_map_types='Same As')

        self.assertEqual(added_concepts, [])

    def test_clone_mappings_resolves_missing_concepts_by_mnemonic(self):
        source = OrganizationSourceFactory(version=HEAD)
        from_concept = ConceptFactory(
            parent=source, names=[ConceptNameFactory.build(locale_preferred=True)])
        to_concept = ConceptFactory(
            parent=source, names=[ConceptNameFactory.build(locale_preferred=True)])
        target = OrganizationSourceFactory(version=HEAD)
        user = from_concept.created_by

        cloned_from = from_concept.versioned_object.clone()
        target.clone_concepts([cloned_from], user, False)
        cloned_to = to_concept.versioned_object.clone()
        target.clone_concepts([cloned_to], user, False)

        unsaved_from = ConceptFactory.build(mnemonic=cloned_from.mnemonic)
        unsaved_to = ConceptFactory.build(mnemonic=cloned_to.mnemonic)
        mapping = MappingFactory.build(from_concept=unsaved_from, to_concept=unsaved_to, parent=target)

        added = target.clone_mappings([mapping], user)

        self.assertEqual(len(added), 1)

    @patch('core.sources.models.Source.update_mappings_count')
    def test_clone_mappings_updates_count_when_update_count_true(self, update_count_mock):
        source = OrganizationSourceFactory(version=HEAD)
        from_concept = ConceptFactory(
            parent=source, names=[ConceptNameFactory.build(locale_preferred=True)])
        to_concept = ConceptFactory(
            parent=source, names=[ConceptNameFactory.build(locale_preferred=True)])
        target = OrganizationSourceFactory(version=HEAD)
        user = from_concept.created_by
        cloned_from = from_concept.versioned_object.clone()
        target.clone_concepts([cloned_from], user, False)
        cloned_to = to_concept.versioned_object.clone()
        target.clone_concepts([cloned_to], user, False)

        mapping = MappingFactory.build(
            from_concept=ConceptFactory.build(mnemonic=cloned_from.mnemonic),
            to_concept=ConceptFactory.build(mnemonic=cloned_to.mnemonic), parent=target)

        target.clone_mappings([mapping], user)

        update_count_mock.assert_called_once()

    @patch('core.sources.models.Source.update_concepts_count')
    def test_clone_concepts_updates_count_when_update_count_true(self, update_count_mock):
        source = OrganizationSourceFactory(version=HEAD)
        target = OrganizationSourceFactory(version=HEAD)
        concept = ConceptFactory(
            parent=source, names=[ConceptNameFactory.build(locale_preferred=True)])
        cloned = concept.versioned_object.clone()

        target.clone_concepts([cloned], concept.created_by)

        update_count_mock.assert_called_once()

    def test_get_map_type_distribution(self):
        source1 = OrganizationSourceFactory(version=HEAD)
        source2 = OrganizationSourceFactory(version=HEAD)
        c1 = ConceptFactory(parent=source1)
        c2 = ConceptFactory(parent=source2)
        MappingFactory(from_concept=c1, to_concept=c2, parent=source1, to_source=source2, map_type='Same As')
        MappingFactory(from_concept=c1, to_concept=c2, parent=source2, from_source=source1, map_type='Same As')

        to_dist = source1.get_to_source_map_type_distribution(source2)
        self.assertEqual(to_dist['total'], 1)
        self.assertEqual(to_dist['map_types'][0]['map_type'], 'Same As')

        from_dist = source2.get_from_source_map_type_distribution(source1)
        self.assertEqual(from_dist['total'], 1)

    @patch('core.sources.models.Source.get_mapping_facets')
    def test_get_to_sources_map_type_distribution_with_source_names_filter(self, get_mapping_facets_mock):
        source1 = OrganizationSourceFactory(version=HEAD)
        source2 = OrganizationSourceFactory(version=HEAD)
        c1 = ConceptFactory(parent=source1)
        c2 = ConceptFactory(parent=source2)
        MappingFactory(from_concept=c1, to_concept=c2, parent=source1, to_source=source2, map_type='Same As')

        get_mapping_facets_mock.side_effect = [
            SimpleNamespace(mapType=[('Same As', 2)]),
            SimpleNamespace(mapType=[('Same As', 1)]),
        ]

        distribution = source1.get_to_sources_map_type_distribution(source_names=[source2.mnemonic])

        self.assertEqual(len(distribution), 1)
        self.assertEqual(distribution[0]['distribution']['active'], 2)
        self.assertEqual(distribution[0]['distribution']['retired'], 1)
        self.assertEqual(distribution[0]['distribution']['total'], 3)

    @patch('core.sources.models.Source.get_mapping_facets')
    def test_get_from_sources_map_type_distribution_with_source_names_filter(self, get_mapping_facets_mock):
        source1 = OrganizationSourceFactory(version=HEAD)
        source2 = OrganizationSourceFactory(version=HEAD)
        c1 = ConceptFactory(parent=source1)
        c2 = ConceptFactory(parent=source2)
        MappingFactory(from_concept=c1, to_concept=c2, parent=source2, from_source=source1, map_type='Same As')

        get_mapping_facets_mock.side_effect = [
            SimpleNamespace(mapType=[('Same As', 3)]),
            SimpleNamespace(mapType=[]),
        ]

        distribution = source2.get_from_sources_map_type_distribution(source_names=[source1.mnemonic])

        self.assertEqual(len(distribution), 1)
        self.assertEqual(distribution[0]['distribution']['active'], 3)
        self.assertEqual(distribution[0]['distribution']['retired'], 0)

    def test_get_resource_facet_filters_non_head_uses_source_version(self):
        source = OrganizationSourceFactory(version='v1')
        filters = source._get_resource_facet_filters()  # pylint: disable=protected-access
        self.assertEqual(filters['source_version'], 'v1')
        self.assertNotIn('is_latest_version', filters)

    def test_get_ordered_concept_facets_by_filter_order(self):
        source = Source(meta={'display': {'concept_filter_order': ['status']}})
        facets = {
            'properties__status': [('active', 3)],
            'properties__other': [('x', 1)],
            'plainFacet': [('y', 2)],
        }

        ordered = source.get_ordered_concept_facets_by_filter_order(facets)

        self.assertEqual(list(ordered.keys()), ['properties__status', 'properties__other', 'plainFacet'])


class SourceSignalsTest(OCLTestCase):
    def test_propagate_parent_attributes_updates_mapping_public_access(self):
        source = OrganizationSourceFactory(public_access=ACCESS_TYPE_EDIT)
        mapping = MappingFactory(parent=source, public_access=ACCESS_TYPE_EDIT)

        source.public_access = ACCESS_TYPE_VIEW
        source._should_update_public_access = True  # pylint: disable=protected-access
        source.save()

        mapping.refresh_from_db()
        self.assertEqual(mapping.public_access, ACCESS_TYPE_VIEW)


class SourceCloneAPITest(OCLAPITestCase):
    def test_clone_api_returns_structured_errors_and_rolls_back(self):
        source1 = OrganizationSourceFactory(mnemonic='source1')
        concept_to_clone = ConceptFactory(
            mnemonic='concept1', parent=source1, names=[ConceptNameFactory.build(name='concept1')])
        source2 = OrganizationSourceFactory(mnemonic='source2')
        user = source2.created_by
        source2.organization.members.add(user)
        self.client.force_authenticate(user=user)

        url = f'/orgs/{source2.organization.mnemonic}/sources/{source2.mnemonic}/concepts/$clone/'

        with patch('core.concepts.models.Concept.save_cloned', autospec=True) as save_cloned_mock:
            def fail_save_cloned(concept):
                concept.errors = {'__all__': ['concept clone failed']}

            save_cloned_mock.side_effect = fail_save_cloned
            response = self.client.post(url, {'expressions': [concept_to_clone.uri]}, format='json')

        self.assertEqual(response.status_code, 200)
        payload = response.data[concept_to_clone.uri]
        self.assertEqual(payload['status'], 400)
        self.assertEqual(payload['errors']['concepts'][0]['mnemonic'], concept_to_clone.mnemonic)
        self.assertEqual(payload['errors']['concepts'][0]['source_url'], concept_to_clone.uri)
        self.assertEqual(payload['errors']['concepts'][0]['errors'], {'__all__': ['concept clone failed']})
        self.assertEqual(source2.get_active_concepts().count(), 0)
        self.assertEqual(source2.get_active_mappings().count(), 0)


class SourceValidationTest(OCLTestCase):
    def test_property_types(self):
        self.assertEqual(Source().property_types, {})
        self.assertEqual(Source(properties=[]).property_types, {})
        self.assertEqual(
            Source(properties=[
                {'code': 'is_clinical', 'type': 'boolean'},
                {'code': 'height', 'type': 'integer'},
                {'code': 'label'},
                {'type': 'string'},
            ]).property_types,
            {'is_clinical': 'boolean', 'height': 'integer', 'label': None}
        )

    def test_clean_properties_valid(self):
        source = Source(properties=[
            {'code': 'height', 'type': 'integer'},
            {'code': 'weight', 'type': 'decimal', 'description': 'in kilograms'}
        ])

        source.clean_properties()

    def test_clean_properties_missing_code(self):
        source = Source(properties=[{'type': 'string'}])
        with self.assertRaises(ValidationError):
            source.clean_properties()

    def test_clean_properties_invalid_type(self):
        source = Source(properties=[{'code': 'age', 'type': 'unsupported'}])
        with self.assertRaises(ValidationError):
            source.clean_properties()

    def test_clean_properties_extra_keys(self):
        source = Source(properties=[{'code': 'age', 'type': 'integer', 'foo': 'bar'}])
        with self.assertRaises(ValidationError):
            source.clean_properties()

    def test_clean_filters_valid(self):
        source = Source(filters=[
            {'code': 'gender', 'operator': ['='], 'value': 'male'},
            {'code': 'birthdate', 'operator': ['='], 'value': '2000-01-01'}
        ])

        source.clean_filters()

    def test_clean_filters_invalid_operator(self):
        source = Source(filters=[{'code': 'status', 'operator': ['unsupported'], 'value': 'active'}])
        with self.assertRaises(ValidationError):
            source.clean_filters()

    def test_clean_filters_missing_code(self):
        source = Source(filters=[{'operator': ['='], 'value': 'yes'}])
        with self.assertRaises(ValidationError):
            source.clean_filters()

    def test_clean_filters_missing_value(self):
        source = Source(filters=[{'code': 'status', 'operator': ['=']}])
        with self.assertRaises(ValidationError):
            source.clean_filters()

    def test_clean_filters_operator_not_list(self):
        source = Source(filters=[{'code': 'status', 'operator': '=', 'value': 'active'}])
        with self.assertRaises(ValidationError):
            source.clean_filters()

    def test_clean_filters_extra_keys(self):
        source = Source(filters=[{'code': 'gender', 'operator': ['='], 'value': 'female', 'extra': 'nope'}])
        with self.assertRaises(ValidationError):
            source.clean_filters()


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

    @patch('core.sources.models.index_source_mappings')
    @patch('core.sources.models.index_source_concepts')
    @patch('core.common.tasks.export_source')
    def test_seed_children_task_should_partially_index_new_unreleased_version(
            self, export_source_task, index_source_concepts_task_mock, index_source_mappings_task_mock
    ):
        export_source_task.__name__ = 'export_source'
        index_source_concepts_task_mock.__name__ = 'index_source_concepts'
        index_source_mappings_task_mock.__name__ = 'index_source_mappings'
        source = OrganizationSourceFactory()
        ConceptFactory(parent=source)
        MappingFactory(parent=source)

        source_v1 = OrganizationSourceFactory(organization=source.organization, version='v1', mnemonic=source.mnemonic)

        seed_children_to_new_version('source', source_v1.id, False)  # pylint: disable=no-value-for-parameter

        index_source_concepts_task_mock.apply_async.assert_called_once_with(
            (source_v1.id, {'_append_source_version': 'v1'}), queue='indexing', persist_args=True, task_id=ANY
        )
        index_source_mappings_task_mock.apply_async.assert_called_once_with(
            (source_v1.id, {'_append_source_version': 'v1'}), queue='indexing', persist_args=True, task_id=ANY
        )

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
            (source_v1.id, {'_append_source_version': 'v1', 'is_in_latest_source_version': True}),
            queue='indexing', persist_args=True, task_id=ANY)
        index_source_mappings_task_mock.apply_async.assert_called_once_with(
            (source_v1.id, {'_append_source_version': 'v1', 'is_in_latest_source_version': True}),
            queue='indexing', persist_args=True, task_id=ANY)

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
                call(
                    (source_v1.id, {'is_in_latest_source_version': False}),
                    queue='indexing', persist_args=True, task_id=ANY
                ),
                call(
                    (source_v2.id, {'_append_source_version': 'v2', 'is_in_latest_source_version': True}),
                    queue='indexing', persist_args=True, task_id=ANY
                )
            ]
        )
        self.assertEqual(
            index_source_mappings_task_mock.apply_async.mock_calls,
            [
                call(
                    (source_v1.id, {'is_in_latest_source_version': False}),
                    queue='indexing', persist_args=True, task_id=ANY
                ),
                call(
                    (source_v2.id, {'_append_source_version': 'v2', 'is_in_latest_source_version': True}),
                    queue='indexing', persist_args=True, task_id=ANY
                )
            ]
        )

    @patch.object(Source, 'index_children_async', autospec=True)
    def test_index_resources_for_self_as_latest_released_should_partially_index_new_version_on_create(
            self, index_children_async_mock
    ):
        head = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(
            organization=head.organization, version='v1', mnemonic=head.mnemonic, released=True)
        source_v2 = OrganizationSourceFactory(
            organization=head.organization, version='v2', mnemonic=head.mnemonic, released=True)

        source_v2.index_resources_for_self_as_latest_released()

        self.assertEqual(
            index_children_async_mock.mock_calls,
            [
                call(source_v1, source_v2.created_by, {'is_in_latest_source_version': False}),
                call(
                    source_v2, source_v2.created_by,
                    {'_append_source_version': 'v2', 'is_in_latest_source_version': True}
                )
            ]
        )

    @patch.object(Source, 'index_children_async', autospec=True)
    def test_index_resources_for_self_as_latest_released_should_only_partially_update_on_release_state_update(
            self, index_children_async_mock
    ):
        head = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(
            organization=head.organization, version='v1', mnemonic=head.mnemonic, released=True)
        source_v2 = OrganizationSourceFactory(
            organization=head.organization, version='v2', mnemonic=head.mnemonic, released=True)

        source_v2.index_resources_for_self_as_latest_released(only_update=True)

        self.assertEqual(
            index_children_async_mock.mock_calls,
            [
                call(source_v1, source_v2.created_by, {'is_in_latest_source_version': False}),
                call(source_v2, source_v2.created_by, {'is_in_latest_source_version': True})
            ]
        )

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

    @patch('core.sources.models.Source.clear_mappings_cache')
    @patch('core.sources.models.Source.mappings')
    @patch('core.sources.models.Source.batch_index')
    def test_index_source_mappings(self, batch_index_mock, source_mappings_mock, clear_mappings_cache_mock):
        source = OrganizationSourceFactory()
        index_source_mappings(source.id)
        batch_index_mock.assert_called_once_with(
            source_mappings_mock, MappingDocument,
            prefetch=['sources'],
            select_related=['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'],
            single_batch=False,
            parallel=True
        )
        clear_mappings_cache_mock.assert_called_once()

    @patch('core.sources.models.Source.clear_concepts_cache')
    @patch('core.sources.models.Source.concepts')
    @patch('core.sources.models.Source.batch_index')
    def test_index_source_concepts(self, batch_index_mock, source_concepts_mock, clear_concepts_cache_mock):
        source = OrganizationSourceFactory()
        index_source_concepts(source.id)
        batch_index_mock.assert_called_once_with(
            source_concepts_mock, ConceptDocument,
            prefetch=['sources', 'names', 'descriptions'],
            select_related=['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'],
            single_batch=False,
            parallel=True
        )
        clear_concepts_cache_mock.assert_called_once()

    @patch('core.sources.models.Source.clear_concepts_cache')
    @patch('core.sources.models.Source.concepts')
    @patch('core.sources.models.Source.batch_index')
    def test_index_source_concepts_partial_update(
            self, batch_index_mock, source_concepts_mock, clear_concepts_cache_mock
    ):
        source = OrganizationSourceFactory()
        index_source_concepts(source.id, {'is_in_latest_source_version': False})
        batch_index_mock.assert_called_once_with(
            source_concepts_mock, ConceptDocument,
            partial_doc={'is_in_latest_source_version': False},
            single_batch=False,
            parallel=True
        )
        clear_concepts_cache_mock.assert_called_once()

    @patch('core.sources.models.Source.clear_concepts_cache')
    @patch('core.common.tasks.logger.exception')
    @patch('core.sources.models.Source.concepts')
    @patch('core.sources.models.Source.batch_index')
    def test_index_source_concepts_partial_update_failure_should_fallback_to_full_index(
            self, batch_index_mock, source_concepts_mock, logger_exception_mock, clear_concepts_cache_mock
    ):
        source = OrganizationSourceFactory()
        batch_index_mock.side_effect = [Exception('boom'), None]

        index_source_concepts(source.id, {'is_in_latest_source_version': False})

        self.assertEqual(
            batch_index_mock.mock_calls,
            [
                call(
                    source_concepts_mock, ConceptDocument,
                    partial_doc={'is_in_latest_source_version': False},
                    single_batch=False,
                    parallel=True
                ),
                call(
                    source_concepts_mock, ConceptDocument,
                    prefetch=['sources', 'names', 'descriptions'],
                    select_related=['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'],
                    parallel=True
                )
            ]
        )
        logger_exception_mock.assert_called_once_with(
            'Falling back to full concept reindex for source %s', source.id
        )
        clear_concepts_cache_mock.assert_called_once()

    @patch('core.sources.models.Source.clear_mappings_cache')
    @patch('core.sources.models.Source.mappings')
    @patch('core.sources.models.Source.batch_index')
    def test_index_source_mappings_partial_update(
            self, batch_index_mock, source_mappings_mock, clear_mappings_cache_mock
    ):
        source = OrganizationSourceFactory()
        index_source_mappings(source.id, {'is_in_latest_source_version': False})
        batch_index_mock.assert_called_once_with(
            source_mappings_mock, MappingDocument,
            partial_doc={'is_in_latest_source_version': False},
            single_batch=False,
            parallel=True
        )
        clear_mappings_cache_mock.assert_called_once()

    @patch('core.sources.models.Source.clear_mappings_cache')
    @patch('core.common.tasks.logger.exception')
    @patch('core.sources.models.Source.mappings')
    @patch('core.sources.models.Source.batch_index')
    def test_index_source_mappings_partial_update_failure_should_fallback_to_full_index(
            self, batch_index_mock, source_mappings_mock, logger_exception_mock, clear_mappings_cache_mock
    ):
        source = OrganizationSourceFactory()
        batch_index_mock.side_effect = [Exception('boom'), None]

        index_source_mappings(source.id, {'is_in_latest_source_version': False})

        self.assertEqual(
            batch_index_mock.mock_calls,
            [
                call(
                    source_mappings_mock, MappingDocument,
                    partial_doc={'is_in_latest_source_version': False},
                    single_batch=False,
                    parallel=True
                ),
                call(
                    source_mappings_mock, MappingDocument,
                    prefetch=['sources'],
                    select_related=['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'],
                    parallel=True
                )
            ]
        )
        logger_exception_mock.assert_called_once_with(
            'Falling back to full mapping reindex for source %s', source.id
        )
        clear_mappings_cache_mock.assert_called_once()

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
