import json
import os
import unittest
import uuid

from celery_once import AlreadyQueued
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models import F
from mock import patch, Mock, ANY, call, PropertyMock
from ocldev.oclcsvtojsonconverter import OclStandardCsvToJsonConverter

from core.collections.models import Collection
from core.common.constants import CUSTOM_VALIDATION_SCHEMA_OPENMRS
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.importers.models import BulkImport, BulkImportInline, BulkImportParallelRunner
from core.importers.views import csv_file_data_to_input_list
from core.mappings.models import Mapping
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
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

        bulk_import = BulkImport(content=content, username='ocladmin', update_if_exists=True)
        bulk_import.run()

        self.assertEqual(bulk_import.result.json, {"all": "200"})
        self.assertEqual(bulk_import.result.detailed_summary, 'summary')
        self.assertEqual(bulk_import.result.report, 'report')

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
        self.assertFalse(Concept.objects.filter(mnemonic='Food').exists())

        OrganizationSourceFactory(
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

        self.assertEqual(Concept.objects.filter(mnemonic='Food').count(), 2)
        self.assertEqual(
            Concept.objects.filter(mnemonic='Food', id=F('versioned_object_id')).first().versions.count(), 1
        )
        self.assertTrue(Concept.objects.filter(mnemonic='Food', is_latest_version=True).exists())
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)

        data = {
            "type": "Concept", "id": "Food", "concept_class": "Root",
            "datatype": "Rule", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(
            Concept.objects.filter(mnemonic='Food', id=F('versioned_object_id')).first().versions.count(), 2
        )
        self.assertTrue(Concept.objects.filter(mnemonic='Food', is_latest_version=True, datatype='Rule').exists())
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.updated), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)
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
        self.assertEqual(
            Mapping.objects.filter(map_type='Has Child', id=F('versioned_object_id')).first().versions.count(), 1
        )
        self.assertTrue(Mapping.objects.filter(map_type='Has Child', is_latest_version=True).exists())
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

        self.assertEqual(
            Mapping.objects.filter(map_type='Has Child', id=F('versioned_object_id')).first().versions.count(), 2
        )
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.updated), 1)
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
        self.assertEqual(len(importer.updated), 4)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        collection = Collection.objects.filter(uri='/orgs/PEPFAR/collections/MER-R-MOH-Facility-FY19/').first()
        self.assertEqual(collection.expansions.count(), 1)
        self.assertEqual(collection.expansion.concepts.count(), 4)
        self.assertEqual(collection.expansion.mappings.count(), 0)
        self.assertEqual(collection.references.count(), 4)
        batch_index_resources_mock.apply_async.assert_called()

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
        self.assertEqual(len(importer.updated), 12)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        batch_index_resources_mock.apply_async.assert_called()

    @patch('core.importers.models.batch_index_resources')
    def test_csv_import_with_retired_concepts(self, batch_index_resources_mock):
        file_content = open(
            os.path.join(os.path.dirname(__file__), '..', 'samples/ocl_csv_with_retired_concepts.csv'), 'r').read()
        data = OclStandardCsvToJsonConverter(
            input_list=csv_file_data_to_input_list(file_content), allow_special_characters=True).process()
        importer = BulkImportInline(data, 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 10)
        self.assertEqual(len(importer.created), 10)
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

    @unittest.skip('[Skipped] OPENMRS CSV Import Sample')
    @patch('core.importers.models.batch_index_resources')
    def test_openmrs_schema_csv_import(self, batch_index_resources_mock):
        call_command('import_lookup_values')
        org = OrganizationFactory(mnemonic='MSFOCP')
        OrganizationSourceFactory(
            mnemonic='Implementationtest', organization=org, custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
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

    @unittest.skip('[Skipped] PEPFAR (small) Import Sample')
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
    @patch('core.importers.models.RedisService')
    def test_make_parts(self, redis_service_mock):
        redis_service_mock.return_value = Mock()

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
        self.assertEqual([l['type'] for l in importer.parts[0]], ['Organization', 'Organization'])
        self.assertEqual([l['type'] for l in importer.parts[1]], ['Source', 'Source'])
        self.assertEqual([l['type'] for l in importer.parts[2]], ['Source Version'])
        self.assertEqual(list({l['type'] for l in importer.parts[3]}), ['Concept'])
        self.assertEqual(list({l['type'] for l in importer.parts[4]}), ['Mapping'])
        self.assertEqual([l['type'] for l in importer.parts[5]], ['Source Version', 'Source Version'])
        self.assertEqual(list({l['type'] for l in importer.parts[6]}), ['Concept'])

    @patch('core.importers.models.RedisService')
    def test_is_any_process_alive(self, redis_service_mock):
        redis_service_mock.return_value = Mock()
        importer = BulkImportParallelRunner(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/sample_ocldev.json'), 'r'
            ).read(),
            'ocladmin', True
        )
        self.assertFalse(importer.is_any_process_alive())

        importer.groups = [
            Mock(completed_count=Mock(return_value=5), __len__=Mock(return_value=5)),
            Mock(completed_count=Mock(return_value=5), __len__=Mock(return_value=5)),
        ]
        self.assertFalse(importer.is_any_process_alive())

        importer.groups = [
            Mock(completed_count=Mock(return_value=10), __len__=Mock(return_value=10)),
            Mock(completed_count=Mock(return_value=5), __len__=Mock(return_value=10)),
        ]
        self.assertTrue(importer.is_any_process_alive())

        importer.groups = [
            Mock(completed_count=Mock(return_value=5), __len__=Mock(return_value=10)),
            Mock(completed_count=Mock(return_value=5), __len__=Mock(return_value=10)),
        ]
        self.assertTrue(importer.is_any_process_alive())

        importer.groups = [
            Mock(completed_count=Mock(return_value=0), __len__=Mock(return_value=10)),
        ]
        self.assertTrue(importer.is_any_process_alive())

        importer.groups = [
            Mock(completed_count=Mock(return_value=9), __len__=Mock(return_value=10)),
        ]
        self.assertTrue(importer.is_any_process_alive())

    @patch('core.importers.models.RedisService')
    def test_get_overall_tasks_progress(self, redis_service_mock):
        redis_instance_mock = Mock()
        redis_instance_mock.get_int.side_effect = [100, 50]
        redis_service_mock.return_value = redis_instance_mock
        importer = BulkImportParallelRunner(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/sample_ocldev.json'), 'r'
            ).read(),
            'ocladmin', True
        )
        self.assertEqual(importer.get_overall_tasks_progress(), 0)
        importer.tasks = [Mock(task_id='task1'), Mock(task_id='task2')]
        self.assertEqual(importer.get_overall_tasks_progress(), 150)

    @patch('core.importers.models.RedisService')
    def test_update_elapsed_seconds(self, redis_service_mock):
        redis_service_mock.return_value = Mock()

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

    @patch('core.importers.models.RedisService')
    def test_notify_progress(self, redis_service_mock):  # pylint: disable=no-self-use
        redis_instance_mock = Mock(set_json=Mock())
        redis_service_mock.return_value = redis_instance_mock
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

        redis_instance_mock.set_json.assert_called_once_with(
            'task-id',
            dict(
                summary="Started: 2020-12-07 13:09:01.793877 | Processed: 0/64 | Time: 10.45secs",
                #sub_task_ids=['task-1', 'task-2']
            )
        )

    def test_chunker_list(self):
        self.assertEqual(
            list(BulkImportParallelRunner.chunker_list([1, 2, 3], 3)), [[1], [2], [3]]
        )
        self.assertEqual(
            list(BulkImportParallelRunner.chunker_list([1, 2, 3], 2)), [[1, 3], [2]]
        )
        self.assertEqual(
            list(BulkImportParallelRunner.chunker_list([1, 2, 3], 1)), [[1, 2, 3]]
        )


class BulkImportViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.superuser = UserProfile.objects.get(username='ocladmin')
        self.token = self.superuser.get_token()

    @patch('core.importers.views.flower_get')
    def test_get_without_task_id(self, flower_get_mock):
        task_id1 = f"{str(uuid.uuid4())}-ocladmin~priority"
        task_id2 = f"{str(uuid.uuid4())}-foobar~normal"
        flower_tasks = {
            task_id1: dict(name='core.common.tasks.bulk_import', state='success'),
            task_id2: dict(name='core.common.tasks.bulk_import', state='failed'),
        }
        flower_get_mock.return_value = Mock(json=Mock(return_value=flower_tasks))

        response = self.client.get(
            '/importers/bulk-import/?username=ocladmin',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [dict(queue='priority', state='success', task=task_id1, username='ocladmin')])

        response = self.client.get(
            '/importers/bulk-import/?username=foobar',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [dict(queue='normal', state='failed', task=task_id2, username='foobar')])

        response = self.client.get(
            '/importers/bulk-import/priority/?username=ocladmin',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [dict(queue='priority', state='success', task=task_id1, username='ocladmin')])

        response = self.client.get(
            '/importers/bulk-import/normal/?username=ocladmin',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
        calls = flower_get_mock.mock_calls
        self.assertTrue(call('api/tasks?taskname=core.common.tasks.bulk_import') in calls)
        self.assertTrue(call('api/tasks?taskname=core.common.tasks.bulk_import_parallel_inline') in calls)
        self.assertTrue(call('api/tasks?taskname=core.common.tasks.bulk_import_inline') in calls)

    @patch('core.importers.views.AsyncResult')
    def test_get_with_task_id_success(self, async_result_klass_mock):
        task_id1 = f"{str(uuid.uuid4())}-ocladmin'~priority"
        task_id2 = f"{str(uuid.uuid4())}-foobar~normal"
        foobar_user = UserProfileFactory(username='foobar')

        response = self.client.get(
            f'/importers/bulk-import/?task={task_id1}',
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 403)

        async_result_mock = dict(json='json-format', report='report-format', detailed_summary='summary')
        async_result_instance_mock = Mock(successful=Mock(return_value=True), get=Mock(return_value=async_result_mock))
        async_result_klass_mock.return_value = async_result_instance_mock

        response = self.client.get(
            f'/importers/bulk-import/?task={task_id2}',
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'summary')

        response = self.client.get(
            f'/importers/bulk-import/?task={task_id2}&result=json',
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'json-format')

        response = self.client.get(
            f'/importers/bulk-import/?task={task_id2}&result=report',
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'report-format')

        async_result_instance_mock.successful.assert_called()

    @patch('core.importers.views.AsyncResult')
    def test_get_with_task_id_failed(self, async_result_klass_mock):
        task_id = f"{str(uuid.uuid4())}-foobar~normal"
        foobar_user = UserProfileFactory(username='foobar')

        async_result_instance_mock = Mock(
            successful=Mock(return_value=False), failed=Mock(return_value=True), result='task-failure-result'
        )
        async_result_klass_mock.return_value = async_result_instance_mock

        response = self.client.get(
            f'/importers/bulk-import/?task={task_id}',
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception='task-failure-result'))
        async_result_instance_mock.successful.assert_called()
        async_result_instance_mock.failed.assert_called()

    @patch('core.importers.views.task_exists')
    @patch('core.importers.views.AsyncResult')
    def test_get_task_pending(self, async_result_klass_mock, task_exists_mock):
        task_exists_mock.return_value = False
        task_id = f"{str(uuid.uuid4())}-foobar~normal"
        foobar_user = UserProfileFactory(username='foobar')

        async_result_instance_mock = Mock(
            successful=Mock(return_value=False), failed=Mock(return_value=False), state='PENDING', id=task_id
        )
        async_result_klass_mock.return_value = async_result_instance_mock

        response = self.client.get(
            f'/importers/bulk-import/?task={task_id}',
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, dict(exception=f"task {task_id} not found"))

        async_result_instance_mock.successful.assert_called_once()
        async_result_instance_mock.failed.assert_called_once()
        task_exists_mock.assert_called_once()

        task_exists_mock.return_value = True
        response = self.client.get(
            f'/importers/bulk-import/?task={task_id}',
            HTTP_AUTHORIZATION='Token ' + foobar_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, dict(task=task_id, state='PENDING', username='foobar', queue='normal'))

    @patch('core.importers.views.flower_get')
    def test_get_flower_failed(self, flower_get_mock):
        flower_get_mock.side_effect = Exception('Service Down')

        response = self.client.get(
            '/importers/bulk-import/?username=ocladmin',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.data,
            dict(detail='Flower service returned unexpected result. Maybe check healthcheck.',
                 exception=str('Service Down'))
        )

    def test_post_400(self):
        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=1',
            'some-data',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception="update_if_exists must be either 'true' or 'false'"))

        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception="No content to import"))

    @patch('core.importers.views.queue_bulk_import')
    def test_post_409(self, queue_bulk_import_mock):
        queue_bulk_import_mock.side_effect = AlreadyQueued('already-queued')

        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=true',
            'some-data',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data, dict(exception="The same import has been already queued"))

    @patch('core.common.tasks.bulk_import')
    def test_post_202(self, bulk_import_mock):
        task_mock = Mock(id='task-id', state='pending')
        bulk_import_mock.apply_async = Mock(return_value=task_mock)

        response = self.client.post(
            "/importers/bulk-import/?update_if_exists=true",
            'some-data',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, dict(task='task-id', state='pending', queue='default', username='ocladmin'))
        self.assertEqual(bulk_import_mock.apply_async.call_count, 1)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], (('"some-data"', 'ocladmin', True),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][37:], 'ocladmin~priority')
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['queue'], 'bulk_import_root')

        random_user = UserProfileFactory(username='oswell')

        response = self.client.post(
            "/importers/bulk-import/?update_if_exists=true",
            'some-data',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, dict(task='task-id', state='pending', queue='default', username='oswell'))
        self.assertEqual(bulk_import_mock.apply_async.call_count, 2)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], (('"some-data"', 'oswell', True),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][37:], 'oswell~default')
        self.assertTrue(bulk_import_mock.apply_async.call_args[1]['queue'].startswith('bulk_import_'))

        response = self.client.post(
            "/importers/bulk-import/foobar-queue/?update_if_exists=true",
            'some-data',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, dict(task='task-id', state='pending', queue='default', username='oswell'))
        self.assertEqual(bulk_import_mock.apply_async.call_count, 3)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], (('"some-data"', 'oswell', True),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][37:], 'oswell~foobar-queue')
        self.assertTrue(bulk_import_mock.apply_async.call_args[1]['queue'].startswith('bulk_import_'))

    def test_post_file_upload_400(self):
        response = self.client.post(
            "/importers/bulk-import/upload/?update_if_exists=true",
            {'file': ''},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception='No content to import'))

    def test_post_file_url_400(self):
        response = self.client.post(
            "/importers/bulk-import/file-url/?update_if_exists=true",
            {'file_url': 'foobar'},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(exception='No content to import'))

    @patch('core.common.tasks.bulk_import_parallel_inline')
    def test_post_inline_parallel_202(self, bulk_import_mock):
        task_id = 'ace5abf4-3b7f-4e4a-b16f-d1c041088c3e-ocladmin~priority'
        task_mock = Mock(id=task_id, state='pending')
        bulk_import_mock.apply_async = Mock(return_value=task_mock)
        file = SimpleUploadedFile('file.json', b'{"key": "value"}', "application/json")

        response = self.client.post(
            "/importers/bulk-import-parallel-inline/?update_if_exists=true",
            {'file': file},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, dict(task=task_id, state='pending', queue='priority', username='ocladmin'))
        self.assertEqual(bulk_import_mock.apply_async.call_count, 1)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], (('{"key": "value"}', 'ocladmin', True, 5),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][37:], 'ocladmin~priority')
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['queue'], 'bulk_import_root')

    @patch('core.common.tasks.bulk_import_inline')
    def test_post_inline_202(self, bulk_import_mock):
        task_id = 'ace5abf4-3b7f-4e4a-b16f-d1c041088c3e-ocladmin~priority'
        task_mock = Mock(id=task_id, state='pending')
        bulk_import_mock.apply_async = Mock(return_value=task_mock)
        file = SimpleUploadedFile('file.json', b'{"key": "value"}', "application/json")

        response = self.client.post(
            "/importers/bulk-import-inline/?update_if_exists=true",
            {'file': file},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, dict(task=task_id, state='pending', queue='priority', username='ocladmin'))
        self.assertEqual(bulk_import_mock.apply_async.call_count, 1)
        self.assertEqual(bulk_import_mock.apply_async.call_args[0], (('{"key": "value"}', 'ocladmin', True),))
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['task_id'][37:], 'ocladmin~priority')
        self.assertEqual(bulk_import_mock.apply_async.call_args[1]['queue'], 'bulk_import_root')

    @patch('core.importers.views.QueueOnce.once_backend', new_callable=PropertyMock)
    @patch('core.importers.views.AsyncResult')
    @patch('core.importers.views.app')
    def test_delete_parallel_import_204(self, celery_app_mock, async_result_mock, queue_once_backend_mock):
        clear_lock_mock = Mock()
        queue_once_backend_mock.return_value = Mock(clear_lock=clear_lock_mock)
        result_mock = Mock(
            args=['content', 'ocladmin', True, 5]  # content, username, update_if_exists, threads
        )
        result_mock.name = 'core.common.tasks.bulk_import_parallel_inline'
        async_result_mock.return_value = result_mock
        task_id = 'ace5abf4-3b7f-4e4a-b16f-d1c041088c3e-ocladmin~priority'
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

    @patch('core.importers.views.QueueOnce.once_backend', new_callable=PropertyMock)
    @patch('core.importers.views.AsyncResult')
    @patch('core.importers.views.app')
    def test_delete_204(self, celery_app_mock, async_result_mock, queue_once_backend_mock):
        clear_lock_mock = Mock()
        queue_once_backend_mock.return_value = Mock(clear_lock=clear_lock_mock)
        result_mock = Mock(
            args=['content', 'ocladmin', True]  # content, username, update_if_exists
        )
        result_mock.name = 'core.common.tasks.bulk_import'
        async_result_mock.return_value = result_mock
        task_id = 'ace5abf4-3b7f-4e4a-b16f-d1c041088c3e-ocladmin~priority'
        response = self.client.delete(
            "/importers/bulk-import/",
            {'task_id': task_id, 'signal': 'SIGTERM'},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 204)
        celery_app_mock.control.revoke.assert_called_once_with(task_id, terminate=True, signal='SIGTERM')
        self.assertTrue(clear_lock_mock.call_args[0][0].endswith(
            'core.common.tasks.bulk_import_to_import-content_update_if_exists-True_username-ocladmin'
        ))

    @patch('core.importers.views.AsyncResult')
    @patch('core.importers.views.app')
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
        celery_app_mock.control.revoke.side_effect = Exception('foobar')
        response = self.client.delete(
            "/importers/bulk-import/",
            {'task_id': task_id, 'signal': 'SIGKILL'},
            HTTP_AUTHORIZATION='Token ' + self.token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, dict(errors=('foobar',)))
        celery_app_mock.control.revoke.assert_called_once_with(task_id, terminate=True, signal='SIGKILL')

    @patch('core.importers.views.AsyncResult')
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
        response = self.client.delete(
            "/importers/bulk-import/",
            {'task_id': task_id, 'signal': 'SIGKILL'},
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
        )

        self.assertEqual(response.status_code, 403)
