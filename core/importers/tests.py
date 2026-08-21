import io
import json
import os
import tarfile
import tempfile
import time
import uuid
from json import JSONDecodeError
from unittest.mock import mock_open
from zipfile import ZipFile

import responses
from celery_once import AlreadyQueued
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models import F
from ijson import JSONError
from mock import patch, Mock, ANY, PropertyMock, call
from ocldev.oclcsvtojsonconverter import OclStandardCsvToJsonConverter
from rest_framework.exceptions import ValidationError

from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.constants import OPENMRS_VALIDATION_SCHEMA, DEPRECATED_API_HEADER, ACCESS_TYPE_NONE
from core.common.tasks import post_import_update_resource_counts, bulk_import_parts_inline, bulk_import_inline, \
    bulk_import
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.common.utils import decode_string, startswith_temp_version
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.importers.importer import ImporterSubtask, ImportTask, ImportTaskSummary, Importer, ResourceImporter
from core.importers.input_parsers import ImportContentParser
from core.importers.models import BulkImport, BulkImportInline, BulkImportParallelRunner, \
    CREATED, UPDATED, DELETED, PERMISSION_DENIED, UNCHANGED, FAILED, NOT_FOUND, \
    BaseImporter, BaseResourceImporter, OrganizationImporter, SourceImporter, SourceVersionImporter, \
    CollectionImporter, CollectionVersionImporter, ConceptImporter, MappingImporter, ReferenceImporter
from core.importers.views import csv_file_data_to_input_list, ImportRetrieveDestroyMixin
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.sources.constants import AUTO_ID_SEQUENTIAL, AUTO_ID_UUID
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

    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
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
    def test_concept_import(self, batch_index_resources_mock):  # pylint: disable=too-many-statements
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
        importer.index_resources = True
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
        importer.index_resources = True
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
        importer.index_resources = True
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

    def test_concept_import_with_nested_mapping_to_concept_code(self):
        """A Concept line's nested mappings must survive the importer's field allowlist (ocl_issues#2683)."""
        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD'
        )
        ConceptFactory(parent=source, mnemonic='Food')

        data = {
            "type": "Concept", "id": "Papaya", "concept_class": "Root",
            "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Papaya", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
            "mappings": [{
                "map_type": "Same As", "to_source_url": "/orgs/DemoOrg/sources/DemoSource/",
                "to_concept_code": "Food"
            }],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.failed, [])
        papaya = Concept.objects.filter(mnemonic='Papaya', id=F('versioned_object_id')).first()
        self.assertIsNotNone(papaya)
        mapping = Mapping.objects.filter(parent=source, id=F('versioned_object_id')).first()
        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.map_type, 'Same As')
        self.assertEqual(mapping.from_concept_id, papaya.id)
        self.assertEqual(mapping.to_concept_code, 'Food')

    def test_concept_import_with_nested_mapping_parent_concept_sentinel(self):
        """An id-less concept line can self-map via to_concept: __parent_concept (ocl_issues#2683)."""
        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD'
        )

        data = {
            "type": "Concept", "concept_class": "Diagnosis",
            "datatype": "N/A", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{
                "name": "Nested mapping probe", "locale": "en", "locale_preferred": "True",
                "name_type": "Fully Specified"
            }],
            "mappings": [{
                "map_type": "Same As", "to_source_url": "/orgs/DemoOrg/sources/DemoSource/",
                "to_concept": "__parent_concept"
            }],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.failed, [])
        concept = source.concepts_set.filter(id=F('versioned_object_id')).first()
        self.assertIsNotNone(concept)
        mapping = Mapping.objects.filter(parent=source, id=F('versioned_object_id')).first()
        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.map_type, 'Same As')
        self.assertEqual(mapping.from_concept_id, concept.id)
        self.assertEqual(mapping.to_concept_id, concept.id)

    def test_concept_import_update_with_nested_mappings(self):
        """Nested mappings must also apply when update_if_exists=true versions an existing concept."""
        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD'
        )
        ConceptFactory(parent=source, mnemonic='Food')

        data = {
            "type": "Concept", "id": "Papaya", "concept_class": "Root",
            "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Papaya", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
        }
        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(Mapping.objects.filter(parent=source).count(), 0)

        update_data = {
            "type": "Concept", "id": "Papaya", "concept_class": "Root",
            "datatype": "Rule", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Papaya", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
            "mappings": [{
                "map_type": "Same As", "to_source_url": "/orgs/DemoOrg/sources/DemoSource/",
                "to_concept_code": "Food"
            }],
        }
        importer = BulkImportInline(json.dumps(update_data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.updated), 1)
        self.assertEqual(importer.failed, [])
        mapping = Mapping.objects.filter(parent=source, id=F('versioned_object_id')).first()
        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.to_concept_code, 'Food')

    def test_concept_import_with_failing_nested_mapping_is_not_silent(self):
        """A nested mapping that fails validation must fail the concept line, not report a silent success."""
        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD'
        )
        ConceptFactory(parent=source, mnemonic='Food')

        data = {
            "type": "Concept", "id": "Papaya", "concept_class": "Root",
            "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Papaya", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
            "mappings": [{
                "to_source_url": "/orgs/DemoOrg/sources/DemoSource/", "to_concept_code": "Food"
            }],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.failed), 1)
        self.assertIn('mappings', importer.failed[0]['errors'])
        self.assertFalse(Concept.objects.filter(mnemonic='Papaya').exists())
        self.assertEqual(Mapping.objects.filter(parent=source).count(), 0)

    def test_concept_import_processes_hierarchy_for_inline_import(self):
        source = OrganizationSourceFactory(
            organization=OrganizationFactory(mnemonic='DemoOrg'), mnemonic='DemoSource', version='HEAD'
        )
        parent_concept = ConceptFactory(parent=source, mnemonic='Parent')
        data = {
            "type": "Concept", "id": "Child", "concept_class": "Root",
            "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Child", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
            "parent_concept_urls": [parent_concept.uri],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.failed, [])
        child_concept = Concept.objects.filter(mnemonic='Child', id=F('versioned_object_id')).first()
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        parent_concept.refresh_from_db()
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])

    def test_concept_import_processes_hierarchy_for_auto_id_when_skip_hierarchy_tasks(self):
        source = OrganizationSourceFactory(
            organization=OrganizationFactory(mnemonic='DemoOrg'), mnemonic='DemoSource', version='HEAD',
            autoid_concept_mnemonic=AUTO_ID_SEQUENTIAL
        )
        parent_concept = ConceptFactory(parent=source, mnemonic='Parent')
        data = {
            "type": "Concept", "concept_class": "Root",
            "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Child", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [],
            "parent_concept_urls": [parent_concept.uri],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True, skip_hierarchy_tasks=True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.failed, [])
        child_concept = Concept.objects.filter(parent=source, mnemonic='1', id=F('versioned_object_id')).first()
        self.assertEqual(list(child_concept.parent_concept_urls), [parent_concept.uri])
        parent_concept.refresh_from_db()
        self.assertEqual(list(parent_concept.child_concept_urls), [child_concept.uri])

    @patch('core.importers.models.batch_index_resources')
    def test_concept_import_with_extras_update(self, batch_index_resources_mock):  # pylint: disable=too-many-statements
        batch_index_resources_mock.__name__ = 'batch_index_resources'
        self.assertFalse(Concept.objects.filter(mnemonic='Food').exists())

        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD'
        )

        data = {
            "type": "Concept", "id": "Food", "concept_class": "Root",
            "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [], 'extras': {'foo': 'bar'}
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
        self.assertEqual(concept.extras, {'foo': 'bar'})
        self.assertEqual(concept.get_latest_version().extras, {'foo': 'bar'})
        self.assertTrue(Concept.objects.filter(mnemonic='Food', is_latest_version=True).exists())
        batch_index_resources_mock.apply_async.assert_not_called()
        self.assertEqual(
            Concept.objects.filter(mnemonic='Food', id=F('versioned_object_id')).first().versions.count(), 1
        )
        self.assertTrue(Concept.objects.filter(mnemonic='Food', is_latest_version=True).exists())
        batch_index_resources_mock.apply_async.assert_not_called()

        data = {
            "type": "Concept", "id": "Food", "concept_class": "Root",
            "datatype": "Rule", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": []
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
        self.assertEqual(concept.extras, {'foo': 'bar'})
        self.assertEqual(concept.get_latest_version().extras, {'foo': 'bar'})
        self.assertTrue(Concept.objects.filter(mnemonic='Food', is_latest_version=True, datatype='Rule').exists())
        batch_index_resources_mock.apply_async.assert_not_called()

        data = {
            "type": "Concept", "id": "Food", "concept_class": "Root",
            "datatype": "Foo", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [], 'extras': {}
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
        self.assertEqual(concept.extras, {})
        self.assertEqual(concept.get_latest_version().extras, {})
        self.assertEqual(concept.get_latest_version().prev_version.extras, {'foo': 'bar'})
        self.assertTrue(Concept.objects.filter(mnemonic='Food', is_latest_version=True, datatype='Foo').exists())
        batch_index_resources_mock.apply_async.assert_not_called()

        data = {
            "type": "Concept", "id": "Food", "concept_class": "Root",
            "datatype": "Foo", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": [], 'extras': {'foo': 'bar'}
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.updated), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)
        self.assertEqual(source.concepts_set.count(), 5)
        concept = Concept.objects.filter(mnemonic='Food', id=F('versioned_object_id')).first()
        self.assertEqual(concept.versions.count(), 4)
        self.assertEqual(concept.extras, {'foo': 'bar'})
        self.assertEqual(concept.get_latest_version().extras, {'foo': 'bar'})
        self.assertEqual(concept.get_latest_version().prev_version.extras, {})

        data = {
            "type": "Concept", "id": "Food", "concept_class": "Root",
            "datatype": "Foo", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [{"name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            "descriptions": []
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.unchanged), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)
        self.assertEqual(source.concepts_set.count(), 5)
        concept = Concept.objects.filter(mnemonic='Food', id=F('versioned_object_id')).first()
        self.assertEqual(concept.versions.count(), 4)
        self.assertEqual(concept.extras, {'foo': 'bar'})
        self.assertEqual(concept.get_latest_version().extras, {'foo': 'bar'})
        self.assertEqual(concept.get_latest_version().prev_version.extras, {})
        self.assertTrue(Concept.objects.filter(mnemonic='Food', is_latest_version=True, datatype='Foo').exists())

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
        batch_index_resources_mock.apply_async.assert_not_called()

    def test_concept_import_without_id(self):
        """An id-less concept line is left to Concept.persist_new (same as mappings) and must not fail the import."""
        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD'
        )

        def concept_data(mnemonic):
            data = {
                "type": "Concept", "concept_class": "Root",
                "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
                "names": [{
                    "name": "Food", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"
                }],
                "descriptions": [],
            }
            if mnemonic is not None:
                data['id'] = mnemonic
            return data

        good_line = concept_data('Food')
        no_id_line = concept_data(None)
        blank_id_line = concept_data('')
        another_good_line = concept_data('Drink')

        importer = BulkImportInline(
            '\n'.join(json.dumps(data) for data in [good_line, no_id_line, blank_id_line, another_good_line]),
            'ocladmin', True
        )
        importer.run()

        self.assertEqual(importer.processed, 4)
        self.assertEqual(importer.created, [good_line, no_id_line, blank_id_line, another_good_line])
        self.assertEqual(importer.failed, [])
        self.assertEqual(importer.invalid, [])

        mnemonics = list(source.concepts_set.filter(id=F('versioned_object_id')).values_list('mnemonic', flat=True))
        self.assertEqual(len(mnemonics), 4)
        self.assertTrue({'Food', 'Drink'}.issubset(set(mnemonics)))
        # the source assigns no mnemonics, so Concept.persist_new falls back to the concept's own id
        for mnemonic in set(mnemonics) - {'Food', 'Drink'}:
            self.assertTrue(mnemonic.isdigit())
            self.assertFalse(startswith_temp_version(mnemonic))

    def test_concept_import_without_id_is_allowed_for_auto_id_source(self):
        """The same id-less line stays valid when the source assigns concept mnemonics itself."""
        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD',
            autoid_concept_mnemonic=AUTO_ID_SEQUENTIAL
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
        self.assertEqual(
            list(source.concepts_set.filter(id=F('versioned_object_id')).values_list('mnemonic', flat=True)), ['1']
        )

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

    def test_mapping_import_without_id(self):
        """Mapping lines carry no "id" in the common case -- the mnemonic is left to Mapping.persist_new.

        This is the behaviour concept lines without an "id" follow too, and chunking must not get in the way of it.
        """
        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD'
        )
        ConceptFactory(parent=source, mnemonic='Vegetable')
        ConceptFactory(parent=source, mnemonic='Corn')
        ConceptFactory(parent=source, mnemonic='Food')

        def mapping_data(map_type, to_concept):
            return {
                "type": "Mapping", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
                "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
                "to_concept_url": f"/orgs/DemoOrg/sources/DemoSource/concepts/{to_concept}/",
                "map_type": map_type,
            }

        lines = [mapping_data('Has Child', 'Corn'), mapping_data('Same As', 'Food')]

        importer = BulkImportInline('\n'.join(json.dumps(line) for line in lines), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 2)
        self.assertEqual(importer.created, lines)
        self.assertEqual(importer.failed, [])
        self.assertEqual(importer.invalid, [])

        mnemonics = list(
            Mapping.objects.filter(parent=source, id=F('versioned_object_id')).values_list('mnemonic', flat=True))
        self.assertEqual(len(mnemonics), 2)
        # the source assigns no mnemonics, so Mapping.persist_new falls back to the mapping's own id
        for mnemonic in mnemonics:
            self.assertTrue(mnemonic.isdigit())
            self.assertFalse(startswith_temp_version(mnemonic))

    def test_mapping_import_without_id_for_auto_id_source(self):
        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD',
            autoid_mapping_mnemonic=AUTO_ID_SEQUENTIAL
        )
        ConceptFactory(parent=source, mnemonic='Vegetable')
        ConceptFactory(parent=source, mnemonic='Corn')

        data = {
            "type": "Mapping", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
            "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Corn/",
            "map_type": "Has Child",
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 1)
        self.assertEqual(importer.failed, [])
        self.assertEqual(
            list(Mapping.objects.filter(parent=source, id=F('versioned_object_id')).values_list('mnemonic', flat=True)),
            ['1']
        )

    @patch('core.importers.models.batch_index_resources')
    def test_mapping_import(self, batch_index_resources_mock):  # pylint: disable=too-many-statements
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
        batch_index_resources_mock.apply_async.assert_not_called()
        self.assertEqual(
            Mapping.objects.filter(map_type='Has Child', id=F('versioned_object_id')).first().versions.count(), 1
        )
        self.assertTrue(Mapping.objects.filter(map_type='Has Child', is_latest_version=True).exists())
        batch_index_resources_mock.apply_async.assert_not_called()

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
        batch_index_resources_mock.apply_async.assert_not_called()
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.updated), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)
        self.assertFalse(mapping.retired)
        self.assertFalse(mapping.get_latest_version().retired)

        data = {
            "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Corn/",
            "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
            "type": "Mapping", "source": "DemoSource",
            "extras": {"foo": "bar"}, "owner": "DemoOrg", "map_type": "Has Child", "owner_type": "Organization",
            "external_id": None, "retired": True
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        mapping = Mapping.objects.filter(map_type='Has Child', id=F('versioned_object_id')).first()
        self.assertEqual(mapping.versions.count(), 3)
        self.assertTrue(mapping.retired)
        self.assertTrue(mapping.get_latest_version().retired)
        batch_index_resources_mock.apply_async.assert_not_called()
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.updated), 1)
        self.assertEqual(importer.failed, [])
        self.assertTrue(importer.elapsed_seconds > 0)

        data = {
            "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Corn/",
            "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
            "type": "Mapping", "source": "DemoSourceNotExisting",
            "extras": {"foo": "bar"}, "owner": "DemoOrg", "map_type": "Has Child", "owner_type": "Organization",
            "external_id": None, "retired": True
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(mapping.versions.count(), 3)
        self.assertTrue(mapping.retired)
        self.assertTrue(mapping.get_latest_version().retired)
        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.failed), 1)
        self.assertEqual(importer.failed[0]['errors'], {'source': 'Not Found'})
        self.assertTrue(importer.elapsed_seconds > 0)

    @patch('core.importers.models.batch_index_resources')
    def test_mapping_import_cache_reuse_correctness(self, batch_index_resources_mock):
        # Several mappings in the same BulkImportInline run sharing the same from/to concepts --
        # mirrors the production case (a mapping chunk resolving concepts that already exist) where
        # the lookup cache introduced for performance should be transparent to correctness: every
        # mapping must resolve to the same from_concept/to_concept regardless of cache hits.
        batch_index_resources_mock.__name__ = 'batch_index_resources'
        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD'
        )
        vegetable = ConceptFactory(parent=source, mnemonic='Vegetable')
        corn = ConceptFactory(parent=source, mnemonic='Corn')
        carrot = ConceptFactory(parent=source, mnemonic='Carrot')

        input_list = [
            {
                "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Corn/",
                "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
                "type": "Mapping", "source": "DemoSource",
                "owner": "DemoOrg", "map_type": "Has Child", "owner_type": "Organization",
            },
            {
                "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Carrot/",
                "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
                "type": "Mapping", "source": "DemoSource",
                "owner": "DemoOrg", "map_type": "Has Child", "owner_type": "Organization",
            },
            {
                "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Corn/",
                "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
                "type": "Mapping", "source": "DemoSource",
                "owner": "DemoOrg", "map_type": "Same As", "owner_type": "Organization",
            },
        ]

        importer = BulkImportInline(content=None, username='ocladmin', update_if_exists=True, input_list=input_list)
        importer.run()

        self.assertEqual(importer.processed, 3)
        self.assertEqual(len(importer.created), 3)
        self.assertEqual(importer.failed, [])

        has_child_corn = Mapping.objects.filter(
            map_type='Has Child', id=F('versioned_object_id'), to_concept_code='Corn'
        ).first()
        has_child_carrot = Mapping.objects.filter(
            map_type='Has Child', id=F('versioned_object_id'), to_concept_code='Carrot'
        ).first()
        same_as_corn = Mapping.objects.filter(
            map_type='Same As', id=F('versioned_object_id'), to_concept_code='Corn'
        ).first()

        self.assertEqual(has_child_corn.from_concept_id, vegetable.id)
        self.assertEqual(has_child_corn.to_concept_id, corn.id)
        self.assertEqual(has_child_carrot.from_concept_id, vegetable.id)
        self.assertEqual(has_child_carrot.to_concept_id, carrot.id)
        self.assertEqual(same_as_corn.from_concept_id, vegetable.id)
        self.assertEqual(same_as_corn.to_concept_id, corn.id)

        # cache was actually exercised and shared across items, not just present and unused
        self.assertEqual(len(importer.cache['source_by_owner']), 1)
        self.assertEqual(len(importer.cache['concept_versioned_id_by_uri']), 3)  # Vegetable, Corn, Carrot
        self.assertEqual(len(importer.cache['concept_by_expr']), 3)

    @patch('core.importers.models.batch_index_resources')
    def test_mapping_import_cache_stale_after_concept_created_in_same_run(self, batch_index_resources_mock):
        # Documents the invariant called out on BulkImportInline.cache: the lookup cache stores
        # misses too, so if a resource type is created mid-run and an earlier item in the *same* run
        # already cached its absence, a later item referencing it can get a stale miss. This is safe
        # in production because BulkImportParallelRunner only ever puts one resource type per chunk
        # (a mapping chunk never creates the concepts it resolves) -- this test exists so that
        # invariant has an executable tripwire if chunking ever changes to interleave resource types.
        batch_index_resources_mock.__name__ = 'batch_index_resources'
        source = OrganizationSourceFactory(
            organization=(OrganizationFactory(mnemonic='DemoOrg')), mnemonic='DemoSource', version='HEAD'
        )
        ConceptFactory(parent=source, mnemonic='Vegetable')

        input_list = [
            {
                "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Sugar/",
                "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
                "type": "Mapping", "source": "DemoSource",
                "owner": "DemoOrg", "map_type": "Has Child", "owner_type": "Organization",
            },
            {
                "id": "Sugar", "type": "Concept", "concept_class": "Misc", "datatype": "None",
                "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
                "names": [
                    {"name": "Sugar", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}],
            },
            {
                "to_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Sugar/",
                "from_concept_url": "/orgs/DemoOrg/sources/DemoSource/concepts/Vegetable/",
                "type": "Mapping", "source": "DemoSource",
                "owner": "DemoOrg", "map_type": "Same As", "owner_type": "Organization",
            },
        ]

        importer = BulkImportInline(content=None, username='ocladmin', update_if_exists=True, input_list=input_list)
        importer.run()

        sugar = Concept.objects.filter(mnemonic='Sugar', id=F('versioned_object_id')).first()
        self.assertIsNotNone(sugar)

        same_as_sugar = Mapping.objects.filter(
            map_type='Same As', id=F('versioned_object_id')
        ).first()
        self.assertIsNotNone(same_as_sugar)
        # Known limitation of the invariant above: the third item's lookup hits the stale cached
        # miss from the first item (Sugar didn't exist yet when that mapping resolved it), so the
        # mapping is created without a linked to_concept even though Sugar exists by this point. If
        # this assertion ever starts failing because to_concept_id became non-null, the cache started
        # being invalidated correctly (or chunking changed) -- update this test and the comment on
        # BulkImportInline.cache rather than treating the new behavior as a regression.
        self.assertIsNone(same_as_sugar.to_concept_id)

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
        batch_index_resources_mock.apply_async.assert_not_called()

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
        self.assertEqual(len(importer.created), 8)
        self.assertEqual(len(importer.failed), 1)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.updated), 0)
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
        self.assertEqual(len(importer.failed), 2)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.exists), 3)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.unchanged), 4)  # due to same concept checksum
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        collection = Collection.objects.filter(uri='/orgs/PEPFAR/collections/MER-R-MOH-Facility-FY19/').first()
        self.assertEqual(collection.expansions.count(), 1)
        self.assertEqual(collection.expansion.concepts.count(), 4)
        self.assertEqual(collection.expansion.mappings.count(), 0)
        self.assertEqual(collection.references.count(), 4)
        batch_index_resources_mock.apply_async.assert_not_called()

    @patch('core.collections.models.batch_index_resources', Mock())
    @patch('core.importers.models.batch_index_resources')
    def test_reference_import_with_delete(self, batch_index_resources_mock):
        importer = BulkImportInline(
            open(
                os.path.join(os.path.dirname(__file__), '..', 'samples/sample_collection_references_with_delete.json'),
                'r'
            ).read(),
            'ocladmin', True
        )
        importer.run()
        self.assertEqual(importer.processed, 11)
        self.assertEqual(len(importer.created), 8)
        self.assertEqual(len(importer.deleted), 2)
        self.assertEqual(len(importer.failed), 1)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.unchanged), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        collection = Collection.objects.filter(uri='/orgs/PEPFAR/collections/MER-R-MOH-Facility-FY19/').first()
        self.assertEqual(collection.expansions.count(), 1)
        self.assertEqual(collection.expansion.concepts.count(), 2)
        self.assertEqual(collection.expansion.mappings.count(), 0)
        self.assertEqual(collection.references.count(), 2)
        batch_index_resources_mock.apply_async.assert_not_called()

    @patch('core.collections.models.batch_index_resources', Mock())
    @patch('core.importers.models.batch_index_resources')
    def test_reference_import_with_delete_all(self, batch_index_resources_mock):
        importer = BulkImportInline(
            open(
                os.path.join(
                    os.path.dirname(__file__), '..', 'samples/sample_collection_references_with_delete_all.json'
                ),
                'r'
            ).read(),
            'ocladmin', True
        )
        importer.run()
        self.assertEqual(importer.processed, 10)
        self.assertEqual(len(importer.created), 8)
        self.assertEqual(len(importer.deleted), 1)
        self.assertEqual(len(importer.failed), 1)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.unchanged), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        collection = Collection.objects.filter(uri='/orgs/PEPFAR/collections/MER-R-MOH-Facility-FY19/').first()
        self.assertEqual(collection.expansions.count(), 1)
        self.assertEqual(collection.expansion.concepts.count(), 0)
        self.assertEqual(collection.expansion.mappings.count(), 0)
        self.assertEqual(collection.references.count(), 0)
        batch_index_resources_mock.apply_async.assert_not_called()

    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.importers.models.batch_index_resources')
    def test_sample_import(self, batch_index_resources_mock):  # pylint: disable=too-many-statements
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
        self.assertEqual(batch_index_resources_mock.apply_async.call_count, 0)

        food_slash_papaya = Concept.objects.filter(mnemonic='Food%2FPapaya').first()
        self.assertEqual(decode_string(food_slash_papaya.mnemonic), 'Food/Papaya')
        self.assertEqual(food_slash_papaya.uri, "/orgs/DemoOrg/sources/DemoSource/concepts/Food%2FPapaya/")
        self.assertEqual(food_slash_papaya.get_indirect_mappings().count(), 1)

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
        self.assertEqual(batch_index_resources_mock.apply_async.call_count, 0)
        concept = Concept.objects.filter(mnemonic='Corn').first()
        self.assertTrue(concept.get_latest_version().retired)
        self.assertTrue(concept.versioned_object.retired)
        self.assertFalse(concept.get_latest_version().prev_version.retired)

        data = {
            "type": "Concept", "id": "Cherry", "concept_class": "Product",
            "datatype": "None", "source": "DemoSource", "owner": "DemoOrg", "owner_type": "Organization",
            "names": [
                {"name": "Cherry", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified",
                 "retire_reason": "Not needed", "retired": True},
                {"name": "Cherri", "locale": "en", "locale_preferred": "True", "name_type": "Fully Specified"}
            ],
            "descriptions": [],
        }

        importer = BulkImportInline(json.dumps(data), 'ocladmin', True)
        importer.run()

        self.assertEqual(importer.processed, 1)
        self.assertEqual(len(importer.created), 0)
        self.assertEqual(len(importer.exists), 0)
        self.assertEqual(len(importer.updated), 1)
        self.assertEqual(len(importer.deleted), 0)
        self.assertEqual(len(importer.failed), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.others), 0)
        self.assertEqual(len(importer.permission_denied), 0)
        self.assertEqual(batch_index_resources_mock.apply_async.call_count, 0)
        concept = Concept.objects.filter(mnemonic='Cherry').first()
        latest = concept.get_latest_version()
        prev = latest.prev_version
        self.assertEqual(latest.names.count(), 2)
        self.assertTrue(latest.names.filter(name='Cherry', retired=True, retire_reason='Not needed').exists())
        self.assertTrue(latest.names.filter(name='Cherri', retired=False, retire_reason__isnull=True).exists())
        self.assertTrue(latest.display_name, 'Cherri')
        self.assertEqual(prev.names.count(), 1)
        self.assertFalse(prev.names.filter(name='Cherri').exists())
        self.assertTrue(prev.names.filter(name='Cherry', retired=False, retire_reason__isnull=True).exists())

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
        self.assertEqual(batch_index_resources_mock.apply_async.call_count, 0)
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
        batch_index_resources_mock.apply_async.assert_not_called()

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
        batch_index_resources_mock.apply_async.assert_not_called()

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
        batch_index_resources_mock.apply_async.assert_not_called()

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
        self.assertEqual(len(importer.created), 28)
        self.assertEqual(len(importer.updated), 0)
        self.assertEqual(len(importer.invalid), 0)
        self.assertEqual(len(importer.failed), 3)
        self.assertEqual(len(importer.permission_denied), 0)
        batch_index_resources_mock.apply_async.assert_not_called()

    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
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
        batch_index_resources_mock.apply_async.assert_not_called()


