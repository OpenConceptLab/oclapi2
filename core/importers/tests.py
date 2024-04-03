import json
import os
import uuid
from json import JSONDecodeError
from zipfile import ZipFile

from celery_once import AlreadyQueued
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models import F
from mock import patch, Mock, ANY, PropertyMock
from ocldev.oclcsvtojsonconverter import OclStandardCsvToJsonConverter

from core.collections.models import Collection
from core.common.constants import OPENMRS_VALIDATION_SCHEMA, DEPRECATED_API_HEADER
from core.common.tasks import post_import_update_resource_counts, bulk_import_parts_inline, bulk_import_inline, \
    bulk_import
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.importers.input_parsers import ImportContentParser
from core.importers.models import BulkImport, BulkImportInline, BulkImportParallelRunner
from core.importers.views import csv_file_data_to_input_list
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.sources.constants import AUTO_ID_UUID
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.tasks.models import Task
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class BulkImportTest(OCLTestCase):
    @patch('core.importers.models.OclFlexImporter')
    def test_run(self, flex_importer_mock):
        user = UserProfile.objects.get(username='ocladmin')
        import_results = Mock(
            to_json=Mock(return_value='{"all": "200"}'),
            get_detailed_summary=Mock(return_value='summary'),
            display_report=Mock(return_value='report')
        )
        flex_importer_instance_mock = Mock(process=Mock(return_value=None), import_results=import_results)
        flex_importer_mock.return_value = flex_importer_instance_mock
        content = '{"foo": "bar"}\n{"foobar": "foo"}'

        bulk_import_instance = BulkImport(content=content, username='ocladmin', update_if_exists=True)
        bulk_import_instance.run()

        self.assertEqual(bulk_import_instance.result.json, {"all": "200"})
        self.assertEqual(bulk_import_instance.result.detailed_summary, 'summary')
        self.assertEqual(bulk_import_instance.result.report, 'report')

        flex_importer_mock.assert_called_once_with(
            input_list=[{"foo": "bar"}, {"foobar": "foo"}],
            api_url_root=ANY,
            api_token=user.get_token(),
            do_update_if_exists=True
        )
        flex_importer_instance_mock.process.assert_called_once()