class ResourceImporterModelsTest(OCLTestCase):
    def test_base_importer_run_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            BaseImporter(content='{}', username='ocladmin', update_if_exists=False).run()

    def test_base_importer_uses_explicit_user(self):
        user = UserProfileFactory()
        importer = BaseImporter(content='{}', username='ocladmin', update_if_exists=False, user=user)
        self.assertEqual(importer.user, user)

    def test_base_resource_importer_get_resource_type_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            BaseResourceImporter.get_resource_type()

    def test_base_resource_importer_process_not_implemented(self):
        importer = BaseResourceImporter({'id': 'x'}, UserProfileFactory())
        with self.assertRaises(NotImplementedError):
            importer.process()

    def test_base_resource_importer_exists_defaults_false(self):
        importer = BaseResourceImporter({'id': 'x'}, UserProfileFactory())
        self.assertFalse(importer.exists())

    def test_base_resource_importer_get_owner_type_filter_user(self):
        importer = OrganizationImporter({'owner_type': 'User'}, UserProfileFactory())
        self.assertEqual(importer.get_owner_type_filter(), 'user__username')

    def test_base_resource_importer_get_owner_user(self):
        owner_user = UserProfileFactory()
        importer = OrganizationImporter({'owner_type': 'User', 'owner': owner_user.username}, owner_user)
        self.assertEqual(importer.get_owner(), owner_user)

    def test_base_resource_importer_clean_invalid_returns_false(self):
        importer = OrganizationImporter({}, UserProfileFactory())
        self.assertFalse(importer.clean())

    def test_organization_importer_delete_permission_denied(self):
        org = OrganizationFactory(mnemonic='PermOrg')
        user = UserProfileFactory()
        importer = OrganizationImporter({'id': 'PermOrg'}, user)
        self.assertEqual(importer.delete(), PERMISSION_DENIED)
        self.assertTrue(Organization.objects.filter(mnemonic=org.mnemonic).exists())

    def test_organization_importer_delete_not_found(self):
        importer = OrganizationImporter({'id': 'DoesNotExist'}, UserProfileFactory())
        self.assertEqual(importer.delete(), NOT_FOUND)

    def test_source_importer_process_permission_denied(self):
        org = OrganizationFactory(mnemonic='PermOrgSrc')
        user = UserProfileFactory()
        importer = SourceImporter(
            {'id': 'perm-src', 'name': 'Perm Source', 'owner_type': 'Organization', 'owner': org.mnemonic}, user
        )
        result = importer.run()
        self.assertEqual(result, PERMISSION_DENIED)

    def test_source_importer_delete_not_found(self):
        importer = SourceImporter(
            {'id': 'no-such-source', 'owner_type': 'Organization', 'owner': 'NoSuchOrg'}, UserProfileFactory()
        )
        self.assertEqual(importer.delete(), NOT_FOUND)

    def test_source_importer_delete_permission_denied(self):
        source = OrganizationSourceFactory(
            organization=OrganizationFactory(mnemonic='PermOrgSrcDel'), mnemonic='PermSrcDel'
        )
        user = UserProfileFactory()
        importer = SourceImporter(
            {'id': source.mnemonic, 'owner_type': 'Organization', 'owner': source.organization.mnemonic}, user
        )
        self.assertEqual(importer.delete(), PERMISSION_DENIED)

    def test_source_importer_delete_success(self):
        source = OrganizationSourceFactory(
            organization=OrganizationFactory(mnemonic='DelOrgSrc'), mnemonic='DelSrc'
        )
        admin = UserProfile.objects.get(username='ocladmin')
        importer = SourceImporter(
            {'id': source.mnemonic, 'owner_type': 'Organization', 'owner': source.organization.mnemonic}, admin
        )
        self.assertEqual(importer.delete(), DELETED)

    def test_source_version_importer_process_permission_denied(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='PermOrgSrcVer'))
        user = UserProfileFactory()
        importer = SourceVersionImporter(
            {
                'id': 'v1', 'source': source.mnemonic, 'owner_type': 'Organization',
                'owner': source.organization.mnemonic},
            user
        )
        self.assertEqual(importer.run(), PERMISSION_DENIED)

    def test_collection_importer_process_permission_denied(self):
        org = OrganizationFactory(mnemonic='PermOrgColl')
        user = UserProfileFactory()
        importer = CollectionImporter(
            {'id': 'perm-coll', 'name': 'Perm Collection', 'owner_type': 'Organization', 'owner': org.mnemonic}, user
        )
        self.assertEqual(importer.run(), PERMISSION_DENIED)

    def test_collection_importer_delete_permission_denied(self):
        collection = OrganizationCollectionFactory(
            organization=OrganizationFactory(mnemonic='PermOrgCollDel'), mnemonic='PermCollDel'
        )
        user = UserProfileFactory()
        importer = CollectionImporter(
            {'id': collection.mnemonic, 'owner_type': 'Organization', 'owner': collection.organization.mnemonic}, user
        )
        self.assertEqual(importer.delete(), PERMISSION_DENIED)

    def test_collection_importer_delete_not_found(self):
        importer = CollectionImporter(
            {'id': 'no-such-collection', 'owner_type': 'Organization', 'owner': 'NoSuchOrg'}, UserProfileFactory()
        )
        self.assertEqual(importer.delete(), NOT_FOUND)

    def test_collection_importer_delete_success(self):
        collection = OrganizationCollectionFactory(
            organization=OrganizationFactory(mnemonic='DelOrgColl'), mnemonic='DelColl'
        )
        admin = UserProfile.objects.get(username='ocladmin')
        importer = CollectionImporter(
            {'id': collection.mnemonic, 'owner_type': 'Organization', 'owner': collection.organization.mnemonic}, admin
        )
        self.assertEqual(importer.delete(), DELETED)

    def test_collection_version_importer_process_permission_denied(self):
        collection = OrganizationCollectionFactory(organization=OrganizationFactory(mnemonic='PermOrgCollVer'))
        user = UserProfileFactory()
        importer = CollectionVersionImporter(
            {
                'id': 'v1', 'collection': collection.mnemonic, 'owner_type': 'Organization',
                'owner': collection.organization.mnemonic
            },
            user
        )
        self.assertEqual(importer.run(), PERMISSION_DENIED)

    def test_concept_importer_clean_invalid_returns_false(self):
        importer = ConceptImporter({}, UserProfileFactory(), False)
        self.assertFalse(importer.clean())

    def test_concept_importer_process_parent_not_found(self):
        user = UserProfile.objects.get(username='ocladmin')
        importer = ConceptImporter(
            {
                'id': 'orphan', 'concept_class': 'Misc', 'datatype': 'None', 'owner_type': 'Organization',
                'owner': 'NoSuchOrg', 'source': 'NoSuchSource',
                'names': [{'name': 'orphan', 'locale': 'en', 'locale_preferred': True, 'name_type': 'Fully Specified'}],
            },
            user, False
        )
        result = importer.run()
        self.assertEqual(result, {'source': 'Not Found'})

    def test_concept_importer_delete_invalid_returns_false(self):
        importer = ConceptImporter({}, UserProfileFactory(), False)
        self.assertFalse(importer.delete())

    def test_concept_importer_delete_not_found(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='ConceptDelOrg'))
        admin = UserProfile.objects.get(username='ocladmin')
        importer = ConceptImporter(
            {
                'id': 'missing-concept', 'concept_class': 'Misc', 'datatype': 'None',
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            },
            admin, False
        )
        self.assertEqual(importer.delete(), NOT_FOUND)

    def test_concept_importer_delete_permission_denied(self):
        source = OrganizationSourceFactory(
            organization=OrganizationFactory(mnemonic='ConceptDelPermOrg'), public_access=ACCESS_TYPE_NONE)
        concept = ConceptFactory(parent=source, mnemonic='ToDelete')
        user = UserProfileFactory()
        importer = ConceptImporter(
            {
                'id': concept.mnemonic, 'concept_class': concept.concept_class, 'datatype': concept.datatype,
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            },
            user, False
        )
        self.assertEqual(importer.delete(), PERMISSION_DENIED)

    def test_concept_importer_process_with_update_comment_and_skip_hierarchy(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='ConceptCommentOrg'))
        admin = UserProfile.objects.get(username='ocladmin')
        importer = ConceptImporter(
            {
                'id': 'commented', 'concept_class': 'Misc', 'datatype': 'None',
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
                'update_comment': 'initial import',
                'names': [
                    {'name': 'commented', 'locale': 'en', 'locale_preferred': True, 'name_type': 'Fully Specified'}],
            },
            admin, False, skip_hierarchy_tasks=True
        )
        result = importer.run()
        self.assertEqual(result, CREATED)

    def test_mapping_importer_clean_invalid_returns_false(self):
        importer = MappingImporter({}, UserProfileFactory(), False)
        self.assertFalse(importer.clean())

    def test_mapping_importer_delete_invalid_returns_false(self):
        importer = MappingImporter({}, UserProfileFactory(), False)
        self.assertFalse(importer.delete())

    def test_mapping_importer_process_permission_denied(self):
        source = OrganizationSourceFactory(
            organization=OrganizationFactory(mnemonic='MapPermOrg'), public_access=ACCESS_TYPE_NONE)
        concept1 = ConceptFactory(parent=source, mnemonic='MapFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='MapTo')
        user = UserProfileFactory()
        importer = MappingImporter(
            {
                'map_type': 'Same As',
                'from_concept_url': concept1.uri, 'to_concept_url': concept2.uri,
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            },
            user, False
        )
        self.assertEqual(importer.run(), PERMISSION_DENIED)

    def test_mapping_importer_delete_not_found(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='MapDelOrg'))
        concept1 = ConceptFactory(parent=source, mnemonic='MapDelFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='MapDelTo')
        admin = UserProfile.objects.get(username='ocladmin')
        importer = MappingImporter(
            {
                'map_type': 'Same As',
                'from_concept_url': concept1.uri, 'to_concept_url': concept2.uri,
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            },
            admin, False
        )
        self.assertEqual(importer.delete(), NOT_FOUND)

    def test_mapping_importer_delete_permission_denied(self):
        source = OrganizationSourceFactory(
            organization=OrganizationFactory(mnemonic='MapDelPermOrg'), public_access=ACCESS_TYPE_NONE)
        concept1 = ConceptFactory(parent=source, mnemonic='MapDelPermFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='MapDelPermTo')
        MappingFactory(from_concept=concept1, to_concept=concept2, parent=source, map_type='Same As')
        user = UserProfileFactory()
        importer = MappingImporter(
            {
                'map_type': 'Same As',
                'from_concept_url': concept1.uri, 'to_concept_url': concept2.uri,
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            },
            user, False
        )
        self.assertEqual(importer.delete(), PERMISSION_DENIED)

    def test_mapping_importer_process_with_id_and_update_comment(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='MapIdOrg'))
        concept1 = ConceptFactory(parent=source, mnemonic='MapIdFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='MapIdTo')
        admin = UserProfile.objects.get(username='ocladmin')
        importer = MappingImporter(
            {
                'id': 'custom-mapping-id', 'map_type': 'Same As',
                'from_concept_url': concept1.uri, 'to_concept_url': concept2.uri,
                'to_source_url': source.url,
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
                'update_comment': 'initial mapping',
            },
            admin, False
        )
        result = importer.run()
        self.assertEqual(result, CREATED)
        self.assertTrue(Mapping.objects.filter(mnemonic='custom-mapping-id').exists())

    def test_mapping_importer_process_with_concept_codes(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='MapCodeOrg'))
        concept1 = ConceptFactory(parent=source, mnemonic='CodeFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='CodeTo')
        admin = UserProfile.objects.get(username='ocladmin')
        importer = MappingImporter(
            {
                'map_type': 'Same As',
                'from_concept_url': concept1.uri, 'from_concept_code': concept1.mnemonic,
                'to_concept_code': concept2.mnemonic,
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            },
            admin, False
        )
        result = importer.run()
        self.assertEqual(result, CREATED)

    def test_mapping_importer_process_returns_unchanged_for_identical_resubmit(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='MapUnchangedOrg'))
        concept1 = ConceptFactory(parent=source, mnemonic='UnchangedFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='UnchangedTo')
        admin = UserProfile.objects.get(username='ocladmin')
        data = {
            'map_type': 'Same As',
            'from_concept_url': concept1.uri, 'to_concept_url': concept2.uri,
            'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
        }
        MappingImporter(dict(data), admin, False).run()

        importer = MappingImporter(dict(data), admin, True)
        result = importer.run()

        self.assertEqual(result, UNCHANGED)

    def test_mapping_importer_process_picks_non_retired_when_multiple_match(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='MapMultiOrg'))
        concept1 = ConceptFactory(parent=source, mnemonic='MultiFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='MultiTo')
        admin = UserProfile.objects.get(username='ocladmin')
        MappingFactory(from_concept=concept1, to_concept=concept2, parent=source, map_type='Same As', retired=True)
        MappingFactory(from_concept=concept1, to_concept=concept2, parent=source, map_type='Same As', retired=False)

        importer = MappingImporter(
            {
                'map_type': 'Same As', 'from_concept_url': concept1.uri, 'to_concept_url': concept2.uri,
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
                'extras': {'note': 'updated'},
            },
            admin, True
        )
        result = importer.run()
        self.assertIn(result, [CREATED, UPDATED, UNCHANGED])

    def test_reference_importer_get_queryset_is_cached(self):
        collection = OrganizationCollectionFactory(organization=OrganizationFactory(mnemonic='RefCacheOrg'))
        importer = ReferenceImporter(
            {
                'data': {'expressions': []}, 'collection': collection.mnemonic, 'owner_type': 'Organization',
                'owner': collection.organization.mnemonic
            },
            UserProfile.objects.get(username='ocladmin')
        )
        first = importer.get_queryset()
        second = importer.get_queryset()
        self.assertIs(first, second)

    def test_reference_importer_process_permission_denied(self):
        collection = OrganizationCollectionFactory(
            organization=OrganizationFactory(mnemonic='RefPermOrg'), public_access=ACCESS_TYPE_NONE)
        user = UserProfileFactory()
        importer = ReferenceImporter(
            {
                'data': {'expressions': []}, 'collection': collection.mnemonic, 'owner_type': 'Organization',
                'owner': collection.organization.mnemonic
            },
            user
        )
        self.assertEqual(importer.process(), PERMISSION_DENIED)

    def test_reference_importer_process_not_found(self):
        importer = ReferenceImporter(
            {
                'data': {'expressions': []}, 'collection': 'NoSuchCollection', 'owner_type': 'Organization',
                'owner': 'NoSuchOrg'
            },
            UserProfile.objects.get(username='ocladmin')
        )
        self.assertEqual(importer.process(), NOT_FOUND)

    def test_reference_importer_delete_not_found_no_collection(self):
        importer = ReferenceImporter(
            {'data': {}, 'collection': 'NoSuchCollection', 'owner_type': 'Organization', 'owner': 'NoSuchOrg'},
            UserProfile.objects.get(username='ocladmin')
        )
        self.assertEqual(importer.delete(), NOT_FOUND)

    def test_reference_importer_delete_permission_denied(self):
        collection = OrganizationCollectionFactory(
            organization=OrganizationFactory(mnemonic='RefDelPermOrg'), public_access=ACCESS_TYPE_NONE)
        user = UserProfileFactory()
        importer = ReferenceImporter(
            {
                'data': {}, 'collection': collection.mnemonic, 'owner_type': 'Organization',
                'owner': collection.organization.mnemonic
            },
            user
        )
        self.assertEqual(importer.delete(), PERMISSION_DENIED)

    def test_reference_importer_delete_all_not_found_when_no_references(self):
        collection = OrganizationCollectionFactory(organization=OrganizationFactory(mnemonic='RefDelAllOrg'))
        admin = UserProfile.objects.get(username='ocladmin')
        importer = ReferenceImporter(
            {
                'data': {'expressions': ['*']}, 'collection': collection.mnemonic, 'owner_type': 'Organization',
                'owner': collection.organization.mnemonic
            },
            admin
        )
        self.assertEqual(importer.delete(), NOT_FOUND)

    def test_reference_importer_delete_with_cascade_and_transform_filters(self):
        collection = OrganizationCollectionFactory(organization=OrganizationFactory(mnemonic='RefDelCascadeOrg'))
        source = OrganizationSourceFactory(organization=collection.organization, mnemonic='RefDelCascadeSrc')
        concept = ConceptFactory(parent=source, mnemonic='RefDelCascadeConcept')
        admin = UserProfile.objects.get(username='ocladmin')

        add_importer = ReferenceImporter(
            {
                'data': {'expressions': [concept.uri]}, 'collection': collection.mnemonic,
                'owner_type': 'Organization', 'owner': collection.organization.mnemonic
            },
            admin
        )
        add_importer.process()

        delete_importer = ReferenceImporter(
            {
                'data': {'expressions': [concept.uri]}, 'collection': collection.mnemonic,
                'owner_type': 'Organization', 'owner': collection.organization.mnemonic,
                '__cascade': 'sourcemappings', 'transform': 'resourceVersions',
            },
            admin
        )
        result = delete_importer.delete()
        self.assertEqual(result, NOT_FOUND)  # reference has no cascade/transform, so filters exclude it

    def test_handle_item_import_result_invalid(self):
        importer = BulkImportInline('', 'ocladmin', False, input_list=[{'type': 'noop'}])
        importer.handle_item_import_result(False, {'id': 'x'})
        self.assertEqual(importer.invalid, [{'id': 'x'}])

    def test_handle_item_import_result_failed(self):
        importer = BulkImportInline('', 'ocladmin', False, input_list=[{'type': 'noop'}])
        importer.handle_item_import_result(FAILED, {'id': 'x'})
        self.assertEqual(importer.failed, [{'id': 'x'}])

    def test_handle_item_import_result_unexpected(self):
        importer = BulkImportInline('', 'ocladmin', False, input_list=[{'type': 'noop'}])
        importer.handle_item_import_result('unexpected-value', {'id': 'x'})
        self.assertEqual(importer.others, [{'id': 'x'}])

    def test_run_appends_unknown_item_type(self):
        data = {'no_type_field': True}
        importer = BulkImportInline(json.dumps(data), 'ocladmin', False)
        importer.run()
        self.assertEqual(importer.unknown, [{'no_type_field': True}])

    @patch('core.importers.models.ERRBIT_LOGGER')
    @patch.object(ConceptImporter, 'run', side_effect=Exception('concept boom'))
    def test_run_logs_concept_import_exception(self, _run_mock, errbit_mock):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='ExcConceptOrg'))
        data = {
            'type': 'Concept', 'id': 'Boom', 'concept_class': 'Misc', 'datatype': 'None',
            'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            'names': [{'name': 'Boom', 'locale': 'en', 'locale_preferred': True, 'name_type': 'Fully Specified'}],
        }
        importer = BulkImportInline(json.dumps(data), 'ocladmin', False)
        importer.run()

        self.assertEqual(len(importer.failed), 1)
        errbit_mock.log.assert_called_once()

    @patch('core.importers.models.batch_index_resources')
    @patch('core.importers.models.ERRBIT_LOGGER')
    @patch.object(MappingImporter, 'run', side_effect=Exception('mapping boom'))
    def test_run_logs_mapping_import_exception(self, _run_mock, errbit_mock, batch_index_resources_mock):
        batch_index_resources_mock.__name__ = 'batch_index_resources'
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='ExcMapOrg'))
        concept1 = ConceptFactory(parent=source, mnemonic='ExcMapFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='ExcMapTo')
        data = {
            'type': 'Mapping', 'map_type': 'Same As',
            'from_concept_url': concept1.uri, 'to_concept_url': concept2.uri,
            'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
        }
        importer = BulkImportInline(json.dumps(data), 'ocladmin', False)
        importer.run()

        self.assertEqual(len(importer.failed), 1)
        errbit_mock.log.assert_called_once()

    @patch('core.importers.models.batch_index_resources')
    def test_mapping_import_indexes_resources(self, batch_index_resources_mock):
        batch_index_resources_mock.__name__ = 'batch_index_resources'
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='MapIndexOrg'))
        concept1 = ConceptFactory(parent=source, mnemonic='IndexFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='IndexTo')
        data = {
            'type': 'Mapping', 'map_type': 'Same As',
            'from_concept_url': concept1.uri, 'to_concept_url': concept2.uri,
            'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
        }
        importer = BulkImportInline(json.dumps(data), 'ocladmin', False)
        importer.index_resources = True
        importer.run()

        self.assertEqual(len(importer.created), 1)
        batch_index_resources_mock.apply_async.assert_called_with(
            ('mapping', {'id__in': ANY}, True), queue='indexing', permanent=False)

    def test_organization_importer_process_creates_successfully(self):
        admin = UserProfile.objects.get(username='ocladmin')
        importer = OrganizationImporter({'id': 'NewOrgViaImporter', 'name': 'New Org'}, admin)
        result = importer.run()
        self.assertEqual(result, CREATED)
        self.assertTrue(Organization.objects.filter(mnemonic='NewOrgViaImporter').exists())

    def test_source_importer_delete_records_exception(self):
        source = OrganizationSourceFactory(
            organization=OrganizationFactory(mnemonic='SrcDelExcOrg'), mnemonic='SrcDelExc')
        admin = UserProfile.objects.get(username='ocladmin')
        importer = SourceImporter(
            {'id': source.mnemonic, 'owner_type': 'Organization', 'owner': source.organization.mnemonic}, admin
        )
        with patch.object(Source, 'delete', side_effect=Exception('delete boom')):
            result = importer.delete()
        self.assertEqual(result, {'errors': ('delete boom',)})

    def test_collection_importer_delete_records_exception(self):
        collection = OrganizationCollectionFactory(
            organization=OrganizationFactory(mnemonic='CollDelExcOrg'), mnemonic='CollDelExc'
        )
        admin = UserProfile.objects.get(username='ocladmin')
        importer = CollectionImporter(
            {'id': collection.mnemonic, 'owner_type': 'Organization', 'owner': collection.organization.mnemonic},
            admin
        )
        with patch.object(Collection, 'delete', side_effect=Exception('delete boom')):
            result = importer.delete()
        self.assertEqual(result, {'errors': ('delete boom',)})

    def test_concept_importer_delete_records_exception(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='ConceptDelExcOrg'))
        concept = ConceptFactory(parent=source, mnemonic='ConceptDelExc')
        admin = UserProfile.objects.get(username='ocladmin')
        importer = ConceptImporter(
            {
                'id': concept.mnemonic, 'concept_class': concept.concept_class, 'datatype': concept.datatype,
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            },
            admin, False
        )
        with patch.object(Concept, 'retire', side_effect=Exception('retire boom')):
            result = importer.delete()
        self.assertEqual(result, {'errors': ('retire boom',)})

    def test_mapping_importer_delete_records_exception(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='MapDelExcOrg'))
        concept1 = ConceptFactory(parent=source, mnemonic='MapDelExcFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='MapDelExcTo')
        MappingFactory(from_concept=concept1, to_concept=concept2, parent=source, map_type='Same As')
        admin = UserProfile.objects.get(username='ocladmin')
        importer = MappingImporter(
            {
                'map_type': 'Same As', 'from_concept_url': concept1.uri, 'to_concept_url': concept2.uri,
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            },
            admin, False
        )
        with patch.object(Mapping, 'retire', side_effect=Exception('retire boom')):
            result = importer.delete()
        self.assertEqual(result, {'errors': ('retire boom',)})

    @patch('core.importers.models.Mapping.persist_new')
    def test_mapping_importer_process_returns_errors_when_persist_fails(self, persist_new_mock):
        persist_new_mock.return_value = Mock(id=None, errors={'to_concept_url': ['invalid']})
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='MapFailOrg'))
        concept1 = ConceptFactory(parent=source, mnemonic='FailFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='FailTo')
        admin = UserProfile.objects.get(username='ocladmin')
        importer = MappingImporter(
            {
                'map_type': 'Same As', 'from_concept_url': concept1.uri, 'to_concept_url': concept2.uri,
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            },
            admin, False
        )
        result = importer.run()
        self.assertEqual(result, {'to_concept_url': ['invalid']})

    def test_mapping_importer_process_orders_by_id_when_all_matches_retired(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='MapAllRetiredOrg'))
        concept1 = ConceptFactory(parent=source, mnemonic='AllRetiredFrom')
        concept2 = ConceptFactory(parent=source, mnemonic='AllRetiredTo')
        admin = UserProfile.objects.get(username='ocladmin')
        MappingFactory(from_concept=concept1, to_concept=concept2, parent=source, map_type='Same As', retired=True)
        MappingFactory(from_concept=concept1, to_concept=concept2, parent=source, map_type='Same As', retired=True)

        importer = MappingImporter(
            {
                'map_type': 'Same As', 'from_concept_url': concept1.uri, 'to_concept_url': concept2.uri,
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            },
            admin, True
        )
        result = importer.run()
        self.assertIn(result, [CREATED, UPDATED, UNCHANGED])

    def test_mapping_importer_parse_encodes_concept_codes_with_special_chars(self):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='MapEncodeOrg2'))
        importer = MappingImporter(
            {
                'map_type': 'Same As', 'from_concept_url': '/x/', 'from_concept_code': 'has/slash',
                'to_concept_code': 'other/slash',
                'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
            },
            UserProfile.objects.get(username='ocladmin'), False
        )
        with patch.object(MappingImporter, 'allowed_fields', MappingImporter.allowed_fields + ['from_concept_code']):
            importer.parse()

        self.assertNotIn('/', importer.data.get('from_concept_code', ''))
        self.assertNotIn('/', importer.data.get('to_concept_code', ''))

    def test_notify_progress_updates_task_summary(self):
        admin = UserProfile.objects.get(username='ocladmin')
        task = Task.objects.create(id='notify-task-id', name='test-task', created_by=admin)
        importer = BulkImportInline('', 'ocladmin', False, input_list=[{'type': 'noop'}])
        importer.self_task_id = task.id
        importer.set_task()
        importer.total = 5
        importer.processed = 2

        importer.notify_progress(force=True)

        task.refresh_from_db()
        self.assertEqual(task.summary['total'], 5)
        self.assertEqual(task.summary['processed'], 2)

    def test_notify_progress_throttled_when_recently_notified(self):
        admin = UserProfile.objects.get(username='ocladmin')
        task = Task.objects.create(id='notify-task-id-2', name='test-task-2', created_by=admin)
        importer = BulkImportInline('', 'ocladmin', False, input_list=[{'type': 'noop'}])
        importer.self_task_id = task.id
        importer.set_task()
        importer.last_progress_notified_at = time.time()

        importer.notify_progress(force=False)

        task.refresh_from_db()
        self.assertIsNone(task.summary)

    def test_organization_importer_process_returns_none_when_already_exists(self):
        org = OrganizationFactory(mnemonic='AlreadyExistsOrg')
        admin = UserProfile.objects.get(username='ocladmin')
        importer = OrganizationImporter({'id': org.mnemonic, 'name': org.name}, admin)
        self.assertIsNone(importer.process())


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

    def test_notify_progress_includes_hierarchy_reconciliation_step(self):
        task = Task(id='task-id', name='bulk_import')
        task.save()
        Task(id='task-1', name='sub_task', summary={'processed': 100, 'total': 200}).save()
        Task(id='task-2', name='sub_task', summary={'processed': 50, 'total': 100}).save()

        content = json.dumps({
            "type": "Concept", "id": "ChildConcept",
            "owner": "TestOrg", "owner_type": "Organization", "source": "TestSource",
            "parent_concept_urls": ["/orgs/TestOrg/sources/TestSource/concepts/ParentConcept/"]
        })
        importer = BulkImportParallelRunner(content, 'ocladmin', True, None, 'task-id')
        importer.tasks = [Mock(task_id='task-1'), Mock(task_id='task-2')]

        importer.notify_progress()
        task.refresh_from_db()
        self.assertEqual(task.summary, {'processed': 150, 'total': 2})

        importer.hierarchy_reconciliation_done = True
        importer.notify_progress()
        task.refresh_from_db()
        self.assertEqual(task.summary, {'processed': 151, 'total': 2})

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

    def test_chunker_list_with_missing_or_blank_concept_id(self):
        """A concept line without a usable "id" must not blow up the whole chunking (and hence the whole import).

        Such lines are still valid input for sources with auto-id assignment, and for every other source they are
        rejected later, per line, by the resource importer -- so chunking only needs to keep them in the stream.
        """
        concepts = [
            {"type": "Concept", "id": "B", "update_comment": "B.1"},
            {"type": "Concept", "update_comment": "no-id"},
            {"type": "Concept", "id": "A", "update_comment": "A.1"},
            {"type": "Concept", "id": None, "update_comment": "null-id"},
            {"type": "Concept", "id": "B", "update_comment": "B.2"},
        ]

        # id-less lines sort first, remaining lines keep the "same id in a single chunk" guarantee
        self.assertEqual(
            BulkImportParallelRunner.chunker_list(concepts, 1, True),
            [
                [
                    {"type": "Concept", "update_comment": "no-id"},
                    {"type": "Concept", "id": None, "update_comment": "null-id"},
                    {"type": "Concept", "id": "A", "update_comment": "A.1"},
                    {"type": "Concept", "id": "B", "update_comment": "B.1"},
                    {"type": "Concept", "id": "B", "update_comment": "B.2"},
                ],
            ]
        )

        # id-less lines are not merged with each other, so they stay spread over the chunks
        self.assertEqual(
            BulkImportParallelRunner.chunker_list(concepts, 5, True),
            [
                [{"type": "Concept", "update_comment": "no-id"}],
                [{"type": "Concept", "id": None, "update_comment": "null-id"}],
                [{"type": "Concept", "id": "A", "update_comment": "A.1"}],
                [
                    {"type": "Concept", "id": "B", "update_comment": "B.1"},
                    {"type": "Concept", "id": "B", "update_comment": "B.2"},
                ],
            ]
        )

        # no line is lost, whatever the number of chunks
        for size in range(1, 8):
            chunks_ = BulkImportParallelRunner.chunker_list(concepts, size, True)
            self.assertEqual(
                sorted(json.dumps(line, sort_keys=True) for chunk in chunks_ for line in chunk),
                sorted(json.dumps(line, sort_keys=True) for line in concepts),
                f'lines lost/duplicated for size={size}'
            )

    def test_chunker_list_for_mappings_without_id(self):
        """Mapping chunks are never sorted/grouped by id, so id-less mapping lines (the common case) pass through."""
        mappings = [
            {"type": "Mapping", "map_type": "Has Child", "to_concept_code": "C"},
            {"type": "Mapping", "map_type": "Same As", "to_concept_code": "A"},
            {"type": "Mapping", "map_type": "Same As", "to_concept_code": "B"},
        ]

        self.assertEqual(
            BulkImportParallelRunner.chunker_list(mappings, 2, True), [mappings[:2], mappings[2:]]
        )

    def test_chunker_list_with_non_string_concept_id(self):
        concepts = [
            {"type": "Concept", "id": 2, "update_comment": "2.1"},
            {"type": "Concept", "id": "1", "update_comment": "1.1"},
            {"type": "Concept", "id": 2, "update_comment": "2.2"},
        ]

        # both "2" versions end up in the same chunk even though the id is not a string
        self.assertEqual(
            BulkImportParallelRunner.chunker_list(concepts, 2, True),
            [
                [
                    {"type": "Concept", "id": "1", "update_comment": "1.1"},
                    {"type": "Concept", "id": 2, "update_comment": "2.1"},
                    {"type": "Concept", "id": 2, "update_comment": "2.2"},
                ],
            ]
        )

    @responses.activate
    def test_import_subtask_single_resource_per_file(self):
        pass

    @responses.activate
    def test_import_subtask_multiple_resource_per_file(self):
        with open(os.path.join(os.path.dirname(__file__), '..', 'samples/BI-FY19-baseline.json'), 'rb') \
                as file:
            responses.add(responses.GET, 'http://fetch.com/some/npm/package', body=file.read(), status=200,
                          content_type='application/json', stream=True)

            org_result = ImporterSubtask('http://fetch.com/some/npm/package', 'ocladmin', 'organization',
                                         'OCL', 'Organization', [{'start_index': 0, 'end_index': 1}]).run()
            self.assertEqual(org_result, [1])

            source_result = ImporterSubtask('http://fetch.com/some/npm/package', 'ocladmin', 'organization',
                                            'OCL', 'Source', [{'start_index': 0, 'end_index': 1}]).run()
            self.assertEqual(source_result, [1])

            concept_result = ImporterSubtask('http://fetch.com/some/npm/package', 'ocladmin', 'organization',
                                             'OCL', 'Concept', [{'start_index': 5, 'end_index': 10}]).run()
            self.assertEqual(concept_result, [1, 1, 1, 1, 1])

    # ── collect_concept_hierarchy_map ────────────────────────────────────────

    def test_collect_concept_hierarchy_map_basic(self):
        """Concepts with parent_concept_urls are collected; those without are ignored."""
        content = '\n'.join([
            json.dumps({
                "type": "Concept", "id": "ChildConcept",
                "owner": "TestOrg", "owner_type": "Organization", "source": "TestSource",
                "parent_concept_urls": ["/orgs/TestOrg/sources/TestSource/concepts/ParentConcept/"]
            }),
            json.dumps({
                "type": "Concept", "id": "ParentConcept",
                "owner": "TestOrg", "owner_type": "Organization", "source": "TestSource",
            }),
        ])
        importer = BulkImportParallelRunner(content, 'ocladmin', True)

        self.assertEqual(
            importer.concept_hierarchy_map,
            {
                '/orgs/TestOrg/sources/TestSource/concepts/ChildConcept/':
                    ['/orgs/TestOrg/sources/TestSource/concepts/ParentConcept/']
            }
        )

    def test_collect_concept_hierarchy_map_encodes_special_chars(self):
        """Concept IDs with special characters (e.g. &) are URL-encoded to match persisted URIs (P2)."""
        content = json.dumps({
            "type": "Concept", "id": "1A40.0&XA8UM1",
            "owner": "OpenMRS-OCL-Squad", "owner_type": "Organization", "source": "ICD-11-CIEL-Bridge",
            "parent_concept_urls": ["/orgs/OpenMRS-OCL-Squad/sources/ICD-11-CIEL-Bridge/concepts/BlockL1-1A0/"]
        })
        importer = BulkImportParallelRunner(content, 'ocladmin', True)

        encoded_uri = '/orgs/OpenMRS-OCL-Squad/sources/ICD-11-CIEL-Bridge/concepts/1A40.0%26XA8UM1/'
        raw_uri     = '/orgs/OpenMRS-OCL-Squad/sources/ICD-11-CIEL-Bridge/concepts/1A40.0&XA8UM1/'

        self.assertIn(encoded_uri, importer.concept_hierarchy_map)
        self.assertNotIn(raw_uri, importer.concept_hierarchy_map)

    def test_collect_concept_hierarchy_map_user_owner_type(self):
        """Concepts owned by a User (not an Org) use /users/ prefix in their URI."""
        content = json.dumps({
            "type": "Concept", "id": "MyConcept",
            "owner": "johndoe", "owner_type": "User", "source": "MySource",
            "parent_concept_urls": ["/users/johndoe/sources/MySource/concepts/RootConcept/"]
        })
        importer = BulkImportParallelRunner(content, 'ocladmin', True)

        self.assertIn('/users/johndoe/sources/MySource/concepts/MyConcept/', importer.concept_hierarchy_map)

    def test_collect_concept_hierarchy_map_ignores_non_concepts(self):
        """Only Concept lines are indexed; Source, Mapping, and Reference lines are ignored."""
        content = '\n'.join([
            json.dumps({"type": "Source", "id": "S1", "owner": "O", "owner_type": "Organization",
                        "parent_concept_urls": ["/orgs/O/sources/S1/concepts/Root/"]}),
            json.dumps({"type": "Mapping", "id": "M1", "owner": "O", "owner_type": "Organization",
                        "source": "S1",
                        "parent_concept_urls": ["/orgs/O/sources/S1/concepts/Root/"]}),
            json.dumps({"type": "Concept", "id": "C1", "owner": "O", "owner_type": "Organization",
                        "source": "S1"}),
        ])
        importer = BulkImportParallelRunner(content, 'ocladmin', True)

        self.assertEqual(importer.concept_hierarchy_map, {})

    # ── run() hierarchy reconciliation ───────────────────────────────────────

    @patch('core.importers.models.post_import_update_resource_counts.apply_async', Mock())
    @patch('core.importers.models.BulkImportParallelRunner.wait_till_tasks_alive', Mock())
    @patch('core.importers.models.BulkImportParallelRunner.queue_tasks', Mock())
    @patch('core.importers.models.make_hierarchy')
    def test_run_calls_make_hierarchy_with_inverted_map(self, make_hierarchy_mock):
        """After all chunks complete, make_hierarchy receives the inverted {parent_uri: [child_uris]} map."""
        org = OrganizationFactory(mnemonic='TestOrg')
        source = OrganizationSourceFactory(organization=org, mnemonic='TestSource', version='HEAD')
        parent = ConceptFactory(parent=source, mnemonic='ParentConcept')
        child = ConceptFactory(parent=source, mnemonic='ChildConcept')

        # File order: child before parent (the exact scenario the fix targets)
        content = '\n'.join([
            json.dumps({
                "type": "Concept", "id": child.mnemonic,
                "owner": "TestOrg", "owner_type": "Organization", "source": "TestSource",
                "parent_concept_urls": [parent.uri]
            }),
            json.dumps({
                "type": "Concept", "id": parent.mnemonic,
                "owner": "TestOrg", "owner_type": "Organization", "source": "TestSource",
            }),
        ])

        importer = BulkImportParallelRunner(content, 'ocladmin', True)
        importer.run()

        make_hierarchy_mock.assert_called_once()
        self.assertTrue(importer.hierarchy_reconciliation_done)
        inverted = make_hierarchy_mock.call_args[0][0]
        self.assertIn(parent.uri, inverted)
        self.assertIn(child.uri, inverted[parent.uri])

    @patch('core.importers.models.post_import_update_resource_counts.apply_async', Mock())
    @patch('core.importers.models.BulkImportParallelRunner.wait_till_tasks_alive', Mock())
    @patch('core.importers.models.BulkImportParallelRunner.queue_tasks', Mock())
    @patch('core.importers.models.make_hierarchy')
    def test_run_skips_make_hierarchy_when_no_hierarchy(self, make_hierarchy_mock):
        """make_hierarchy is not called when no concept in the import has parent_concept_urls."""
        content = '\n'.join([
            json.dumps({
                "type": "Concept", "id": "StandaloneConcept",
                "owner": "TestOrg", "owner_type": "Organization", "source": "TestSource",
            }),
        ])
        importer = BulkImportParallelRunner(content, 'ocladmin', True)
        importer.run()

        make_hierarchy_mock.assert_not_called()

    @patch('core.importers.models.post_import_update_resource_counts.apply_async', Mock())
    @patch('core.importers.models.BulkImportParallelRunner.wait_till_tasks_alive', Mock())
    @patch('core.importers.models.BulkImportParallelRunner.queue_tasks', Mock())
    def test_run_hierarchy_child_before_parent(self):
        """
        End-to-end: when child appears before parent in the import file, the reconciliation
        step must correctly establish the parent_concepts M2M link in the database.
        This is the primary bug scenario fixed by this PR.
        """
        org    = OrganizationFactory(mnemonic='TestOrg')
        source = OrganizationSourceFactory(organization=org, mnemonic='TestSource', version='HEAD')
        parent = ConceptFactory(parent=source, mnemonic='ParentConcept')
        child  = ConceptFactory(parent=source, mnemonic='ChildConcept')

        # child listed before parent — the exact ordering that broke hierarchy before the fix
        content = '\n'.join([
            json.dumps({
                "type": "Concept", "id": child.mnemonic,
                "owner": "TestOrg", "owner_type": "Organization", "source": "TestSource",
                "parent_concept_urls": [parent.uri]
            }),
            json.dumps({
                "type": "Concept", "id": parent.mnemonic,
                "owner": "TestOrg", "owner_type": "Organization", "source": "TestSource",
            }),
        ])

        importer = BulkImportParallelRunner(content, 'ocladmin', True)
        importer.run()

        child.refresh_from_db()
        self.assertIn(parent.get_latest_version(), child.parent_concepts.all())

    @patch('core.importers.models.post_import_update_resource_counts.apply_async', Mock())
    @patch('core.importers.models.BulkImportParallelRunner.wait_till_tasks_alive', Mock())
    @patch('core.importers.models.BulkImportParallelRunner.queue_tasks', Mock())
    def test_run_hierarchy_parent_before_child(self):
        """
        End-to-end: when parent appears before child (natural order), the reconciliation
        must also establish the link correctly — confirming the fix is order-agnostic.
        """
        org = OrganizationFactory(mnemonic='TestOrg')
        source = OrganizationSourceFactory(organization=org, mnemonic='TestSource', version='HEAD')
        parent = ConceptFactory(parent=source, mnemonic='ParentConcept')
        child = ConceptFactory(parent=source, mnemonic='ChildConcept')

        # parent listed before child — normal/expected order
        content = '\n'.join([
            json.dumps({
                "type": "Concept", "id": parent.mnemonic,
                "owner": "TestOrg", "owner_type": "Organization", "source": "TestSource",
            }),
            json.dumps({
                "type": "Concept", "id": child.mnemonic,
                "owner": "TestOrg", "owner_type": "Organization", "source": "TestSource",
                "parent_concept_urls": [parent.uri]
            }),
        ])

        importer = BulkImportParallelRunner(content, 'ocladmin', True)
        importer.run()

        child.refresh_from_db()
        self.assertIn(parent.get_latest_version(), child.parent_concepts.all())

    @patch('core.importers.models.post_import_update_resource_counts.apply_async', Mock())
    @patch('core.importers.models.BulkImportParallelRunner.wait_till_tasks_alive', Mock())
    @patch('core.importers.models.BulkImportParallelRunner.queue_tasks', Mock())
    @patch('core.importers.models.make_hierarchy')
    def test_run_excludes_inaccessible_concepts(self, make_hierarchy_mock):
        """
        Concepts from sources the importing user cannot edit must be excluded from make_hierarchy,
        even when they exist in the database. This prevents hierarchy changes on foreign sources
        that were denied during import (P1 security fix).
        """
        # OrgA — importing user is a member → has edit access
        org_a = OrganizationFactory(mnemonic='OrgA')
        source_a = OrganizationSourceFactory(organization=org_a, mnemonic='SourceA', version='HEAD')
        parent_a = ConceptFactory(parent=source_a, mnemonic='ParentA')
        child_a = ConceptFactory(parent=source_a, mnemonic='ChildA')

        # OrgB — importing user is NOT a member and source is not publicly editable → no edit access
        org_b = OrganizationFactory(mnemonic='OrgB')
        source_b = OrganizationSourceFactory(
            organization=org_b, mnemonic='SourceB', version='HEAD', public_access='None')
        parent_b = ConceptFactory(parent=source_b, mnemonic='ParentB')
        child_b = ConceptFactory(parent=source_b, mnemonic='ChildB')

        importing_user = UserProfileFactory(username='importer-user')
        org_a.members.add(importing_user)
        self.assertFalse(org_b.is_member(importing_user))

        content = '\n'.join([
            # accessible concept (OrgA)
            json.dumps({
                "type": "Concept", "id": child_a.mnemonic,
                "owner": "OrgA", "owner_type": "Organization", "source": "SourceA",
                "parent_concept_urls": [parent_a.uri]
            }),
            # inaccessible concept (OrgB — user has no permission)
            json.dumps({
                "type": "Concept", "id": child_b.mnemonic,
                "owner": "OrgB", "owner_type": "Organization", "source": "SourceB",
                "parent_concept_urls": [parent_b.uri]
            }),
        ])

        importer = BulkImportParallelRunner(content, importing_user.username, True)
        importer.run()

        make_hierarchy_mock.assert_called_once()
        inverted = make_hierarchy_mock.call_args[0][0]

        # OrgA child must be linked to its parent
        self.assertIn(parent_a.uri, inverted)
        self.assertIn(child_a.uri, inverted[parent_a.uri])

        # OrgB child must NOT appear — user has no access to SourceB
        self.assertNotIn(parent_b.uri, inverted)
        all_children = [uri for uris in inverted.values() for uri in uris]
        self.assertNotIn(child_b.uri, all_children)


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
                 'children': [],
                 'message': None
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
                'children': [],
                'message': None
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
                'children': [],
                'message': None
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
                'children': [],
                'message': None
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
                'children': [],
                'message': None,
                'kwargs': None,
                'error_message': None,
                'traceback': None,
                'retry': 0,
                'report': None
            }
        )

        response = self.client.get(
            f'/importers/bulk-import/?task={task_id}&result=json',
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
                'children': [],
                'message': None,
                'kwargs': None,
                'error_message': None,
                'traceback': None,
                'retry': 0,
                'report': None,
                'result': None
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

        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=true',
            [{'type': 'Concept', 'id': '1'}],
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
                'children': [],
                'message': None
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
                'children': [],
                'message': None
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
                'children': [],
                'message': None
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
                'children': [],
                'message': None
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
                'children': [],
                'message': None
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

    @patch.object(Task, 'refresh_from_db', side_effect=AlreadyQueued('boom'))
    @patch('core.importers.views.queue_bulk_import')
    def test_post_409_with_existing_task_deletes_it(self, queue_bulk_import_mock, _refresh_mock):
        task = Task.objects.create(id='to-delete-task', name='bulk_import_test', created_by=self.superuser)
        queue_bulk_import_mock.return_value = task

        response = self.client.post(
            '/importers/bulk-import/?update_if_exists=true',
            {'data': 'some-data'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(Task.objects.filter(id='to-delete-task').exists())

    def test_import_retrieve_destroy_mixin_get_throttles(self):
        view = ImportRetrieveDestroyMixin()
        view.request = Mock(user=self.superuser)
        throttles = view.get_throttles()
        self.assertIsNotNone(throttles)

    def test_get_user_not_found(self):
        response = self.client.get(
            '/importers/bulk-import/?username=nonexistent-user',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 404)

    def test_get_forbidden_for_non_staff_different_user(self):
        random_user = UserProfileFactory(username='notstaff')
        other_user = UserProfileFactory(username='someoneelse')
        response = self.client.get(
            f'/importers/bulk-import/?username={other_user.username}',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 403)

    def test_delete_task_not_found(self):
        response = self.client.delete(
            '/importers/bulk-import/',
            {'task_id': 'nonexistent-task-id'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_task_forbidden(self):
        random_user = UserProfileFactory(username='notowner')
        task = Task.objects.create(id='someones-task', name='bulk_import_test', created_by=self.superuser)
        response = self.client.delete(
            '/importers/bulk-import/',
            {'task_id': task.id},
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 403)

    @patch.object(ImportContentParser, 'parse', side_effect=Exception('parse boom'))
    def test_post_parallel_inline_parse_exception(self, _parse_mock):
        response = self.client.post(
            '/importers/bulk-import-parallel-inline/?update_if_exists=true',
            {'data': 'some-data'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Failed to parse input', response.data['exception'])

    @patch('core.importers.views.bulk_import_new')
    def test_post_import_type_with_file_url(self, bulk_import_new_mock):
        bulk_import_new_mock.__name__ = 'bulk_import_new'
        task_mock = Mock(id='task-id-url', state='PENDING')
        bulk_import_new_mock.apply_async = Mock(return_value=task_mock)

        response = self.client.post(
            '/importers/bulk-import/',
            {'import_type': 'npm', 'file_url': 'http://fetch/package.zip'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['task'], 'task-id-url')
        bulk_import_new_mock.apply_async.assert_called_once()
        call_args = bulk_import_new_mock.apply_async.call_args[0][0]
        self.assertEqual(call_args[0], 'http://fetch/package.zip')

    @patch('core.importers.views.bulk_import_new')
    def test_post_import_type_with_file_upload_debug_true(self, bulk_import_new_mock):
        bulk_import_new_mock.__name__ = 'bulk_import_new'
        task_mock = Mock(id='task-id-data', state='PENDING')
        bulk_import_new_mock.apply_async = Mock(return_value=task_mock)

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch('core.settings.MEDIA_ROOT', tmp_dir):
                uploaded_file = SimpleUploadedFile('data.json', b'{"resourceType": "CodeSystem"}')
                response = self.client.post(
                    '/importers/bulk-import/',
                    {'import_type': 'npm', 'file': uploaded_file},
                    HTTP_AUTHORIZATION='Token ' + self.token,
                    format='multipart'
                )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['task'], 'task-id-data')
        call_args = bulk_import_new_mock.apply_async.call_args[0][0]
        self.assertTrue(call_args[0].startswith(tmp_dir))

    @patch('core.importers.views.bulk_import_new')
    def test_post_import_type_with_data_text_debug_false(self, bulk_import_new_mock):
        bulk_import_new_mock.__name__ = 'bulk_import_new'
        task_mock = Mock(id='task-id-upload', state='PENDING')
        bulk_import_new_mock.apply_async = Mock(return_value=task_mock)
        upload_service_mock = Mock()

        with patch('core.settings.DEBUG', False):
            with patch('core.importers.views.get_export_service', return_value=upload_service_mock):
                response = self.client.post(
                    '/importers/bulk-import/',
                    {'import_type': 'npm', 'data': '{"resourceType": "CodeSystem"}'},
                    HTTP_AUTHORIZATION='Token ' + self.token,
                    format='json'
                )

        self.assertEqual(response.status_code, 202)
        upload_service_mock.upload.assert_called_once()
        call_args = bulk_import_new_mock.apply_async.call_args[0][0]
        self.assertTrue(call_args[0].startswith(Importer.IMPORT_CACHE))


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
            self_task_id=ANY, skip_hierarchy_tasks=True
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


class ImportTaskTest(OCLTestCase):

    def test_import_task_from_async_result_for_unexisting_task(self):
        async_result = Mock()
        async_result.state = 'PENDING'
        import_task = ImportTask.import_task_from_async_result(async_result)
        self.assertIsNone(import_task)

    def test_import_task_from_async_result_for_other_task(self):
        async_result = Mock()
        async_result.result = {}
        async_result.state = 'SUCCESS'
        import_task = ImportTask.import_task_from_async_result(async_result)
        self.assertIsNone(import_task)

    def test_import_task_from_async_result_for_import_task(self):
        async_result = Mock()
        async_result.result = {'import_task': ('id', '1')}
        async_result.state = 'SUCCESS'
        import_task = ImportTask.import_task_from_async_result(async_result)
        self.assertIsNotNone(import_task)

    def test_import_task_from_json(self):
        json_task = {'import_task': ('id', '1')}
        import_task = ImportTask.import_task_from_json(json_task)
        self.assertIsNotNone(import_task)

    def test_import_task_from_json_without_import_task(self):
        self.assertIsNone(ImportTask.import_task_from_json({}))
        self.assertIsNone(ImportTask.import_task_from_json({'foo': 'bar'}))

    def test_revoke(self):
        import_task = ImportTask()
        mock = Mock()

        import_task.import_async_result = mock
        import_task.revoke()

        mock.revoke.assert_called_once_with()

    def test_revoke_with_subtasks(self):
        import_task = ImportTask(subtask_ids=['sub-1', 'sub-2'])
        import_task.import_async_result = Mock()

        with patch('core.importers.importer.AsyncResult') as async_result_mock:
            import_task.revoke()

        self.assertEqual(async_result_mock.call_count, 2)
        async_result_mock.assert_has_calls([call('sub-1'), call('sub-2')], any_order=True)
        self.assertEqual(async_result_mock.return_value.revoke.call_count, 2)

    def test_import_async_result_from_import_task_tuple(self):
        import_task = ImportTask(import_task=(('task-id', None), None))
        result = import_task.import_async_result
        self.assertIsNotNone(result)
        self.assertEqual(result.id, 'task-id')
        self.assertIs(import_task.import_async_result, result)  # cached on second access

    def test_time_finished_from_ready_import_async_result(self):
        import_task = ImportTask()
        import_task.import_async_result = Mock(
            ready=Mock(return_value=True), result={'time_finished': 'sometime'}
        )
        self.assertEqual(import_task.time_finished, 'sometime')

    def test_time_finished_setter(self):
        import_task = ImportTask()
        import_task.time_finished = 'explicit-value'
        self.assertEqual(import_task.time_finished, 'explicit-value')

    def test_summary_and_related_computed_fields(self):
        import_task = ImportTask(subtask_ids=['t1', 't2', 't3', 't4', 't5', 't6', 't7'])
        import_task.import_async_result = Mock(ready=Mock(return_value=False))

        def make_child(ready, result):
            return Mock(ready=Mock(return_value=ready), result=result)

        children_by_id = {
            't1': make_child(True, CREATED),
            't2': make_child(True, UPDATED),
            't3': make_child(True, DELETED),
            't4': make_child(True, PERMISSION_DENIED),
            't5': make_child(True, UNCHANGED),
            't6': make_child(True, 'some failure'),
            't7': make_child(False, None),
        }

        with patch('core.importers.importer.AsyncResult', side_effect=lambda tid: children_by_id[tid]):
            summary = import_task.summary

        self.assertEqual(summary.processed, 6)
        self.assertEqual(summary.created, 1)
        self.assertEqual(summary.updated, 1)
        self.assertEqual(summary.deleted, 1)
        self.assertEqual(summary.permission_denied, 1)
        self.assertEqual(summary.unchanged, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.failures, ['some failure'])

        self.assertIsNotNone(import_task.json)
        self.assertIsNotNone(import_task.report)
        self.assertGreaterEqual(import_task.elapsed_seconds, 0)
        self.assertIn('some failure', import_task.detailed_summary)

    def test_summary_returns_initial_summary_when_no_import_async_result(self):
        import_task = ImportTask(initial_summary=ImportTaskSummary(total=5))
        self.assertEqual(import_task.summary.total, 5)
        self.assertEqual(import_task.summary.processed, 0)

    def test_summary_returns_final_summary_when_ready(self):
        import_task = ImportTask()
        import_task.import_async_result = Mock(
            ready=Mock(return_value=True),
            result={'final_summary': {'total': 9, 'processed': 9}}
        )
        self.assertEqual(import_task.summary.total, 9)
        self.assertEqual(import_task.summary.processed, 9)


class ImporterTest(OCLTestCase):

    @staticmethod
    def get_absolute_path(path):
        module_dir = os.path.dirname(__file__)  # get current directory
        file_path = os.path.join(module_dir, path)
        return file_path

    @patch.object(Importer, 'prepare_resources')
    def test_traverse_dependencies(self, mocked_prepare_resources):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        with patch('builtins.open', mock_open(read_data='{ "dependencies" : '
                                                        '{'
                                                        '"hl7.fhir.r4.core" : "4.0.1",'
                                                        '"hl7.terminology.r4" : "5.3.0",'
                                                        '"hl7.fhir.uv.extensions.r4" : "1.0.0",'
                                                        '"ans.fr.nos" : "1.2.0"'
                                                        '}'
                                                        '}')):
            with open('/dev/null') as package_file:
                importer.traverse_dependencies(package_file, '/', [], [], [], {})
                mocked_prepare_resources.assert_has_calls([
                    call('https://packages.simplifier.net/hl7.terminology.r4/5.3.0/', [],
                         ['https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/', '/'],
                         ['/', 'https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/'], {}),
                    call('https://packages.simplifier.net/hl7.fhir.uv.extensions.r4/1.0.0/', [],
                         ['https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/', '/'],
                         ['/', 'https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/'], {}),
                    call('https://packages.simplifier.net/ans.fr.nos/1.2.0/', [],
                         ['https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/', '/'],
                         ['/', 'https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/'], {})])

    @patch.object(Importer, 'prepare_resources')
    def test_traverse_dependencies_with_circuit(self, mocked_prepare_resources):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        with patch('builtins.open', mock_open(read_data='{ "dependencies" : '
                                                        '{'
                                                        '"hl7.fhir.r4.core" : "4.0.1",'
                                                        '"hl7.terminology.r4" : "5.3.0",'
                                                        '"hl7.fhir.uv.extensions.r4" : "1.0.0",'
                                                        '"ans.fr.nos" : "1.2.0"'
                                                        '}'
                                                        '}')):
            with open('/dev/null') as package_file:
                visited_dependencies = ['https://packages.simplifier.net/hl7.fhir.uv.extensions.r4/1.0.0/']
                importer.traverse_dependencies(package_file, '/', [], [], visited_dependencies, {})
                mocked_prepare_resources.assert_has_calls([
                    call('https://packages.simplifier.net/hl7.terminology.r4/5.3.0/', [],
                         ['https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/', '/'],
                         ['https://packages.simplifier.net/hl7.fhir.uv.extensions.r4/1.0.0/', '/',
                          'https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/'], {}),
                    call('https://packages.simplifier.net/ans.fr.nos/1.2.0/', [],
                         ['https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/', '/'],
                         ['https://packages.simplifier.net/hl7.fhir.uv.extensions.r4/1.0.0/', '/',
                          'https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/'], {})])

    @responses.activate
    @patch.object(Importer, 'prepare_resources')
    def test_traverse_dependencies_with_x(self, mocked_prepare_resources):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        with patch('builtins.open', mock_open(read_data='{ "dependencies" : '
                                                        '{'
                                                        '"hl7.fhir.r4.core" : "4.0.1",'
                                                        '"hl7.terminology.r4" : "5.3.0",'
                                                        '"hl7.fhir.uv.extensions.r4" : "1.0.0",'
                                                        '"ans.fr.nos" : "1.1.x"'
                                                        '}'
                                                        '}')):
            with open('/dev/null') as package_file:
                responses.add(responses.GET, 'https://packages.simplifier.net/ans.fr.nos',
                              body='{"_id":"ans.fr.nos","name":"ans.fr.nos","description":"Les nomenclatures des '
                                   'objets de Sante (built Wed, Feb 28, 2024 14:48+0000+00:00)",'
                                   '"dist-tags":{"latest":"1.2.0"},'
                                   '"versions":{"1.1.0":{"name":"ans.fr.nos","version":"1.1.0",'
                                   '"description":"None.","dist":{"shasum":"65b8a03213e0760e6fd083d89aa5dbaf5dc320a9",'
                                   '"tarball":"https://packages.simplifier.net/ans.fr.nos/1.1.0"},"fhirVersion":"R4",'
                                   '"url":"https://packages.simplifier.net/ans.fr.nos/1.1.0"},'
                                   '"1.2.0":{"name":"ans.fr.nos","version":"1.2.0","description":"None.",'
                                   '"dist":{"shasum":"f881709302cf869fa8159e21550ec5a77e80c1b2","tarball":'
                                   '"https://packages.simplifier.net/ans.fr.nos/1.2.0"},"fhirVersion":"R4",'
                                   '"url":"https://packages.simplifier.net/ans.fr.nos/1.2.0"}}}', status=200,
                              content_type='application/json', stream=True)
                importer.traverse_dependencies(package_file, '/', [], [], [], {})
                mocked_prepare_resources.assert_has_calls([
                    call('https://packages.simplifier.net/hl7.terminology.r4/5.3.0/', [],
                         ['https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/', '/'],
                         ['/', 'https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/'], {}),
                    call('https://packages.simplifier.net/hl7.fhir.uv.extensions.r4/1.0.0/', [],
                         ['https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/', '/'],
                         ['/', 'https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/'], {}),
                    call('https://packages.simplifier.net/ans.fr.nos/1.1.0/', [],
                         ['https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/', '/'],
                         ['/', 'https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/'], {})])

    @responses.activate
    @patch.object(Importer, 'prepare_resources')
    def test_traverse_dependencies_with_x_without_match(self, _):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        with patch('builtins.open', mock_open(read_data='{ "dependencies" : '
                                                        '{'
                                                        '"hl7.fhir.r4.core" : "4.0.1",'
                                                        '"hl7.terminology.r4" : "5.3.0",'
                                                        '"hl7.fhir.uv.extensions.r4" : "1.0.0",'
                                                        '"ans.fr.nos" : "1.3.x"'
                                                        '}'
                                                        '}')):
            with open('/dev/null') as package_file:
                responses.add(responses.GET, 'https://packages.simplifier.net/ans.fr.nos',
                              body='{"_id":"ans.fr.nos","name":"ans.fr.nos","description":"Les nomenclatures des '
                                   'objets de Sante (built Wed, Feb 28, 2024 14:48+0000+00:00)",'
                                   '"dist-tags":{"latest":"1.2.0"},'
                                   '"versions":{"1.1.0":{"name":"ans.fr.nos","version":"1.1.0",'
                                   '"description":"None.","dist":{"shasum":"65b8a03213e0760e6fd083d89aa5dbaf5dc320a9",'
                                   '"tarball":"https://packages.simplifier.net/ans.fr.nos/1.1.0"},"fhirVersion":"R4",'
                                   '"url":"https://packages.simplifier.net/ans.fr.nos/1.1.0"},'
                                   '"1.2.0":{"name":"ans.fr.nos","version":"1.2.0","description":"None.",'
                                   '"dist":{"shasum":"f881709302cf869fa8159e21550ec5a77e80c1b2","tarball":'
                                   '"https://packages.simplifier.net/ans.fr.nos/1.2.0"},"fhirVersion":"R4",'
                                   '"url":"https://packages.simplifier.net/ans.fr.nos/1.2.0"}}}', status=200,
                              content_type='application/json', stream=True)
                with self.assertRaises(LookupError) as err:
                    importer.traverse_dependencies(package_file, '/', [], [], [], {})
                self.assertEqual("No version matching 1.3.x found in ['1.2.0', '1.1.0'] for package ans.fr.nos",
                                 str(err.exception))

    def test_categorize_resources_all_from_file(self):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        resources = {}
        with open(ImporterTest.get_absolute_path('tests/fhir_resources_01.json')) as json_file:
            importer.categorize_resources(json_file, '/path', 'json', ['CodeSystem', 'ValueSet'], resources)

        self.assertEqual(list(resources.keys()), ['CodeSystem', 'ValueSet'])
        self.assertEqual(resources.get('CodeSystem'),  {'/path/json': 2})
        self.assertEqual(resources.get('ValueSet'),  {'/path/json': 2})

    def test_categorize_resources_none_from_file(self):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        resources = {}
        with open(ImporterTest.get_absolute_path('tests/fhir_resources_01.json')) as json_file:
            importer.categorize_resources(json_file, '/path', 'json', ['ConceptMap'], resources)

        self.assertEqual(list(resources.keys()), ['ConceptMap'])
        self.assertEqual(resources.get('ConceptMap'), {})

    def test_categorize_resources_few_from_file(self):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        resources = {}
        with open(ImporterTest.get_absolute_path('tests/fhir_resources_01.json')) as json_file:
            importer.categorize_resources(json_file, '/path', 'json', ['CodeSystem'], resources)

        self.assertEqual(list(resources.keys()), ['CodeSystem'])
        self.assertEqual(resources.get('CodeSystem'), {'/path/json': 2})

    def test_categorize_resources_for_npm_file(self):
        importer = Importer('1', 'path', 'root', 'users', 'root', 'npm')
        resources = {}
        with open(ImporterTest.get_absolute_path('tests/fhir_resources_01.json')) as json_file:
            importer.categorize_resources(json_file, '/path', 'json', ['ValueSet', 'CodeSystem'], resources)

        self.assertEqual(list(resources.keys()), ['ValueSet', 'CodeSystem'])
        self.assertEqual(resources.get('ValueSet'), {'/path/json': 1})
        self.assertEqual(resources.get('CodeSystem'), {})

    def test_prepare_tasks(self):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        tasks = importer.prepare_tasks(['CodeSystem', 'ValueSet', 'ConceptMap'], ['/package2', '/package1'], {
                                   'ValueSet': {'/package1/path1': 101, '/package1/path2': 299, '/package2/path3': 50},
                                   'CodeSystem': {'/package1/path4:': 10},
                                   'ConceptMap': {'/package2/path5': 250}
                               })
        self.assertEqual(tasks, [
            [{'path': '/package2', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
             'resource_type': 'ValueSet', 'files': [{'filepath': 'path3', 'start_index': 0, 'end_index': 50}]}],
            [{'path': '/package2', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ConceptMap', 'files': [{'filepath': 'path5', 'start_index': 0, 'end_index': 50}]},
             {'path': '/package2', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ConceptMap', 'files': [{'filepath': 'path5', 'start_index': 50, 'end_index': 100}]},
             {'path': '/package2', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ConceptMap', 'files': [{'filepath': 'path5', 'start_index': 100, 'end_index': 150}]},
             {'path': '/package2', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ConceptMap', 'files': [{'filepath': 'path5', 'start_index': 150, 'end_index': 200}]},
             {'path': '/package2', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ConceptMap', 'files': [{'filepath': 'path5', 'start_index': 200, 'end_index': 250}]}],
            [{'path': '/package1', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'CodeSystem', 'files': [{'filepath': 'path4:', 'start_index': 0, 'end_index': 10}]}],
            [{'path': '/package1', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ValueSet', 'files': [{'filepath': 'path1', 'start_index': 0, 'end_index': 50}]},
             {'path': '/package1', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ValueSet', 'files': [{'filepath': 'path1', 'start_index': 50, 'end_index': 100}]},
             {'path': '/package1', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ValueSet', 'files': [{'filepath': 'path1', 'start_index': 100, 'end_index': 101},
                                                     {'filepath': 'path2', 'start_index': 0, 'end_index': 49}]},
             {'path': '/package1', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ValueSet', 'files': [{'filepath': 'path2', 'start_index': 49, 'end_index': 99}]},
             {'path': '/package1', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ValueSet', 'files': [{'filepath': 'path2', 'start_index': 99, 'end_index': 149}]},
             {'path': '/package1', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ValueSet', 'files': [{'filepath': 'path2', 'start_index': 149, 'end_index': 199}]},
             {'path': '/package1', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ValueSet', 'files': [{'filepath': 'path2', 'start_index': 199, 'end_index': 249}]},
             {'path': '/package1', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ValueSet', 'files': [{'filepath': 'path2', 'start_index': 249, 'end_index': 299}]}]])

    hl7_fhir_fr_core_resources = {
        'CodeSystem': {'http://fetch/npm/package/package/CodeSystem-fr-core-cs-circonstances-sortie.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-contact-relationship.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-fiabilite-identite.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-identifier-type.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-location-identifier-type.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-location-physical-type.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-location-position-room.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-location-type.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-marital-status.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-method-collection.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-mode-validation-identity.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-schedule-type.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-type-admission.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-type-organisation.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-v2-0203.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-v2-0445.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-v2-3307.json': 1,
                       'http://fetch/npm/package/package/CodeSystem-fr-core-cs-v2-3311.json': 1},
        'ValueSet': {'http://fetch/npm/package/package/ValueSet-fr-core-vs-availability-time-day.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-availability-time-rule.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-bp-method.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-civility-exercice-rass.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-civility-rass.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-civility.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-cog-commune-pays.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-contact-relationship.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-email-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-encounter-class.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-encounter-discharge-disposition.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-encounter-identifier-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-encounter-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-height-body-position.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-identity-method-collection.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-identity-reliability.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-insee-code.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-location-identifier-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-location-physical-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-location-position-room.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-location-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-marital-status.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-mode-validation-identity.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-organization-activity-field.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-organization-identifier-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-organization-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-organization-uf-activity-field.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-patient-contact-role.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-patient-gender-INS.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-patient-identifier-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-practitioner-identifier-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-practitioner-qualification.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-practitioner-role-exercice.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-practitioner-role-profession.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-practitioner-specialty.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-relation-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-schedule-type.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-schedule-unavailability-reason.json': 1,
                     'http://fetch/npm/package/package/ValueSet-fr-core-vs-title.json': 1}}

    @patch.object(Importer, 'traverse_dependencies')
    @responses.activate
    def test_prepare_resources_tar_gzip(self, _):
        importer = Importer('1', 'path', 'root', 'users', 'root', 'npm')
        path = ImporterTest.get_absolute_path('tests/hl7.fhir.fr.core-2.0.1.tgz')
        resources = {}
        with open(path, 'rb') as file:
            responses.add(responses.GET, 'http://fetch/npm/package', body=file.read(), status=200,
                          content_type='application/tar+gzip', stream=True)
            importer.prepare_resources('http://fetch/npm/package', ['CodeSystem', 'ValueSet'], [], [], resources)

        self.assertEqual(resources, self.hl7_fhir_fr_core_resources)

    @patch.object(Importer, 'traverse_dependencies')
    @responses.activate
    def test_prepare_resources_zip(self, _):
        importer = Importer('1', 'path', 'root', 'users', 'root', 'npm')
        path = ImporterTest.get_absolute_path('tests/hl7.fhir.fr.core-2.0.1.zip')
        resources = {}
        with open(path, 'rb') as file:
            responses.add(responses.GET, 'http://fetch/npm/package', body=file.read(), status=200,
                          content_type='application/zip', stream=True)
            importer.prepare_resources('http://fetch/npm/package', ['CodeSystem', 'ValueSet'], [], [], resources)

        self.assertEqual(resources, self.hl7_fhir_fr_core_resources)

    @patch.object(Importer, 'traverse_dependencies')
    @responses.activate
    def test_prepare_resources_json(self, _):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        path = ImporterTest.get_absolute_path('tests/fhir_resources_01.json')
        resources = {}
        with open(path, 'rb') as file:
            responses.add(responses.GET, 'http://fetch/json', body=file.read(), status=200,
                          content_type='application/json', stream=True)
            importer.prepare_resources('http://fetch/json', ['CodeSystem', 'ValueSet'], [], [], resources)

        self.assertEqual(resources, {'CodeSystem': {'http://fetch/json': 2}, 'ValueSet': {'http://fetch/json': 2}})

    @patch.object(Importer, 'schedule_tasks')
    def test_run_local_path_with_tasks(self, schedule_tasks_mock):
        task_result = Mock()
        task_result.as_tuple.return_value = (('the-id', None), None)
        schedule_tasks_mock.return_value = (task_result, ['sub-1'])

        path = ImporterTest.get_absolute_path('tests/fhir_resources_01.json')
        importer = Importer('task-1', path, 'root', 'users', 'root')

        result = importer.run()

        self.assertEqual(result['initial_summary']['total'], 4)
        self.assertEqual(result['subtask_ids'], ['sub-1'])
        schedule_tasks_mock.assert_called_once()

    def test_run_local_path_without_tasks(self):
        path = ImporterTest.get_absolute_path('tests/fhir_resources_01.json')
        importer = Importer('task-2', path, 'root', 'users', 'root')

        with patch.object(Importer, 'prepare_tasks', return_value=[]):
            result = importer.run()

        self.assertIsNotNone(result['time_finished'])
        self.assertEqual(result['initial_summary']['total'], 0)

    @responses.activate
    def test_run_downloads_remote_path_when_debug(self):
        path = ImporterTest.get_absolute_path('tests/fhir_resources_01.json')
        with open(path, 'rb') as file:
            content = file.read()
        responses.add(
            responses.GET, 'http://fetch/remote.json', body=content, status=200,
            content_type='application/json', stream=True
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch('core.importers.importer.settings.DEBUG', True):
                with patch('core.importers.importer.settings.MEDIA_ROOT', tmp_dir):
                    importer = Importer('task-3', 'http://fetch/remote.json', 'root', 'users', 'root')
                    with patch.object(Importer, 'prepare_tasks', return_value=[]):
                        result = importer.run()
                    downloaded_path = importer.path

        self.assertIsNotNone(result['time_finished'])
        self.assertTrue(downloaded_path.startswith(tmp_dir))

    @responses.activate
    def test_run_download_failure_when_debug(self):
        responses.add(responses.GET, 'http://fetch/missing.json', body='not found', status=404)

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch('core.importers.importer.settings.DEBUG', True):
                with patch('core.importers.importer.settings.MEDIA_ROOT', tmp_dir):
                    importer = Importer('task-3b', 'http://fetch/missing.json', 'root', 'users', 'root')
                    with self.assertRaises(ImportError):
                        importer.run()

    @responses.activate
    def test_run_uploads_remote_path_when_not_debug(self):
        upload_service_mock = Mock()
        upload_service_mock.exists.return_value = False
        responses.add(
            responses.GET, 'http://fetch/remote2.json', body=b'{}', status=200,
            content_type='application/json', stream=True
        )

        with patch('core.importers.importer.settings.DEBUG', False):
            with patch('core.importers.importer.get_export_service', return_value=upload_service_mock):
                importer = Importer('task-4', 'http://fetch/remote2.json', 'root', 'users', 'root')
                with patch.object(Importer, 'prepare_resources'):
                    with patch.object(Importer, 'prepare_tasks', return_value=[]):
                        result = importer.run()

        self.assertIsNotNone(result['time_finished'])
        upload_service_mock.upload.assert_called_once()

    @responses.activate
    def test_run_uploads_remote_path_failure_when_not_debug(self):
        upload_service_mock = Mock()
        upload_service_mock.exists.return_value = False
        responses.add(responses.GET, 'http://fetch/missing2.json', body='not found', status=404)

        with patch('core.importers.importer.settings.DEBUG', False):
            with patch('core.importers.importer.get_export_service', return_value=upload_service_mock):
                importer = Importer('task-4b', 'http://fetch/missing2.json', 'root', 'users', 'root')
                with self.assertRaises(ImportError):
                    importer.run()

    def test_run_uses_import_cache_url_for(self):
        upload_service_mock = Mock()
        upload_service_mock.url_for.return_value = 'http://fetch/cached.json'

        with patch('core.importers.importer.get_export_service', return_value=upload_service_mock):
            importer = Importer('task-5', 'path', 'root', 'users', 'root')
            with patch.object(Importer, 'is_npm_import', return_value=False):
                with patch('core.importers.importer.requests.get') as requests_get_mock:
                    requests_get_mock.return_value = Mock(
                        ok=True, iter_content=Mock(return_value=iter([b'{}']))
                    )
                    importer.prepare_resources(
                        importer.IMPORT_CACHE + 'foo.json', ['CodeSystem'], [], [], {}
                    )

        upload_service_mock.url_for.assert_called_once_with(importer.IMPORT_CACHE + 'foo.json')

    def test_prepare_resources_npm_tar_without_package_json_ignores_key_error(self):
        with tempfile.NamedTemporaryFile(suffix='.tgz', delete=False) as tmp_file:
            with tarfile.open(fileobj=tmp_file, mode='w:gz') as tar:
                data = json.dumps({'resourceType': 'CodeSystem', 'id': 'x'}).encode('utf-8')
                info = tarfile.TarInfo(name='other/file.json')
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            tmp_path = tmp_file.name

        try:
            importer = Importer('1', tmp_path, 'root', 'users', 'root', 'npm')
            resources = {}
            importer.prepare_resources(tmp_path, ['CodeSystem'], [], [], resources)
        finally:
            os.remove(tmp_path)

        self.assertEqual(resources, {})

    def test_calculate_batch_size_large_volume(self):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        batch_size = importer.calculate_batch_size({'ValueSet': {'a': 60000}})
        self.assertEqual(batch_size, 60)

    def test_calculate_batch_size_small_volume(self):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        batch_size = importer.calculate_batch_size({'ValueSet': {'a': 10}})
        self.assertEqual(batch_size, Importer.MIN_BATCH_SIZE)

    @patch('core.importers.importer.bulk_import_queue')
    @patch('core.importers.importer.import_finisher')
    @patch('core.importers.importer.bulk_import_subtask_empty')
    @patch('core.importers.importer.bulk_import_subtask')
    def test_schedule_tasks(self, subtask_mock, empty_mock, finisher_mock, queue_mock):
        subtask_mock.si.return_value = Mock(set=Mock(return_value=Mock()))
        empty_mock.si.return_value = Mock()
        finisher_mock.si.return_value = Mock(set=Mock(return_value=Mock()))
        queue_mock.si.return_value = Mock(apply_async=Mock())

        importer = Importer('task-id', 'path', 'root', 'users', 'root')
        tasks = [
            [{'path': 'p', 'username': 'root', 'owner_type': 'users', 'owner': 'root',
              'resource_type': 'ValueSet', 'files': []}],
        ]

        final_task, subtask_ids = importer.schedule_tasks(tasks)

        self.assertEqual(len(subtask_ids), 1)
        self.assertIsNotNone(final_task)
        queue_mock.si.assert_called_once()
        queue_mock.si.return_value.apply_async.assert_called_once_with(queue='concurrent')
        empty_mock.si.assert_called_once()  # single-task group padded to avoid celery collapsing it

    def test_categorize_resources_json_error(self):
        importer = Importer('1', 'path', 'root', 'users', 'root')
        with patch('core.importers.importer.ijson.parse', side_effect=JSONError('bad json')):
            with self.assertRaises(JSONError):
                importer.categorize_resources(io.BytesIO(b'garbage'), '/path', 'file.json', ['CodeSystem'], {})


class ImporterSubtaskTest(OCLTestCase):

    @staticmethod
    def get_absolute_path(path):
        module_dir = os.path.dirname(__file__)  # get current directory
        file_path = os.path.join(module_dir, path)
        return file_path

    @patch.object(ResourceImporter, 'import_resource')
    @responses.activate
    def test_run(self, mocked_import_resource):
        mocked_import_resource.return_value = 1

        path = 'http://fetch/npm/package'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': 'package/ValueSet-fr-core-vs-location-type.json', 'start_index': 0, 'end_index': 1},
            {'filepath': 'package/ValueSet-fr-core-vs-marital-status.json', 'start_index': 0, 'end_index': 1},
            {'filepath': 'package/ValueSet-fr-core-vs-identity-reliability.json', 'start_index': 0, 'end_index': 1},
        ])
        with open(ImporterSubtaskTest.get_absolute_path('tests/hl7.fhir.fr.core-2.0.1.tgz'), 'rb') as file:
            responses.add(responses.GET, path, body=file.read(), status=200,
                          content_type='application/tar+gzip', stream=True)
            importer.run()

        mocked_import_resource.assert_has_calls([
            call({'resourceType': 'ValueSet', 'id': 'fr-core-vs-location-type',
                  'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/shareablevalueset']},
                  'text': {'status': 'generated',
                           'div': '<div xmlns="http://www.w3.org/1999/xhtml"><ul><li>Include all codes defined in '
                                  '<a href="CodeSystem-fr-core-cs-location-type.html"><code>https://hl7.fr/ig/fhir/'
                                  'core/CodeSystem/fr-core-cs-location-type</code></a></li></ul></div>'},
                  'extension': [{'url': 'http://hl7.org/fhir/StructureDefinition/valueset-warning',
                                 'valueMarkdown': 'Types are for general categories of identifiers. See [the '
                                                  'identifier registry](identifier-registry.html) for a list of common '
                                                  'identifier systems'},
                                {'url': 'http://hl7.org/fhir/StructureDefinition/structuredefinition-standards-status',
                                 'valueCode': 'informative'},
                                {'url': 'http://hl7.org/fhir/StructureDefinition/structuredefinition-fmm',
                                 'valueInteger': 1},
                                {'url': 'http://hl7.org/fhir/StructureDefinition/structuredefinition-wg',
                                 'valueCode': 'fhir'}],
                  'url': 'https://hl7.fr/ig/fhir/core/ValueSet/fr-core-vs-location-type', 'version': '2.0.1',
                  'name': 'FRCoreValueSetLocationType', 'title': 'FR Core ValueSet Location type', 'status': 'active',
                  'experimental': False, 'date': '2024-04-16T11:41:28+02:00', 'publisher': "Interop'Santé", 'contact': [
                    {'name': "Interop'Santé", 'telecom': [{'system': 'url', 'value': 'http://interopsante.org/'}]},
                    {'name': 'InteropSanté',
                     'telecom': [{'system': 'email', 'value': 'fhir@interopsante.org', 'use': 'work'}]}],
                  'description': 'A role for a location | Jeu de valeurs du rôle joué par un lieu',
                  'jurisdiction': [{'coding': [{'system': 'urn:iso:std:iso:3166', 'code': 'FR', 'display': 'France'}]}],
                  'compose': {
                      'include': [{'system': 'https://hl7.fr/ig/fhir/core/CodeSystem/fr-core-cs-location-type'}]}},
                 'root', 'user', 'root'),
            call({'resourceType': 'ValueSet', 'id': 'fr-core-vs-marital-status',
                  'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/shareablevalueset']},
                  'text': {'status': 'extensions',
                           'div': '<div xmlns="http://www.w3.org/1999/xhtml"><p>This value set includes codes based on '
                                  'the following rules:</p><ul><li>Include all codes defined in <a href="CodeSystem-fr-'
                                  'core-cs-marital-status.html"><code>https://hl7.fr/ig/fhir/core/CodeSystem/fr-core-cs'
                                  '-marital-status</code></a></li><li>Include all codes defined in <a href="http://'
                                  'terminology.hl7.org/5.3.0/CodeSystem-v3-MaritalStatus.html"><code>http://terminology'
                                  '.hl7.org/CodeSystem/v3-MaritalStatus</code></a></li><li>Include these codes as '
                                  'defined in <a href="http://terminology.hl7.org/5.3.0/CodeSystem-v3-NullFlavor.html">'
                                  '<code>http://terminology.hl7.org/CodeSystem/v3-NullFlavor</code></a><table '
                                  'class="none"><tr><td style="white-space:nowrap"><b>XXXX</b></td><td><b>XXXX</b></td>'
                                  '<td><b>XXXX</b></td></tr><tr><td><a href="http://terminology.hl7.org/5.3.0/'
                                  'CodeSystem-v3-NullFlavor.html#v3-NullFlavor-UNK">UNK</a></td><td>unknown</td><td>'
                                  '**Description:**A proper value is applicable, but not known.<br/><br/>**Usage Notes'
                                  '**: This means the actual value is not known. If the only thing that is unknown is '
                                  'how to properly express the value in the necessary constraints (value set, datatype,'
                                  ' etc.), then the OTH or UNC flavor should be used. No properties should be included '
                                  'for a datatype with this property unless:<br/><br/>1.  Those properties themselves '
                                  'directly translate to a semantic of &quot;unknown&quot;. (E.g. a local code sent as '
                                  'a translation that conveys \'unknown\')<br/>2.  Those properties further qualify '
                                  'the nature of what is unknown. (E.g. specifying a use code of &quot;H&quot; and a '
                                  'URL prefix of &quot;tel:&quot; to convey that it is the home phone number that is '
                                  'unknown.)</td></tr></table></li></ul></div>'},
                  'url': 'https://hl7.fr/ig/fhir/core/ValueSet/fr-core-vs-marital-status', 'version': '2.0.1',
                  'name': 'FRCoreValueSetMaritalStatus', 'title': 'FR Core ValueSet Patient gender INS ValueSet',
                  'status': 'active', 'experimental': False, 'date': '2024-04-16T11:41:28+02:00',
                  'publisher': "Interop'Santé", 'contact': [
                    {'name': "Interop'Santé", 'telecom': [{'system': 'url', 'value': 'http://interopsante.org/'}]},
                    {'name': 'InteropSanté',
                     'telecom': [{'system': 'email', 'value': 'fhir@interopsante.org', 'use': 'work'}]}],
                  'description': 'Patient Gender for INS : male | female | unknown',
                  'jurisdiction': [{'coding': [{'system': 'urn:iso:std:iso:3166', 'code': 'FR', 'display': 'France'}]}],
                  'compose': {
                      'include': [{'system': 'https://hl7.fr/ig/fhir/core/CodeSystem/fr-core-cs-marital-status'},
                                  {'system': 'http://terminology.hl7.org/CodeSystem/v3-MaritalStatus'},
                                  {'system': 'http://terminology.hl7.org/CodeSystem/v3-NullFlavor',
                                   'concept': [{'code': 'UNK', 'display': 'unknown'}]}]}}, 'root', 'user', 'root'),
            call({'resourceType': 'ValueSet', 'id': 'fr-core-vs-identity-reliability',
                  'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/shareablevalueset']},
                  'text': {'status': 'generated',
                           'div': '<div xmlns="http://www.w3.org/1999/xhtml"><ul><li>Include all codes defined in '
                                  '<a href="CodeSystem-fr-core-cs-v2-0445.html"><code>https://hl7.fr/ig/fhir/core/'
                                  'CodeSystem/fr-core-cs-v2-0445</code></a></li></ul></div>'},
                  'url': 'https://hl7.fr/ig/fhir/core/ValueSet/fr-core-vs-identity-reliability', 'version': '2.0.1',
                  'name': 'FRCoreValueSetIdentityReliabilityStatus', 'title': 'FR Core ValueSet Identity reliability',
                  'status': 'active', 'experimental': False, 'date': '2024-04-16T11:41:28+02:00',
                  'publisher': "Interop'Santé", 'contact': [
                    {'name': "Interop'Santé", 'telecom': [{'system': 'url', 'value': 'http://interopsante.org/'}]},
                    {'name': 'InteropSanté',
                     'telecom': [{'system': 'email', 'value': 'fhir@interopsante.org', 'use': 'work'}]}],
                  'description': 'The reliability of the identity.',
                  'jurisdiction': [{'coding': [{'system': 'urn:iso:std:iso:3166', 'code': 'FR', 'display': 'France'}]}],
                  'compose': {'include': [{'system': 'https://hl7.fr/ig/fhir/core/CodeSystem/fr-core-cs-v2-0445'}]}},
                 'root', 'user', 'root')])

    @patch.object(ResourceImporter, 'import_resource')
    @responses.activate
    def test_run_with_start_index(self, mocked_import_resource):
        mocked_import_resource.return_value = 1

        path = 'http://fetch/npm/package'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': '/', 'start_index': 0, 'end_index': 1},
            {'filepath': '/', 'start_index': 1, 'end_index': 2}
        ])
        with open(ImporterSubtaskTest.get_absolute_path('tests/fhir_resources_01.json'), 'rb') as file:
            responses.add(responses.GET, path, body=file.read(), status=200,
                          content_type='application/json', stream=True)
            importer.run()

        mocked_import_resource.assert_has_calls([
            call({'resourceType': 'ValueSet', 'id': 'fr-core-vs-email-type',
                  'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/shareablevalueset']},
                  'text': {'status': 'generated',
                           'div': '<div xmlns="http://www.w3.org/1999/xhtml"><ul><li>Include all codes defined in '
                                  '<a href="https://interop.esante.gouv.fr/ig/nos/1.2.0/CodeSystem-TRE-R256-'
                                  'TypeMessagerie.html"><code>https://mos.esante.gouv.fr/NOS/TRE_R256-TypeMessagerie'
                                  '/FHIR/TRE-R256-TypeMessagerie</code></a></li></ul></div>'},
                  'extension': [{'url': 'http://hl7.org/fhir/StructureDefinition/structuredefinition-standards-status',
                                 'valueCode': 'informative'},
                                {'url': 'http://hl7.org/fhir/StructureDefinition/structuredefinition-fmm',
                                 'valueInteger': 0},
                                {'url': 'http://hl7.org/fhir/StructureDefinition/structuredefinition-wg',
                                 'valueCode': 'fhir'}],
                  'url': 'https://hl7.fr/ig/fhir/core/ValueSet/fr-core-vs-email-type', 'version': '2.0.1',
                  'name': 'FRCoreValueSetEmailType', 'title': 'FR Core ValueSet Email type', 'status': 'draft',
                  'experimental': False, 'date': '2024-04-16T11:41:28+02:00', 'publisher': "Interop'Santé", 'contact': [
                    {'name': "Interop'Santé", 'telecom': [{'system': 'url', 'value': 'http://interopsante.org/'}]},
                    {'name': 'InteropSanté',
                     'telecom': [{'system': 'email', 'value': 'fhir@interopsante.org', 'use': 'work'}]}],
                  'description': 'The type of email',
                  'jurisdiction': [{'coding': [{'system': 'urn:iso:std:iso:3166', 'code': 'FR', 'display': 'France'}]}],
                  'compose': {'include': [{
                                              'system': 'https://mos.esante.gouv.fr/NOS/TRE_R256-TypeMessagerie/'
                                                        'FHIR/TRE-R256-TypeMessagerie'}]}},
                 'root', 'user', 'root'),
            call({'resourceType': 'ValueSet', 'id': 'fr-core-vs-encounter-class',
                  'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/shareablevalueset']},
                  'text': {'status': 'generated',
                           'div': '<div xmlns="http://www.w3.org/1999/xhtml"><ul><li>Include these codes as defined '
                                  'in <code>http://terminology.hl7.org/ValueSet/v3-ActEncounterCode</code><table '
                                  'class="none"><tr><td style="white-space:nowrap"><b>XXXX</b></td><td><b>XXXX</b>'
                                  '</td></tr><tr><td>ACUTE</td><td>Inpatient acute</td></tr><tr><td>NONAC</td><td>'
                                  'Inpatient non acute</td></tr><tr><td>PRENC</td><td>Pre-admission</td></tr><tr>'
                                  '<td>SS</td><td>Short stay</td></tr><tr><td>VR</td><td>Virtual</td></tr></table>'
                                  '</li></ul></div>'},
                  'extension': [{'url': 'http://hl7.org/fhir/StructureDefinition/structuredefinition-standards-status',
                                 'valueCode': 'informative'},
                                {'url': 'http://hl7.org/fhir/StructureDefinition/structuredefinition-fmm',
                                 'valueInteger': 2},
                                {'url': 'http://hl7.org/fhir/StructureDefinition/structuredefinition-wg',
                                 'valueCode': 'pa'}],
                  'url': 'https://hl7.fr/ig/fhir/core/ValueSet/fr-core-vs-encounter-class', 'version': '2.0.1',
                  'name': 'FRCoreValueSetEncounterClass', 'title': 'FR Core ValueSet Encounter class',
                  'status': 'active', 'experimental': False, 'date': '2024-04-16T11:41:28+02:00',
                  'publisher': "Interop'Santé", 'contact': [
                    {'name': "Interop'Santé", 'telecom': [{'system': 'url', 'value': 'http://interopsante.org/'}]},
                    {'name': 'InteropSanté',
                     'telecom': [{'system': 'email', 'value': 'fhir@interopsante.org', 'use': 'work'}]}],
                  'description': 'A set of codes that can be used to indicate the class of the encounter.',
                  'jurisdiction': [{'coding': [{'system': 'urn:iso:std:iso:3166', 'code': 'FR', 'display': 'France'}]}],
                  'compose': {'include': [{'system': 'http://terminology.hl7.org/ValueSet/v3-ActEncounterCode',
                                           'concept': [{'code': 'ACUTE', 'display': 'Inpatient acute'},
                                                       {'code': 'NONAC', 'display': 'Inpatient non acute'},
                                                       {'code': 'PRENC', 'display': 'Pre-admission'},
                                                       {'code': 'SS', 'display': 'Short stay'},
                                                       {'code': 'VR', 'display': 'Virtual'}]}]}}, 'root', 'user',
                 'root')])

    @patch.object(ResourceImporter, 'import_resource')
    @responses.activate
    def test_run_with_import_exception(self, mocked_import_resource):
        path = 'http://fetch/npm/package'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': '/', 'start_index': 0, 'end_index': 1},
            {'filepath': '/', 'start_index': 1, 'end_index': 2}
        ])
        with open(ImporterSubtaskTest.get_absolute_path('tests/fhir_resources_01.json'), 'rb') as file:
            responses.add(responses.GET, path, body=file.read(), status=200,
                          content_type='application/json', stream=True)

            mocked_import_resource.side_effect = ImportError('Failed to save')
            results = importer.run()

        self.assertEqual(results, ['Failed to import resource with id fr-core-vs-email-type from '
                                    'http://fetch/npm/package// to user/root by root due to: Failed to save',
                                   'Failed to import resource with id fr-core-vs-encounter-class from '
                                    'http://fetch/npm/package// to user/root by root due to: Failed to save'])

    @patch.object(ResourceImporter, 'import_resource')
    @responses.activate
    def test_run_with_request_exception(self, _):
        path = 'http://fetch/npm/package'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': '/', 'start_index': 0, 'end_index': 1},
            {'filepath': '/', 'start_index': 1, 'end_index': 2}
        ])
        responses.add(responses.GET, path, body='Not found', status=404, content_type='application/text', stream=True)
        results = importer.run()

        self.assertEqual(results, ['Failed to GET http://fetch/npm/package, responded with 404',
                                   'Failed to GET http://fetch/npm/package, responded with 404'])

    @responses.activate
    def test_run_with_request_exception_and_missing_end_index_raises(self):
        # Documents existing behaviour: the except-handler in run() does `results_count += count`
        # where count is None when a file entry has no end_index, which raises TypeError.
        path = 'http://fetch/npm/package4'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': '/', 'start_index': 0},
        ])
        responses.add(
            responses.GET, path, body='Not found', status=404, content_type='application/text', stream=True)

        with self.assertRaises(TypeError):
            importer.run()

    @patch('core.importers.importer.get_export_service')
    @patch.object(ResourceImporter, 'import_resource')
    @responses.activate
    def test_run_uses_import_cache_url_for(self, mocked_import_resource, get_export_service_mock):
        mocked_import_resource.return_value = 1
        upload_service_mock = Mock()
        upload_service_mock.url_for.return_value = 'http://fetch/cached-subtask.json'
        get_export_service_mock.return_value = upload_service_mock

        path = Importer.IMPORT_CACHE + 'foo.json'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': '/', 'start_index': 0, 'end_index': 1},
        ])
        with open(ImporterSubtaskTest.get_absolute_path('tests/fhir_resources_01.json'), 'rb') as file:
            responses.add(
                responses.GET, 'http://fetch/cached-subtask.json', body=file.read(), status=200,
                content_type='application/json', stream=True)
            importer.run()

        upload_service_mock.url_for.assert_called_once_with(path)

    @patch.object(ResourceImporter, 'import_resource')
    @responses.activate
    def test_run_zip_file(self, mocked_import_resource):
        mocked_import_resource.return_value = 1
        path = 'http://fetch/npm/package.zip'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': 'package/ValueSet-fr-core-vs-location-type.json', 'start_index': 0, 'end_index': 1},
        ])
        with open(ImporterSubtaskTest.get_absolute_path('tests/hl7.fhir.fr.core-2.0.1.zip'), 'rb') as file:
            responses.add(
                responses.GET, path, body=file.read(), status=200, content_type='application/zip', stream=True)
            results = importer.run()

        self.assertEqual(results, [1])

    @patch.object(ResourceImporter, 'import_resource')
    @responses.activate
    def test_run_zip_file_without_end_index(self, mocked_import_resource):
        mocked_import_resource.return_value = 1
        path = 'http://fetch/npm/package1b.zip'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': 'package/ValueSet-fr-core-vs-location-type.json', 'start_index': 0},
        ])
        with open(ImporterSubtaskTest.get_absolute_path('tests/hl7.fhir.fr.core-2.0.1.zip'), 'rb') as file:
            responses.add(
                responses.GET, path, body=file.read(), status=200, content_type='application/zip', stream=True)
            results = importer.run()

        self.assertEqual(results, [1])

    def test_import_files_records_exception_when_import_resource_raises(self):
        importer = ImporterSubtask('local', 'root', 'user', 'root', 'ValueSet', [
            {'filepath': 'f1', 'start_index': 0},
        ])
        results = []
        with patch.object(ImporterSubtask, 'import_resource', side_effect=Exception('boom')):
            importer.import_files(Mock(), results)

        self.assertEqual(len(results), 1)
        self.assertIn('Failed to process', results[0])

    @responses.activate
    def test_run_zip_file_missing_entry_records_exception(self):
        path = 'http://fetch/npm/package2.zip'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': 'package/does-not-exist.json', 'start_index': 0, 'end_index': 1},
        ])
        with open(ImporterSubtaskTest.get_absolute_path('tests/hl7.fhir.fr.core-2.0.1.zip'), 'rb') as file:
            responses.add(
                responses.GET, path, body=file.read(), status=200, content_type='application/zip', stream=True)
            results = importer.run()

        self.assertEqual(len(results), 1)
        self.assertIn('Failed to process', results[0])

    @responses.activate
    def test_run_tar_file_missing_entry_records_exception(self):
        path = 'http://fetch/npm/package3'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': 'package/does-not-exist.json'},
        ])
        with open(ImporterSubtaskTest.get_absolute_path('tests/hl7.fhir.fr.core-2.0.1.tgz'), 'rb') as file:
            responses.add(
                responses.GET, path, body=file.read(), status=200,
                content_type='application/tar+gzip', stream=True)
            results = importer.run()

        self.assertEqual(len(results), 1)
        self.assertIn('Failed to process', results[0])

    @patch.object(ResourceImporter, 'import_resource')
    @responses.activate
    def test_run_local_files_without_end_index(self, mocked_import_resource):
        mocked_import_resource.return_value = 1
        path = 'http://fetch/plain.json'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': '/', 'start_index': 0},
        ])
        with open(ImporterSubtaskTest.get_absolute_path('tests/fhir_resources_01.json'), 'rb') as file:
            responses.add(
                responses.GET, path, body=file.read(), status=200, content_type='application/json', stream=True)
            results = importer.run()

        self.assertEqual(results, [1, 1])

    @responses.activate
    def test_run_move_to_start_index_no_matching_resource_type(self):
        path = 'http://fetch/plain2.json'
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'NonExistentType', [
            {'filepath': '/', 'start_index': 1, 'end_index': 2},
        ])
        with open(ImporterSubtaskTest.get_absolute_path('tests/fhir_resources_01.json'), 'rb') as file:
            responses.add(
                responses.GET, path, body=file.read(), status=200, content_type='application/json', stream=True)
            results = importer.run()

        self.assertEqual(results, [])

    @responses.activate
    def test_run_injects_owner_fields_for_source_resource_type(self):
        path = 'http://fetch/source.json'
        resource_json = json.dumps({'type': 'source', 'id': 'imported-src', 'name': 'Imported Source'})
        importer = ImporterSubtask(path, 'ocladmin', 'Organization', 'OCL', 'source', [
            {'filepath': '/', 'start_index': 0, 'end_index': 1},
        ])
        responses.add(
            responses.GET, path, body=resource_json, status=200, content_type='application/json', stream=True)
        importer.run()

        source = Source.objects.filter(mnemonic='imported-src').first()
        self.assertIsNotNone(source)
        self.assertEqual(source.organization.mnemonic, 'OCL')

    @patch.object(ResourceImporter, 'import_resource')
    @responses.activate
    def test_run_appends_error_message_when_result_not_int(self, mocked_import_resource):
        mocked_import_resource.return_value = {'id': ['This field is required.']}
        path = 'http://fetch/badresource.json'
        resource_json = json.dumps({'resourceType': 'ValueSet', 'id': 'bad'})
        importer = ImporterSubtask(path, 'root', 'user', 'root', 'ValueSet', [
            {'filepath': '/', 'start_index': 0, 'end_index': 1},
        ])
        responses.add(
            responses.GET, path, body=resource_json, status=200, content_type='application/json', stream=True)
        results = importer.run()

        self.assertEqual(len(results), 1)
        self.assertIn("due to: {'id': ['This field is required.']}", results[0])