class BulkImportInlineTest(OCLTestCase):
    def test_org_import(self):
        self.assertFalse(Organization.objects.filter(mnemonic='DATIM-MOH-BI-FY19').exists())

        OrganizationFactory(mnemonic='DATIM-MOH-BI-FY19', location='blah')
        self.assertTrue(Organization.objects.filter(mnemonic='DATIM-MOH-BI-FY19').exists())

        data = '{"type": "Organization", "__action": "DELETE", "id": "DATIM-MOH-BI-FY19"}\n' \
               '{"name": "DATIM MOH Burundi", "extras": {"datim_moh_country_code": "BI", "datim_moh_period": "FY19",' \
               ' "datim_moh_object": true}, "location": "Burundi", "public_access": "None", "type": "Organization",' \
               ' "id": "DATIM-MOH-BI-FY19"}'
        importer = BulkImportInline(data, 'ocladmin', True)
        importer.run()

        self.assertTrue(Organization.objects.filter(mnemonic='DATIM-MOH-BI-FY19').exists())
        self.assertEqual(importer.processed, 2)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(len(importer.deleted), 1)
        self.assertTrue(importer.elapsed_seconds > 0)

        data = {
            "name": "DATIM MOH Burundi", "extras": {
                "datim_moh_country_code": "BI", "datim_moh_period": "FY19", "datim_moh_object": True
            }, "location": "Burundi", "public_access": "None", "type": "Organization", "id": "DATIM-MOH-BI-FY19"
        }
        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.deleted), 0)
        self.assertEqual(len(importer.exists), 1)
        self.assertEqual(importer.exists[0], data)
        self.assertTrue(importer.elapsed_seconds > 0)

        data = {"type": "Organization", "__action": "DELETE", "id": "FOOBAR"}
        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.deleted), 0)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.not_found), 1)
        self.assertEqual(importer.not_found[0], data)
        self.assertTrue(importer.elapsed_seconds > 0)

    def test_source_import_success(self):
        OrganizationFactory(mnemonic='DemoOrg')
        self.assertFalse(Source.objects.filter(mnemonic='DemoSource').exists())

        data = {
            "type": "Source", "id": "DemoSource", "short_code": "DemoSource", "name": "OCL Demo Source",
            "full_name": "OCL Demo Source", "owner_type": "Organization", "owner": "DemoOrg",
            "description": "Source used for demo purposes", "default_locale": "en", "source_type": "Dictionary",
            "public_access": "View", "supported_locales": "en", "custom_validation_schema": "None"
        }
        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertTrue(Source.objects.filter(mnemonic='DemoSource', version='HEAD').exists())
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.created[0], data)
        self.assertTrue(importer.elapsed_seconds > 0)

    def test_source_import_failed(self):
        self.assertFalse(Source.objects.filter(mnemonic='DemoSource').exists())

        data = {
            "type": "Source", "id": "DemoSource", "short_code": "DemoSource", "name": "OCL Demo Source",
            "full_name": "OCL Demo Source", "owner_type": "Organization", "owner": "DemoOrg",
            "description": "Source used for demo purposes", "default_locale": "en", "source_type": "Dictionary",
            "public_access": "View", "supported_locales": "en", "custom_validation_schema": "None"
        }
        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertFalse(Source.objects.filter(mnemonic='DemoSource').exists())
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.failed), 1)
        self.assertEqual(importer.failed[0], {**data, 'errors': {'parent': 'Parent resource cannot be None.'}})
        self.assertTrue(importer.elapsed_seconds > 0)

    @patch('core.sources.models.index_source_mappings', Mock())
    @patch('core.sources.models.index_source_concepts', Mock())
    def test_source_and_version_import(self):
        OrganizationFactory(mnemonic='DemoOrg')
        self.assertFalse(Source.objects.filter(mnemonic='DemoSource').exists())

        data = '{"type": "Source", "id": "DemoSource", "short_code": "DemoSource", "name": "OCL Demo Source", ' \
               '"full_name": "OCL Demo Source", "owner_type": "Organization", "owner": "DemoOrg", "description": ' \
               '"Source used for demo purposes", "default_locale": "en", "source_type": "Dictionary", ' \
               '"public_access": "View", "supported_locales": "en", "custom_validation_schema": "None"}\n' \
               '{"type": "Source Version", "id": "initial", "source": "DemoSource", "description": "Initial empty ' \
               'repository version", "released": true, "owner": "DemoOrg", "owner_type": "Organization"} '

        importer = BulkImportInline(data, 'ocladmin', True)
        importer.run()

        self.assertTrue(Source.objects.filter(mnemonic='DemoSource', version='HEAD').exists())
        self.assertTrue(Source.objects.filter(mnemonic='DemoSource', version='initial').exists())
        self.assertEqual(importer.processed, 2)
        self.assertEqual(len(importer.created), 2)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)

    def test_collection_import_success(self):
        OrganizationFactory(mnemonic='PEPFAR')
        self.assertFalse(Collection.objects.filter(mnemonic='MER-R-MOH-Facility-FY19').exists())

        data = {
            "type": "Collection", "id": "MER-R-MOH-Facility-FY19", "name": "MER R: MOH Facility Based FY19",
            "default_locale": "en", "short_code": "MER-R-MOH-Facility-FY19", "external_id": "OBhi1PUW3OL",
            "extras": {
                "Period": "FY19", "Period Description": "COP18 (FY19Q1)",
                "datim_sync_moh_fy19": True, "DHIS2-Dataset-Code": "MER_R_MOH"
            },
            "collection_type": "Code List", "full_name": "MER Results: MOH Facility Based FY19", "owner": "PEPFAR",
            "public_access": "View", "owner_type": "Organization", "supported_locales": "en"
        }
        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertTrue(Collection.objects.filter(mnemonic='MER-R-MOH-Facility-FY19', version='HEAD').exists())
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.created[0], data)
        self.assertTrue(importer.elapsed_seconds > 0)

    def test_collection_import_failed(self):
        self.assertFalse(Collection.objects.filter(mnemonic='MER-R-MOH-Facility-FY19').exists())

        data = {
            "type": "Collection", "id": "MER-R-MOH-Facility-FY19", "name": "MER R: MOH Facility Based FY19",
            "default_locale": "en", "short_code": "MER-R-MOH-Facility-FY19", "external_id": "OBhi1PUW3OL",
            "extras": {
                "Period": "FY19", "Period Description": "COP18 (FY19Q1)",
                "datim_sync_moh_fy19": True, "DHIS2-Dataset-Code": "MER_R_MOH"
            },
            "collection_type": "Code List", "full_name": "MER Results: MOH Facility Based FY19", "owner": "PEPFAR",
            "public_access": "View", "owner_type": "Organization", "supported_locales": "en"
        }
        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertFalse(Collection.objects.filter(mnemonic='MER-R-MOH-Facility-FY19').exists())
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.failed), 1)
        self.assertEqual(importer.failed[0], {**data, 'errors': {'parent': 'Parent resource cannot be None.'}})
        self.assertTrue(importer.elapsed_seconds > 0)

    def test_collection_and_version_import(self):
        OrganizationFactory(mnemonic='PEPFAR')
        self.assertFalse(Collection.objects.filter(mnemonic='MER-R-MOH-Facility-FY19').exists())

        data = '{"type": "Collection", "id": "MER-R-MOH-Facility-FY19", "name": "MER R: MOH Facility Based FY19", ' \
               '"default_locale": "en", "short_code": "MER-R-MOH-Facility-FY19", "external_id": "OBhi1PUW3OL", ' \
               '"extras": {"Period": "FY19", "Period Description": "COP18 (FY19Q1)", "datim_sync_moh_fy19": true, ' \
               '"DHIS2-Dataset-Code": "MER_R_MOH"}, "collection_type": "Code List", "full_name": ' \
               '"MER Results: MOH Facility Based FY19", "owner": "PEPFAR", "public_access": "View", ' \
               '"owner_type": "Organization", "supported_locales": "en"}\n' \
               '{"type": "Collection Version", "id": "FY19.v0", ' \
               '"description": "Initial release of FY19 DATIM-MOH definitions", ' \
               '"collection": "MER-R-MOH-Facility-FY19", "released": true, "owner": "PEPFAR", ' \
               '"owner_type": "Organization"}'

        importer = BulkImportInline(data, 'ocladmin', True)
        importer.run()

        self.assertTrue(Collection.objects.filter(mnemonic='MER-R-MOH-Facility-FY19', version='HEAD').exists())
        self.assertTrue(Collection.objects.filter(mnemonic='MER-R-MOH-Facility-FY19', version='FY19.v0').exists())
        self.assertEqual(importer.processed, 2)
        self.assertEqual(len(importer.created), 2)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)

    @patch('core.importers.models.batch_index_resources')
    def test_concept_import(self, batch_index_resources_mock):
        batch_index_resources_mock.__name__ = 'batch_index_resources'
        self.assertFalse(Concept.objects.filter(mnemonic='Food').exists())

        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD'
        )

        data = {
            "type": "Concept", "id": "Food", "concept_class": "Root",
            "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)

        self.assertEqual(source.concepts_set.count(), 2)
        self.assertEqual(Concept.objects.filter(mnemonic='Food').count(), 2)
        concept = Concept.objects.filter(mnemonic='Food', id=F('versioned_object_id')).first()
        self.assertEqual(concept.versions.count(), 1)
        self.assertTrue(Concept.objects.filter(mnemonic='Food', is_latest_version=True).exists())
        batch_index_resources_mock.apply_async.assert_called_with(
            ('concept', {'id__in': ANY}, True), queue='indexing', permanent=False)
        self.assertEqual(
            Concept.objects.filter(mnemonic='Food', id=F('versioned_object_id')).first().versions.count(), 1
        )
        self.assertTrue(Concept.objects.filter(mnemonic='Food', is_latest_version=True).exists())
        batch_index_resources_mock.apply_async.assert_called_with(
            ('concept', {'id__in': ANY}, True), queue='indexing', permanent=False)
        self.assertEqual(
            sorted(batch_index_resources_mock.apply_async.mock_calls[0][1][0][1]['id__in']),
            sorted([concept.id, concept.get_latest_version().id])
        )

        data = {
            "type": "Concept", "id": "Food", "concept_class": "Root",
            "datatype": "Rule", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.updated), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)
        self.assertEqual(source.concepts_set.count(), 3)
        concept = Concept.objects.filter(mnemonic='Food', id=F('versioned_object_id')).first()
        self.assertEqual(concept.versions.count(), 2)
        self.assertTrue(Concept.objects.filter(mnemonic='Food', is_latest_version=True, datatype='Rule').exists())
        batch_index_resources_mock.apply_async.assert_called_with(
            ('concept', {'id__in': ANY}, True), queue='indexing', permanent=False)
        self.assertEqual(
            sorted(batch_index_resources_mock.apply_async.mock_calls[1][1][0][1]['id__in']),
            sorted([concept.id, concept.get_latest_version().prev_version.id, concept.get_latest_version().id])
        )

        data = {
            "type": "Concept", "id": "Food", "concept_class": "Root",
            "datatype": "Foo", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.updated), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)
        self.assertEqual(source.concepts_set.count(), 4)
        concept = Concept.objects.filter(mnemonic='Food', id=F('versioned_object_id')).first()
        self.assertEqual(concept.versions.count(), 3)
        self.assertTrue(Concept.objects.filter(mnemonic='Food', is_latest_version=True, datatype='Foo').exists())
        batch_index_resources_mock.apply_async.assert_called_with(
            ('concept', {'id__in': ANY}, True), queue='indexing', permanent=False)
        self.assertEqual(
            sorted(batch_index_resources_mock.apply_async.mock_calls[2][1][0][1]['id__in']),
            sorted([concept.id, concept.get_latest_version().prev_version.id, concept.get_latest_version().id])
        )

    @patch('core.importers.models.batch_index_resources')
    def test_concept_import_with_auto_assignment_mnemonic(self, batch_index_resources_mock):
        self.assertFalse(Concept.objects.filter(mnemonic='Food').exists())

        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD',
            autoid_concept_mnemonic=AUTO_ID_UUID
        )

        data = {
            "type": "Concept", "concept_class": "Root",
            "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)

        self.assertEqual(source.concepts_set.count(), 2)
        concept = source.concepts_set.filter(id=F('versioned_object_id')).first()

        self.assertEqual(len(concept.mnemonic), 36)
        self.assertEqual(
            concept.versions.count(), 1
        )
        self.assertTrue(Concept.objects.filter(mnemonic=concept.mnemonic, is_latest_version=True).exists())

        data = {
            "type": "Concept", "id": concept.mnemonic, "concept_class": "Root",
            "datatype": "Rule", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.updated), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)

        self.assertEqual(
            Concept.objects.filter(mnemonic=concept.mnemonic, id=F('versioned_object_id')).first().versions.count(), 2
        )
        self.assertTrue(
            Concept.objects.filter(mnemonic=concept.mnemonic, is_latest_version=True, datatype='Rule').exists())
        batch_index_resources_mock.apply_async.assert_called()

    def test_concept_import_permission_denied(self):
        self.assertFalse(Concept.objects.filter(mnemonic='Food').exists())

        org = OrganizationFactory(mnemonic='DemoOrg')
        source = OrganizationSourceFactory(
            organization=org, mnemonic='DemoSource', version='HEAD', public_access='None')
        self.assertFalse(source.public_can_view)

        data = {
            "type": "Concept", "id": "Food", "concept_class": "Root",
            "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
        }

        random_user = UserProfileFactory(username='random-user')
        self.assertFalse(org.is_member(random_user))

        importer = BulkImportInline(json.dumps(data), 'random-user', True)
        importer.run()

        self.assertEqual(Concept.objects.filter(mnemonic='Food').count(), 0)
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.permission_denied), 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(importer.permission_denied, [data])

    @patch('core.importers.models.batch_index_resources')
    def test_mapping_import(self, batch_index_resources_mock):
        batch_index_resources_mock.__name__ = 'batch_index_resources'
        self.assertEqual(Mapping.objects.count(), 0)

        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD'
        )
        ConceptFactory(parent=source, mnemonic='Corn')
        ConceptFactory(parent=source, mnemonic='Vegetable')

        data = {
            "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Corn/",
            "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
            "type": "Mapping", "source": "DemoSource",
            "extras": None, "owner": "DemoOrg", "map_type": "Has Child", "owner_type": "Organization",
            "external_id": None
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(Mapping.objects.filter(map_type='Has Child').count(), 2)
        mapping = Mapping.objects.filter(map_type='Has Child', id=F('versioned_object_id')).first()
        self.assertEqual(mapping.versions.count(), 1)
        self.assertTrue(Mapping.objects.filter(map_type='Has Child', is_latest_version=True).exists())
        batch_index_resources_mock.apply_async.assert_called_with(
            ('mapping', {'id__in': ANY}, True), queue='indexing', permanent=False)
        self.assertEqual(
            Mapping.objects.filter(map_type='Has Child', id=F('versioned_object_id')).first().versions.count(), 1
        )
        self.assertTrue(Mapping.objects.filter(map_type='Has Child', is_latest_version=True).exists())
        batch_index_resources_mock.apply_async.assert_called_with(
            ('mapping', {'id__in': ANY}, True), queue='indexing', permanent=False)
        self.assertEqual(
            sorted(batch_index_resources_mock.apply_async.mock_calls[0][1][0][1]['id__in']),
            sorted([mapping.id, mapping.get_latest_version().id])
        )

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)

        data = {
            "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Corn/",
            "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
            "type": "Mapping", "source": "DemoSource",
            "extras": {"foo": "bar"}, "owner": "DemoOrg", "map_type": "Has Child", "owner_type": "Organization",
            "external_id": None
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        mapping = Mapping.objects.filter(map_type='Has Child', id=F('versioned_object_id')).first()
        self.assertEqual(mapping.versions.count(), 2)
        batch_index_resources_mock.apply_async.assert_called_with(
            ('mapping', {'id__in': ANY}, True), queue='indexing', permanent=False)
        self.assertEqual(
            sorted(batch_index_resources_mock.apply_async.mock_calls[1][1][0][1]['id__in']),
            sorted([mapping.id, mapping.get_latest_version().prev_version.id, mapping.get_latest_version().id])
        )
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.updated), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)

    @patch('core.importers.models.batch_index_resources')
    def test_mapping_import_with_autoid_assignment(self, batch_index_resources_mock):
        self.assertEqual(Mapping.objects.count(), 0)

        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD',
            autoid_mapping_mnemonic=AUTO_ID_UUID
        )
        ConceptFactory(parent=source, mnemonic='Corn')
        ConceptFactory(parent=source, mnemonic='Vegetable')

        data = {
            "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Corn/",
            "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
            "type": "Mapping", "source": "DemoSource",
            "extras": None, "owner": "DemoOrg", "map_type": "Has Child", "owner_type": "Organization",
            "external_id": None
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(Mapping.objects.filter(map_type='Has Child').count(), 2)
        self.assertEqual(
            Mapping.objects.filter(map_type='Has Child', id=F('versioned_object_id')).first().versions.count(), 1
        )
        self.assertEqual(
            len(Mapping.objects.filter(map_type='Has Child', id=F('versioned_object_id')).first().mnemonic), 36
        )
        self.assertTrue(Mapping.objects.filter(map_type='Has Child', is_latest_version=True).exists())
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)
        batch_index_resources_mock.apply_async.assert_called()

    @patch('core.importers.models.batch_index_resources')
    def test_reference_import(self, batch_index_resources_mock):
        importer = BulkImportInline(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/sample_collection_references.json'), 'r'
            ).read(),
            'ocladmin', True
        )
        importer.run()
        self.assertEqual(importer.processed, 9)
        self.assertEqual(len(importer.created), 9)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.unchanged), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        collection = Collection.objects.filter(uri='/orgs/PEPFAR/collections/MER-R-MOH-Facility-FY19/').first()
        self.assertEqual(collection.expansions.count(), 1)
        self.assertEqual(collection.expansion.concepts.count(), 4)
        self.assertEqual(collection.expansion.mappings.count(), 0)
        self.assertEqual(collection.references.count(), 4)

        # duplicate run
        importer = BulkImportInline(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/sample_collection_references.json'), 'r'
            ).read(),
            'ocladmin', True
        )
        importer.run()
        self.assertEqual(importer.processed, 9)
        self.assertEqual(len(importer.created), 2)
        self.assertEqual(len(importer.exists), 3)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.unchanged), 4)  # due to same concept checksum
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        collection = Collection.objects.filter(uri='/orgs/PEPFAR/collections/MER-R-MOH-Facility-FY19/').first()
        self.assertEqual(collection.expansions.count(), 1)
        self.assertEqual(collection.expansion.concepts.count(), 4)
        self.assertEqual(collection.expansion.mappings.count(), 0)
        self.assertEqual(collection.references.count(), 4)
        batch_index_resources_mock.apply_async.assert_called()

    @patch('core.sources.models.index_source_mappings', Mock())
    @patch('core.sources.models.index_source_concepts', Mock())
    @patch('core.importers.models.batch_index_resources')
    def test_sample_import(self, batch_index_resources_mock):
        importer = BulkImportInline(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/sample_ocldev.json'), 'r'
            ).read(),
            'ocladmin', True
        )
        importer.run()

        self.assertEqual(importer.processed, 64)
        self.assertEqual(len(importer.created), 49)
        self.assertEqual(len(importer.exists), 3)
        self.assertEqual(len(importer.updated), 1)  # last 11 rows are duplicate rows
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.unchanged), 11)
        self.assertEqual(len(importer.deleted), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        self.assertEqual(batch_index_resources_mock.apply_async.call_count, 2)

        data = {
            "type": "Concept", "id": "Corn", "concept_class": "Root",
            "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [], '__action': 'delete'
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.deleted), 1)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        self.assertEqual(batch_index_resources_mock.apply_async.call_count, 2)  # no new indexing call
        concept = Concept.objects.filter(mnemonic='Corn').first()
        self.assertTrue(concept.get_latest_version().retired)
        self.assertTrue(concept.versioned_object.retired)
        self.assertFalse(concept.get_latest_version().prev_version.retired)

        data = {
            "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Corn/",
            "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
            "type": "Mapping", "source": "DemoSource",
            "extras": None, "owner": "DemoOrg", "map_type": "Has Child", "owner_type": "Organization",
            "external_id": None, '__action': 'delete'
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.deleted), 1)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        self.assertEqual(batch_index_resources_mock.apply_async.call_count, 2)  # no new indexing call
        mapping = Mapping.objects.filter(
            to_concept__uri="/orgs/DemoOrg/sources/DemoSource/concepts/Corn/",
            from_concept__uri="/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
        ).first()
        self.assertTrue(mapping.get_latest_version().retired)
        self.assertTrue(mapping.versioned_object.retired)
        self.assertFalse(mapping.get_latest_version().prev_version.retired)

    @patch('core.importers.models.batch_index_resources')
    def test_csv_import_with_retired_concepts(self, batch_index_resources_mock):
        file_content = open(
            os.path.join(os.path.dirname(__file__), '..', 'samples/ocl_csv_with_retired_concepts.csv'), 'r').read()
        data = OclStandardCsvToJsonConverter(
            input_list=csv_file_data_to_input_list(file_content), allow_special_characters=True).process()
        importer = BulkImportInline(data, 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 11)
        self.assertEqual(len(importer.created), 11)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        batch_index_resources_mock.apply_async.assert_called()

        self.assertEqual(Concept.objects.filter(parent__mnemonic='MyDemoSource', is_latest_version=True).count(), 4)
        self.assertEqual(
            Concept.objects.filter(parent__mnemonic='MyDemoSource', is_latest_version=True, retired=True).count(), 1)
        self.assertEqual(
            Concept.objects.filter(parent__mnemonic='MyDemoSource', is_latest_version=True, retired=False).count(), 3)
        self.assertEqual(
            Mapping.objects.filter(
                map_type="Parent-child", parent__mnemonic='MyDemoSource', is_latest_version=True, retired=False
            ).count(), 1)
        self.assertEqual(
            Mapping.objects.filter(
                map_type="Parent-child-retired", parent__mnemonic='MyDemoSource', is_latest_version=True, retired=True
            ).count(), 1)

    @patch('core.importers.models.batch_index_resources')
    def test_csv_import_with_retired_concepts_and_mappings(self, batch_index_resources_mock):
        file_content = open(
            os.path.join(os.path.dirname(__file__), '..', 'samples/ocl_csv_import_example_test_retired.csv'), 'r'
        ).read()
        data = OclStandardCsvToJsonConverter(
            input_list=csv_file_data_to_input_list(file_content), allow_special_characters=True).process()
        importer = BulkImportInline(data, 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 12)
        self.assertEqual(len(importer.created), 12)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        batch_index_resources_mock.apply_async.assert_called()

        self.assertTrue(
            Concept.objects.filter(mnemonic='Act', is_latest_version=True, retired=False).exists())
        self.assertTrue(
            Concept.objects.filter(mnemonic='Child', is_latest_version=True, retired=False).exists())
        self.assertTrue(
            Concept.objects.filter(mnemonic='Child_of_child', is_latest_version=True, retired=False).exists())
        self.assertTrue(
            Concept.objects.filter(mnemonic='Ret', is_latest_version=True, retired=True).exists())
        self.assertTrue(
            Concept.objects.filter(mnemonic='Ret-with-mappings', is_latest_version=True, retired=True).exists())
        self.assertTrue(
            Mapping.objects.filter(map_type='Child-Parent', is_latest_version=True, retired=False).exists())
        self.assertTrue(
            Mapping.objects.filter(map_type='SAME-AS', is_latest_version=True, retired=True).exists())
        self.assertTrue(
            Mapping.objects.filter(map_type='Parent-child', is_latest_version=True, retired=False).exists())

    @patch('core.importers.models.batch_index_resources')
    def test_csv_import_mappings_with_sort_weight(self, batch_index_resources_mock):
        file_content = open(
            os.path.join(os.path.dirname(__file__), '..', 'samples/mappings_with_sort_weight.csv'), 'r'
        ).read()
        data = OclStandardCsvToJsonConverter(
            input_list=csv_file_data_to_input_list(file_content), allow_special_characters=True).process()
        importer = BulkImportInline(data, 'ocladmin', True)

        self.assertEqual(len(data), 12)

        importer.run()

        self.assertEqual(importer.processed, 12)
        self.assertEqual(len(importer.created), 12)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        batch_index_resources_mock.apply_async.assert_called()

        self.assertTrue(
            Concept.objects.filter(mnemonic='Act', is_latest_version=True, retired=False).exists())
        self.assertTrue(
            Concept.objects.filter(mnemonic='Child', is_latest_version=True, retired=False).exists())
        self.assertTrue(
            Concept.objects.filter(mnemonic='Child_of_child', is_latest_version=True, retired=False).exists())
        self.assertTrue(
            Concept.objects.filter(mnemonic='Ret', is_latest_version=True, retired=True).exists())
        self.assertTrue(
            Mapping.objects.filter(map_type='Child-Parent', is_latest_version=True, retired=False).exists())
        self.assertEqual(
            Mapping.objects.filter(map_type='Child-Parent', is_latest_version=True, retired=False).first().sort_weight,
            None
        )
        self.assertEqual(
            Mapping.objects.filter(
                to_concept__uri='/orgs/DemoOrg/sources/MyDemoSource/concepts/Child/', is_latest_version=True
            ).first().sort_weight,
            2.2
        )
        self.assertEqual(
            Mapping.objects.filter(
                to_concept__uri='/orgs/DemoOrg/sources/MyDemoSource/concepts/Child_of_child/', is_latest_version=True
            ).first().sort_weight,
            3.0
        )
        self.assertEqual(
            Mapping.objects.filter(
                to_concept_code='non-existant', is_latest_version=True
            ).first().sort_weight,
            1.0
        )

    @patch('core.importers.models.batch_index_resources')
    def test_openmrs_schema_csv_import(self, batch_index_resources_mock):
        call_command('import_lookup_values')
        org = OrganizationFactory(mnemonic='MSFOCP')
        OrganizationSourceFactory(
            mnemonic='Implementationtest', organization=org, custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        file_content = open(
            os.path.join(os.path.dirname(__file__), '..', 'samples/msfocp_concepts.csv'), 'r').read()
        data = OclStandardCsvToJsonConverter(
            input_list=csv_file_data_to_input_list(file_content),
            allow_special_characters=True
        ).process()
        importer = BulkImportInline(data, 'ocladmin', True)
        importer.run()
        self.assertEqual(importer.processed, 31)
        self.assertEqual(len(importer.created), 21)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.failed), 10)
        self.assertEqual(len(importer.permission_denied), 0)
        batch_index_resources_mock.apply_async.assert_called()

    @patch('core.sources.models.index_source_mappings', Mock())
    @patch('core.sources.models.index_source_concepts', Mock())
    @patch('core.importers.models.batch_index_resources')
    def test_pepfar_import(self, batch_index_resources_mock):
        importer = BulkImportInline(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/pepfar_datim_moh_fy19.json'), 'r').read(),
            'ocladmin', True
        )
        importer.run()

        self.assertEqual(importer.processed, 413)
        self.assertEqual(len(importer.created), 413)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        batch_index_resources_mock.apply_async.assert_called()