class ResourceImporterTest(OCLAPITestCase):

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_import_code_system(self):
        ResourceImporter().import_resource(
            {"resourceType": "CodeSystem", "id": "fr-core-cs-identifier-type",
             "meta": {"profile": ["http://hl7.org/fhir/StructureDefinition/shareablecodesystem"]},
             "text": {"status": "generated",
                      "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\"><p>ThXXXX</p><table class=\"codes\"><tr>"
                             "<td style=\"white-space:nowrap\"><b>XXXX</b></td><td><b>XXXX</b></td></tr><tr>"
                             "<td style=\"white-space:nowrap\">VN<a name=\"fr-core-cs-identifier-type-VN\"> </a></td>"
                             "<td>Visit Number</td></tr><tr><td style=\"white-space:nowrap\">MN"
                             "<a name=\"fr-core-cs-identifier-type-MN\"> </a></td><td>Movement Number</td></tr>"
                             "</table></div>"},
             "url": "https://hl7.fr/ig/fhir/core/CodeSystem/fr-core-cs-identifier-type", "version": "2.0.1",
             "name": "FRCoreCodeSystemIdentifierType", "title": "FR Core CodeSystem Identifier Type", "status": "draft",
             "experimental": False, "date": "2024-04-16T11:41:28+02:00", "publisher": "Interop'Santé",
             "contact": [{"name": "Interop'Santé", "telecom": [{"system": "url", "value": "http://interopsante.org/"}]},
                         {"name": "InteropSanté",
                          "telecom": [{"system": "email", "value": "fhir@interopsante.org", "use": "work"}]}],
             "description": "Identifier type",
             "jurisdiction": [{"coding": [{"system": "urn:iso:std:iso:3166", "code": "FR", "display": "France"}]}],
             "caseSensitive": True, "content": "complete", "count": 2,
             "concept": [{"code": "VN", "display": "Visit Number"}, {"code": "MN", "display": "Movement Number"}]}
            , 'ocladmin', 'orgs', 'OCL')
        source = Source.objects.filter(mnemonic='fr-core-cs-identifier-type').first()
        self.assertEqual(source.mnemonic, 'fr-core-cs-identifier-type')

    def test_import_value_set(self):
        ResourceImporter().import_resource(
            {"resourceType": "ValueSet", "id": "fr-core-vs-identity-reliability",
             "meta": {"profile": ["http://hl7.org/fhir/StructureDefinition/shareablevalueset"]},
             "text": {"status": "generated",
                      "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\"><ul><li>Include all codes defined in "
                             "<a href=\"CodeSystem-fr-core-cs-v2-0445.html\"><code>https://hl7.fr/ig/fhir/core/"
                             "CodeSystem/fr-core-cs-v2-0445</code></a></li></ul></div>"},
             "url": "https://hl7.fr/ig/fhir/core/ValueSet/fr-core-vs-identity-reliability", "version": "2.0.1",
             "name": "FRCoreValueSetIdentityReliabilityStatus", "title": "FR Core ValueSet Identity reliability",
             "status": "active", "experimental": False, "date": "2024-04-16T11:41:28+02:00",
             "publisher": "Interop'Santé",
             "contact": [{"name": "Interop'Santé", "telecom": [{"system": "url", "value": "http://interopsante.org/"}]},
                         {"name": "InteropSanté",
                          "telecom": [{"system": "email", "value": "fhir@interopsante.org", "use": "work"}]}],
             "description": "The reliability of the identity.",
             "jurisdiction": [{"coding": [{"system": "urn:iso:std:iso:3166", "code": "FR", "display": "France"}]}],
             "compose": {"include": [{"system": "https://hl7.fr/ig/fhir/core/CodeSystem/fr-core-cs-v2-0445"}]}}
            , 'ocladmin', 'orgs', 'OCL')
        collection = Collection.objects.filter(mnemonic='fr-core-vs-identity-reliability').first()
        self.assertEqual(collection.mnemonic, 'fr-core-vs-identity-reliability')

    def test_import_source(self):
        ResourceImporter().import_resource({'type': 'source', 'id': 'full_name', 'name': 'Full name',
                                            'owner_type': 'Organization', 'owner': 'OCL'},
                                           'ocladmin', 'orgs', 'OCL')
        source = Source.objects.filter(mnemonic='full_name').first()
        self.assertEqual(source.mnemonic, 'full_name')

    @patch('core.importers.models.Concept.persist_new')
    def test_import_concept_does_not_skip_hierarchy_tasks_by_default(self, persist_new_mock):
        source = OrganizationSourceFactory(
            organization=OrganizationFactory(mnemonic='DemoOrg'), mnemonic='DemoSource', version='HEAD'
        )
        parent_concept = ConceptFactory(parent=source, mnemonic='Parent')
        persist_new_mock.return_value = Mock(id=1, errors={})

        result = ResourceImporter().import_resource(
            {
                'type': 'concept', 'id': 'Child', 'concept_class': 'Root', 'datatype': 'None',
                'source': 'DemoSource', 'owner': 'DemoOrg', 'owner_type': 'Organization',
                'names': [{'name': 'Child', 'locale': 'en', 'locale_preferred': True, 'name_type': 'Fully Specified'}],
                'descriptions': [], 'parent_concept_urls': [parent_concept.uri]
            },
            'ocladmin', 'orgs', 'DemoOrg'
        )

        self.assertEqual(result, 1)
        self.assertNotIn('_skip_hierarchy_tasks', persist_new_mock.call_args.kwargs['data'])

    def test_get_resource_types(self):
        resource_types = ResourceImporter.get_resource_types()
        self.assertIn('Source', resource_types)
        self.assertIn('Concept', resource_types)
        self.assertIn('Mapping', resource_types)

    @patch.object(ResourceImporter, 'import_value_set')
    def test_import_resource_dispatches_value_set(self, import_value_set_mock):
        import_value_set_mock.return_value = CREATED
        result = ResourceImporter().import_resource(
            {'resourceType': 'ValueSet', 'url': 'http://x'}, 'ocladmin', 'orgs', 'OCL')
        self.assertEqual(result, CREATED)
        import_value_set_mock.assert_called_once_with(
            'OCL', 'orgs', {'resourceType': 'ValueSet', 'url': 'http://x'}, 'ValueSet', 'http://x', 'ocladmin')

    @patch.object(ResourceImporter, 'import_concept_map')
    def test_import_resource_dispatches_concept_map(self, import_concept_map_mock):
        import_concept_map_mock.return_value = CREATED
        result = ResourceImporter().import_resource(
            {'resourceType': 'ConceptMap', 'url': 'http://y'}, 'ocladmin', 'orgs', 'OCL')
        self.assertEqual(result, CREATED)
        import_concept_map_mock.assert_called_once_with(
            'OCL', 'orgs', {'resourceType': 'ConceptMap', 'url': 'http://y'}, 'ConceptMap', 'http://y', 'ocladmin')

    def test_import_resource_returns_none_for_unhandled_fhir_resource_type(self):
        result = ResourceImporter().import_resource({'resourceType': 'Patient'}, 'ocladmin', 'orgs', 'OCL')
        self.assertIsNone(result)

    def test_import_resource_returns_none_when_no_importer_can_handle(self):
        result = ResourceImporter().import_resource({'type': 'unknown-type'}, 'ocladmin', 'orgs', 'OCL')
        self.assertIsNone(result)

    def test_find_existing_source_org_owner_by_canonical_url(self):
        source = OrganizationSourceFactory(canonical_url='http://find.me/org')
        found = ResourceImporter.find_existing_source(source.organization.mnemonic, 'orgs', 'http://find.me/org')
        self.assertIn(source, list(found))

    def test_find_existing_source_user_owner_by_canonical_url(self):
        user = UserProfileFactory()
        source = OrganizationSourceFactory(organization=None, user=user, canonical_url='http://find.me/user')
        found = ResourceImporter.find_existing_source(user.username, 'users', 'http://find.me/user')
        self.assertIn(source, list(found))

    def test_find_existing_source_raises_when_owner_not_found(self):
        with self.assertRaises(ValidationError):
            ResourceImporter.find_existing_source('missing-owner', 'orgs', 'http://x')

    def test_find_existing_source_falls_back_to_uri_org_owner(self):
        source = OrganizationSourceFactory()
        found = ResourceImporter.find_existing_source(source.organization.mnemonic, 'orgs', source.uri)
        self.assertIn(source, list(found))

    def test_find_existing_source_falls_back_to_uri_user_owner(self):
        user = UserProfileFactory()
        source = OrganizationSourceFactory(organization=None, user=user)
        found = ResourceImporter.find_existing_source(user.username, 'users', source.uri)
        self.assertIn(source, list(found))

    @patch('core.importers.importer.ConceptMapDetailSerializer')
    def test_import_concept_map_creates_when_not_existing(self, serializer_cls_mock):
        serializer_instance = Mock(is_valid=Mock(return_value=True), errors=None)
        serializer_cls_mock.return_value = serializer_instance

        result = ResourceImporter.import_concept_map(
            'OCL', 'orgs', {'resourceType': 'ConceptMap', 'url': 'http://cm.new'}, 'ConceptMap',
            'http://cm.new', 'ocladmin'
        )

        self.assertEqual(result, CREATED)
        serializer_instance.save.assert_called_once()

    @patch('core.importers.importer.ConceptMapDetailSerializer')
    def test_import_concept_map_updates_when_existing(self, serializer_cls_mock):
        source = OrganizationSourceFactory(canonical_url='http://cm.existing')
        serializer_instance = Mock(is_valid=Mock(return_value=True), errors=None)
        serializer_cls_mock.return_value = serializer_instance

        result = ResourceImporter.import_concept_map(
            source.organization.mnemonic, 'orgs', {'resourceType': 'ConceptMap'}, 'ConceptMap',
            'http://cm.existing', 'ocladmin'
        )

        self.assertEqual(result, UPDATED)
        serializer_cls_mock.assert_called_once_with(source, data={'resourceType': 'ConceptMap'}, context=ANY)

    @patch('core.importers.importer.ConceptMapDetailSerializer')
    def test_import_concept_map_returns_errors_when_invalid(self, serializer_cls_mock):
        serializer_instance = Mock(is_valid=Mock(return_value=False), errors={'url': ['bad']})
        serializer_cls_mock.return_value = serializer_instance

        result = ResourceImporter.import_concept_map(
            'OCL', 'orgs', {'resourceType': 'ConceptMap'}, 'ConceptMap', 'http://cm.err', 'ocladmin'
        )

        self.assertEqual(result, {'url': ['bad']})
        serializer_instance.save.assert_not_called()

    def test_import_value_set_raises_when_owner_not_found(self):
        with self.assertRaises(ValidationError):
            ResourceImporter.import_value_set('missing-owner', 'orgs', {}, 'ValueSet', 'http://x', 'ocladmin')

    @patch('core.importers.importer.ValueSetDetailSerializer')
    def test_import_value_set_user_owner_creates(self, serializer_cls_mock):
        user = UserProfileFactory()
        serializer_instance = Mock(is_valid=Mock(return_value=True), errors=None)
        serializer_cls_mock.return_value = serializer_instance

        result = ResourceImporter.import_value_set(
            user.username, 'users', {'resourceType': 'ValueSet'}, 'ValueSet', 'http://newvs.user', 'ocladmin'
        )

        self.assertEqual(result, CREATED)

    @patch('core.importers.importer.ValueSetDetailSerializer')
    def test_import_value_set_updates_when_existing(self, serializer_cls_mock):
        collection = OrganizationCollectionFactory(canonical_url='http://vs.existing')
        serializer_instance = Mock(is_valid=Mock(return_value=True), errors=None)
        serializer_cls_mock.return_value = serializer_instance

        result = ResourceImporter.import_value_set(
            collection.organization.mnemonic, 'orgs', {'resourceType': 'ValueSet'}, 'ValueSet',
            'http://vs.existing', 'ocladmin'
        )

        self.assertEqual(result, UPDATED)
        serializer_cls_mock.assert_called_once_with(collection, data={'resourceType': 'ValueSet'}, context=ANY)

    @patch('core.importers.importer.ValueSetDetailSerializer')
    def test_import_value_set_falls_back_to_uri_org_owner(self, serializer_cls_mock):
        collection = OrganizationCollectionFactory()
        serializer_instance = Mock(is_valid=Mock(return_value=True), errors=None)
        serializer_cls_mock.return_value = serializer_instance

        result = ResourceImporter.import_value_set(
            collection.organization.mnemonic, 'orgs', {'resourceType': 'ValueSet'}, 'ValueSet',
            collection.uri, 'ocladmin'
        )

        self.assertEqual(result, UPDATED)

    @patch('core.importers.importer.ValueSetDetailSerializer')
    def test_import_value_set_falls_back_to_uri_user_owner(self, serializer_cls_mock):
        user = UserProfileFactory()
        collection = OrganizationCollectionFactory(organization=None, user=user)
        serializer_instance = Mock(is_valid=Mock(return_value=True), errors=None)
        serializer_cls_mock.return_value = serializer_instance

        result = ResourceImporter.import_value_set(
            user.username, 'users', {'resourceType': 'ValueSet'}, 'ValueSet', collection.uri, 'ocladmin'
        )

        self.assertEqual(result, UPDATED)

    @patch('core.importers.importer.CodeSystemDetailSerializer')
    def test_import_code_system_updates_when_existing(self, serializer_cls_mock):
        source = OrganizationSourceFactory(canonical_url='http://cs.existing')
        serializer_instance = Mock(is_valid=Mock(return_value=True), errors=None)
        serializer_cls_mock.return_value = serializer_instance

        result = ResourceImporter.import_code_system(
            source.organization.mnemonic, 'orgs', {'resourceType': 'CodeSystem'}, 'CodeSystem',
            'http://cs.existing', 'ocladmin'
        )

        self.assertEqual(result, UPDATED)
        serializer_cls_mock.assert_called_once_with(source, data={'resourceType': 'CodeSystem'}, context=ANY)


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
        self.assertEqual(len(parser.content), 48)
        zipfile_mock.assert_called_once_with(file, 'r')

    @patch('core.importers.input_parsers.ZipFile')
    def test_parse_minified_zip_file(self, zipfile_mock):
        file = open(
            os.path.join(
                os.path.dirname(__file__), '..', 'samples/DemoSource_v1.0.20230526120030.minified.zip'), 'r')
        real_zipfile = ZipFile(file.name, 'r')
        zipfile_mock.return_value = real_zipfile

        parser = ImportContentParser(file=file)
        parser.parse()

        self.assertIsNotNone(parser.content)
        self.assertEqual(len(parser.content), 48)
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
        self.assertEqual(len(parser.content), 48)
        requests_get_mock.assert_called_once_with(
            'https://file.zip', headers={'User-Agent': 'OCL'}, stream=True, timeout=30)

    @patch('requests.get')
    def test_fetch_file_from_url_request_exception(self, requests_get_mock):
        requests_get_mock.side_effect = Exception('boom')

        parser = ImportContentParser(file_url='https://file.json')
        parser.parse()

        self.assertEqual(parser.errors, ['Failed to download file from https://file.json, Exception: boom.'])

    @patch('requests.get')
    def test_set_file_from_response_non_zip_sets_file_to_text(self, requests_get_mock):
        requests_get_mock.return_value = Mock(ok=True, text='{"type": "foobar"}')

        parser = ImportContentParser(file_url='https://file.json')
        parser.set_content_type()
        parser.set_file_from_response(parser.fetch_file_from_url())

        self.assertEqual(parser.file, '{"type": "foobar"}')

    @patch('requests.get')
    def test_parse_file_url_failed_response(self, requests_get_mock):
        requests_get_mock.return_value = Mock(ok=False, status_code=404)

        parser = ImportContentParser(file_url='https://file.json')
        parser.parse()

        self.assertEqual(parser.errors, ['Failed to download file from https://file.json, Status: 404.'])

    @patch('core.importers.input_parsers.OclStandardCsvToJsonConverter')
    def test_parse_csv_file_processing_exception(self, converter_mock):
        converter_mock.return_value.process.side_effect = Exception('bad csv')
        file = open(os.path.join(os.path.dirname(__file__), '..', 'samples/ocl_csv_with_retired_concepts.csv'), 'r')

        parser = ImportContentParser(file=file)
        parser.parse()

        self.assertEqual(parser.errors, ['Failed to process CSV file: bad csv.'])

    def test_parse_zip_file_with_multiple_files_errors(self):
        buffer = io.BytesIO()
        with ZipFile(buffer, 'w') as zip_file:
            zip_file.writestr('a.json', '{}')
            zip_file.writestr('b.json', '{}')
        buffer.seek(0)

        parser = ImportContentParser(file=buffer)
        parser.file_name = 'multi.zip'
        parser.parse()

        self.assertEqual(parser.errors, ['Zip file must contain exactly one file.'])

    def test_parse_zip_file_with_single_csv_file(self):
        csv_path = os.path.join(os.path.dirname(__file__), '..', 'samples/ocl_csv_with_retired_concepts.csv')
        with open(csv_path, 'r') as csv_file:
            csv_content = csv_file.read()

        buffer = io.BytesIO()
        with ZipFile(buffer, 'w') as zip_file:
            zip_file.writestr('data.csv', csv_content)
        buffer.seek(0)

        parser = ImportContentParser(file=buffer)
        parser.file_name = 'single.csv.zip'
        parser.parse()

        self.assertIsInstance(parser.content, list)
        self.assertTrue(len(parser.content) > 0)