class BulkImportParallelRunnerTest(OCLTestCase):
    def test_invalid_json(self):
        with self.assertRaises(JSONDecodeError) as ex:
            BulkImportParallelRunner(
                open(
                    os.path.join(os.path.dirname(__file__), '..', 'samples/invalid_import_json.json'), 'r'
                ).read(),
                'ocladmin', True
            )
        self.assertEqual(ex.exception.msg, 'Expecting property name enclosed in double quotes')

    def test_make_parts(self):
        importer = BulkImportParallelRunner(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/sample_ocldev.json'), 'r'
            ).read(),
            'ocladmin', True
        )

        self.assertEqual(len(importer.parts), 7)
        self.assertEqual(len(importer.parts[0]), 2)
        self.assertEqual(len(importer.parts[1]), 2)
        self.assertEqual(len(importer.parts[2]), 1)
        self.assertEqual(len(importer.parts[3]), 23)
        self.assertEqual(len(importer.parts[4]), 22)
        self.assertEqual(len(importer.parts[5]), 2)
        self.assertEqual(len(importer.parts[6]), 12)
        self.assertEqual([part['type'] for part in importer.parts[0]], ['Organization', 'Organization'])
        self.assertEqual([part['type'] for part in importer.parts[1]], ['Source', 'Source'])
        self.assertEqual([part['type'] for part in importer.parts[2]], ['Source Version'])
        self.assertEqual(list({part['type'] for part in importer.parts[3]}), ['Concept'])
        self.assertEqual(list({part['type'] for part in importer.parts[4]}), ['Mapping'])
        self.assertEqual([part['type'] for part in importer.parts[5]], ['Source Version', 'Source Version'])
        self.assertEqual(list({part['type'] for part in importer.parts[6]}), ['Concept'])

    @patch('core.importers.models.app.control')
    def test_is_any_process_alive(self, celery_app_mock):
        importer = BulkImportParallelRunner(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/sample_ocldev.json'), 'r'
            ).read(),
            'ocladmin', True
        )
        self.assertFalse(importer.is_any_process_alive())

        importer.groups = [
            Mock(ready=Mock(return_value=True)),
            Mock(ready=Mock(return_value=True)),
        ]
        self.assertFalse(importer.is_any_process_alive())

        # worker1 and worker2 failed after processing some jobs and/or part of started jobs
        # worker3 finished everything
        importer.tasks = [
            Mock(task_id='task1', worker='worker1', status='SUCCESS'),
            Mock(task_id='task2', worker='worker1', status='FAILED'),
            Mock(task_id='task3', worker='worker1', status='STARTED'),
            Mock(task_id='task4', worker='worker1', status='STARTED'),
            Mock(task_id='task5', worker='worker2', status='PENDING'),
            Mock(task_id='task6', worker='worker2', status='STARTED'),
            Mock(task_id='task7', worker='worker3', status='SUCCESS'),
        ]

        celery_app_mock.ping = Mock(return_value=[])

        importer.groups = [
            Mock(ready=Mock(return_value=True)),
            Mock(ready=Mock(return_value=False)),
        ]
        self.assertFalse(importer.is_any_process_alive())
        self.assertCountEqual(celery_app_mock.ping.call_args[1]['destination'], ['worker1', 'worker2'])

        # worker1 is up
        celery_app_mock.ping = Mock(return_value=[{'worker1': {'ping': 'ok'}}])

        self.assertTrue(importer.is_any_process_alive())
        self.assertCountEqual(celery_app_mock.ping.call_args[1]['destination'], ['worker1', 'worker2'])

        # worker1 and worker2 both are up
        celery_app_mock.ping = Mock(return_value=[{'worker1': {'ping': 'ok'}}, {'worker2': {'ping': 'ok'}}])

        self.assertTrue(importer.is_any_process_alive())
        self.assertCountEqual(celery_app_mock.ping.call_args[1]['destination'], ['worker1', 'worker2'])

    def test_get_overall_tasks_progress(self):
        Task(id='task1', name='sub_task', summary={'processed': 100, 'total': 200}).save()
        Task(id='task2', name='sub_task', summary={'processed': 50, 'total': 100}).save()
        importer = BulkImportParallelRunner(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/sample_ocldev.json'), 'r'
            ).read(),
            'ocladmin', True
        )
        self.assertEqual(importer.get_overall_tasks_progress(), 0)
        importer.tasks = [Mock(task_id='task1'), Mock(task_id='task2')]
        self.assertEqual(importer.get_overall_tasks_progress(), 150)

    def test_update_elapsed_seconds(self):
        importer = BulkImportParallelRunner(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/sample_ocldev.json'), 'r'
            ).read(),
            'ocladmin', True
        )
        self.assertIsNotNone(importer.start_time)
        self.assertEqual(importer.elapsed_seconds, 0)
        importer.update_elapsed_seconds()
        self.assertTrue(importer.elapsed_seconds > 0)

    def test_notify_progress(self):
        task = Task(id='task-id', name='bulk_import')
        task.save()
        Task(id='task-1', name='sub_task', summary={'processed': 100, 'total': 200}).save()
        Task(id='task-2', name='sub_task', summary={'processed': 50, 'total': 100}).save()

        importer = BulkImportParallelRunner(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/sample_ocldev.json'), 'r'
            ).read(),
            'ocladmin', True, None, 'task-id'
        )
        importer.tasks = [Mock(task_id='task-1'), Mock(task_id='task-2')]
        now = 1607346541.793877  # datetime.datetime(2020, 12, 7, 13, 09, 1, 793877) UTC
        importer.start_time = now
        importer.elapsed_seconds = 10.45
        importer.notify_progress()

        task.refresh_from_db()
        self.assertEqual(task.summary, {'processed': 150, 'total': 64})

    def test_chunker_list(self):
        self.assertEqual(
            list(BulkImportParallelRunner.chunker_list([1, 2, 3], 3, False)), [[1], [2], [3]]
        )
        self.assertEqual(
            list(BulkImportParallelRunner.chunker_list([1, 2, 3], 2, False)), [[1, 2], [3]]
        )
        self.assertEqual(
            list(BulkImportParallelRunner.chunker_list([1, 2, 3], 1, False)), [[1, 2, 3]]
        )

        concepts = [
            {"type": "Concept", "id": "A", "update_comment": "A.1"},
            {"type": "Concept", "id": "B", "update_comment": "B.1"},
            {"type": "Concept", "id": "A", "update_comment": "A.2"},
            {"type": "Concept", "id": "C", "update_comment": "C.1"},
            {"type": "Concept", "id": "B", "update_comment": "B.2"},
            {"type": "Concept", "id": "B", "update_comment": "B.3"},
            {"type": "Concept", "id": "A", "update_comment": "A.3"}
        ]

        self.assertEqual(
            list(BulkImportParallelRunner.chunker_list(concepts, 1, True)),
            [
                [
                    {"type": "Concept", "id": "A", "update_comment": "A.1"},
                    {"type": "Concept", "id": "A", "update_comment": "A.2"},
                    {"type": "Concept", "id": "A", "update_comment": "A.3"},
                    {"type": "Concept", "id": "B", "update_comment": "B.1"},
                    {"type": "Concept", "id": "B", "update_comment": "B.2"},
                    {"type": "Concept", "id": "B", "update_comment": "B.3"},
                    {"type": "Concept", "id": "C", "update_comment": "C.1"},
                ],
            ]
        )

        self.assertEqual(
            list(BulkImportParallelRunner.chunker_list(concepts, 2, True)),
            [
                [
                    {"type": "Concept", "id": "A", "update_comment": "A.1"},
                    {"type": "Concept", "id": "A", "update_comment": "A.2"},
                    {"type": "Concept", "id": "A", "update_comment": "A.3"},
                    {"type": "Concept", "id": "B", "update_comment": "B.1"},
                    {"type": "Concept", "id": "B", "update_comment": "B.2"},
                    {"type": "Concept", "id": "B", "update_comment": "B.3"},
                ],
                [
                    {"type": "Concept", "id": "C", "update_comment": "C.1"},
                ]
            ]
        )

        self.assertEqual(
            list(BulkImportParallelRunner.chunker_list(concepts, 3, True)),
            [
                [
                    {"type": "Concept", "id": "A", "update_comment": "A.1"},
                    {"type": "Concept", "id": "A", "update_comment": "A.2"},
                    {"type": "Concept", "id": "A", "update_comment": "A.3"},
                ],
                [
                    {"type": "Concept", "id": "B", "update_comment": "B.1"},
                    {"type": "Concept", "id": "B", "update_comment": "B.2"},
                    {"type": "Concept", "id": "B", "update_comment": "B.3"},
                ],
                [
                    {"type": "Concept", "id": "C", "update_comment": "C.1"},
                ]
            ]
        )

        self.assertEqual(
            list(BulkImportParallelRunner.chunker_list(concepts, 5, True)),
            [
                [
                    {"type": "Concept", "id": "A", "update_comment": "A.1"},
                    {"type": "Concept", "id": "A", "update_comment": "A.2"},
                    {"type": "Concept", "id": "A", "update_comment": "A.3"},
                ],
                [
                    {"type": "Concept", "id": "B", "update_comment": "B.1"},
                    {"type": "Concept", "id": "B", "update_comment": "B.2"},
                    {"type": "Concept", "id": "B", "update_comment": "B.3"},
                ],
                [
                    {"type": "Concept", "id": "C", "update_comment": "C.1"},
                ]
            ]
        )


class BulkImportViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.superuser = UserProfile.objects.get(username='ocladmin')
        self.token = self.superuser.get_token()

    def test_get_without_task_id(self, ):
        random_user = UserProfileFactory(username='foobar')
        task_id1 = f"{str(uuid.uuid4())}-ocladmin~priority"
        task_id2 = f"{str(uuid.uuid4())}-foobar~normal"
        task_id3 = f"{str(uuid.uuid4())}-foobar~pending"
        Task(
            queue='priority', id=task_id1,
            name='core.common.tasks.bulk_import_parallel_inline', created_by=self.superuser, state='SUCCESS').save()
        Task(
            queue='normal', id=task_id2,
            name='core.common.tasks.bulk_import_parallel_inline', created_by=random_user, state='FAILED').save()
        Task(
            queue='pending', id=task_id3,
            name='core.common.tasks.bulk_import_parallel_inline', created_by=random_user, state='PENDING').save()

        response = self.client.get(
            '/importers/bulk-import/?username=ocladmin&verbose=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [dict(d) for d in response.data],
            [{
                 'id': task_id1,
                 'task': task_id1,
                 'state': 'SUCCESS',
                 'name': 'core.common.tasks.bulk_import_parallel_inline',
                 'queue': 'priority',
                 'username': 'ocladmin',
                 'created_at': ANY,
                 'started_at': None,
                 'finished_at': None,
                 'runtime': None,
                 'summary': None,
                 'children': []
             }]
        )

        response = self.client.get(
            '/importers/bulk-import/?username=foobar',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted([dict(d) for d in response.data], key=lambda x: x['id']),
            sorted([{
                'id': task_id2,
                'task': task_id2,
                'state': 'FAILED',
                'name': 'core.common.tasks.bulk_import_parallel_inline',
                'queue': 'normal',
                'username': 'foobar',
                'created_at': ANY,
                'started_at': None,
                'finished_at': None,
                'runtime': None,
                'summary': None,
                'children': []
            }, {
                'id': task_id3,
                'task': task_id3,
                'state': 'PENDING',
                'name': 'core.common.tasks.bulk_import_parallel_inline',
                'queue': 'pending',
                'username': 'foobar',
                'created_at': ANY,
                'started_at': None,
                'finished_at': None,
                'runtime': None,
                'summary': None,
                'children': []
            }], key= lambda x: x['id'])
        )

        response = self.client.get(
            '/importers/bulk-import/priority/?username=ocladmin',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [dict(d) for d in response.data],
            [{
                'id': task_id1,
                'task': task_id1,
                'state': 'SUCCESS',
                'name': 'core.common.tasks.bulk_import_parallel_inline',
                'queue': 'priority',
                'username': 'ocladmin',
                'created_at': ANY,
                'started_at': None,
                'finished_at': None,
                'runtime': None,
                'summary': None,
                'children': []
            }]
        )

        response = self.client.get(
            '/importers/bulk-import/normal/?username=ocladmin',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_get_task(self):
        task_id = f"{str(uuid.uuid4())}-foobar~normal"
        foobar_user = UserProfileFactory(username='foobar')

        response = self.client.get(
            f'/importers/bulk-import/?task={task_id}',
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 404)

        Task(
            id=task_id, created_by=foobar_user, queue='normal', state='PENDING',
            name='core.common.tasks.bulk_import_parallel_inline').save()
        response = self.client.get(
            f'/importers/bulk-import/?task={task_id}',
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data, {
                'id': task_id,
                'task': task_id,
                'state': 'PENDING',
                'name': 'core.common.tasks.bulk_import_parallel_inline',
                'queue': 'normal',
                'username': 'foobar',
                'created_at': ANY,
                'started_at': None,
                'finished_at': None,
                'runtime': None,
                'summary': None,
                'children': []
            })

    def test_post_400(self):
        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=1',
            {'data': 'some-data'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'exception': "update_if_exists must be either 'true' or 'false'"})

        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'exception': "Invalid input."})

    @patch('core.importers.views.queue_bulk_import')
    def test_post_409(self, queue_bulk_import_mock):
        queue_bulk_import_mock.side_effect = AlreadyQueued('already-queued')

        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=true',
            {'data': 'some-data'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data, {'exception': "The same import has been already queued"})

    @patch('core.common.tasks.bulk_import_parallel_inline')
    def test_post_202(self, bulk_import_mock):
        bulk_import_mock.__name__ = 'bulk_import_parallel_inline'
        task_mock = Mock(id='task-id', state='pending')
        bulk_import_mock.apply_async = Mock(return_value=task_mock)

        response = self.client.post(
            "/importers/bulk-import/?update_if_exists=true",
            {'data': ['some-data']},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data,
            {
                'id': ANY,
                'task': ANY,
                'state': 'PENDING',
                'name': 'bulk_import_parallel_inline',
                'queue': 'bulk_import_root',
                'username': 'ocladmin',
                'created_at': ANY,
                'started_at': None,
                'finished_at': None,
                'runtime': None,
                'summary': None,
                'children': []
            }
        )
        self.assertTrue(DEPRECATED_API_HEADER not in response)
        self.assertEqual(bulk_import_mock.apply_async.call_count, 1)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], ((["some-data"], 'ocladmin', True, 5),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][36:], '-ocladmin~bulk_import_root')
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['queue'], 'bulk_import_root')

        random_user = UserProfileFactory(username='oswell')

        response = self.client.post(
            "/importers/bulk-import/?update_if_exists=true",
            {'data': ['some-data'], 'parallel': 2},
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        task = random_user.async_tasks.order_by('id').last()
        self.assertEqual(
            response.data,
            {
                'id': task.id,
                'task': task.id,
                'state': 'PENDING',
                'name': 'bulk_import_parallel_inline',
                'queue': task.queue,
                'username': random_user.username,
                'created_at': ANY,
                'started_at': None,
                'finished_at': None,
                'runtime': None,
                'summary': None,
                'children': []
            }
        )
        self.assertEqual(bulk_import_mock.apply_async.call_count, 2)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], ((["some-data"], 'oswell', True, 2),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][36:], f'-oswell~{task.queue}')
        self.assertTrue(bulk_import_mock.apply_async.call_args[1]['queue'].startswith('bulk_import_'))

        response = self.client.post(
            "/importers/bulk-import/foobar-queue/?update_if_exists=true",
            {'data': ['some-data'], 'parallel': 10},
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        task = random_user.async_tasks.filter(id__icontains='foobar').order_by('id').last()
        self.assertTrue(task.queue.startswith('bulk_import_'))
        self.assertTrue(task.user_queue, 'foobar-queue')
        self.assertEqual(
            response.data,
            {
                'id': task.id,
                'task': task.id,
                'state': 'PENDING',
                'name': 'bulk_import_parallel_inline',
                'queue': 'foobar-queue',
                'username': random_user.username,
                'created_at': ANY,
                'started_at': None,
                'finished_at': None,
                'runtime': None,
                'summary': None,
                'children': []
            }
        )
        self.assertEqual(bulk_import_mock.apply_async.call_count, 3)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], ((["some-data"], 'oswell', True, 10),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][36:], '-oswell~foobar-queue')
        self.assertTrue(bulk_import_mock.apply_async.call_args[1]['queue'].startswith('bulk_import_'))

    def test_post_file_upload_400(self):
        response = self.client.post(
            "/importers/bulk-import/?update_if_exists=true",
            {'file': ''},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'exception': 'Invalid input.'})

        file = open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/invalid_import_csv.csv'), 'r'
            )
        response = self.client.post(
            "/importers/bulk-import/?update_if_exists=true",
            {'file': file},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'exception': 'Invalid input.'})

    def test_post_file_url_400(self):
        response = self.client.post(
            "/importers/bulk-import/file-url/?update_if_exists=true",
            {'file_url': 'foobar'},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'exception': 'No content to import'})

    def test_post_invalid_csv_400(self):
        file = open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/invalid_import_csv.csv'), 'r'
            )

        response = self.client.post(
            "/importers/bulk-import-inline/?update_if_exists=true",
            {'file': file},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'exception': 'No content to import'})

    @patch('core.common.tasks.bulk_import_parallel_inline')
    def test_post_inline_parallel_202(self, bulk_import_mock):
        bulk_import_mock.__name__ = 'bulk_import_parallel_inline'
        file = SimpleUploadedFile('file.json', b'{"key": "value"}', "application/json")

        response = self.client.post(
            "/importers/bulk-import-parallel-inline/?update_if_exists=true",
            {'file': file},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, {
                'id': ANY,
                'task': ANY,
                'state': 'PENDING',
                'name': 'bulk_import_parallel_inline',
                'queue': 'bulk_import_root',
                'username': 'ocladmin',
                'created_at': ANY,
                'started_at': None,
                'finished_at': None,
                'runtime': None,
                'summary': None,
                'children': []
            })
        self.assertTrue(DEPRECATED_API_HEADER in response)
        self.assertEqual(response[DEPRECATED_API_HEADER], 'True')
        self.assertEqual(bulk_import_mock.apply_async.call_count, 1)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], (('{"key": "value"}', 'ocladmin', True, 5),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][37:], 'ocladmin~bulk_import_root')
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['queue'], 'bulk_import_root')

    @patch('core.common.tasks.bulk_import_inline')
    def test_post_inline_202(self, bulk_import_mock):
        bulk_import_mock.__name__ = 'bulk_import_inline'
        file = SimpleUploadedFile('file.json', b'{"key": "value"}', "application/json")

        response = self.client.post(
            "/importers/bulk-import-inline/?update_if_exists=true",
            {'file': file},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, {
                'id': ANY,
                'task': ANY,
                'state': 'PENDING',
                'name': 'bulk_import_inline',
                'queue': 'bulk_import_root',
                'username': self.superuser.username,
                'created_at': ANY,
                'started_at': None,
                'finished_at': None,
                'runtime': None,
                'summary': None,
                'children': []
            })
        self.assertEqual(bulk_import_mock.apply_async.call_count, 1)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], (('{"key": "value"}', 'ocladmin', True),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][37:], 'ocladmin~bulk_import_root')
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['queue'], 'bulk_import_root')

    @patch('core.tasks.models.QueueOnce.once_backend', new_callable=PropertyMock)
    @patch('core.tasks.models.AsyncResult')
    @patch('core.tasks.models.app')
    def test_delete_parallel_import_204(self, celery_app_mock, async_result_mock, queue_once_backend_mock):
        clear_lock_mock = Mock()
        queue_once_backend_mock.return_value = Mock(clear_lock=clear_lock_mock)
        result_mock = Mock(
            args=['content', 'ocladmin', True, 5]  # content, username, update_if_exists, threads
        )
        result_mock.name = 'core.common.tasks.bulk_import_parallel_inline'
        async_result_mock.return_value = result_mock
        task_id = 'ace5abf4-3b7f-4e4a-b16f-d1c041088c3e-ocladmin~priority'
        Task(
            id=task_id, created_by=self.superuser, queue='priority', state='PENDING',
            name='core.common.tasks.bulk_import_parallel_inline').save()
        response = self.client.delete(
            "/importers/bulk-import/",
            {'task_id': task_id},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 204)
        celery_app_mock.control.revoke.assert_called_once_with(task_id, terminate=True, signal='SIGKILL')
        self.assertTrue(clear_lock_mock.call_args[0][0].endswith(
            'core.common.tasks.bulk_import_parallel_inline_threads-5_to_import-content_update_if_exists-True_username-ocladmin'  # pylint: disable=line-too-long
        ))

    @patch('core.tasks.models.QueueOnce.once_backend', new_callable=PropertyMock)
    @patch('core.tasks.models.AsyncResult')
    @patch('core.tasks.models.app')
    def test_delete_204(self, celery_app_mock, async_result_mock, queue_once_backend_mock):
        clear_lock_mock = Mock()
        queue_once_backend_mock.return_value = Mock(clear_lock=clear_lock_mock)
        result_mock = Mock(
            args=['content', 'ocladmin', True]  # content, username, update_if_exists
        )
        result_mock.name = 'core.common.tasks.bulk_import'
        async_result_mock.return_value = result_mock
        task_id = 'ace5abf4-3b7f-4e4a-b16f-d1c041088c3e-ocladmin~priority'
        Task(
            id=task_id, created_by=self.superuser, queue='priority', state='PENDING',
            name='core.common.tasks.bulk_import').save()
        response = self.client.delete(
            "/importers/bulk-import/",
            {'task_id': task_id},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 204)
        celery_app_mock.control.revoke.assert_called_once_with(task_id, terminate=True, signal='SIGKILL')
        self.assertTrue(clear_lock_mock.call_args[0][0].endswith(
            'core.common.tasks.bulk_import_to_import-content_update_if_exists-True_username-ocladmin'
        ))

    @patch('core.tasks.models.AsyncResult')
    @patch('core.tasks.models.app')
    def test_delete_400(self, celery_app_mock, async_result_mock):
        response = self.client.delete(
            "/importers/bulk-import/",
            {'task_id': ''},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 400)

        result_mock = Mock(
            args=['content', 'ocladmin', True]  # content, username, update_if_exists
        )
        result_mock.name = 'core.common.tasks.bulk_import'
        async_result_mock.return_value = result_mock

        task_id = 'ace5abf4-3b7f-4e4a-b16f-d1c041088c3e-ocladmin~priority'
        Task(
            id=task_id, created_by=self.superuser, queue='priority', state='PENDING',
            name='core.common.tasks.bulk_import').save()
        celery_app_mock.control.revoke.side_effect = Exception('foobar')
        response = self.client.delete(
            "/importers/bulk-import/",
            {'task_id': task_id},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'errors': ('foobar',)})
        celery_app_mock.control.revoke.assert_called_once_with(task_id, terminate=True, signal='SIGKILL')

    @patch('core.tasks.models.AsyncResult')
    def test_delete_403(self, async_result_mock):
        random_user = UserProfileFactory(username='random_user')
        response = self.client.delete(
            "/importers/bulk-import/",
            {'task_id': ''},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 400)

        async_result_mock.return_value = Mock(
            args=['content', 'ocladmin', True]  # content, username, update_if_exists
        )

        task_id = 'ace5abf4-3b7f-4e4a-b16f-d1c041088c3e-ocladmin~priority'
        Task(
            id=task_id, created_by=self.superuser, queue='priority', state='PENDING',
            name='core.common.tasks.bulk_import').save()
        response = self.client.delete(
            "/importers/bulk-import/",
            {'task_id': task_id},
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
        )

        self.assertEqual(response.status_code, 403)


class TasksTest(OCLTestCase):
    @patch('core.sources.models.Source.update_mappings_count')
    @patch('core.sources.models.Source.update_concepts_count')
    def test_post_import_update_resource_counts(self, update_concepts_count_mock, update_mappings_count_mock):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(_counted=None, parent=source)
        concept2 = ConceptFactory(_counted=True, parent=source)
        mapping1 = MappingFactory(_counted=None, parent=source)
        mapping2 = MappingFactory(_counted=True, parent=source)

        post_import_update_resource_counts()
        concept1.refresh_from_db()
        mapping1.refresh_from_db()
        concept2.refresh_from_db()
        mapping2.refresh_from_db()

        self.assertTrue(concept1._counted)  # pylint: disable=protected-access
        self.assertTrue(mapping1._counted)  # pylint: disable=protected-access
        self.assertTrue(concept2._counted)  # pylint: disable=protected-access
        self.assertTrue(mapping2._counted)  # pylint: disable=protected-access

        update_concepts_count_mock.assert_called_once_with(sync=True)
        update_mappings_count_mock.assert_called_once_with(sync=True)

    @patch('core.importers.models.BulkImportInline')
    def test_bulk_import_parts_inline(self, bulk_import_inline_mock):
        bulk_import_inline_mock.run = Mock()

        bulk_import_parts_inline([1, 2], 'username', True)  # pylint: disable=no-value-for-parameter
        bulk_import_inline_mock.assert_called_once_with(
            content=None, username='username', update_if_exists=True, input_list=[1, 2],
            self_task_id=ANY
        )
        bulk_import_inline_mock().run.assert_called_once()

    @patch('core.importers.models.BulkImportInline')
    def test_bulk_import_inline(self, bulk_import_inline_mock):
        bulk_import_inline_mock.run = Mock()

        bulk_import_inline([1, 2], 'username', True)
        bulk_import_inline_mock.assert_called_once_with(
            content=[1, 2], username='username', update_if_exists=True
        )
        bulk_import_inline_mock().run.assert_called_once()

    @patch('core.importers.models.BulkImport')
    def test_bulk_import(self, bulk_import_mock):
        bulk_import_mock.run = Mock()

        bulk_import([1, 2], 'username', True)
        bulk_import_mock.assert_called_once_with(
            content=[1, 2], username='username', update_if_exists=True
        )
        bulk_import_mock().run.assert_called_once()


class ImportContentParserTest(OCLTestCase):
    def test_parse_content(self):
        parser = ImportContentParser(content='foobar')
        parser.parse()

        self.assertEqual(parser.content, 'foobar')

    def test_parse_json_file(self):
        file = open(os.path.join(os.path.dirname(__file__), '..', 'samples/sample_collection_references.json'), 'r')

        parser = ImportContentParser(file=file)
        parser.parse()

        self.assertIsNotNone(parser.content)

    def test_parse_csv_file(self):
        file = open(os.path.join(os.path.dirname(__file__), '..', 'samples/ocl_csv_with_retired_concepts.csv'), 'r')

        parser = ImportContentParser(file=file)
        parser.parse()

        self.assertEqual(
            parser.content,
            [{
                 'company': 'DemoLand Inc.',
                 'extras': {
                     'Ex_Num': '6'
                 },
                 'id': 'DemoOrg',
                 'location': 'DemoLand',
                 'name': 'My Demo Organization',
                 'public_access': 'View',
                 'type': 'Organization',
                 'website': 'https://www.demoland.fake'
             },
             {
                 'canonical_url': 'https://demo.fake/CodeSystem/Source',
                 'custom_validation_schema': 'None',
                 'default_locale': 'en',
                 'description': 'Using this source just for testing purposes',
                 'external_id': '164531246546-IDK',
                 'extras': {
                     'ex_name': 'Source Name'
                 },
                 'full_name': 'My Demonstrative Test Source',
                 'id': 'MyDemoSource',
                 'name': 'My Test Source',
                 'owner': 'DemoOrg',
                 'owner_type': 'Organization',
                 'public_access': 'Edit',
                 'short_code': 'MyDemoSource',
                 'source_type': 'Dictionary',
                 'supported_locales': 'en,fk',
                 'type': 'Source',
                 'website': 'https://www.demoland.fake/source'
             },
             {
                 'canonical_url': 'https://demo.fake/CodeSystem/FHIRSource',
                 'custom_validation_schema': 'None',
                 'default_locale': 'en',
                 'description': 'Using this source just for FHIR testing purposes',
                 'external_id': 'FHIR1641246546-IDK',
                 'extras': {
                     'ex_name': 'FHIR Source Name'
                 },
                 'full_name': 'My Demonstrative FHIR Test Source',
                 'id': 'MyFHIRSource',
                 'name': 'My FHIR Source',
                 'owner': 'DemoOrg',
                 'owner_type': 'Organization',
                 'public_access': 'Edit',
                 'short_code': 'MyFHIRSource',
                 'source_type': 'Dictionary',
                 'supported_locales': 'en,fk',
                 'type': 'Source',
                 'website': 'https://www.demoland.fake/source'
             },
             {
                 'canonical_url': 'https://demo.fake/ValueSet/Collection',
                 'collection_type': 'Value Set',
                 'custom_validation_schema': 'None',
                 'default_locale': 'en',
                 'description': 'Using this collection just for testing purposes',
                 'external_id': '654246546-IDK',
                 'extras': {
                     'ex_name': 'Collection Name'
                 },
                 'full_name': 'My Demonstrative Test Collection',
                 'id': 'MyDemoCollection',
                 'name': 'My Test Collection',
                 'owner': 'DemoOrg',
                 'owner_type': 'Organization',
                 'public_access': 'Edit',
                 'short_code': 'MyDemoCollection',
                 'supported_locales': 'en,fk',
                 'type': 'Collection',
                 'website': 'https://www.demoland.fake/source'
             },
             {
                 'concept_class': 'Misc',
                 'datatype': 'None',
                 'descriptions': [{
                                      'description': 'Just one description',
                                      'locale': 'en'
                                  }],
                 'external_id': 'HSpL3hSBx6F',
                 'id': 'Act',
                 'names': [{
                               'locale': 'en',
                               'name': 'Active Demo Concept',
                               'name_type': 'Fully Specified'
                           }],
                 'owner': 'DemoOrg',
                 'owner_type': 'Organization',
                 'retired': False,
                 'source': 'MyDemoSource',
                 'type': 'Concept'
             },
             {
                 'concept_class': 'Misc',
                 'datatype': 'None',
                 'external_id': 'HSpL3hSBx6F',
                 'id': 'Ret',
                 'names': [{
                               'locale': 'en',
                               'name': 'Retired Demo Concept',
                               'name_type': 'Fully Specified'
                           }],
                 'owner': 'DemoOrg',
                 'owner_type': 'Organization',
                 'retired': True,
                 'source': 'MyDemoSource',
                 'type': 'Concept'
             },
             {
                 'concept_class': 'Misc',
                 'datatype': 'None',
                 'external_id': 'HSpL3hSBx6F',
                 'id': 'Child',
                 'names': [{
                               'locale': 'en',
                               'name': 'Child Demo Concept',
                               'name_type': 'Fully Specified'
                           }],
                 'owner': 'DemoOrg',
                 'owner_type': 'Organization',
                 'retired': False,
                 'source': 'MyDemoSource',
                 'type': 'Concept'
             },
             {
                 'concept_class': 'Misc',
                 'datatype': 'None',
                 'descriptions': [{
                                      'description': 'Main description',
                                      'locale': 'en'
                                  },
                                  {
                                      'description': 'Secondary description',
                                      'locale': 'en'
                                  }],
                 'external_id': 'asdkfjhasLKfjhsa',
                 'id': 'Child_of_child',
                 'names': [{
                               'locale': 'en',
                               'name': 'Child of the Child Demo Concept',
                               'name_type': 'Fully Specified'
                           }],
                 'owner': 'DemoOrg',
                 'owner_type': 'Organization',
                 'retired': False,
                 'source': 'MyDemoSource',
                 'type': 'Concept'
             },
             {
                 'from_concept_url': '/orgs/DemoOrg/sources/MyDemoSource/concepts//orgs/DemoOrg/sources/MyDemoSource/concepts/Child_of_child//',  # pylint: disable=line-too-long
                 'map_type': 'Child-Parent',
                 'owner': 'DemoOrg',
                 'owner_type': 'Organization',
                 'retired': False,
                 'source': 'MyDemoSource',
                 'to_concept_url': '/orgs/DemoOrg/sources/MyDemoSource/concepts//orgs/DemoOrg/sources/MyDemoSource/concepts/Child//',  # pylint: disable=line-too-long
                 'type': 'Mapping'
             },
             {
                 'from_concept_url': '/orgs/DemoOrg/sources/MyDemoSource/concepts/Act/',
                 'map_type': 'Parent-child',
                 'owner': 'DemoOrg',
                 'owner_type': 'Organization',
                 'source': 'MyDemoSource',
                 'to_concept_url': '/orgs/DemoOrg/sources/MyDemoSource/concepts/Child/',
                 'type': 'Mapping'
             },
             {
                 'from_concept_url': '/orgs/DemoOrg/sources/MyDemoSource/concepts/Act/',
                 'map_type': 'Parent-child-retired',
                 'owner': 'DemoOrg',
                 'owner_type': 'Organization',
                 'retired': True,
                 'source': 'MyDemoSource',
                 'to_concept_url': '/orgs/DemoOrg/sources/MyDemoSource/concepts/Child/',
                 'type': 'Mapping'
             }]
        )

    @patch('core.importers.input_parsers.ZipFile')
    def test_parse_zip_file(self, zipfile_mock):
        file = open(os.path.join(os.path.dirname(__file__), '..', 'samples/DemoSource_v1.0.20230526120030.zip'), 'r')
        real_zipfile = ZipFile(file.name, 'r')
        zipfile_mock.return_value = real_zipfile

        parser = ImportContentParser(file=file)
        parser.parse()

        self.assertIsNotNone(parser.content)
        zipfile_mock.assert_called_once_with(file, 'r')

    @patch('core.importers.input_parsers.ZipFile')
    @patch('requests.get')
    def test_parse_zip_file_url(self, requests_get_mock, zipfile_mock):
        file = open(os.path.join(os.path.dirname(__file__), '..', 'samples/DemoSource_v1.0.20230526120030.zip'), 'r')
        requests_get_mock.return_value = Mock(ok=True, content=b'file-content')
        real_zipfile = ZipFile(file.name, 'r')
        zipfile_mock.return_value = real_zipfile

        parser = ImportContentParser(file_url='https://file.zip')
        parser.parse()

        self.assertIsNotNone(parser.content)

        file = open(os.path.join(os.path.dirname(__file__), '..', 'samples/DemoSource_v1.0.20230526120030.zip'), 'r')
        real_zipfile = ZipFile(file.name, 'r')
        zipfile_mock.return_value = real_zipfile
        parser1 = ImportContentParser(file=file)
        parser1.parse()

        self.assertEqual(parser1.content, parser.content)
        requests_get_mock.assert_called_once_with(
            'https://file.zip', headers={'User-Agent': 'OCL'}, stream=True, timeout=30)
