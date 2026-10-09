import datetime
import json
import os
import time
import uuid
from collections import OrderedDict
from unittest.mock import patch, Mock, MagicMock, ANY

import django
import factory
from celery_once import AlreadyQueued
from colour_runner.django_runner import ColourRunnerMixin
from django.conf import settings
from django.contrib.auth.models import AnonymousUser, Group
from django.core.files.base import File
from django.core.management import call_command, CommandError
from django.http import HttpResponse
from django.test import TestCase, override_settings, RequestFactory
from django.test.runner import DiscoverRunner
from django.utils import timezone
from mock.mock import call
from requests import ConnectTimeout
from requests.auth import HTTPBasicAuth
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase, APITransactionTestCase

from core.collections.models import CollectionReference, Expansion
from core.collections.tests.factories import ExpansionFactory, OrganizationCollectionFactory, \
    UserCollectionFactory
from core.common.checksums import VersionCompareMixin, ChecksumDiff
from core.common.constants import HEAD
from core.common.es import ESScript
from core.common.exceptions import BatchIndexingError, DeactivatedAccountLoginRefused
from core.common.models import BaseModel
from core.common.tasks import delete_s3_objects, bulk_import_parallel_inline, resources_report, calculate_checksums, \
    delete_organization, delete_source, delete_collection, add_references, handle_m2m_changed, handle_pre_delete, \
    populate_indexes, rebuild_indexes, bulk_import_subtask_empty, import_finisher, \
    process_hierarchy_for_new_parent_concept_version, batch_index_resources, index_expansion_concepts, \
    index_expansion_mappings, vacuum_and_analyze_db, resolve_url_registry_entries, expire_old_celery_tasks, \
    source_version_compare, bulk_import_new, bulk_import_subtask, bulk_import_queue, \
    collection_version_compare, expansion_compare, index_concepts_mapped_codes
from core.common.throttling import (
    CoreDayThrottle,
    CoreMinuteThrottle,
    GuestDayThrottle,
    GuestMinuteThrottle,
    MatchCoreDayThrottle,
    MatchCoreMinuteThrottle,
    MatchStandardDayThrottle,
    MatchStandardMinuteThrottle,
    StandardDayThrottle,
    StandardMinuteThrottle,
    ThrottleUtil,
)
from core.common.utils import (
    compact_dict_by_values, to_snake_case, flower_get,
    to_camel_case,
    drop_version, is_versioned_uri, separate_version, to_parent_uri, jsonify_safe, es_get,
    get_resource_class_from_resource_name, flatten_dict, flatten_extras, is_csv_file, is_url_encoded_string,
    to_parent_uri_from_kwargs,
    set_current_user, get_current_user, set_request_url, get_request_url, nested_dict_values, chunks, api_get,
    split_list_by_condition, is_zip_file, get_date_range_label, get_prev_month, from_string_to_date, get_end_of_month,
    get_start_of_month, es_id_in, web_url, get_queue_task_names, get_resource_class_from_resource_uri, encode_string,
    to_parent_kwargs_from_uri, reverse_resource, reverse_resource_version, write_export_file, queue_bulk_import,
    get_bulk_import_celery_once_lock_key, generic_sort, get_embeddings, encode_texts, get_lm_model)
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.mappings.documents import MappingDocument
from core.middlewares.middlewares import RequireAuthenticationMiddleware
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.constants import CORE_USER_GROUP, GUEST_GROUP
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory
from .backends import OCLOIDCAuthenticationBackend
from .checksums import Checksum
from .fhir_helpers import translate_fhir_query
from .serializers import IdentifierSerializer
from .validators import URIValidator
from ..code_systems.serializers import CodeSystemDetailSerializer
from ..concepts.serializers import ConceptDetailSerializer
from ..concepts.tests.factories import ConceptFactory, ConceptNameFactory
from ..mappings.serializers import MappingDetailSerializer
from ..mappings.tests.factories import MappingFactory
from ..sources.tests.factories import OrganizationSourceFactory, UserSourceFactory

PREVIEW_GROUP_NAME = 'preview'
TEST_GROUPS_CONFIG_FILE = 'core/capabilities/tests/groups.test.yaml'


class CustomTestRunner(ColourRunnerMixin, DiscoverRunner):
    pass


class SetupTestEnvironment:
    settings.TEST_MODE = True
    settings.ELASTICSEARCH_DSL_AUTOSYNC = True
    settings.ES_SYNC = True
    if settings.ENV == 'ci':
        # No redis service available on CI; use a no-op cache so cache reads/writes are harmless no-ops
        # instead of raising connection errors.
        settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}


class BaseTestCase(SetupTestEnvironment):
    @staticmethod
    def patch_concept_es_mapping_for_ci():
        """
        On a freshly created CI Elasticsearch index, `*_text` fields are plain `text` fields with no
        `.keyword` multi-field, unlike long-lived indexes (local/staging/prod) where this sub-field
        already exists. Facet/filter assertions need it, so patch it in additively for CI test runs
        only, rather than declaring it in the Document mapping itself.
        """
        if settings.ENV != 'ci':
            return
        from elasticsearch_dsl.connections import connections  # pylint: disable=import-outside-toplevel
        connections.get_connection().indices.put_mapping(
            index='concepts',
            body={
                'properties': {
                    field: {'type': 'text', 'fields': {'keyword': {'type': 'keyword'}}}
                    for field in ('concept_class_text', 'datatype_text', 'source_text', 'owner_text')
                }
            }
        )

    @staticmethod
    def create_lookup_concept_classes(user=None, org=None):
        org = org or Organization.objects.get(mnemonic='OCL')
        user = user or UserProfile.objects.get(username='ocladmin')

        classes_source = OrganizationSourceFactory(updated_by=user, organization=org, mnemonic="Classes", version=HEAD)
        datatypes_source = OrganizationSourceFactory(
            updated_by=user, organization=org, mnemonic="Datatypes", version=HEAD
        )
        nametypes_source = OrganizationSourceFactory(
            updated_by=user, organization=org, mnemonic="NameTypes", version=HEAD
        )
        descriptiontypes_source = OrganizationSourceFactory(
            updated_by=user, organization=org, mnemonic="DescriptionTypes", version=HEAD
        )
        maptypes_source = OrganizationSourceFactory(
            updated_by=user, organization=org, mnemonic="MapTypes", version=HEAD
        )
        locales_source = OrganizationSourceFactory(updated_by=user, organization=org, mnemonic="Locales", version=HEAD)

        ConceptFactory(
            version=HEAD, updated_by=user, parent=classes_source, concept_class="Concept Class",
            names=[ConceptNameFactory.build(name="Diagnosis")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=classes_source, concept_class="Concept Class",
            names=[ConceptNameFactory.build(name="Drug")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=classes_source, concept_class="Concept Class",
            names=[ConceptNameFactory.build(name="Test")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=classes_source, concept_class="Concept Class",
            names=[ConceptNameFactory.build(name="Procedure")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=datatypes_source, concept_class="Datatype",
            names=[ConceptNameFactory.build(name="None"), ConceptNameFactory.build(name="N/A")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=datatypes_source, concept_class="Datatype",
            names=[ConceptNameFactory.build(name="Numeric")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=datatypes_source, concept_class="Datatype",
            names=[ConceptNameFactory.build(name="Coded")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=datatypes_source, concept_class="Datatype",
            names=[ConceptNameFactory.build(name="Text")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=nametypes_source, concept_class="NameType",
            names=[ConceptNameFactory.build(name="FULLY_SPECIFIED"), ConceptNameFactory.build(name="Fully Specified")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=nametypes_source, concept_class="NameType",
            names=[ConceptNameFactory.build(name="Short"), ConceptNameFactory.build(name="SHORT")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=nametypes_source, concept_class="NameType",
            names=[ConceptNameFactory.build(name="INDEX_TERM"), ConceptNameFactory.build(name="Index Term")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=nametypes_source, concept_class="NameType",
            names=[ConceptNameFactory.build(name="None")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=descriptiontypes_source, concept_class="DescriptionType",
            names=[ConceptNameFactory.build(name="None")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=descriptiontypes_source, concept_class="DescriptionType",
            names=[ConceptNameFactory.build(name="FULLY_SPECIFIED")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=descriptiontypes_source, concept_class="DescriptionType",
            names=[ConceptNameFactory.build(name="Definition")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[ConceptNameFactory.build(name="SAME-AS"), ConceptNameFactory.build(name="Same As")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[ConceptNameFactory.build(name="Is Subset of")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[ConceptNameFactory.build(name="Different")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[
                ConceptNameFactory.build(name="BROADER-THAN"), ConceptNameFactory.build(name="Broader Than"),
                ConceptNameFactory.build(name="BROADER_THAN")
            ]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[
                ConceptNameFactory.build(name="NARROWER-THAN"), ConceptNameFactory.build(name="Narrower Than"),
                ConceptNameFactory.build(name="NARROWER_THAN")
            ]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[ConceptNameFactory.build(name="Q-AND-A")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[ConceptNameFactory.build(name="More specific than")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[ConceptNameFactory.build(name="Less specific than")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[ConceptNameFactory.build(name="Something Else")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[ConceptNameFactory.build(name="en")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[ConceptNameFactory.build(name="es")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[ConceptNameFactory.build(name="fr")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[ConceptNameFactory.build(name="tr")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[ConceptNameFactory.build(name="Abkhazian")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[ConceptNameFactory.build(name="English")]
        )


class OCLAPITransactionTestCase(APITransactionTestCase, BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")
        call_command("loaddata", "core/fixtures/auth_groups.yaml")
        call_command("loaddata", "core/fixtures/capabilities.yaml")
        call_command("loaddata", "core/fixtures/toggles.json")
        call_command("seed_group_capabilities", file=TEST_GROUPS_CONFIG_FILE)
        org = Organization.objects.get(id=1)
        org.members.add(1)


class OCLAPITestCase(APITestCase, BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")
        call_command("loaddata", "core/fixtures/auth_groups.yaml")
        call_command("loaddata", "core/fixtures/capabilities.yaml")
        call_command("loaddata", "core/fixtures/toggles.json")
        call_command("seed_group_capabilities", file=TEST_GROUPS_CONFIG_FILE)
        org = Organization.objects.get(id=1)
        org.members.add(1)

    def setUp(self):
        super().setUp()
        self.maxDiff = None


class OCLTestCase(TestCase, BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")
        call_command("loaddata", "core/fixtures/auth_groups.yaml")
        call_command("loaddata", "core/fixtures/capabilities.yaml")
        call_command("loaddata", "core/fixtures/toggles.json")
        call_command("seed_group_capabilities", file=TEST_GROUPS_CONFIG_FILE)

    @staticmethod
    def factory_to_params(factory_klass, **kwargs):
        return {
            **factory.build(dict, FACTORY_CLASS=factory_klass),
            **kwargs
        }

    def setUp(self):
        super().setUp()
        self.maxDiff = None


class FhirHelpersTest(OCLTestCase):
    def test_language_to_default_locale(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'language': 'eng'}
        query_set = Source.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('"sources"."default_locale" = eng' in str(query_set.query))

    def test_status_retired(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'status': 'retired'}
        query_set = Source.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('WHERE "sources"."retired"' in str(query_set.query))

    def test_status_active(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'status': 'active'}
        query_set = Source.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('WHERE "sources"."released"' in str(query_set.query))

    def test_status_draft(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'status': 'draft'}
        query_set = Source.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('WHERE NOT "sources"."released"' in str(query_set.query))

    def test_title_to_full_name(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'title': 'some title'}
        query_set = Source.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('WHERE "sources"."full_name" = some title' in str(query_set.query))

    def test_other_fields(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'version': 'v1', 'id': '2'}
        query_set = Source.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('"sources"."version" = v1' in str(query_set.query))
        self.assertTrue('"sources"."id" = 2' in str(query_set.query))


class IdentifierSerializerTest(OCLTestCase):
    def test_deserialize(self):
        data = {'system': '/org/OCL/test',
                'value': '1',
                'type': {
                    'text': 'Accession ID',
                    'coding': [{
                        'system': 'http://hl7.org/fhir/v2/0203',
                        'code': 'ACSN',
                        'display': 'ACSN'
                    }]
                }}
        serializer = IdentifierSerializer(data=data)
        valid = serializer.is_valid()
        self.assertTrue(valid, serializer.errors)
        self.assertDictEqual(serializer.validated_data, OrderedDict([
            ('system', '/org/OCL/test'),
            ('value', '1'),
            ('type', OrderedDict([
                ('text', 'Accession ID'),
                ('coding', [OrderedDict([
                    ('system', 'http://hl7.org/fhir/v2/0203'),
                    ('code', 'ACSN'),
                    ('display', 'ACSN')])])]))]))

    def test_include_ocl_identifier(self):
        rep = {}
        IdentifierSerializer.include_ocl_identifier('/orgs/OCL/test/1', 'org', rep)

        self.assertDictEqual(rep, {'identifier': [
            {'system': 'http://localhost:8000',
             'type': {
                 'coding': [{
                     'code': 'ACSN',
                     'display': 'Accession ID',
                     'system': 'http://hl7.org/fhir/v2/0203'}],
                 'text': 'Accession ID'},
             'value': '/orgs/OCL/test/1/'}]})

    def test_validate_identifier(self):
        IdentifierSerializer.validate_identifier([
            {'system': 'http://localhost:8000',
             'type': {
                 'coding': [{
                     'code': 'ACSN',
                     'display': 'Accession ID',
                     'system': 'http://hl7.org/fhir/v2/0203'}],
                 'text': 'Accession ID'},
             'value': '/orgs/OCL/CodeSystem/1/'}])

    def test_validate_identifier_with_wrong_owner(self):
        with self.assertRaisesRegex(ValidationError, "Owner type='org' is invalid. It must be 'users' or 'orgs'"):
            IdentifierSerializer.validate_identifier([
                {'system': 'http://localhost:8000',
                 'type': {
                     'coding': [{
                         'code': 'ACSN',
                         'display': 'Accession ID',
                         'system': 'http://hl7.org/fhir/v2/0203'}],
                     'text': 'Accession ID'},
                 'value': '/org/OCL/CodeSystem/1/'}])

    def test_validate_identifier_with_wrong_type(self):
        with self.assertRaisesRegex(ValidationError, "Resource type='Code' is invalid. "
                                                     "It must be 'CodeSystem' or 'ValueSet' or 'ConceptMap'"):
            IdentifierSerializer.validate_identifier([
                {'system': 'http://localhost:8000',
                 'type': {
                     'coding': [{
                         'code': 'ACSN',
                         'display': 'Accession ID',
                         'system': 'http://hl7.org/fhir/v2/0203'}],
                     'text': 'Accession ID'},
                 'value': '/orgs/OCL/Code/1/'}])


class UtilsTest(OCLTestCase):
    def test_set_and_get_current_user(self):
        set_current_user(lambda self: 'foo')
        self.assertEqual(get_current_user(), 'foo')

    def test_set_and_get_request_url(self):
        set_request_url(lambda self: 'https://foobar.org/foo')
        self.assertEqual(get_request_url(), 'https://foobar.org/foo')

    def test_compact_dict_by_values(self):
        self.assertEqual(compact_dict_by_values({}), {})
        self.assertEqual(compact_dict_by_values({'foo': None}), {})
        self.assertEqual(compact_dict_by_values({'foo': None, 'bar': None}), {})
        self.assertEqual(compact_dict_by_values({'foo': None, 'bar': 1}), {'bar': 1})
        self.assertEqual(compact_dict_by_values({'foo': 2, 'bar': 1}), {'foo': 2, 'bar': 1})
        self.assertEqual(compact_dict_by_values({'foo': 2, 'bar': ''}), {'foo': 2})

    def test_to_snake_case(self):
        self.assertEqual(to_snake_case(""), "")
        self.assertEqual(to_snake_case("foobar"), "foobar")
        self.assertEqual(to_snake_case("foo_bar"), "foo_bar")
        self.assertEqual(to_snake_case("fooBar"), "foo_bar")

    def test_to_camel_case(self):
        self.assertEqual(to_camel_case(""), "")
        self.assertEqual(to_camel_case("foobar"), "foobar")
        self.assertEqual(to_camel_case("foo_bar"), "fooBar")
        self.assertEqual(to_camel_case("fooBar"), "fooBar")

    @patch('core.common.utils.requests.get')
    def test_flower_get(self, http_get_mock):
        http_get_mock.return_value = 'foo-task-response'

        self.assertEqual(flower_get('some-url'), 'foo-task-response')

        http_get_mock.assert_called_once_with(
            'http://flower:5555/some-url',
            auth=HTTPBasicAuth(settings.FLOWER_USER, settings.FLOWER_PASSWORD)
        )

    @patch('core.common.utils.requests.get')
    def test_api_get(self, http_get_mock):
        user = UserProfileFactory()
        http_get_mock.return_value = Mock(json=Mock(return_value='api-response'))

        self.assertEqual(api_get('/some-url', user), 'api-response')

        http_get_mock.assert_called_once_with(
            'http://localhost:8000/some-url',
            headers={'Authorization': f'Token {user.get_token()}'}
        )

    @patch('core.common.utils.settings')
    @patch('core.common.utils.requests.get')
    def test_es_get(self, http_get_mock, settings_mock):
        settings_mock.ES_USER = 'es-user'
        settings_mock.ES_PASSWORD = 'es-password'
        settings_mock.ES_HOSTS = 'es:9200'
        settings_mock.ES_SCHEME = 'http'
        http_get_mock.return_value = 'dummy-response'

        self.assertEqual(es_get('some-url', timeout=1), 'dummy-response')

        http_get_mock.assert_called_with(
            'http://es:9200/some-url',
            auth=HTTPBasicAuth('es-user', 'es-password'),
            timeout=1
        )

        settings_mock.ES_HOSTS = None
        settings_mock.ES_HOST = 'es'
        settings_mock.ES_PORT = '9201'

        self.assertEqual(es_get('some-url', timeout=1), 'dummy-response')

        http_get_mock.assert_called_with(
            'http://es:9201/some-url',
            auth=HTTPBasicAuth('es-user', 'es-password'),
            timeout=1
        )

    def test_drop_version(self):
        self.assertEqual(drop_version(None), None)
        self.assertEqual(drop_version(''), '')
        self.assertEqual(drop_version('/foo/bar'), '/foo/bar')
        self.assertEqual(drop_version('/users/username/'), '/users/username/')
        # user-source-concept
        self.assertEqual(
            drop_version("/users/user/sources/source/concepts/concept/"),
            "/users/user/sources/source/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/users/user/sources/source/concepts/concept/version/"),
            "/users/user/sources/source/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/users/user/sources/source/concepts/concept/1.23/"),
            "/users/user/sources/source/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/users/user/sources/source/source-version/concepts/concept/1.23/"),
            "/users/user/sources/source/source-version/concepts/concept/"
        )
        # org-source-concept
        self.assertEqual(
            drop_version("/orgs/org/sources/source/concepts/concept/"),
            "/orgs/org/sources/source/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/orgs/org/sources/source/concepts/concept/version/"),
            "/orgs/org/sources/source/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/orgs/org/sources/source/concepts/concept/1.24/"),
            "/orgs/org/sources/source/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/orgs/org/sources/source/source-version/concepts/concept/1.24/"),
            "/orgs/org/sources/source/source-version/concepts/concept/"
        )
        # user-collection-concept
        self.assertEqual(
            drop_version("/users/user/collections/coll/concepts/concept/"),
            "/users/user/collections/coll/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/users/user/collections/coll/concepts/concept/version/"),
            "/users/user/collections/coll/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/users/user/collections/coll/concepts/concept/1.23/"),
            "/users/user/collections/coll/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/users/user/collections/coll/coll-version/concepts/concept/1.23/"),
            "/users/user/collections/coll/coll-version/concepts/concept/"
        )
        # org-collection-concept
        self.assertEqual(
            drop_version("/orgs/org/collections/coll/concepts/concept/"),
            "/orgs/org/collections/coll/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/orgs/org/collections/coll/concepts/concept/version/"),
            "/orgs/org/collections/coll/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/orgs/org/collections/coll/concepts/concept/1.24/"),
            "/orgs/org/collections/coll/concepts/concept/"
        )
        self.assertEqual(
            drop_version("/orgs/org/collections/coll/coll-version/concepts/concept/1.24/"),
            "/orgs/org/collections/coll/coll-version/concepts/concept/"
        )
        # user-source
        self.assertEqual(drop_version("/users/user/sources/source/"), "/users/user/sources/source/")
        self.assertEqual(drop_version("/users/user/sources/source/1.2/"), "/users/user/sources/source/")
        # org-source
        self.assertEqual(drop_version("/orgs/org/sources/source/"), "/orgs/org/sources/source/")
        self.assertEqual(drop_version("/orgs/org/sources/source/version/"), "/orgs/org/sources/source/")

    def test_is_versioned_uri(self):
        self.assertFalse(is_versioned_uri("/users/user/sources/source/"))
        self.assertFalse(is_versioned_uri("/orgs/org/sources/source/"))
        self.assertFalse(is_versioned_uri("/orgs/org/collections/coll/concepts/concept/"))

        self.assertTrue(is_versioned_uri("/orgs/org/sources/source/version/"))
        self.assertTrue(is_versioned_uri("/users/user/sources/source/1.2/"))
        self.assertTrue(is_versioned_uri("/orgs/org/sources/source/concepts/concept/1.24/"))
        self.assertTrue(is_versioned_uri("/orgs/org/sources/source/source-version/concepts/concept/1.24/"))
        self.assertTrue(is_versioned_uri("/orgs/org/collections/coll/concepts/concept/1.24/"))
        self.assertTrue(is_versioned_uri("/orgs/org/collections/coll/concepts/concept/version/"))
        self.assertTrue(is_versioned_uri("/orgs/org/collections/coll/coll-version/concepts/concept/1.24/"))
        self.assertTrue(is_versioned_uri("/users/user/collections/coll/coll-version/concepts/concept/1.23/"))
        self.assertTrue(is_versioned_uri("/users/user/collections/coll/concepts/concept/1.23/"))

    def test_separate_version(self):
        self.assertEqual(
            separate_version("/orgs/org/collections/coll/coll-version/concepts/concept/1.24/"),
            ("1.24", "/orgs/org/collections/coll/coll-version/concepts/concept/")
        )
        self.assertEqual(
            separate_version("/orgs/org/collections/coll/concepts/concept/1.24/"),
            ("1.24", "/orgs/org/collections/coll/concepts/concept/")
        )
        self.assertEqual(
            separate_version("/orgs/org/collections/coll/concepts/concept/"),
            (None, "/orgs/org/collections/coll/concepts/concept/")
        )
        self.assertEqual(
            separate_version("/orgs/org/collections/coll/123/"),
            ("123", "/orgs/org/collections/coll/")
        )
        self.assertEqual(
            separate_version("/orgs/org/sources/source/HEAD/"),
            ("HEAD", "/orgs/org/sources/source/")
        )
        self.assertEqual(
            separate_version("/orgs/org/sources/source/"),
            (None, "/orgs/org/sources/source/")
        )

    def test_to_parent_uri(self):
        self.assertEqual(
            to_parent_uri("/orgs/org/collections/coll/coll-version/concepts/concept/1.24/"),
            "/orgs/org/collections/coll/coll-version/"
        )
        self.assertEqual(
            to_parent_uri("/users/user/collections/coll/coll-version/mappings/M1234/1.24/"),
            "/users/user/collections/coll/coll-version/"
        )
        self.assertEqual(
            to_parent_uri("/orgs/org/collections/coll/coll-version/concepts/concept"),
            "/orgs/org/collections/coll/coll-version/"
        )
        self.assertEqual(
            to_parent_uri("/users/user/collections/coll/coll-version/"),
            "/users/user/collections/coll/coll-version/"
        )
        self.assertEqual(
            to_parent_uri("/users/user/collections/coll/coll-version/references/r1/"),
            "/users/user/collections/coll/coll-version/"
        )
        self.assertEqual(
            to_parent_uri("/users/user/collections/coll/references/r1/"),
            "/users/user/collections/coll/"
        )
        self.assertEqual(
            to_parent_uri("/users/user/collections/coll/references/"),
            "/users/user/collections/coll/"
        )
        self.assertEqual(
            to_parent_uri("/users/user/collections/coll/coll-version/expansions/e1/"),
            "/users/user/collections/coll/coll-version/"
        )
        self.assertEqual(
            to_parent_uri("/users/user/collections/coll/expansions/e1/"),
            "/users/user/collections/coll/"
        )
        self.assertEqual(
            to_parent_uri("/users/user/collections/coll/expansions/"),
            "/users/user/collections/coll/"
        )
        self.assertEqual(
            to_parent_uri("/users/user/collections/coll/"),
            "/users/user/collections/coll/"
        )
        self.assertEqual(
            to_parent_uri("/users/user/"),
            "/users/user/"
        )
        self.assertEqual(
            to_parent_uri("https://foobar.com/users/user/"),
            "https://foobar.com/users/user/"
        )
        self.assertEqual(
            to_parent_uri("https://foobar.com/users/user/sources/source/"),
            "https://foobar.com/users/user/sources/source/"
        )
        self.assertEqual(
            to_parent_uri("https://foobar.com/users/user/sources/source/mappings/mapping1/v1/"),
            "https://foobar.com/users/user/sources/source/"
        )
        self.assertEqual(
            to_parent_uri("/concepts/"),
            "/"
        )

    def test_jsonify_safe(self):
        self.assertEqual(jsonify_safe(None), None)
        self.assertEqual(jsonify_safe({}), {})
        self.assertEqual(jsonify_safe({'a': 1}), {'a': 1})
        self.assertEqual(jsonify_safe('foobar'), 'foobar')
        self.assertEqual(jsonify_safe('{"foo": "bar"}'), {'foo': 'bar'})

    def test_get_resource_class_from_resource_name(self):
        self.assertEqual(get_resource_class_from_resource_name(None), None)
        self.assertEqual(get_resource_class_from_resource_name('mappings').__name__, 'Mapping')
        self.assertEqual(get_resource_class_from_resource_name('concepts').__name__, 'Concept')
        self.assertEqual(get_resource_class_from_resource_name('sources').__name__, 'Source')
        self.assertEqual(get_resource_class_from_resource_name('source').__name__, 'Source')
        self.assertEqual(get_resource_class_from_resource_name('collections').__name__, 'Collection')
        self.assertEqual(get_resource_class_from_resource_name('collection').__name__, 'Collection')
        self.assertEqual(get_resource_class_from_resource_name('expansion').__name__, 'Expansion')
        self.assertEqual(get_resource_class_from_resource_name('reference').__name__, 'CollectionReference')
        for name in ['orgs', 'organizations', 'org', 'ORG']:
            self.assertEqual(get_resource_class_from_resource_name(name).__name__, 'Organization')
        for name in ['user', 'USer', 'user_profile', 'USERS']:
            self.assertEqual(get_resource_class_from_resource_name(name).__name__, 'UserProfile')

    def test_get_resource_class_from_resource_uri(self):
        self.assertEqual(get_resource_class_from_resource_uri(None), None)
        self.assertEqual(
            get_resource_class_from_resource_uri(
                '/orgs/org/sources/source/mappings/mapping/').__name__, 'Mapping'
        )
        self.assertEqual(
            get_resource_class_from_resource_uri(
                '/orgs/org/sources/source/concepts/concept/').__name__, 'Concept'
        )
        self.assertEqual(
            get_resource_class_from_resource_uri(
                '/orgs/org/collections/collection/concepts/concept/').__name__, 'Concept'
        )
        self.assertEqual(
            get_resource_class_from_resource_uri(
                '/orgs/org/collections/collection/expansions/expansion/concepts/concept/').__name__, 'Concept'
        )
        self.assertEqual(
            get_resource_class_from_resource_uri(
                '/orgs/org/collections/collection/expansions/expansion/').__name__, 'Expansion'
        )
        self.assertEqual(
            get_resource_class_from_resource_uri(
                '/orgs/org/collections/collection/references/ref/').__name__, 'CollectionReference'
        )
        self.assertEqual(
            get_resource_class_from_resource_uri(
                '/orgs/org/collections/collection/1/').__name__, 'Collection'
        )
        self.assertEqual(
            get_resource_class_from_resource_uri(
                '/orgs/org/collections/collection/').__name__, 'Collection'
        )
        self.assertEqual(
            get_resource_class_from_resource_uri(
                '/orgs/org/sources/source/').__name__, 'Source'
        )
        self.assertEqual(
            get_resource_class_from_resource_uri(
                '/orgs/org/blah').__name__, 'Organization'
        )
        self.assertEqual(
            get_resource_class_from_resource_uri(
                '/users/user/orgs/').__name__, 'UserProfile'
        )

    def test_flatten_dict(self):
        self.assertEqual(flatten_dict({'foo': 'bar'}), {'foo': 'bar'})
        self.assertEqual(flatten_dict({'foo': 1}), {'foo': '1'})
        self.assertEqual(flatten_dict({'foo': 1.1}), {'foo': '1.1'})
        self.assertEqual(flatten_dict({'foo': True}), {'foo': 'True'})
        self.assertEqual(
            flatten_dict({'foo': True, 'bar': {'tao': {'te': 'ching'}}}),
            {'foo': 'True', 'bar__tao__te': 'ching'})
        self.assertEqual(
            flatten_dict({'foo': True, 'bar': {'tao': {'te': 'tao-te-ching'}}}),
            {'foo': 'True', 'bar__tao__te': 'tao_te_ching'}
        )
        # self.assertEqual(
        #     flatten_dict(
        #         {
        #             'path': [
        #                 {'text': 'MedicationStatement', 'linkid': '/MedicationStatement'},
        #                 {'text': 'Family Planning Modern Method', 'linkid': '/MedicationStatement/method'}
        #             ],
        #             'header_concept_id': 'ModernMethod',
        #             'questionnaire_choice_value': 'LA27919-2'
        #         }
        #     ),
        #     dict(
        #         path__0__text='MedicationStatement', path__0__linkid='/MedicationStatement',
        #         path__1__text='Family Planning Modern Method', path__1__linkid='/MedicationStatement/method',
        #         header_concept_id='ModernMethod', questionnaire_choice_value='LA27919-2'
        #     )
        # )
        #
        # self.assertEqual(
        #     flatten_dict(
        #         {
        #             'path': [
        #                 {'text': 'MedicationStatement', 'linkid': '/MedicationStatement'},
        #                 {'text': 'Family Planning Modern Method', 'linkid': '/MedicationStatement/method'}
        #             ],
        #             'Applicable Periods': ['FY19', 'FY18'],
        #             'foobar': [1],
        #             'bar': [],
        #             'header_concept_id': 'ModernMethod',
        #             'questionnaire_choice_value': 'LA27919-2'
        #         }
        #     ),
        #     {
        #         'path__0__text': 'MedicationStatement',
        #         'path__0__linkid': '/MedicationStatement',
        #         'path__1__text': 'Family Planning Modern Method',
        #         'path__1__linkid': '/MedicationStatement/method',
        #         'Applicable Periods__0': 'FY19',
        #         'Applicable Periods__1': 'FY18',
        #         'foobar__0': '1',
        #         'header_concept_id': 'ModernMethod',
        #         'questionnaire_choice_value': 'LA27919-2',
        #     }
        # )

    def test_flatten_extras(self):
        self.assertEqual(flatten_extras({'foo': 'bar'}), {'foo': 'bar'})
        self.assertEqual(flatten_extras({'foo': 1}), {'foo': '1'})
        self.assertEqual(
            flatten_extras({'foo': True, 'bar': {'tao': {'te': 'ching'}}}),
            {'foo': 'True', 'bar__tao__te': 'ching'}
        )
        self.assertEqual(
            flatten_extras(
                {
                    'is_clinical': False,
                    'tao': {'te': 'ching'},
                    'reference': {
                        'display': 'Reference',
                        'type': 'Default value type, can be replaced by property type in the definition',
                        'value': 'The reference value to index here',
                        'anything_else': 'my-precious-description-uuid'
                    }
                }
            ),
            {
                'is_clinical': 'False',
                'tao__te': 'ching',
                'reference': 'The reference value to index here'
            }
        )
        self.assertEqual(
            flatten_extras(
                {
                    'reference': {
                        'display': 'Reference',
                        'value': {'code': 'foo', 'system': 'bar'}
                    }
                }
            ),
            {'reference__code': 'foo', 'reference__system': 'bar'}
        )

    def test_is_csv_file(self):
        self.assertFalse(is_csv_file(name='foo/bar'))
        self.assertTrue(is_csv_file(name='foo/bar.csv'))
        self.assertFalse(is_csv_file(name='foo.zip'))

        file_mock = Mock(spec=File)

        file_mock.name = 'unknown_file'
        self.assertFalse(is_csv_file(file=file_mock))

        file_mock.name = 'unknown_file.json'
        self.assertFalse(is_csv_file(file=file_mock))

        file_mock.name = 'unknown_file.csv'
        self.assertTrue(is_csv_file(file=file_mock))

    def test_is_zip_file(self):
        self.assertFalse(is_zip_file(name='foo/bar'))
        self.assertFalse(is_zip_file(name='foo/bar.csv'))
        self.assertTrue(is_zip_file(name='foo.zip'))
        self.assertTrue(is_zip_file(name='foo.csv.zip'))
        self.assertTrue(is_zip_file(name='foo.json.zip'))

        file_mock = Mock(spec=File)

        file_mock.name = 'unknown_file'
        self.assertFalse(is_zip_file(file=file_mock))

        file_mock.name = 'unknown_file.json'
        self.assertFalse(is_zip_file(file=file_mock))

        file_mock.name = 'unknown_file.csv'
        self.assertFalse(is_zip_file(file=file_mock))

        file_mock.name = 'unknown_file.csv.zip'
        self.assertTrue(is_zip_file(file=file_mock))

        file_mock.name = 'unknown_file.json.zip'
        self.assertTrue(is_zip_file(file=file_mock))

    def test_is_url_encoded_string(self):
        self.assertTrue(is_url_encoded_string('foo'))
        self.assertFalse(is_url_encoded_string('foo/bar'))
        self.assertTrue(is_url_encoded_string('foo%2Fbar'))
        self.assertTrue(is_url_encoded_string('foo%2Fbar', False))

    def test_to_parent_uri_from_kwargs(self):
        self.assertEqual(
            to_parent_uri_from_kwargs({'org': 'OCL', 'collection': 'c1'}),
            '/orgs/OCL/collections/c1/'
        )
        self.assertEqual(
            to_parent_uri_from_kwargs({'org': 'OCL', 'collection': 'c1', 'version': 'v1'}),
            '/orgs/OCL/collections/c1/v1/'
        )
        self.assertEqual(
            to_parent_uri_from_kwargs({'user': 'admin', 'collection': 'c1', 'version': 'v1'}),
            '/users/admin/collections/c1/v1/'
        )
        self.assertEqual(
            to_parent_uri_from_kwargs({'user': 'admin', 'source': 's1', 'version': 'v1'}),
            '/users/admin/sources/s1/v1/'
        )
        self.assertEqual(
            to_parent_uri_from_kwargs({'org': 'OCL', 'source': 's1'}),
            '/orgs/OCL/sources/s1/'
        )
        self.assertEqual(
            to_parent_uri_from_kwargs({'org': 'OCL', 'source': 's1', 'concept': 'c1', 'concept_version': 'v1'}),
            '/orgs/OCL/sources/s1/'
        )
        self.assertEqual(
            to_parent_uri_from_kwargs(
                {'org': 'OCL', 'source': 's1', 'version': 'v1', 'concept': 'c1', 'concept_version': 'v1'}),
            '/orgs/OCL/sources/s1/v1/'
        )
        self.assertEqual(to_parent_uri_from_kwargs({'org': 'OCL'}), '/orgs/OCL/')
        self.assertEqual(to_parent_uri_from_kwargs({'user': 'admin'}), '/users/admin/')
        self.assertIsNone(to_parent_uri_from_kwargs({}))
        self.assertIsNone(to_parent_uri_from_kwargs(None))

    def test_nested_dict_values(self):
        self.assertEqual(list(nested_dict_values({})), [])
        self.assertEqual(list(nested_dict_values({'a': 1})), [1])
        self.assertEqual(list(nested_dict_values({'a': 1, 'b': 'foobar'})), [1, 'foobar'])
        self.assertEqual(
            list(nested_dict_values({'a': 1, 'b': 'foobar', 'c': {'a': 1, 'b': 'foobar'}})),
            [1, 'foobar', 1, 'foobar']
        )
        self.assertEqual(
            list(
                nested_dict_values(
                    {'a': 1, 'b': 'foobar', 'c': {'a': 1, 'b': 'foobar', 'c': {'d': [{'a': 1}, {'b': 'foobar'}]}}}
                )
            ),
            [1, 'foobar', 1, 'foobar', [{'a': 1}, {'b': 'foobar'}]]
        )

    def test_chunks(self):
        self.assertEqual(list(chunks([], 1000)), [])
        self.assertEqual(list(chunks([1, 2, 3, 4], 3)), [[1, 2, 3], [4]])
        self.assertEqual(list(chunks([1, 2, 3, 4], 2)), [[1, 2], [3, 4]])
        self.assertEqual(list(chunks([1, 2, 3, 4], 7)), [[1, 2, 3, 4]])
        self.assertEqual(list(chunks([1, 2, 3, 4], 4)), [[1, 2, 3, 4]])

    def test_split_list_by_condition(self):
        even, odd = split_list_by_condition([2, 3, 4, 5, 6, 7], lambda x: x % 2 == 0)
        self.assertEqual(even, [2, 4, 6])
        self.assertEqual(odd, [3, 5, 7])

        even, odd = split_list_by_condition([3, 5, 7], lambda num: num % 2 == 0)
        self.assertEqual(even, [])
        self.assertEqual(odd, [3, 5, 7])

        ref1 = CollectionReference(id=1, include=True)
        ref2 = CollectionReference(id=2, include=False)
        ref3 = CollectionReference(id=3, include=False)
        ref4 = CollectionReference(id=3, include=True)

        include, exclude = split_list_by_condition([ref1, ref2, ref3, ref4], lambda ref: ref.include)

        self.assertEqual(include, [ref1, ref4])
        self.assertEqual(exclude, [ref2, ref3])

    def test_get_date_range_label(self):
        self.assertEqual(
            get_date_range_label('2019-01-01', '2019-01-31'),
            '01 - 31 January 2019'
        )
        self.assertEqual(
            get_date_range_label('2019-01-01 10:00:00', '2019-01-01 11:00:00'),
            '01 - 01 January 2019'
        )
        self.assertEqual(
            get_date_range_label('2019-01-02 10:10:00', '2019-01-01'),
            '02 - 01 January 2019'
        )
        self.assertEqual(
            get_date_range_label('2019-02-01 10:10:00', '2019-01-01'),
            '01 February - 01 January 2019'
        )
        self.assertEqual(
            get_date_range_label('2019-01-01', '2020-01-01'),
            '01 January 2019 - 01 January 2020'
        )

    def test_get_prev_month(self):
        self.assertEqual(get_prev_month(from_string_to_date('2023-01-01')), from_string_to_date('2022-12-31'))
        self.assertEqual(get_prev_month(from_string_to_date('2024-12-01')), from_string_to_date('2024-11-30'))
        self.assertEqual(get_prev_month(from_string_to_date('2024-12-05')), from_string_to_date('2024-11-30'))

    def test_get_end_of_month(self):
        self.assertEqual(get_end_of_month(from_string_to_date('2023-01-01')), from_string_to_date('2023-01-31'))
        self.assertEqual(get_end_of_month(from_string_to_date('2024-12-01')), from_string_to_date('2024-12-31'))
        self.assertEqual(get_end_of_month(from_string_to_date('2024-12-05')), from_string_to_date('2024-12-31'))
        self.assertEqual(get_end_of_month(from_string_to_date('2024-11-05')), from_string_to_date('2024-11-30'))
        self.assertEqual(get_end_of_month(from_string_to_date('2024-02-05')), from_string_to_date('2024-02-29'))
        self.assertEqual(
            get_end_of_month(from_string_to_date('2024-11-30 11:00')), from_string_to_date('2024-11-30 11:00'))
        self.assertEqual(
            get_end_of_month(from_string_to_date('2024-11-15 11:00')), from_string_to_date('2024-11-30 11:00'))

    def test_get_start_of_month(self):
        self.assertEqual(get_start_of_month(from_string_to_date('2023-01-01')), from_string_to_date('2023-01-01'))
        self.assertEqual(get_start_of_month(from_string_to_date('2024-12-31')), from_string_to_date('2024-12-01'))
        self.assertEqual(get_start_of_month(from_string_to_date('2024-02-05')), from_string_to_date('2024-02-01'))
        self.assertEqual(get_start_of_month(from_string_to_date('2023-02-28')), from_string_to_date('2023-02-01'))

    def test_es_id_in(self):
        search = Mock(query=Mock(return_value='search'))

        self.assertEqual(es_id_in(search, []), search)

        self.assertEqual(es_id_in(search, [1, 2, 3]), 'search')
        search.query.assert_called_once_with("terms", _id=[1, 2, 3])

    @patch('core.common.utils.settings')
    def test_web_url(self, settings_mock):
        settings_mock.WEB_URL = 'https://ocl.org'
        self.assertEqual(web_url(), 'https://ocl.org')

        settings_mock.WEB_URL = None

        for env in [None, 'development', 'ci']:
            settings_mock.ENV = env
            self.assertEqual(web_url(), 'http://localhost:4000')

        settings_mock.ENV = 'production'
        self.assertEqual(web_url(), 'https://app.openconceptlab.org')

        settings_mock.ENV = 'staging'
        self.assertEqual(web_url(), 'https://app.staging.openconceptlab.org')

        settings_mock.ENV = 'foo'
        self.assertEqual(web_url(), 'https://app.foo.openconceptlab.org')

    def test_from_string_to_date(self):
        self.assertEqual(
            from_string_to_date('2023-02-28'), datetime.datetime(2023, 2, 28))
        self.assertEqual(
            from_string_to_date('2023-02-28 10:00:00'), datetime.datetime(2023, 2, 28, 10))
        self.assertEqual(
            from_string_to_date('2023-02-29'), None)

    @patch('core.tasks.models.Task.new')
    def test_get_queue_task_names(self, task_new_mock):
        task_new_mock.return_value = 'task'

        self.assertEqual(get_queue_task_names(None, 'ocladmin'), 'task')
        self.assertEqual(
            task_new_mock.mock_calls[0],
            call(queue='bulk_import_root', username='ocladmin', import_queue=None)
        )

        self.assertEqual(get_queue_task_names('foobar', 'ocladmin'), 'task')
        self.assertEqual(
            task_new_mock.mock_calls[1],
            call(queue='bulk_import_root', username='ocladmin', import_queue='foobar')
        )

        self.assertEqual(get_queue_task_names('concurrent', 'ocladmin'), 'task')
        self.assertEqual(
            task_new_mock.mock_calls[2],
            call(queue='concurrent', username='ocladmin', import_queue='concurrent')
        )

        self.assertEqual(
            get_queue_task_names('concurrent', 'ocladmin', foo='bar'), 'task')
        self.assertEqual(
            task_new_mock.mock_calls[3],
            call(queue='concurrent', username='ocladmin', import_queue='concurrent', foo='bar')
        )

        self.assertEqual(get_queue_task_names(None, 'datim-admin'), 'task')
        self.assertEqual(
            task_new_mock.mock_calls[4],
            call(queue=ANY, username='datim-admin', import_queue=None)
        )

        self.assertEqual(get_queue_task_names('merfy24', 'datim-admin'), 'task') # first
        self.assertEqual(
            task_new_mock.mock_calls[5],
            call(queue='bulk_import_3', username='datim-admin', import_queue='merfy24')
        )

        self.assertEqual(get_queue_task_names('merfy24', 'datim-admin'), 'task') # second
        self.assertEqual(
            task_new_mock.mock_calls[6],
            call(queue='bulk_import_3', username='datim-admin', import_queue='merfy24')
        )

        self.assertEqual(get_queue_task_names('merfy24', 'datim-admin'), 'task') # third
        self.assertEqual(
            task_new_mock.mock_calls[7],
            call(queue='bulk_import_3', username='datim-admin', import_queue='merfy24')
        )

        self.assertEqual(get_queue_task_names('merfy25', 'datim-admin'), 'task')
        self.assertEqual(
            task_new_mock.mock_calls[8],
            call(queue='bulk_import_1', username='datim-admin', import_queue='merfy25')
        )

        self.assertEqual(get_queue_task_names('merfy25', 'datim-admin'), 'task')
        self.assertEqual(
            task_new_mock.mock_calls[9],
            call(queue='bulk_import_1', username='datim-admin', import_queue='merfy25')
        )

    def test_to_parent_kwargs_from_uri(self):
        self.assertEqual(
            to_parent_kwargs_from_uri('/orgs/o/collections/c/HEAD/expansions/e1/'),
            {'owner_type': 'Organization', 'owner': 'o', 'repo_type': 'Collections',
             'repo': 'c', 'version': 'HEAD', 'url': '/orgs/o/collections/c/HEAD/'}
        )
        self.assertEqual(
            to_parent_kwargs_from_uri('/orgs/OCL/collections/MyCol/v1.0/expansions/e1/'),
            {'owner_type': 'Organization', 'owner': 'OCL', 'repo_type': 'Collections',
             'repo': 'MyCol', 'version': 'v1.0', 'url': '/orgs/OCL/collections/MyCol/v1.0/'}
        )
        self.assertEqual(
            to_parent_kwargs_from_uri('/users/admin/collections/c1/v2/'),
            {'owner_type': 'User', 'owner': 'admin', 'repo_type': 'Collections',
             'repo': 'c1', 'version': 'v2', 'url': '/users/admin/collections/c1/v2/'}
        )
        self.assertEqual(
            to_parent_kwargs_from_uri('/users/admin/collections/c1/'),
            {'owner_type': 'User', 'owner': 'admin', 'repo_type': 'Collections',
             'repo': 'c1', 'version': HEAD, 'url': '/users/admin/collections/c1/'}
        )
        self.assertEqual(
            to_parent_kwargs_from_uri('/orgs/CIEL/sources/CIEL/v2026-02-26/'),
            {'owner_type': 'Organization', 'owner': 'CIEL', 'repo_type': 'Sources',
             'repo': 'CIEL', 'version': 'v2026-02-26', 'url': '/orgs/CIEL/sources/CIEL/v2026-02-26/'}
        )
        self.assertEqual(
            to_parent_kwargs_from_uri('/orgs/CIEL/sources/CIEL/v2026-02-26/concepts/123/456/'),
            {'owner_type': 'Organization', 'owner': 'CIEL', 'repo_type': 'Sources',
             'repo': 'CIEL', 'version': 'v2026-02-26', 'url': '/orgs/CIEL/sources/CIEL/v2026-02-26/'}
        )
        self.assertEqual(
            to_parent_kwargs_from_uri('/orgs/CIEL/sources/CIEL/v2026-02-26/mappings/123/456/'),
            {'owner_type': 'Organization', 'owner': 'CIEL', 'repo_type': 'Sources',
             'repo': 'CIEL', 'version': 'v2026-02-26', 'url': '/orgs/CIEL/sources/CIEL/v2026-02-26/'}
        )
        self.assertEqual(
            to_parent_kwargs_from_uri('/orgs/CIEL/sources/CIEL/concepts/123/456/'),
            {'owner_type': 'Organization', 'owner': 'CIEL', 'repo_type': 'Sources',
             'repo': 'CIEL', 'version': 'HEAD', 'url': '/orgs/CIEL/sources/CIEL/'}
        )
        self.assertEqual(
            to_parent_kwargs_from_uri('/orgs/CIEL/sources/CIEL/mappings/123/456/'),
            {'owner_type': 'Organization', 'owner': 'CIEL', 'repo_type': 'Sources',
             'repo': 'CIEL', 'version': 'HEAD', 'url': '/orgs/CIEL/sources/CIEL/'}
        )
        self.assertEqual(to_parent_kwargs_from_uri(''), {})
        self.assertEqual(to_parent_kwargs_from_uri(None), {})

    def test_to_parent_kwargs_from_uri_too_short(self):
        self.assertEqual(to_parent_kwargs_from_uri('/orgs/CIEL/'), {})

    def test_reverse_resource_non_source_collection_uses_head_property(self):
        concept = ConceptFactory()
        concept_v1 = ConceptFactory(
            parent=concept.parent, mnemonic=concept.mnemonic, version='v1', versioned_object=concept
        )
        url = reverse_resource(concept_v1, concept_v1.view_name)
        self.assertIsNotNone(url)

    def test_reverse_resource_version_non_source_collection_uses_head_property(self):
        concept = ConceptFactory()
        concept_v1 = ConceptFactory(
            parent=concept.parent, mnemonic=concept.mnemonic, version='v1', versioned_object=concept
        )
        url = reverse_resource_version(concept_v1, concept_v1.view_name)
        self.assertIsNotNone(url)

    @patch('core.common.utils.get_export_service')
    def test_write_export_file_head_version(self, get_export_service_mock):
        export_service_mock = Mock()
        export_service_mock.upload_file.return_value = 200
        export_service_mock.url_for.return_value = 'http://export.url/export.zip'
        export_service_mock.delete_objects = Mock()
        get_export_service_mock.return_value = export_service_mock

        source = OrganizationSourceFactory()
        ConceptFactory(parent=source)

        with override_settings(TEST_MODE=False):
            write_export_file(
                source, 'source', 'core.sources.serializers.SourceVersionExportSerializer', Mock(), time.time()
            )

        export_service_mock.upload_file.assert_called_once()

    @patch('core.common.utils.settings')
    @patch('core.common.utils.requests.get')
    def test_es_get_connect_timeout_on_all_hosts_returns_none(self, http_get_mock, settings_mock):
        settings_mock.ES_USER = None
        settings_mock.ES_PASSWORD = None
        settings_mock.ES_HOSTS = 'es1:9200,es2:9200'
        settings_mock.ES_SCHEME = 'http'
        http_get_mock.side_effect = ConnectTimeout()

        self.assertIsNone(es_get('some-url', timeout=1))
        self.assertEqual(http_get_mock.call_count, 2)

    @patch('core.common.tasks.bulk_import.apply_async')
    def test_queue_bulk_import_not_inline(self, apply_async_mock):
        task = queue_bulk_import('{}', 'default', 'ocladmin', False)
        self.assertIsNotNone(task)
        apply_async_mock.assert_called_once()

    @patch('core.common.tasks.bulk_import.apply_async')
    def test_queue_bulk_import_already_queued_deletes_task_and_raises(self, apply_async_mock):
        apply_async_mock.side_effect = AlreadyQueued(10)
        with self.assertRaises(AlreadyQueued):
            queue_bulk_import('{}', 'default', 'ocladmin', False)

    def test_get_bulk_import_celery_once_lock_key_no_args(self):
        async_result = Mock(args=None)
        self.assertIsNone(get_bulk_import_celery_once_lock_key(async_result))

    def test_generic_sort(self):
        self.assertEqual(generic_sort([3, 1, 2]), [1, 2, 3])
        self.assertEqual(generic_sort(['b', 'a', {'x': 1}]), ['a', 'b', {'x': 1}])

    @patch('core.common.utils.settings')
    def test_get_embeddings_ci_env_returns_none(self, settings_mock):
        settings_mock.LM_DISABLED = True
        self.assertIsNone(get_embeddings('some text'))
        settings_mock.LM.encode.assert_not_called()

    @patch('sentence_transformers.SentenceTransformer')
    @patch('core.common.utils.settings')
    def test_get_embeddings_loads_model_when_not_ci(self, settings_mock, sentence_transformer_mock):
        settings_mock.LM_DISABLED = False
        settings_mock.LM = None
        settings_mock.LM_MODEL_NAME = 'some-model'
        model_instance_mock = Mock()
        model_instance_mock.encode.return_value = [0.1, 0.2]
        sentence_transformer_mock.return_value = model_instance_mock

        result = get_embeddings('some text')

        sentence_transformer_mock.assert_called_once_with('some-model')
        model_instance_mock.encode.assert_called_once_with('some text')
        self.assertEqual(result, [0.1, 0.2])

    @patch('core.common.utils.settings')
    def test_get_lm_model_is_the_loaded_model(self, settings_mock):
        self.assertIs(get_lm_model(), settings_mock.LM)

    @patch('sentence_transformers.SentenceTransformer')
    @patch('core.common.utils.settings')
    def test_get_lm_model_loads_it_when_not_loaded(self, settings_mock, sentence_transformer_mock):
        settings_mock.LM = None
        settings_mock.LM_MODEL_NAME = 'some-model'

        self.assertIs(get_lm_model(), sentence_transformer_mock.return_value)

        sentence_transformer_mock.assert_called_once_with('some-model')

    @patch('core.common.utils.settings')
    def test_encode_texts_returns_none_for_each_text_when_the_model_is_disabled(self, settings_mock):
        settings_mock.LM_DISABLED = True
        self.assertEqual(encode_texts(['a', 'b']), [None, None])
        self.assertEqual(encode_texts([]), [])
        settings_mock.LM.encode.assert_not_called()

    @patch('core.common.utils.settings')
    def test_encode_texts_encodes_in_one_batched_call(self, settings_mock):
        settings_mock.LM_DISABLED = False
        settings_mock.LM_ENCODE_BATCH_SIZE = 32
        settings_mock.LM.encode.return_value = [[0.1], [0.2]]
        texts = ['a', 2]

        self.assertEqual(encode_texts(texts), [[0.1], [0.2]])

        settings_mock.LM.encode.assert_called_once_with(['a', '2'], batch_size=32)
        self.assertEqual(texts, ['a', 2])
        self.assertEqual(encode_texts([]), [])
        settings_mock.LM.encode.assert_called_once()  # not for no texts

    @patch('sentence_transformers.SentenceTransformer')
    @patch('core.common.utils.settings')
    def test_encode_texts_loads_model_when_not_loaded(self, settings_mock, sentence_transformer_mock):
        settings_mock.LM_DISABLED = False
        settings_mock.LM = None
        settings_mock.LM_MODEL_NAME = 'some-model'
        settings_mock.LM_ENCODE_BATCH_SIZE = 64
        sentence_transformer_mock.return_value.encode.return_value = [[0.1]]

        self.assertEqual(encode_texts(['a']), [[0.1]])

        sentence_transformer_mock.assert_called_once_with('some-model')
        sentence_transformer_mock.return_value.encode.assert_called_once_with(['a'], batch_size=64)


class BaseModelTest(OCLTestCase):
    def test_model_name(self):
        self.assertEqual(Concept().model_name, 'Concept')
        self.assertEqual(Source().model_name, 'Source')

    def test_app_name(self):
        self.assertEqual(Concept().app_name, 'concepts')
        self.assertEqual(Source().app_name, 'sources')

    @override_settings(TEST_MODE=False)
    @patch('core.common.models.parallel_bulk', return_value=[])
    def test_batch_index_full_streams_batches_without_parallel_bulk(self, parallel_bulk_mock):
        ordered_queryset = MagicMock()

        def get_batch(batch_slice):
            if batch_slice == slice(0, 500, None):
                return [1, 2]
            return []

        ordered_queryset.__getitem__.side_effect = get_batch

        queryset = Mock()
        queryset.order_by.return_value = ordered_queryset
        queryset.prefetch_related.return_value = queryset
        queryset.select_related.return_value = queryset

        doc_instance = Mock()
        doc_instance.django.auto_refresh = False
        doc_instance.django.queryset_pagination = None
        document = Mock(return_value=doc_instance)

        BaseModel.batch_index_full(False, queryset, document, None, None)

        queryset.order_by.assert_called_with('-id')
        self.assertEqual(ordered_queryset.__getitem__.call_args_list, [
            call(slice(0, 500, None)),
            call(slice(500, 1000, None)),
        ])
        doc_instance._get_actions.assert_called_once_with([1, 2], 'index')  # pylint: disable=protected-access
        parallel_bulk_mock.assert_called_once_with(
            doc_instance._get_connection.return_value,  # pylint: disable=protected-access
            doc_instance._get_actions.return_value,  # pylint: disable=protected-access
            raise_on_error=False
        )

    @staticmethod
    def get_batched_full_index_mocks(batches):
        """queryset/document mocks for batch_index_full whose 500-slices are `batches`."""
        def get_batch(batch_slice):
            index = batch_slice.start // 500
            return batches[index] if index < len(batches) else []

        ordered_queryset = MagicMock()
        ordered_queryset.__getitem__.side_effect = get_batch

        queryset = Mock()
        queryset.order_by.return_value = ordered_queryset
        queryset.prefetch_related.return_value = queryset
        queryset.select_related.return_value = queryset

        doc_instance = Mock()
        doc_instance.django.auto_refresh = False
        doc_instance.django.queryset_pagination = None
        document = Mock(return_value=doc_instance)
        document.__name__ = 'ConceptDocument'
        return queryset, doc_instance, document

    @staticmethod
    def get_bulk_failures(ids, status=429, error_type='cluster_block_exception', reason=None):
        """What the ES bulk helpers yield (raise_on_error=False) for items of `ids` that failed."""
        reason = reason or ('index [concepts] blocked by: [TOO_MANY_REQUESTS/12/disk usage exceeded flood-stage '
                            'watermark, index has read-only-allow-delete block];')
        return [
            (False, {'update': {'_index': 'concepts', '_id': str(_id), 'status': status,
                                'error': {'type': error_type, 'reason': reason}}})
            for _id in ids
        ]

    @staticmethod
    def get_actions_batches(doc_instance):
        return [args[0][0] for args in doc_instance._get_actions.call_args_list]  # pylint: disable=protected-access

    @override_settings(TEST_MODE=False)
    @patch('core.common.models.parallel_bulk', return_value=[])
    def test_batch_index_full_refreshes_once_after_the_last_batch(self, parallel_bulk_mock):
        queryset, doc_instance, document = self.get_batched_full_index_mocks([[1, 2], [3]])
        doc_instance.django.auto_refresh = True

        BaseModel.batch_index_full(False, queryset, document, None, None)

        self.assertEqual(parallel_bulk_mock.call_count, 2)
        for bulk_call in parallel_bulk_mock.call_args_list:
            self.assertNotIn('refresh', bulk_call[1])
        doc_instance._index.refresh.assert_called_once_with()  # pylint: disable=protected-access

    @override_settings(TEST_MODE=False)
    @patch('core.common.models.parallel_bulk', return_value=[])
    def test_batch_index_full_can_skip_the_refresh(self, parallel_bulk_mock):
        queryset, doc_instance, document = self.get_batched_full_index_mocks([[1, 2]])
        doc_instance.django.auto_refresh = True

        BaseModel.batch_index_full(False, queryset, document, None, None, refresh=False)

        self.assertNotIn('refresh', parallel_bulk_mock.call_args[1])
        doc_instance._index.refresh.assert_not_called()  # pylint: disable=protected-access

    @override_settings(TEST_MODE=False)
    @patch('core.common.models.parallel_bulk', return_value=[])
    def test_batch_index_full_does_not_refresh_when_nothing_was_sent(self, _):
        queryset, doc_instance, document = self.get_batched_full_index_mocks([])
        doc_instance.django.auto_refresh = True

        BaseModel.batch_index_full(False, queryset, document, None, None)

        doc_instance._index.refresh.assert_not_called()  # pylint: disable=protected-access

    @override_settings(TEST_MODE=False)
    def test_batch_index_full_refreshes_even_when_a_batch_failed(self):
        queryset, doc_instance, document = self.get_batched_full_index_mocks([[1, 2], [3]])
        doc_instance.django.auto_refresh = True

        with patch('core.common.models.parallel_bulk', side_effect=[Exception('mapping conflict'), []]), \
                patch('core.common.models.ERRBIT_LOGGER'):
            with self.assertRaises(BatchIndexingError):
                BaseModel.batch_index_full(False, queryset, document, None, None)

        doc_instance._index.refresh.assert_called_once_with()  # pylint: disable=protected-access

    @override_settings(TEST_MODE=False)
    @patch('core.common.models.parallel_bulk', return_value=[])
    def test_batch_index_full_refresh_error_does_not_fail_the_run(self, _):
        queryset, doc_instance, document = self.get_batched_full_index_mocks([[1, 2]])
        doc_instance.django.auto_refresh = True
        doc_instance._index.refresh.side_effect = Exception('timeout')  # pylint: disable=protected-access

        summary = BaseModel.batch_index_full(False, queryset, document, None, None)

        self.assertEqual(summary['failed_batches'], 0)
        self.assertEqual(summary['docs'], 2)

    @override_settings(TEST_MODE=False)
    @patch('core.common.models.parallel_bulk', return_value=[])
    def test_batch_index_partial_by_ids_refreshes_once_after_the_last_batch(self, parallel_bulk_mock):
        id_queryset = MagicMock()
        id_queryset.__getitem__.side_effect = lambda batch_slice: [1, 2] if batch_slice.start == 0 else []
        queryset = Mock()
        queryset.order_by.return_value.values_list.return_value = id_queryset
        doc_instance = Mock()
        doc_instance.django.auto_refresh = True
        doc_instance.django.queryset_pagination = None
        document = Mock(return_value=doc_instance)

        BaseModel.batch_index_partial_by_ids(queryset, document, lambda ids: iter([]))

        self.assertNotIn('refresh', parallel_bulk_mock.call_args[1])
        doc_instance._index.refresh.assert_called_once_with()  # pylint: disable=protected-access

    @override_settings(TEST_MODE=False)
    def test_batch_index_full_continues_remaining_batches_after_error(self):
        # A failing batch must not abort the remaining batches (ocl_issues#2694), and must still fail the run once
        # they've all been attempted (ocl_online#241).
        queryset, doc_instance, document = self.get_batched_full_index_mocks([[1, 2], [3]])

        with patch('core.common.models.parallel_bulk', side_effect=[Exception('mapping conflict'), []]), \
                patch('core.common.models.ERRBIT_LOGGER') as errbit_mock:
            with self.assertRaises(BatchIndexingError) as context:
                BaseModel.batch_index_full(False, queryset, document, None, None)

        self.assertEqual(doc_instance._get_actions.call_args_list, [  # pylint: disable=protected-access
            call([1, 2], 'index'),
            call([3], 'index'),
        ])
        errbit_mock.log.assert_called_once()
        self.assertEqual(
            str(context.exception),
            'ConceptDocument indexing failed: 1 of 2 batch(es) and 2 of 3 document(s) failed to index'
        )
        self.assertEqual(
            context.exception.summary, {'batches': 2, 'failed_batches': 1, 'docs': 3, 'failed_docs': 2})
        self.assertFalse(context.exception.rejected)

    @override_settings(TEST_MODE=False)
    def test_batch_index_full_logs_first_item_errors_without_documents(self):
        queryset, _, document = self.get_batched_full_index_mocks([list(range(1, 8))])
        errors = [
            {'index': {'_index': 'concepts', '_id': str(_id), 'status': 400,
                       'error': {'type': 'mapper_parsing_exception', 'reason': f'failed to parse field [x] of {_id}'},
                       'data': {'id': _id, '_embeddings': {'vector': [0.123456789] * 3}}}}
            for _id in range(1, 8)
        ]

        with patch('core.common.models.parallel_bulk', return_value=[(False, e) for e in errors]) as bulk_mock, \
                patch('core.common.models.ERRBIT_LOGGER') as errbit_mock, \
                patch('core.common.models.logger') as logger_mock, \
                patch('core.common.models.time.sleep') as sleep_mock:
            with self.assertRaises(BatchIndexingError) as context:
                BaseModel.batch_index_full(False, queryset, document, None, None)

        sleep_mock.assert_not_called()  # a mapping error isn't worth a retry
        self.assertEqual(bulk_mock.call_count, 1)
        message = logger_mock.error.call_args[0][0]
        self.assertTrue(message.startswith(
            'ConceptDocument batch (start=0, size=7) failed to index 7 document(s): '
            '7 document(s) failed to index, first 5: '
        ))
        for _id in range(1, 6):
            self.assertIn(
                str({'id': str(_id), 'status': 400, 'type': 'mapper_parsing_exception',
                     'reason': f'failed to parse field [x] of {_id}'}),
                message
            )
        self.assertNotIn("'id': '6'", message)
        self.assertNotIn('0.123456789', message)
        self.assertNotIn('_embeddings', message)
        errbit_exception = errbit_mock.log.call_args[0][0]
        self.assertIsInstance(errbit_exception, BatchIndexingError)
        self.assertEqual(str(errbit_exception), message)
        self.assertEqual(context.exception.summary, {'batches': 1, 'failed_batches': 1, 'docs': 7, 'failed_docs': 7})

    @override_settings(TEST_MODE=False)
    def test_batch_index_full_retries_bulk_request_rejected_by_circuit_breaker(self):
        from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig
        from elasticsearch import ApiError
        queryset, doc_instance, document = self.get_batched_full_index_mocks([[1, 2]])
        meta = ApiResponseMeta(
            status=429, http_version='1.1', headers=HttpHeaders(), duration=0.0, node=NodeConfig('http', 'es', 9200))
        rejection = ApiError(
            'circuit_breaking_exception', meta=meta, body={'error': {'type': 'circuit_breaking_exception'}})

        with patch('core.common.models.parallel_bulk', side_effect=[rejection, []]) as bulk_mock, \
                patch('core.common.models.ERRBIT_LOGGER') as errbit_mock, \
                patch('core.common.models.time.sleep') as sleep_mock:
            summary = BaseModel.batch_index_full(False, queryset, document, None, None)

        self.assertEqual(bulk_mock.call_count, 2)
        self.assertEqual(self.get_actions_batches(doc_instance), [[1, 2]] * 2)
        sleep_mock.assert_called_once_with(10)
        errbit_mock.log.assert_not_called()
        self.assertEqual(summary, {'batches': 1, 'failed_batches': 0, 'docs': 2, 'failed_docs': 0})

    @override_settings(TEST_MODE=False)
    def test_batch_index_full_gives_one_attempt_per_batch_while_es_keeps_rejecting(self):
        # Once a batch has outlasted its retries, later batches don't each wait the whole backoff again: a
        # read-only index should fail a LOINC-size run in minutes. The first batch ES takes resets that.
        queryset, doc_instance, document = self.get_batched_full_index_mocks([[1, 2], [3], [4], [5]])
        results = [self.get_bulk_failures([1, 2])] * 5 + [
            self.get_bulk_failures([3]), [], self.get_bulk_failures([5]), []
        ]

        with patch('core.common.models.parallel_bulk', side_effect=results), \
                patch('core.common.models.ERRBIT_LOGGER'), patch('core.common.models.time.sleep') as sleep_mock:
            with self.assertRaises(BatchIndexingError) as context:
                BaseModel.batch_index_full(False, queryset, document, None, None)

        self.assertEqual(self.get_actions_batches(doc_instance), [[1, 2]] * 5 + [[3], [4], [5], [5]])
        self.assertEqual([args[0][0] for args in sleep_mock.call_args_list], [10, 20, 40, 80, 10])
        self.assertTrue(context.exception.rejected)
        self.assertEqual(context.exception.summary, {'batches': 4, 'failed_batches': 2, 'docs': 5, 'failed_docs': 3})
        self.assertEqual(
            str(context.exception),
            'ConceptDocument indexing failed: 2 of 4 batch(es) and 3 of 5 document(s) failed to index, '
            '2 batch(es) still rejected by Elasticsearch after retries'
        )

    @override_settings(TEST_MODE=False)
    def test_batch_index_full_with_no_failures_is_unchanged(self):
        ordered_queryset = MagicMock()
        ordered_queryset.__getitem__.side_effect = lambda s: [1, 2] if s == slice(0, 500, None) else []

        queryset = Mock()
        queryset.order_by.return_value = ordered_queryset
        queryset.prefetch_related.return_value = queryset
        queryset.select_related.return_value = queryset

        doc_instance = Mock()
        doc_instance.django.auto_refresh = False
        doc_instance.django.queryset_pagination = None
        document = Mock(return_value=doc_instance)
        document.__name__ = 'ConceptDocument'

        with patch('core.common.models.parallel_bulk', return_value=[]), \
                patch('core.common.models.ERRBIT_LOGGER') as errbit_mock:
            summary = BaseModel.batch_index_full(False, queryset, document, None, None)

        self.assertEqual(self.get_actions_batches(doc_instance), [[1, 2]])
        errbit_mock.log.assert_not_called()
        self.assertEqual(summary, {'batches': 1, 'failed_batches': 0, 'docs': 2, 'failed_docs': 0})

    @override_settings(TEST_MODE=False)
    def test_batch_index_full_single_batch_is_one_retried_batch(self):
        queryset = Mock()
        queryset.all.return_value = [1, 2, 3]
        doc_instance = Mock()
        doc_instance.django.auto_refresh = True
        document = Mock(return_value=doc_instance)
        document.__name__ = 'ConceptDocument'

        with patch('core.common.models.streaming_bulk', side_effect=[self.get_bulk_failures([2]), []]) as bulk_mock, \
                patch('core.common.models.parallel_bulk') as parallel_bulk_mock, \
                patch('core.common.models.time.sleep') as sleep_mock:
            summary = BaseModel.batch_index_full(True, queryset, document, None, None, parallel=False)

        parallel_bulk_mock.assert_not_called()
        self.assertEqual(bulk_mock.call_args_list, [call(
            doc_instance._get_connection.return_value,  # pylint: disable=protected-access
            doc_instance._get_actions.return_value,  # pylint: disable=protected-access
            raise_on_error=False
        )] * 2)
        doc_instance._index.refresh.assert_called_once_with()  # pylint: disable=protected-access
        self.assertEqual(self.get_actions_batches(doc_instance), [[1, 2, 3]] * 2)
        sleep_mock.assert_called_once_with(10)
        self.assertEqual(summary, {'batches': 1, 'failed_batches': 0, 'docs': 3, 'failed_docs': 0})

    def test_batch_index_routes_append_source_version_partial_doc(self):
        queryset, document = Mock(), Mock()
        with patch.object(BaseModel, 'batch_index_source_version_append') as append_mock:
            with patch.object(BaseModel, 'batch_index_partial') as partial_mock:
                with patch.object(BaseModel, 'batch_index_full') as full_mock:
                    BaseModel.batch_index(
                        queryset, document,
                        partial_doc={'_append_source_version': 'v1', 'is_in_latest_source_version': True}
                    )
        append_mock.assert_called_once_with(queryset, document, 'v1', True, False, True)
        partial_mock.assert_not_called()
        full_mock.assert_not_called()

    def test_batch_index_routes_plain_partial_doc(self):
        queryset, document = Mock(), Mock()
        with patch.object(BaseModel, 'batch_index_source_version_append') as append_mock:
            with patch.object(BaseModel, 'batch_index_partial') as partial_mock:
                with patch.object(BaseModel, 'batch_index_full') as full_mock:
                    BaseModel.batch_index(queryset, document, partial_doc={'is_in_latest_source_version': True})
        partial_mock.assert_called_once_with(queryset, document, False, {'is_in_latest_source_version': True}, True)
        append_mock.assert_not_called()
        full_mock.assert_not_called()

    @override_settings(TEST_MODE=False)
    @patch('core.common.models.parallel_bulk', return_value=[])
    def test_batch_index_partial_streams_batches_without_parallel_bulk(self, parallel_bulk_mock):
        # The document bulk helper stores index metadata on protected members.
        ids_queryset = MagicMock()

        def get_batch(batch_slice):
            if batch_slice == slice(0, 500, None):
                return [10, 11]
            return []

        ids_queryset.__getitem__.side_effect = get_batch

        queryset = Mock()
        ordered_queryset = Mock()
        ordered_queryset.values_list.return_value = ids_queryset
        queryset.order_by.return_value = ordered_queryset

        doc_instance = Mock()
        doc_instance.django.auto_refresh = False
        doc_instance.django.queryset_pagination = None
        document = Mock(return_value=doc_instance)

        BaseModel.batch_index_partial(queryset, document, False, {'flag': True})

        queryset.order_by.assert_called_once_with('-id')
        ordered_queryset.values_list.assert_called_once_with('id', flat=True)
        self.assertEqual(ids_queryset.__getitem__.call_args_list, [
            call(slice(0, 500, None)),
            call(slice(500, 1000, None)),
        ])
        bulk_calls = parallel_bulk_mock.call_args_list
        self.assertEqual(len(bulk_calls), 1)
        self.assertEqual(
            bulk_calls[0].args[0], doc_instance._get_connection.return_value)  # pylint: disable=protected-access
        self.assertEqual(list(bulk_calls[0].args[1]), [
            {
                '_op_type': 'update',
                '_index': doc_instance._index._name,  # pylint: disable=protected-access
                '_id': 10,
                'doc': {'flag': True},
            },
            {
                '_op_type': 'update',
                '_index': doc_instance._index._name,  # pylint: disable=protected-access
                '_id': 11,
                'doc': {'flag': True},
            }
        ])
        self.assertEqual([call.kwargs for call in bulk_calls], [{'raise_on_error': False}])

    @override_settings(TEST_MODE=False)
    @patch('core.common.models.parallel_bulk', return_value=[(False, {'update': {'_id': 11, 'status': 404}})])
    def test_batch_index_partial_full_indexes_missing_docs_instead_of_upserting(self, _):
        ids_queryset = MagicMock()
        ids_queryset.__getitem__.side_effect = lambda s: [10, 11] if s == slice(0, 500, None) else []

        queryset = Mock()
        ordered_queryset = Mock()
        ordered_queryset.values_list.return_value = ids_queryset
        queryset.order_by.return_value = ordered_queryset
        missing_queryset = Mock()
        queryset.filter.return_value = missing_queryset

        doc_instance = Mock()
        doc_instance.django.auto_refresh = False
        doc_instance.django.queryset_pagination = None
        document = Mock(return_value=doc_instance)

        with patch.object(BaseModel, 'batch_index_full') as batch_index_full_mock:
            BaseModel.batch_index_partial(queryset, document, False, {'public_can_view': False})

        queryset.filter.assert_called_once_with(id__in={11})
        batch_index_full_mock.assert_called_once_with(
            single_batch=False, queryset=missing_queryset, document=document, prefetch=[], select_related=[],
            refresh=False
        )

    @override_settings(TEST_MODE=False)
    @patch('core.common.models.parallel_bulk', return_value=[])
    def test_batch_index_source_version_append_streams_batches_without_parallel_bulk(self, parallel_bulk_mock):
        ids_queryset = MagicMock()

        def get_batch(batch_slice):
            if batch_slice == slice(0, 500, None):
                return [10, 11]
            return []

        ids_queryset.__getitem__.side_effect = get_batch

        queryset = Mock()
        ordered_queryset = Mock()
        ordered_queryset.values_list.return_value = ids_queryset
        queryset.order_by.return_value = ordered_queryset

        doc_instance = Mock()
        doc_instance.django.auto_refresh = False
        doc_instance.django.queryset_pagination = None
        document = Mock(return_value=doc_instance)

        BaseModel.batch_index_source_version_append(queryset, document, 'v1', True, False)

        bulk_calls = parallel_bulk_mock.call_args_list
        self.assertEqual(len(bulk_calls), 1)
        actions = list(bulk_calls[0].args[1])
        expected_script = {
            'source': ESScript.APPEND_SOURCE_VERSION_SCRIPT,
            'params': {'version': 'v1', 'is_in_latest_source_version': True}
        }
        self.assertEqual(actions, [
            {
                '_op_type': 'update',
                '_index': doc_instance._index._name,  # pylint: disable=protected-access
                '_id': 10,
                'retry_on_conflict': 3,
                'script': expected_script,
            },
            {
                '_op_type': 'update',
                '_index': doc_instance._index._name,  # pylint: disable=protected-access
                '_id': 11,
                'retry_on_conflict': 3,
                'script': expected_script,
            }
        ])
        for action in actions:
            self.assertNotIn('doc_as_upsert', action)
            self.assertNotIn('upsert', action)

    @override_settings(TEST_MODE=False)
    @patch('core.common.models.parallel_bulk', return_value=[(False, {'update': {'_id': 11, 'status': 404}})])
    def test_batch_index_source_version_append_falls_back_to_full_index_for_missing_docs(self, _):
        ids_queryset = MagicMock()
        ids_queryset.__getitem__.side_effect = lambda s: [10, 11] if s == slice(0, 500, None) else []

        queryset = Mock()
        ordered_queryset = Mock()
        ordered_queryset.values_list.return_value = ids_queryset
        queryset.order_by.return_value = ordered_queryset
        missing_queryset = Mock()
        queryset.filter.return_value = missing_queryset

        doc_instance = Mock()
        doc_instance.django.auto_refresh = False
        doc_instance.django.queryset_pagination = None
        document = Mock(return_value=doc_instance)

        with patch.object(BaseModel, 'batch_index_full') as batch_index_full_mock:
            BaseModel.batch_index_source_version_append(queryset, document, 'v1', True, False)

        queryset.filter.assert_called_once_with(id__in={11})
        batch_index_full_mock.assert_called_once_with(
            single_batch=False, queryset=missing_queryset, document=document, prefetch=[], select_related=[],
            refresh=False
        )

    @override_settings(TEST_MODE=False)
    def test_batch_index_source_version_append_continues_remaining_batches_after_bulk_error(self):
        # A BulkIndexError (missing docs) on an earlier batch must not stop later batches
        # from being attempted.
        ids_queryset = MagicMock()

        def get_batch(batch_slice):
            if batch_slice == slice(0, 500, None):
                return [10, 11]
            if batch_slice == slice(500, 1000, None):
                return [20]
            return []

        ids_queryset.__getitem__.side_effect = get_batch

        queryset = Mock()
        ordered_queryset = Mock()
        ordered_queryset.values_list.return_value = ids_queryset
        queryset.order_by.return_value = ordered_queryset
        missing_queryset = Mock()
        queryset.filter.return_value = missing_queryset

        doc_instance = Mock()
        doc_instance.django.auto_refresh = False
        doc_instance.django.queryset_pagination = None
        document = Mock(return_value=doc_instance)

        with patch('core.common.models.parallel_bulk', side_effect=[
            [(False, {'update': {'_id': 11, 'status': 404}})], []
        ]) as bulk_mock, patch.object(BaseModel, 'batch_index_full') as batch_index_full_mock:
            BaseModel.batch_index_source_version_append(queryset, document, 'v1', True, False)

        self.assertEqual(bulk_mock.call_count, 2)
        queryset.filter.assert_called_once_with(id__in={11})
        batch_index_full_mock.assert_called_once_with(
            single_batch=False, queryset=missing_queryset, document=document, prefetch=[], select_related=[],
            refresh=False
        )

    @override_settings(TEST_MODE=False)
    def test_batch_index_source_version_append_fails_on_real_errors(self):
        ids_queryset = MagicMock()
        ids_queryset.__getitem__.side_effect = lambda s: [10] if s == slice(0, 500, None) else []

        queryset = Mock()
        ordered_queryset = Mock()
        ordered_queryset.values_list.return_value = ids_queryset
        queryset.order_by.return_value = ordered_queryset

        doc_instance = Mock()
        doc_instance.django.auto_refresh = False
        doc_instance.django.queryset_pagination = None
        document = Mock(return_value=doc_instance)
        results = [(False, {'update': {'_id': 10, 'status': 500}})]

        with patch('core.common.models.parallel_bulk', return_value=results), \
                patch.object(BaseModel, 'batch_index_full') as batch_index_full_mock, \
                patch('core.common.models.ERRBIT_LOGGER'), patch('core.common.models.time.sleep') as sleep_mock:
            with self.assertRaises(BatchIndexingError) as context:
                BaseModel.batch_index_source_version_append(queryset, document, 'v1', True, False)

        batch_index_full_mock.assert_not_called()
        sleep_mock.assert_not_called()
        self.assertFalse(context.exception.rejected)
        self.assertEqual(context.exception.summary, {'batches': 1, 'failed_batches': 1, 'docs': 1, 'failed_docs': 1})

    @staticmethod
    def get_append_mocks(ids):
        ids_queryset = MagicMock()
        ids_queryset.__getitem__.side_effect = lambda s: ids if s == slice(0, 500, None) else []
        queryset = Mock()
        ordered_queryset = Mock()
        ordered_queryset.values_list.return_value = ids_queryset
        queryset.order_by.return_value = ordered_queryset
        doc_instance = Mock()
        doc_instance.django.auto_refresh = False
        doc_instance.django.queryset_pagination = None
        doc_instance._index._name = 'concepts'  # pylint: disable=protected-access
        document = Mock(return_value=doc_instance)
        document.__name__ = 'ConceptDocument'
        return queryset, doc_instance, document

    @override_settings(TEST_MODE=False)
    def test_batch_index_source_version_append_retries_read_only_index_then_succeeds(self):
        queryset, _, document = self.get_append_mocks([10, 11])
        actions_sent = []

        def bulk(client, actions, **kwargs):  # pylint: disable=unused-argument
            actions_sent.append([action['_id'] for action in actions])
            if len(actions_sent) == 1:
                return self.get_bulk_failures([10, 11])
            if len(actions_sent) == 2:
                return self.get_bulk_failures([11], error_type='es_rejected_execution_exception', reason='queue full')
            return []

        with patch('core.common.models.parallel_bulk', side_effect=bulk), \
                patch.object(BaseModel, 'batch_index_full') as batch_index_full_mock, \
                patch('core.common.models.ERRBIT_LOGGER') as errbit_mock, \
                patch('core.common.models.logger') as logger_mock, \
                patch('core.common.models.time.sleep') as sleep_mock:
            summary = BaseModel.batch_index_source_version_append(queryset, document, 'v1', True, False)

        self.assertEqual(actions_sent, [[10, 11]] * 3)  # every retry re-sends the whole batch
        self.assertEqual([args[0][0] for args in sleep_mock.call_args_list], [10, 20])
        self.assertEqual(logger_mock.warning.call_count, 2)
        self.assertIn('cluster_block_exception', logger_mock.warning.call_args_list[0][0][2])
        logger_mock.error.assert_not_called()
        errbit_mock.log.assert_not_called()
        batch_index_full_mock.assert_not_called()
        self.assertEqual(summary, {'batches': 1, 'failed_batches': 0, 'docs': 2, 'failed_docs': 0})

    @override_settings(TEST_MODE=False, ES_BULK_RETRY_MAX_ATTEMPTS=3)
    def test_batch_index_source_version_append_fails_rejected_when_index_stays_read_only(self):
        queryset, _, document = self.get_append_mocks([10, 11])

        with patch('core.common.models.parallel_bulk', return_value=self.get_bulk_failures([10, 11])) as bulk_mock, \
                patch.object(BaseModel, 'batch_index_full') as batch_index_full_mock, \
                patch('core.common.models.ERRBIT_LOGGER') as errbit_mock, \
                patch('core.common.models.time.sleep') as sleep_mock:
            with self.assertRaises(BatchIndexingError) as context:
                BaseModel.batch_index_source_version_append(queryset, document, 'v1', True, False)

        self.assertEqual(bulk_mock.call_count, 3)
        self.assertEqual([args[0][0] for args in sleep_mock.call_args_list], [10, 20])
        batch_index_full_mock.assert_not_called()
        errbit_mock.log.assert_called_once()
        self.assertIn('read-only-allow-delete', str(errbit_mock.log.call_args[0][0]))
        self.assertTrue(context.exception.rejected)
        self.assertEqual(context.exception.summary, {'batches': 1, 'failed_batches': 1, 'docs': 2, 'failed_docs': 2})

    @override_settings(TEST_MODE=False)
    def test_batch_index_source_version_append_indexes_missing_docs_before_failing_on_real_errors(self):
        # One doc missing from ES (404) and one real error: the missing doc still gets its full index.
        queryset, _, document = self.get_append_mocks([10, 11])
        missing_queryset = Mock()
        queryset.filter.return_value = missing_queryset
        results = [(False, {'update': {'_id': '11', 'status': 404}})] + self.get_bulk_failures(
            [10], status=400, error_type='mapper_parsing_exception', reason='failed to parse')

        with patch('core.common.models.parallel_bulk', return_value=results), \
                patch.object(BaseModel, 'batch_index_full') as batch_index_full_mock, \
                patch('core.common.models.ERRBIT_LOGGER'), patch('core.common.models.time.sleep') as sleep_mock:
            with self.assertRaises(BatchIndexingError) as context:
                BaseModel.batch_index_source_version_append(queryset, document, 'v1', True, False)

        queryset.filter.assert_called_once_with(id__in={'11'})
        batch_index_full_mock.assert_called_once_with(
            single_batch=False, queryset=missing_queryset, document=document, prefetch=[], select_related=[],
            refresh=False
        )
        sleep_mock.assert_not_called()
        self.assertFalse(context.exception.rejected)
        self.assertEqual(context.exception.summary, {'batches': 1, 'failed_batches': 1, 'docs': 2, 'failed_docs': 1})

    @override_settings(TEST_MODE=False)
    def test_batch_index_source_version_append_counts_missing_docs_whose_full_index_failed(self):
        queryset, _, document = self.get_append_mocks([10, 11])
        results = [(False, {'update': {'_id': '11', 'status': 404}})] + self.get_bulk_failures(
            [10], status=400, error_type='mapper_parsing_exception', reason='failed to parse')

        with patch('core.common.models.parallel_bulk', return_value=results), \
                patch.object(BaseModel, 'batch_index_full', side_effect=BatchIndexingError(
                    'ConceptDocument indexing failed',
                    {'batches': 1, 'failed_batches': 1, 'docs': 1, 'failed_docs': 1})) as batch_index_full_mock, \
                patch('core.common.models.ERRBIT_LOGGER'):
            with self.assertRaises(BatchIndexingError) as context:
                BaseModel.batch_index_source_version_append(queryset, document, 'v1', True, False)

        batch_index_full_mock.assert_called_once()
        self.assertEqual(context.exception.summary, {'batches': 1, 'failed_batches': 1, 'docs': 2, 'failed_docs': 2})

    @override_settings(TEST_MODE=False)
    def test_batch_index_source_version_append_keeps_rejection_from_missing_docs_full_index(self):
        # A 400 next to missing docs whose full index ES then rejected: the run still counts as rejected, so the task
        # doesn't fall back to a full reindex against an index that refuses writes.
        queryset, _, document = self.get_append_mocks([10, 11, 12])
        results = [(False, {'update': {'_id': _id, 'status': 404}}) for _id in ['11', '12']] + self.get_bulk_failures(
            [10], status=400, error_type='mapper_parsing_exception', reason='failed to parse')

        with patch('core.common.models.parallel_bulk', return_value=results), \
                patch.object(BaseModel, 'batch_index_full', side_effect=BatchIndexingError(
                    'ConceptDocument indexing failed', {'batches': 1, 'failed_batches': 1, 'docs': 2, 'failed_docs': 2},
                    rejected=True)), \
                patch('core.common.models.ERRBIT_LOGGER'), patch('core.common.models.time.sleep'):
            with self.assertRaises(BatchIndexingError) as context:
                BaseModel.batch_index_source_version_append(queryset, document, 'v1', True, False)

        self.assertTrue(context.exception.rejected)
        self.assertEqual(context.exception.summary, {'batches': 1, 'failed_batches': 1, 'docs': 3, 'failed_docs': 3})

    @override_settings(TEST_MODE=False)
    def test_batch_index_source_version_append_counts_missing_docs_recovered_by_full_index(self):
        # A 400 next to two missing docs, and the full index recovers one of them: 2 docs failed, not 3.
        queryset, _, document = self.get_append_mocks([10, 11, 12])
        results = [(False, {'update': {'_id': _id, 'status': 404}}) for _id in ['11', '12']] + self.get_bulk_failures(
            [10], status=400, error_type='mapper_parsing_exception', reason='failed to parse')

        with patch('core.common.models.parallel_bulk', return_value=results), \
                patch.object(BaseModel, 'batch_index_full', side_effect=BatchIndexingError(
                    'ConceptDocument indexing failed',
                    {'batches': 1, 'failed_batches': 1, 'docs': 2, 'failed_docs': 1})), \
                patch('core.common.models.ERRBIT_LOGGER') as errbit_mock, patch('core.common.models.time.sleep'):
            with self.assertRaises(BatchIndexingError) as context:
                BaseModel.batch_index_source_version_append(queryset, document, 'v1', True, False)

        self.assertFalse(context.exception.rejected)
        self.assertEqual(context.exception.summary, {'batches': 1, 'failed_batches': 1, 'docs': 3, 'failed_docs': 2})
        self.assertIn('mapper_parsing_exception', str(errbit_mock.log.call_args[0][0]))  # the real error's reason

    @override_settings(TEST_MODE=False, ES_BULK_RETRY_MAX_ATTEMPTS=2)
    def test_batch_index_source_version_append_leaves_missing_docs_failed_while_es_rejects(self):
        # A 404 next to a 429: the missing doc's full index would be rejected too (and restart the backoff), so it
        # is counted as failed with the rejected one instead.
        queryset, _, document = self.get_append_mocks([10, 11])
        results = [(False, {'update': {'_id': '11', 'status': 404}})] + self.get_bulk_failures([10])

        with patch('core.common.models.parallel_bulk', return_value=results) as bulk_mock, \
                patch.object(BaseModel, 'batch_index_full') as batch_index_full_mock, \
                patch('core.common.models.ERRBIT_LOGGER'), patch('core.common.models.time.sleep') as sleep_mock:
            with self.assertRaises(BatchIndexingError) as context:
                BaseModel.batch_index_source_version_append(queryset, document, 'v1', True, False)

        self.assertEqual(bulk_mock.call_count, 2)
        sleep_mock.assert_called_once_with(10)
        batch_index_full_mock.assert_not_called()
        self.assertTrue(context.exception.rejected)
        self.assertEqual(context.exception.summary, {'batches': 1, 'failed_batches': 1, 'docs': 2, 'failed_docs': 2})

    @override_settings(TEST_MODE=False)
    def test_batch_index_partial_by_ids_counts_failed_items_in_every_bulk_chunk(self):
        # One logical batch of 1,200 docs goes out in three helper chunks (500, 500, 200). Every chunk must be sent
        # and every failed item counted: the helpers' own raise_on_error stops at the first failed chunk.
        from elasticsearch import Elasticsearch
        queryset = Mock()
        queryset.all.return_value.values_list.return_value = list(range(1, 1201))
        chunk_sizes = []

        def bulk(operations=None, **kwargs):  # pylint: disable=unused-argument
            headers = [json.loads(line) for line in operations[::2]]  # each action here has a body line
            chunk_sizes.append(len(headers))
            return Mock(body={'errors': True, 'items': [
                {'update': {'_index': 'concepts', '_id': str(header['update']['_id']), 'status': 400,
                            'error': {'type': 'mapper_parsing_exception', 'reason': 'failed to parse'}}}
                for header in headers
            ]})

        def get_actions(ids):
            for _id in ids:
                yield {'_op_type': 'update', '_index': 'concepts', '_id': _id, 'doc': {'flag': True}}

        for parallel in (True, False):
            chunk_sizes.clear()
            with self.subTest(parallel=parallel), patch.object(Elasticsearch, 'bulk', side_effect=bulk), \
                    patch('core.common.models.ERRBIT_LOGGER'):
                with self.assertRaises(BatchIndexingError) as context:
                    BaseModel.batch_index_partial_by_ids(
                        queryset, ConceptDocument, get_actions, single_batch=True, parallel=parallel)

                self.assertEqual(sorted(chunk_sizes), [200, 500, 500])
                self.assertEqual(
                    context.exception.summary, {'batches': 1, 'failed_batches': 1, 'docs': 1200, 'failed_docs': 1200})


class TaskTest(OCLTestCase):
    @patch('core.common.tasks.get_export_service')
    def test_delete_s3_objects(self, export_service_mock):
        s3_mock = Mock(delete_objects=Mock())
        export_service_mock.return_value = s3_mock
        delete_s3_objects('/some/path')
        s3_mock.delete_objects.assert_called_once_with('/some/path')

    @patch('core.importers.models.BulkImportParallelRunner.run')
    def test_bulk_import_parallel_inline_invalid_json(self, import_run_mock):
        content = open(os.path.join(os.path.dirname(__file__), '..', 'samples/invalid_import_json.json'), 'r').read()

        result = bulk_import_parallel_inline(to_import=content, username='ocladmin', update_if_exists=False)  # pylint: disable=no-value-for-parameter

        self.assertEqual(result, {
            'error': 'Invalid JSON (Expecting property name enclosed in double quotes)'
        })
        import_run_mock.assert_not_called()

    @patch('core.importers.models.BulkImportParallelRunner.run')
    def test_bulk_import_parallel_inline_invalid_without_resource_type(self, import_run_mock):
        content = open(
            os.path.join(os.path.dirname(__file__), '..', 'samples/invalid_import_without_type.json'), 'r').read()

        result = bulk_import_parallel_inline(to_import=content, username='ocladmin', update_if_exists=False)  # pylint: disable=no-value-for-parameter

        self.assertEqual(result, {
            'error': 'Invalid Input ("type" should be present in each line)'
        })
        import_run_mock.assert_not_called()

    @patch('core.importers.models.BulkImportParallelRunner.run')
    def test_bulk_import_parallel_inline_valid_json(self, import_run_mock):
        import_run_mock.return_value = 'Import Result'
        content = open(os.path.join(os.path.dirname(__file__), '..', 'samples/sample_ocldev.json'), 'r').read()

        result = bulk_import_parallel_inline(to_import=content, username='ocladmin', update_if_exists=False)  # pylint: disable=no-value-for-parameter

        self.assertEqual(result, 'Import Result')
        import_run_mock.assert_called_once()

    @patch('core.common.tasks.EmailMessage')
    def test_resources_report(self, email_message_mock):
        email_message_instance_mock = Mock(send=Mock(return_value=1))
        email_message_mock.return_value = email_message_instance_mock
        res = resources_report()

        email_message_mock.assert_called_once()
        email_message_instance_mock.send.assert_called_once()
        email_message_instance_mock.attach.assert_called_once_with(ANY, ANY, 'text/csv')
        self.assertTrue('_resource_report_' in email_message_instance_mock.attach.call_args[0][0])
        self.assertTrue('.csv' in email_message_instance_mock.attach.call_args[0][0])
        self.assertTrue(b'OCL Usage Report' in email_message_instance_mock.attach.call_args[0][1])

        self.assertEqual(res, 1)
        call_args = email_message_mock.call_args[1]
        self.assertTrue("Monthly Resources Report" in call_args['subject'])
        self.assertEqual(call_args['to'], ['admin@openconceptlab.org'])
        self.assertTrue('Please find attached resources report of' in call_args['body'])
        self.assertTrue('for the period of' in call_args['body'])

    def test_calculate_checksums(self):
        concept = ConceptFactory()
        concept_prev_latest = concept.get_latest_version()
        Concept.create_new_version_for(
            instance=concept.clone(),
            data={
                'names': [{'locale': 'en', 'name': 'English', 'locale_preferred': True}]
            },
            user=concept.created_by,
            create_parent_version=False
        )
        concept_latest = concept.get_latest_version()

        Concept.objects.filter(id__in=[concept.id, concept_latest.id, concept_prev_latest.id]).update(checksums={})

        concept.refresh_from_db()
        concept_prev_latest.refresh_from_db()
        concept_latest.refresh_from_db()

        self.assertEqual(concept.checksums, {})
        self.assertEqual(concept_prev_latest.checksums, {})
        self.assertEqual(concept_latest.checksums, {})

        calculate_checksums('concepts', concept_prev_latest.id)

        concept.refresh_from_db()
        concept_prev_latest.refresh_from_db()
        concept_latest.refresh_from_db()

        self.assertEqual(concept_prev_latest.checksums, {'smart': ANY, 'standard': ANY})
        self.assertEqual(concept_latest.checksums, {'smart': ANY, 'standard': ANY})
        self.assertEqual(concept.checksums, {'smart': ANY, 'standard': ANY})

    def test_delete_organization_not_found(self):
        self.assertIsNone(delete_organization(999999999))

    @patch('core.orgs.models.Organization.delete')
    def test_delete_organization_exception(self, delete_mock):
        from core.orgs.tests.factories import OrganizationFactory
        delete_mock.side_effect = Exception('boom')
        org = OrganizationFactory()

        self.assertIsNone(delete_organization(org.id))

    def test_delete_source_not_found(self):
        self.assertIsNone(delete_source(999999999))

    @patch('core.sources.models.Source.delete')
    def test_delete_source_exception(self, delete_mock):
        delete_mock.side_effect = Exception('boom')
        source = OrganizationSourceFactory()

        self.assertFalse(delete_source(source.id))

    def test_delete_collection_not_found(self):
        self.assertIsNone(delete_collection(999999999))

    @patch('core.collections.models.Collection.delete')
    def test_delete_collection_exception(self, delete_mock):
        delete_mock.side_effect = Exception('boom')
        collection = OrganizationCollectionFactory()

        self.assertFalse(delete_collection(collection.id))

    def test_add_references_collection_not_found(self):
        user = UserProfileFactory()

        added_references, errors = add_references(  # pylint: disable=no-value-for-parameter
            user.id, {'expressions': []}, 999999999, False, False
        )

        self.assertEqual(added_references, [])
        self.assertEqual(errors, {'error': 'Collection not found'})

    def test_handle_pre_delete_task(self):
        concept = ConceptFactory()
        handle_pre_delete('concepts', 'concept', concept.id)

    def test_handle_m2m_changed_pre_remove(self):
        concept = ConceptFactory()
        handle_m2m_changed('concepts', 'concept', concept.id, 'pre_remove')

    def test_handle_m2m_changed_unknown_action_noop(self):
        concept = ConceptFactory()
        handle_m2m_changed('concepts', 'concept', concept.id, 'unknown_action')

    @patch('core.common.tasks.call_command')
    def test_populate_indexes_with_app_names(self, call_command_mock):
        populate_indexes(['concepts'])
        call_command_mock.assert_called_once_with(
            'search_index', '--populate', '-f', '--models', 'concepts', '--parallel', refresh=False)

    @patch('core.common.tasks.call_command')
    def test_populate_indexes_without_app_names(self, call_command_mock):
        populate_indexes(None)
        call_command_mock.assert_called_once_with('search_index', '--populate', '-f', '--parallel', refresh=False)

    @patch('core.common.tasks.call_command')
    def test_rebuild_indexes_with_app_names(self, call_command_mock):
        rebuild_indexes(['concepts'])
        call_command_mock.assert_called_once_with(
            'search_index', '--rebuild', '-f', '--models', 'concepts', '--parallel', '--use-alias', refresh=False)

    @patch('core.common.tasks.call_command')
    def test_rebuild_indexes_without_app_names(self, call_command_mock):
        rebuild_indexes(None)
        call_command_mock.assert_called_once_with(
            'search_index', '--rebuild', '-f', '--parallel', '--use-alias', refresh=False)

    @patch('core.common.tasks.call_command')
    def test_rebuild_indexes_restores_the_index_names(self, call_command_mock):
        def rename(*_, **__):  # as django_elasticsearch_dsl's _rebuild does with --use-alias
            ConceptDocument._index._name = 'concepts-20261006000000000000'  # pylint: disable=protected-access
            MappingDocument._index._name = 'mappings-20261006000000000000'  # pylint: disable=protected-access

        call_command_mock.side_effect = rename
        rebuild_indexes(['concepts', 'mappings'])
        self.assertEqual(ConceptDocument._index._name, 'concepts')  # pylint: disable=protected-access
        self.assertEqual(MappingDocument._index._name, 'mappings')  # pylint: disable=protected-access

        def rename_and_fail(*args, **kwargs):
            rename(*args, **kwargs)
            raise CommandError('populate failed')

        call_command_mock.side_effect = rename_and_fail
        with self.assertRaisesMessage(CommandError, 'populate failed'):
            rebuild_indexes(['concepts'])
        self.assertEqual(ConceptDocument._index._name, 'concepts')  # pylint: disable=protected-access

    @patch('core.importers.importer.Importer.run')
    def test_bulk_import_new(self, run_mock):
        run_mock.return_value = 'import-result'

        result = bulk_import_new(  # pylint: disable=no-value-for-parameter
            'some/path', 'ocladmin', 'Organization', 'org1', 'default'
        )

        self.assertEqual(result, 'import-result')
        run_mock.assert_called_once()

    @patch('core.importers.importer.ImporterSubtask.run')
    def test_bulk_import_subtask(self, run_mock):
        run_mock.return_value = 'import-result'

        result = bulk_import_subtask('some/path', 'ocladmin', 'Organization', 'org1', 'concepts', ['f1.json'])

        self.assertEqual(result, 'import-result')
        run_mock.assert_called_once()

    @patch('core.importers.importer.Importer')
    def test_bulk_import_new_passes_index(self, importer_mock):
        bulk_import_new(  # pylint: disable=no-value-for-parameter
            'some/path', 'ocladmin', 'Organization', 'org1', 'npm', False
        )

        importer_mock.assert_called_once_with(ANY, 'some/path', 'ocladmin', 'Organization', 'org1', 'npm', False)

    @patch('core.importers.importer.ImporterSubtask')
    def test_bulk_import_subtask_passes_index(self, subtask_mock):
        bulk_import_subtask('some/path', 'ocladmin', 'Organization', 'org1', 'Concept', ['f1.json'], False)

        subtask_mock.assert_called_once_with(
            'some/path', 'ocladmin', 'Organization', 'org1', 'Concept', ['f1.json'], False)

    @patch('core.common.tasks.chord')
    def test_bulk_import_queue(self, chord_mock):
        chord_instance_mock = Mock()
        chord_mock.return_value = chord_instance_mock
        task_queue = [['task1'], ['task2']]

        bulk_import_queue(task_queue)

        chord_mock.assert_called_once_with(['task1'], ANY)
        chord_instance_mock.apply_async.assert_called_once_with(queue='concurrent')

    def test_bulk_import_subtask_empty(self):
        self.assertEqual(bulk_import_subtask_empty(), [])

    def test_import_finisher_no_task(self):
        result = import_finisher('does-not-exist')
        self.assertIn('time_finished', result)

    def test_import_finisher_task_without_result(self):
        from core.tasks.models import Task
        task = Task.objects.create(id=str(uuid.uuid4()), name='some-task')

        result = import_finisher(task.id)

        self.assertIn('time_finished', result)

    @patch('core.importers.importer.ImportTask.import_task_from_json')
    def test_import_finisher_with_valid_import_task(self, import_task_from_json_mock):
        from core.tasks.models import Task
        import_task_mock = Mock()
        import_task_mock.model_dump.return_value = {'final_summary': 'done'}
        import_task_from_json_mock.return_value = import_task_mock
        task = Task.objects.create(id=str(uuid.uuid4()), name='some-task', result=json.dumps({'foo': 'bar'}))

        result = import_finisher(task.id)

        self.assertEqual(result, {'final_summary': 'done'})

    @patch('core.importers.importer.ImportTask.import_task_from_json')
    def test_import_finisher_import_task_none_falls_back(self, import_task_from_json_mock):
        from core.tasks.models import Task
        import_task_from_json_mock.return_value = None
        task = Task.objects.create(id=str(uuid.uuid4()), name='some-task', result=json.dumps({'foo': 'bar'}))

        result = import_finisher(task.id)

        self.assertIsNotNone(result)

    def test_process_hierarchy_for_new_parent_concept_version(self):
        parent_concept = ConceptFactory()
        parent_v2 = ConceptFactory(
            parent=parent_concept.parent, mnemonic=parent_concept.mnemonic, version='v2',
            versioned_object=parent_concept
        )
        child_concept = ConceptFactory()
        child_concept.parent_concepts.add(parent_concept)

        process_hierarchy_for_new_parent_concept_version(parent_concept.id, parent_v2.id)

        self.assertIn(parent_v2.id, list(child_concept.parent_concepts.values_list('id', flat=True)))

    def test_batch_index_resources_with_string_filters_and_update_indexed(self):
        concept = ConceptFactory()

        result = batch_index_resources('concepts', json.dumps({'id': concept.id}), update_indexed=True)

        self.assertEqual(result, 1)
        concept.refresh_from_db()

    @patch('core.mappings.models.Mapping.batch_index')
    @patch('core.concepts.models.Concept.batch_index')
    def test_batch_index_resources_loads_relations_per_batch(self, concept_batch_index_mock, mapping_batch_index_mock):
        batch_index_resources('concept', {'id__in': [1]}, False, False)
        batch_index_resources('mapping', {'id__in': [1]})

        concept_batch_index_mock.assert_called_once_with(
            ANY, ConceptDocument, refresh=False, prefetch=['names'],
            select_related=['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by'])
        mapping_batch_index_mock.assert_called_once_with(
            ANY, MappingDocument, refresh=None,
            select_related=['parent', 'parent__organization', 'parent__user', 'created_by', 'updated_by',
                            'from_concept', 'to_concept', 'from_source', 'to_source'])

    @patch('core.concepts.models.Concept.index_mapped_codes')
    def test_index_concepts_mapped_codes(self, index_mapped_codes_mock):
        concept = ConceptFactory()

        index_concepts_mapped_codes([concept.id])

        self.assertEqual(list(index_mapped_codes_mock.call_args[0][0]), [concept])

    @patch('core.concepts.models.Concept.batch_index')
    def test_batch_index_resources_failure_still_ends_indexing_deferral(self, batch_index_mock):
        concept = ConceptFactory()
        Concept.objects.filter(id=concept.id).update(_index=False)
        batch_index_mock.side_effect = BatchIndexingError('ConceptDocument indexing failed')

        with self.assertRaises(BatchIndexingError):
            batch_index_resources('concepts', {'id': concept.id}, update_indexed=True)

        concept.refresh_from_db()
        self.assertTrue(concept._index)  # pylint: disable=protected-access

    def test_index_expansion_concepts_without_concept_ids(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)

        index_expansion_concepts(expansion.id)

    def test_index_expansion_mappings_without_mapping_ids(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)

        index_expansion_mappings(expansion.id)

    def test_vacuum_and_analyze_db(self):
        vacuum_and_analyze_db()

    def test_resolve_url_registry_entries(self):
        source = OrganizationSourceFactory()
        resolve_url_registry_entries(source.id, 'sources')

    def test_resolve_url_registry_entries_unknown_repo_type(self):
        resolve_url_registry_entries(999999999, 'unknown-type')

    def test_expire_old_celery_tasks(self):
        from core.tasks.models import Task
        old_task = Task.objects.create(id=str(uuid.uuid4()), name='old-task')
        Task.objects.filter(id=old_task.id).update(
            updated_at=timezone.now() - datetime.timedelta(days=10))

        expire_old_celery_tasks()

        self.assertFalse(Task.objects.filter(id=old_task.id).exists())

    def test_generate_cache_key(self):
        self.assertEqual(
            VersionCompareMixin._generate_cache_key(  # pylint: disable=protected-access
                'a', 'b', x=1, y=2), "'a'|'b'|x=1|y=2")

    def test_source_version_compare_persists_comparison(self):
        from core.repos.models import VersionChangelog
        source1 = OrganizationSourceFactory()  # HEAD
        source2 = OrganizationSourceFactory(
            organization=source1.organization, mnemonic=source1.mnemonic, version='v1')

        result = source_version_compare(  # pylint: disable=no-value-for-parameter
            source1.uri, source2.uri, False, 0
        )

        self.assertIsNotNone(result)
        # HEAD (source1) must always be treated as version2, regardless of call order.
        changelog = VersionChangelog.objects.get(version1_url=source2.url, version2_url=source1.url)
        self.assertEqual(changelog.comparison, result)
        self.assertEqual(changelog.changelog, {})

    def test_source_version_compare_uses_saved_result_without_recompute(self):
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory(
            organization=source1.organization, mnemonic=source1.mnemonic, version='v1')

        source_version_compare(source1.uri, source2.uri, False, 0)  # pylint: disable=no-value-for-parameter

        with patch.object(VersionCompareMixin, 'compare') as compare_mock:
            result = source_version_compare(source1.uri, source2.uri, False, 0)  # pylint: disable=no-value-for-parameter

        self.assertIsNotNone(result)
        compare_mock.assert_not_called()

    def test_collection_version_compare_persists_comparison(self):
        from core.repos.models import VersionChangelog
        collection1 = OrganizationCollectionFactory()  # HEAD
        collection2 = OrganizationCollectionFactory(
            organization=collection1.organization, mnemonic=collection1.mnemonic, version='v1')
        expansion1 = ExpansionFactory(collection_version=collection1)
        expansion2 = ExpansionFactory(collection_version=collection2)
        collection1.expansion_uri = expansion1.uri
        collection1.save()
        collection2.expansion_uri = expansion2.uri
        collection2.save()

        result = collection_version_compare(  # pylint: disable=no-value-for-parameter
            collection1.uri, collection2.uri, False, 0
        )

        self.assertIsNotNone(result)
        changelog = VersionChangelog.objects.get(version1_url=collection2.url, version2_url=collection1.url)
        self.assertEqual(changelog.comparison, result)

    def test_expansion_compare_does_not_persist(self):
        from core.repos.models import VersionChangelog
        collection = OrganizationCollectionFactory()
        expansion1 = ExpansionFactory(collection_version=collection)
        expansion2 = ExpansionFactory(collection_version=collection)

        result = expansion_compare(  # pylint: disable=no-value-for-parameter
            expansion1.uri, expansion2.uri, False, 0
        )

        self.assertIsNotNone(result)
        self.assertFalse(
            VersionChangelog.objects.filter(version1_url=expansion1.url, version2_url=expansion2.url).exists())
        self.assertFalse(
            VersionChangelog.objects.filter(version1_url=expansion2.url, version2_url=expansion1.url).exists())

    def test_source_version_compare_recomputes_when_head_is_stale(self):
        from core.repos.models import VersionChangelog
        source1 = OrganizationSourceFactory()  # HEAD
        source2 = OrganizationSourceFactory(
            organization=source1.organization, mnemonic=source1.mnemonic, version='v1')

        source_version_compare(source1.uri, source2.uri, False, 0)  # pylint: disable=no-value-for-parameter
        changelog = VersionChangelog.objects.get(version1_url=source2.url, version2_url=source1.url)
        VersionChangelog.objects.filter(id=changelog.id).update(
            updated_at=source1.updated_at - datetime.timedelta(days=1))
        source1.save()  # bumps source1.updated_at past the saved changelog's updated_at

        with patch.object(VersionCompareMixin, 'compare', return_value={'fresh': True}) as compare_mock:
            result = source_version_compare(source1.uri, source2.uri, False, 0)  # pylint: disable=no-value-for-parameter

        compare_mock.assert_called_once()
        self.assertEqual(result, {'fresh': True})

    def test_get_checksum_map_populates_and_reuses_cache(self):
        from core.repos.models import VersionChecksumMap
        source = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(organization=source.organization, mnemonic=source.mnemonic, version='v1')
        concept = ConceptFactory(parent=source, mnemonic='c1')
        source_v1.concepts.add(concept)
        concept.set_checksums()

        self.assertFalse(VersionChecksumMap.objects.filter(version_url=source_v1.url).exists())
        checksum_map = Source.get_checksum_map(source_v1, 'concepts')
        cached = VersionChecksumMap.objects.get(version_url=source_v1.url)
        self.assertEqual(checksum_map, cached.concepts_map)
        self.assertEqual(set(checksum_map['active'].keys()), {'c1'})

        with patch.object(ChecksumDiff, 'build_map') as build_map_mock:
            reused = Source.get_checksum_map(source_v1, 'concepts')

        build_map_mock.assert_not_called()
        self.assertEqual(reused, checksum_map)

    def test_get_checksum_map_head_becomes_stale_after_content_change(self):
        from core.repos.models import VersionChecksumMap
        head = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=head, mnemonic='c1')
        concept1.set_checksums()

        initial_map = Source.get_checksum_map(head, 'concepts')
        self.assertEqual(set(initial_map['active'].keys()), {'c1'})

        cached = VersionChecksumMap.objects.get(version_url=head.url)
        VersionChecksumMap.objects.filter(id=cached.id).update(
            updated_at=timezone.now() - datetime.timedelta(days=1))

        concept2 = ConceptFactory(parent=head, mnemonic='c2')
        concept2.set_checksums()

        refreshed_map = Source.get_checksum_map(head, 'concepts')
        self.assertEqual(set(refreshed_map['active'].keys()), {'c1', 'c2'})

    def test_get_checksum_map_non_head_never_considered_stale(self):
        from core.repos.models import VersionChecksumMap
        source = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(organization=source.organization, mnemonic=source.mnemonic, version='v1')
        concept = ConceptFactory(parent=source, mnemonic='c1')
        source_v1.concepts.add(concept)
        concept.set_checksums()

        Source.get_checksum_map(source_v1, 'concepts')
        cached = VersionChecksumMap.objects.get(version_url=source_v1.url)
        VersionChecksumMap.objects.filter(id=cached.id).update(
            updated_at=timezone.now() - datetime.timedelta(days=365))

        with patch.object(ChecksumDiff, 'build_map') as build_map_mock:
            Source.get_checksum_map(source_v1, 'concepts')

        build_map_mock.assert_not_called()

    def test_get_checksum_map_expansion_never_persists(self):
        from core.repos.models import VersionChecksumMap
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)

        Expansion.get_checksum_map(expansion, 'concepts')

        self.assertFalse(VersionChecksumMap.objects.filter(version_url=expansion.url).exists())

    def test_save_changelog_and_comparison_populates_checksum_maps_for_both_sides(self):
        from core.repos.models import VersionChecksumMap
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory(organization=source1.organization, mnemonic=source1.mnemonic, version='v1')

        Source.save_changelog_and_comparison(source2.uri, source1.uri)

        self.assertTrue(VersionChecksumMap.objects.filter(version_url=source1.url).exists())
        self.assertTrue(VersionChecksumMap.objects.filter(version_url=source2.url).exists())

    def test_save_changelog_and_comparison_handles_mnemonic_with_dot(self):
        """
        Regression test: a mnemonic containing a literal '.' (e.g. an ICD-10 code like
        'R63.6') used to raise `KeyError: Unable to resolve DB id for new:R63.6` because
        db-id lookups used pydash's dotted-path get() (f'{identity}.id'), which misparses
        any identity that itself contains a dot as a nested path instead of a literal key.
        """
        source1 = OrganizationSourceFactory()  # HEAD
        source2 = OrganizationSourceFactory(
            organization=source1.organization, mnemonic=source1.mnemonic, version='v1')
        concept = ConceptFactory(parent=source1, mnemonic='R63.6')  # only on HEAD -- the newer side
        concept.set_checksums()

        changelog = Source.save_changelog_and_comparison(source1.uri, source2.uri)

        self.assertIn('R63.6', changelog.changelog['concepts']['new'])


class URIValidatorTest(OCLTestCase):
    validator = URIValidator()

    def test_invalid_value(self):
        with self.assertRaises(django.core.exceptions.ValidationError):
            self.validator([])

    def test_valid_http_uri(self):
        self.validator('https://openconceptlab.org/orgs/OCL/sources')

    def test_valid_custom_scheme_uri(self):
        self.validator('mailto:admin@openconceptlab.org')

    def test_invalid_uri_with_unsafe_char(self):
        with self.assertRaises(django.core.exceptions.ValidationError):
            self.validator("mailto::\nadmin")

    def test_invalid_uri_domain_too_long(self):
        with self.assertRaises(django.core.exceptions.ValidationError):
            hostname = "abc"*100
            self.validator("https://" + hostname)

    def test_invalid_uri_domain_wrong_char(self):
        with self.assertRaises(django.core.exceptions.ValidationError):
            self.validator("https://open[test/?test")

    def test_invalid_uri_ipv6(self):
        with self.assertRaises(django.core.exceptions.ValidationError):
            self.validator("https://[56FE::2159:5BBC::6594]")


class OCLOIDCAuthenticationBackendTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.backend = OCLOIDCAuthenticationBackend()
        self.claim = {
            'preferred_username': 'batman',
            'email': 'batman@gotham.com',
            'given_name': 'Bruce',
            'family_name': 'Wayne',
            'email_verified': True,
            'foo': 'bar'
        }

    @patch('core.users.models.UserProfile.objects')
    def test_create_user(self, user_manager_mock):
        self.backend.create_user(self.claim)
        user_manager_mock.create_user.assert_called_once_with(
            'batman',
            email='batman@gotham.com',
            first_name='Bruce',
            last_name='Wayne',
            verified=True,
            company=None,
            location=None
        )

    def test_update_user(self):
        user = Mock()
        user.is_dirty.return_value = True

        self.backend.update_user(user, self.claim)

        self.assertEqual(user.first_name, 'Bruce')
        self.assertEqual(user.last_name, 'Wayne')
        self.assertEqual(user.email, 'batman@gotham.com')

        user.save.assert_called_once()
        user.set_groups.assert_called_once_with([], _save=False)
        user.set_checksums.assert_called_once()

    def test_update_user_skips_save_when_not_dirty(self):
        user = Mock()
        user.first_name = 'Bruce'
        user.last_name = 'Wayne'
        user.email = 'batman@gotham.com'
        user.company = None
        user.location = None
        user.is_dirty.return_value = False

        self.backend.update_user(user, self.claim)

        user.save.assert_not_called()
        user.set_groups.assert_called_once_with([], _save=False)
        user.set_checksums.assert_not_called()

    def test_filter_users_by_claims(self):
        batman = UserProfileFactory(username='batman')
        UserProfileFactory(username='superman@not-gotham.com')

        users = self.backend.filter_users_by_claims(self.claim)

        self.assertEqual(users.count(), 1)
        self.assertEqual(users.first(), batman)

        self.assertEqual(self.backend.filter_users_by_claims({**self.claim, 'preferred_username': None}).count(), 0)

    def test_filter_users_by_claims_matches_username_exactly(self):
        """Keycloak sends usernames in lowercase, and a mixed-case account is never matched, active or not"""
        UserProfileFactory(username='Batman', email='batman@gotham.com')
        robin = UserProfileFactory(username='Robin', email='robin@gotham.com')
        robin.deactivate()

        self.assertEqual(self.backend.filter_users_by_claims(self.claim).count(), 0)
        self.assertEqual(
            self.backend.filter_users_by_claims(
                {**self.claim, 'preferred_username': 'robin', 'email': 'robin@gotham.com'}).count(),
            0
        )
        robin.refresh_from_db()
        self.assertFalse(robin.is_active)

    def test_filter_users_by_claims_reactivates_deactivated_user_with_its_verified_email(self):
        batman = UserProfileFactory(username='batman', email='Batman@Gotham.com ')
        source = UserSourceFactory(user=batman)
        collection = UserCollectionFactory(user=batman)
        batman.deactivate()
        source.refresh_from_db()
        collection.refresh_from_db()
        self.assertFalse(source.is_active)
        self.assertFalse(collection.is_active)

        users = self.backend.filter_users_by_claims(self.claim)

        self.assertEqual(list(users), [batman])
        self.assertTrue(users[0].is_active)
        batman.refresh_from_db()
        self.assertTrue(batman.is_active)
        self.assertTrue(batman.verified)
        self.assertIsNone(batman.deactivated_at)
        self.assertEqual(batman.status, 'verified')
        source.refresh_from_db()
        collection.refresh_from_db()
        self.assertTrue(source.is_active)
        self.assertTrue(collection.is_active)

    def test_filter_users_by_claims_refuses_deactivated_user_without_its_verified_email(self):
        batman = UserProfileFactory(username='batman', email='batman@gotham.com', first_name='Bat')
        source = UserSourceFactory(user=batman)
        collection = UserCollectionFactory(user=batman)
        batman.deactivate()
        batman.refresh_from_db()
        deactivated_at = batman.deactivated_at
        updated_at = batman.updated_at
        without_email_verified = {key: value for key, value in self.claim.items() if key != 'email_verified'}

        for claims in [
                {**self.claim, 'email_verified': False},
                {**self.claim, 'email_verified': 'true'},
                without_email_verified,
                {**self.claim, 'email': 'joker@gotham.com'},
                {**self.claim, 'email': None},
                {**self.claim, 'email': 42},
                {**self.claim, 'email': ['batman@gotham.com']},
        ]:
            with self.subTest(claims=claims):
                with patch('core.users.models.UserProfile.save') as save_mock:
                    with self.assertRaises(DeactivatedAccountLoginRefused):
                        self.backend.filter_users_by_claims(claims)
                save_mock.assert_not_called()

                batman.refresh_from_db()
                self.assertFalse(batman.is_active)
                self.assertFalse(batman.verified)
                self.assertEqual(batman.email, 'batman@gotham.com')
                self.assertEqual(batman.first_name, 'Bat')
                self.assertEqual(batman.deactivated_at, deactivated_at)
                self.assertEqual(batman.updated_at, updated_at)
                source.refresh_from_db()
                collection.refresh_from_db()
                self.assertFalse(source.is_active)
                self.assertFalse(collection.is_active)

    def test_filter_users_by_claims_looks_up_the_user_once(self):
        UserProfileFactory(username='batman')

        with self.assertNumQueries(1):
            users = self.backend.filter_users_by_claims(self.claim)
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].username, 'batman')

    def test_filter_users_by_claims_refuses_deactivated_user_without_email(self):
        batman = UserProfileFactory(username='batman', email='')
        batman.deactivate()

        with self.assertRaises(DeactivatedAccountLoginRefused):
            self.backend.filter_users_by_claims({**self.claim, 'email': ''})

        batman.refresh_from_db()
        self.assertFalse(batman.is_active)

    @patch('core.common.backends.OCLOIDCAuthenticationBackend.get_userinfo')
    def test_get_or_create_user_for_active_user(self, get_userinfo_mock):
        get_userinfo_mock.return_value = self.claim
        batman = UserProfileFactory(username='batman', email='old@gotham.com', first_name='Bat')

        user = self.backend.get_or_create_user('access-token', None, None)

        self.assertEqual(user, batman)
        batman.refresh_from_db()
        self.assertTrue(batman.is_active)
        self.assertEqual(batman.email, 'batman@gotham.com')
        self.assertEqual(batman.first_name, 'Bruce')

    @patch('core.common.backends.OCLOIDCAuthenticationBackend.get_userinfo')
    def test_get_or_create_user_for_deactivated_user(self, get_userinfo_mock):
        batman = UserProfileFactory(username='batman', email='batman@gotham.com', first_name='Bat')
        batman.deactivate()

        get_userinfo_mock.return_value = {**self.claim, 'email': 'joker@gotham.com'}
        with patch.object(self.backend, 'update_user') as update_user_mock, \
                patch.object(self.backend, 'create_user') as create_user_mock:
            with self.assertRaises(DeactivatedAccountLoginRefused):
                self.backend.get_or_create_user('access-token', None, None)
        update_user_mock.assert_not_called()
        create_user_mock.assert_not_called()
        self.assertEqual(UserProfile.objects.filter(username__iexact='batman').count(), 1)
        batman.refresh_from_db()
        self.assertFalse(batman.is_active)
        self.assertEqual(batman.email, 'batman@gotham.com')
        self.assertEqual(batman.first_name, 'Bat')

        get_userinfo_mock.return_value = self.claim
        user = self.backend.get_or_create_user('access-token', None, None)

        self.assertEqual(user, batman)
        batman.refresh_from_db()
        self.assertTrue(batman.is_active)
        self.assertEqual(batman.first_name, 'Bruce')

    @patch('core.common.backends.OCLOIDCAuthenticationBackend.get_userinfo')
    def test_get_or_create_user_leaves_mixed_case_deactivated_user(self, get_userinfo_mock):
        get_userinfo_mock.return_value = self.claim
        old_batman = UserProfileFactory(username='Batman', email='batman@gotham.com', first_name='Bat')
        old_batman.deactivate()

        user = self.backend.get_or_create_user('access-token', None, None)

        self.assertNotEqual(user, old_batman)
        self.assertEqual(user.username, 'batman')
        self.assertTrue(user.is_active)
        old_batman.refresh_from_db()
        self.assertFalse(old_batman.is_active)
        self.assertEqual(old_batman.first_name, 'Bat')

    @patch('mozilla_django_oidc.auth.OIDCAuthenticationBackend.authenticate')
    def test_authenticate_fails_and_drops_stored_tokens_when_sign_in_is_refused(self, authenticate_mock):
        def store_tokens_then_refuse(request, **kwargs):  # pylint: disable=unused-argument
            request.session['oidc_access_token'] = 'access-token'
            request.session['oidc_id_token'] = 'id-token'
            raise DeactivatedAccountLoginRefused()
        authenticate_mock.side_effect = store_tokens_then_refuse
        request = Mock(session={'other': 'kept'})

        self.assertIsNone(self.backend.authenticate(request, nonce='nonce'))

        authenticate_mock.assert_called_once()
        self.assertEqual(request.session, {'other': 'kept'})

    @override_settings(TEST_MODE=False, ES_SYNC=False, OIDC_SERVER_URL='https://sso.example.org')
    @patch('core.common.backends.OCLOIDCAuthenticationBackend.get_userinfo')
    def test_bearer_request_for_deactivated_user(self, get_userinfo_mock):
        """Through the real middleware, DRF OIDC authentication and backend"""
        batman = UserProfileFactory(username='batman', email='batman@gotham.com', first_name='Bat')
        batman.deactivate()
        middleware = RequireAuthenticationMiddleware(lambda request: HttpResponse('ok'))

        def get(claims):
            get_userinfo_mock.return_value = claims
            request = RequestFactory().get('/user/', HTTP_AUTHORIZATION='Bearer sso-token')
            request.user = AnonymousUser()
            return request, middleware(request)

        _, response = get({**self.claim, 'email': 'joker@gotham.com'})

        self.assertEqual(response.status_code, 401)
        self.assertIn('deactivated OCL account', json.loads(response.content)['detail'])
        batman.refresh_from_db()
        self.assertFalse(batman.is_active)
        self.assertEqual(batman.email, 'batman@gotham.com')
        self.assertEqual(batman.first_name, 'Bat')

        request, response = get(self.claim)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.user, batman)
        batman.refresh_from_db()
        self.assertTrue(batman.is_active)
        self.assertEqual(batman.first_name, 'Bruce')


class ChecksumTest(OCLTestCase):
    def test_generate(self):
        self.assertIsNotNone(Checksum.generate('foo'))
        self.assertEqual(len(Checksum.generate('foo')), 32)
        self.assertIsInstance(Checksum.generate('foo'), str)

        # keys order
        self.assertEqual(
            Checksum.generate({'foo': 'bar', 'bar': 'foo'}), Checksum.generate({'bar': 'foo', 'foo': 'bar'})
        )
        self.assertEqual(
            Checksum.generate({'a': 1, 'z': 100}), Checksum.generate({'z': 100, 'a': 1})
        )

        # datatype
        self.assertEqual(Checksum.generate({'a': 1}), Checksum.generate({'a': 1.0}))
        self.assertEqual(Checksum.generate({'a': 1.1}), Checksum.generate({'a': 1.10}))

        # value order
        self.assertEqual(Checksum.generate([1, 2, 3]), Checksum.generate([2, 1, 3]))
        self.assertEqual(Checksum.generate({'a': [1, 2, 3]}), Checksum.generate({'a': [2, 1, 3]}))
        self.assertEqual(
            Checksum.generate({'a': {'b': [1, 2, 3], 'c': 'd'}}), Checksum.generate({'a': {'c': 'd', 'b': [3, 1, 2]}}))
        self.assertEqual(
            Checksum.generate(
                [
                    {'foo': 'bar', 'bar': 'foo'},
                    {'1': '2',}
                ]
            ),
            Checksum.generate(
                [
                    {'1': '2',},
                    {'foo': 'bar', 'bar': 'foo'}
                ]
            )
        )
        self.assertIsNotNone(Checksum.generate(uuid.uuid4()))
        self.assertNotEqual(
            Checksum.generate({'a': {'b': [1, 2, 3], 'c': 'd'}}), Checksum.generate({'a': {'c': [1, 2, 3], 'b': 'd'}}))

        concept1 = ConceptFactory(mnemonic=encode_string('Foo/bar', safe=' '))

        from ocldev.checksum import Checksum as ChecksumBase
        self.assertEqual(
            ChecksumBase('concept', ConceptDetailSerializer(concept1).data, 'standard').generate(),
            concept1.checksums['standard']
        )
        concept2 = ConceptFactory(mnemonic=encode_string('bar/bar', safe=' '))
        mapping1 = MappingFactory(
            from_concept=concept1, to_concept=concept2,
            from_concept_code=concept1.mnemonic, to_concept_code=concept2.mnemonic
        )

        mapping_data = MappingDetailSerializer(mapping1).data
        self.assertEqual(
            ChecksumBase('mapping', mapping_data, 'standard').generate(),
            mapping1.checksums['standard']
        )
        self.assertEqual(
            ChecksumBase('mapping', mapping_data, 'smart').generate(),
            mapping1.checksums['smart']
        )

    def test_diff_with_precomputed_maps_matches_queryset_driven(self):
        concept1 = ConceptFactory(mnemonic='c1')
        concept2 = ConceptFactory(mnemonic='c2')
        concept3 = ConceptFactory(mnemonic='c3', retired=True)
        for concept in (concept1, concept2, concept3):
            concept.set_checksums()

        queryset = Concept.objects.filter(id__in=[concept1.id, concept2.id, concept3.id])
        via_queryset = ChecksumDiff(resources1=queryset, resources2=queryset, verbosity=3)
        via_queryset.process()

        precomputed_map = ChecksumDiff.build_map(queryset)
        via_map = ChecksumDiff(resources1_map=precomputed_map, resources2_map=precomputed_map, verbosity=3)
        via_map.process()

        self.assertEqual(via_queryset.result, via_map.result)
        self.assertIsNone(via_map.resources1)
        self.assertIsNone(via_map.resources2)


class ChecksumViewTest(OCLAPITestCase):
    def setUp(self):
        self.token = UserProfile.objects.get(username='ocladmin').get_token()

    @patch('core.common.checksums.Checksum.generate')
    def test_post_400(self, checksum_generate_mock):
        response = self.client.post(
            '/$checksum/standard/',
            {},
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        checksum_generate_mock.assert_not_called()

        response = self.client.post(
            '/$checksum/smart/',
            {"foo": "bar"},
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        checksum_generate_mock.assert_not_called()

        response = self.client.post(
            '/$checksum/smart/?resource=foobar',
            {"foo": "bar"},
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'error': 'Invalid resource: foobar'})
        checksum_generate_mock.assert_not_called()

    @patch('core.common.checksums.ChecksumBase.generate')
    def test_post_200_concept(self, checksum_generate_mock):
        checksum_generate_mock.return_value = 'checksum'

        response = self.client.post(
            '/$checksum/standard/?resource=concept',
            data={'foo': 'bar', 'concept_class': 'foobar', 'extras': {}},
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'checksum')


@patch.dict('rest_framework.throttling.UserRateThrottle.THROTTLE_RATES', {
    'guest_minute': '400/minute',
    'guest_day': '10000/day',
    'standard_minute': '500/minute',
    'standard_day': '20000/day',
    'core_minute': '1000/minute',
    'core_day': '40000/day',
    'match_standard_minute': '300/minute',
    'match_standard_day': '5000/day',
    'match_core_minute': '1000/minute',
    'match_core_day': '20000/day',
}, clear=True)
@override_settings(ENABLE_THROTTLING=True)
class ThrottleUtilTest(OCLTestCase):
    """Verify throttle selection for guest, standard, core, and superuser auth groups."""

    def test_get_throttles_for_guest_user(self):
        throttles = ThrottleUtil.get_throttles_by_user_plan(AnonymousUser())

        self.assertIsInstance(throttles[0], GuestMinuteThrottle)
        self.assertIsInstance(throttles[1], GuestDayThrottle)

    def test_get_throttles_for_standard_user(self):
        user = UserProfileFactory()

        throttles = ThrottleUtil.get_throttles_by_user_plan(user)

        self.assertIsInstance(throttles[0], StandardMinuteThrottle)
        self.assertIsInstance(throttles[1], StandardDayThrottle)

    def test_get_throttles_for_core_user(self):
        user = UserProfileFactory()
        user.groups.add(Group.objects.get(name=CORE_USER_GROUP))

        throttles = ThrottleUtil.get_throttles_by_user_plan(user)

        self.assertTrue(user.is_core_group)
        self.assertIsInstance(throttles[0], CoreMinuteThrottle)
        self.assertIsInstance(throttles[1], CoreDayThrottle)

    def test_get_throttles_for_superuser(self):
        """Superusers must bypass view throttling entirely."""
        user = UserProfileFactory(is_superuser=True, is_staff=True)

        throttles = ThrottleUtil.get_throttles_by_user_plan(user)

        self.assertEqual(throttles, [])

    def test_get_match_throttles_for_guest_user(self):
        user = UserProfileFactory()
        user.groups.add(Group.objects.get(name=GUEST_GROUP))

        throttles = ThrottleUtil.get_match_throttles_by_user_plan(user)

        self.assertIsInstance(throttles[0], GuestMinuteThrottle)
        self.assertIsInstance(throttles[1], GuestDayThrottle)

    def test_get_match_throttles_for_standard_user(self):
        user = UserProfileFactory()

        throttles = ThrottleUtil.get_match_throttles_by_user_plan(user)

        self.assertIsInstance(throttles[0], MatchStandardMinuteThrottle)
        self.assertIsInstance(throttles[1], MatchStandardDayThrottle)

    def test_get_match_throttles_for_core_user(self):
        user = UserProfileFactory()
        user.groups.add(Group.objects.get(name=CORE_USER_GROUP))

        throttles = ThrottleUtil.get_match_throttles_by_user_plan(user)

        self.assertIsInstance(throttles[0], MatchCoreMinuteThrottle)
        self.assertIsInstance(throttles[1], MatchCoreDayThrottle)

    def test_get_match_throttles_for_superuser(self):
        """Superusers must bypass match throttling entirely."""
        user = UserProfileFactory(is_superuser=True, is_staff=True)

        throttles = ThrottleUtil.get_match_throttles_by_user_plan(user)

        self.assertEqual(throttles, [])

    def test_core_user_gets_core_throttle_not_standard(self):
        """Core group membership must take precedence over the standard fallthrough."""
        user = UserProfileFactory()
        user.groups.add(Group.objects.get(name=CORE_USER_GROUP))

        throttles = ThrottleUtil.get_throttles_by_user_plan(user)
        match_throttles = ThrottleUtil.get_match_throttles_by_user_plan(user)

        # Must NOT fall through to standard
        self.assertNotIsInstance(throttles[0], StandardMinuteThrottle)
        self.assertNotIsInstance(throttles[1], StandardDayThrottle)
        self.assertNotIsInstance(match_throttles[0], MatchStandardMinuteThrottle)
        self.assertNotIsInstance(match_throttles[1], MatchStandardDayThrottle)

        # Must get core
        self.assertIsInstance(throttles[0], CoreMinuteThrottle)
        self.assertIsInstance(throttles[1], CoreDayThrottle)
        self.assertIsInstance(match_throttles[0], MatchCoreMinuteThrottle)
        self.assertIsInstance(match_throttles[1], MatchCoreDayThrottle)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class LexicalVariantsTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        # locmem has no delete_pattern; add a shim so invalidate_cache() works in tests
        if not hasattr(cache, 'delete_pattern'):
            cache.delete_pattern = lambda pattern: cache.clear()
        cache.clear()

    def test_tokenize_lowercases_and_splits(self):
        from core.common.lexical_variants import LexicalVariantDictionary
        self.assertEqual(LexicalVariantDictionary.tokenize("Leukaemia"), ["leukaemia"])
        self.assertEqual(LexicalVariantDictionary.tokenize("Anti-HCV IgG"), ["anti", "hcv", "igg"])
        self.assertEqual(LexicalVariantDictionary.tokenize("  spaced   out  "), ["spaced", "out"])
        self.assertEqual(LexicalVariantDictionary.tokenize(""), [])
        self.assertEqual(LexicalVariantDictionary.tokenize(None), [])

    @patch('core.common.lexical_variants.LexicalVariantDictionary._resolve_source')
    @patch('core.common.lexical_variants.LexicalVariantDictionary._load_dictionary')
    def test_returns_variants_for_known_token(self, mock_load, mock_resolve):
        from core.common.lexical_variants import LexicalVariant, LexicalVariantDictionary
        mock_resolve.return_value = MagicMock(uri='/orgs/OCL/sources/lexical-variants-en/', version='HEAD')
        mock_load.return_value = {
            'leukaemia': [LexicalVariant(
                term='leukemia', name_type='Fully Specified', locale='en-US',
                source_concept_uri='/orgs/OCL/sources/lexical-variants-en/concepts/leukemia/',
            )],
            'leukemia': [LexicalVariant(
                term='leukaemia', name_type='Fully Specified', locale='en-GB',
                source_concept_uri='/orgs/OCL/sources/lexical-variants-en/concepts/leukemia/',
            )],
        }

        variants = LexicalVariantDictionary.get_lexical_variants('leukaemia')
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].term, 'leukemia')
        self.assertEqual(variants[0].locale, 'en-US')

        terms = LexicalVariantDictionary.get_variant_terms('leukemia')
        self.assertEqual(terms, ['leukaemia'])

    @patch('core.common.lexical_variants.LexicalVariantDictionary._resolve_source')
    @patch('core.common.lexical_variants.LexicalVariantDictionary._load_dictionary')
    def test_returns_empty_for_unknown_token(self, mock_load, mock_resolve):
        """Regression: words containing 'hem'/'haem' as a substring must NOT match."""
        from core.common.lexical_variants import LexicalVariant, LexicalVariantDictionary
        mock_resolve.return_value = MagicMock(uri='/orgs/OCL/sources/lexical-variants-en/', version='HEAD')
        mock_load.return_value = {
            'hemorrhage': [LexicalVariant(
                term='haemorrhage', name_type='Fully Specified', locale='en-GB',
                source_concept_uri='/orgs/OCL/sources/lexical-variants-en/concepts/hemorrhage/',
            )],
        }

        for false_positive in ['themselves', 'anthem', 'hemisphere', 'hemp', 'hemlock', 'remember']:
            with self.subTest(token=false_positive):
                self.assertEqual(LexicalVariantDictionary.get_lexical_variants(false_positive), [])

    @patch('core.common.lexical_variants.LexicalVariantDictionary._resolve_source')
    def test_returns_empty_when_source_missing(self, mock_resolve):
        from core.common.lexical_variants import LexicalVariantDictionary
        mock_resolve.return_value = None
        self.assertEqual(LexicalVariantDictionary.get_lexical_variants('leukaemia'), [])

    def test_returns_empty_for_empty_input(self):
        from core.common.lexical_variants import LexicalVariantDictionary
        self.assertEqual(LexicalVariantDictionary.get_lexical_variants(''), [])
        self.assertEqual(LexicalVariantDictionary.get_lexical_variants(None), [])

    @patch('core.common.lexical_variants.LexicalVariantDictionary._resolve_source')
    @patch('core.common.lexical_variants.LexicalVariantDictionary._load_dictionary')
    def test_caches_dictionary_per_source_version(self, mock_load, mock_resolve):
        from core.common.lexical_variants import LexicalVariantDictionary
        mock_resolve.return_value = MagicMock(uri='/orgs/OCL/sources/lexical-variants-en/', version='v1.0')
        mock_load.return_value = {}

        LexicalVariantDictionary.get_lexical_variants('leukaemia')
        LexicalVariantDictionary.get_lexical_variants('color')
        LexicalVariantDictionary.get_lexical_variants('anything')
        self.assertEqual(mock_load.call_count, 1)

        LexicalVariantDictionary.invalidate_cache()
        LexicalVariantDictionary.get_lexical_variants('leukaemia')
        self.assertEqual(mock_load.call_count, 2)

    @patch('core.common.lexical_variants.LexicalVariantDictionary._resolve_source')
    @patch('core.common.lexical_variants.LexicalVariantDictionary._load_dictionary')
    def test_multi_token_input_expands_each_known_token(self, mock_load, mock_resolve):
        from core.common.lexical_variants import LexicalVariant, LexicalVariantDictionary
        mock_resolve.return_value = MagicMock(uri='/orgs/OCL/sources/lexical-variants-en/', version='HEAD')
        mock_load.return_value = {
            'leukaemia': [LexicalVariant(
                term='leukemia', name_type='Fully Specified', locale='en-US',
                source_concept_uri='/orgs/OCL/sources/lexical-variants-en/concepts/leukemia/',
            )],
            'colour': [LexicalVariant(
                term='color', name_type='Fully Specified', locale='en-US',
                source_concept_uri='/orgs/OCL/sources/lexical-variants-en/concepts/color/',
            )],
        }

        terms = LexicalVariantDictionary.get_variant_terms('childhood leukaemia colour')
        self.assertEqual(set(terms), {'leukemia', 'color'})


class ESSplitIndexCommandTest(OCLTestCase):
    @staticmethod
    def get_client(**overrides):
        client = MagicMock()
        client.options.return_value = client
        client.indices.exists_alias.return_value = False
        client.indices.exists.return_value = True
        client.indices.get_settings.return_value = {
            'concepts': {'settings': {'index': {'number_of_shards': '1', 'number_of_replicas': '0'}}}}
        client.cluster.health.return_value = {'status': 'green', 'timed_out': False}
        client.cat.shards.return_value = [{'prirep': 'p', 'node': 'es2', 'store': str(10 * 1024 ** 3)}]
        client.cat.allocation.return_value = [
            {'node': 'es', 'disk.used': '0', 'disk.total': str(10 * 1024 ** 3)},
            {'node': 'es2', 'disk.used': str(45 * 1024 ** 3), 'disk.total': str(100 * 1024 ** 3)},
        ]
        client.count.return_value = {'count': 42}
        for attr, value in overrides.items():
            obj = client
            *path, last = attr.split('.')
            for part in path:
                obj = getattr(obj, part)
            setattr(obj, last, value)
        return client

    def run_command(self, client, *args):
        stderr = Mock()
        with patch('core.common.management.commands.es_split_index.connections.get_connection', return_value=client):
            call_command('es_split_index', 'concepts', *args, stdout=Mock(), stderr=stderr)
        return stderr

    def test_splits_then_swaps_alias_atomically(self):
        client = self.get_client()

        self.run_command(client, '--shards', '6', '--yes')

        client.indices.add_block.assert_called_once_with(index='concepts', block='write')
        client.indices.put_settings.assert_not_called()
        client.indices.flush.assert_called_once_with(index='concepts')
        split_kwargs = client.indices.split.call_args[1]
        target = split_kwargs['target']
        self.assertRegex(target, r'^concepts-\d{20}$')
        self.assertEqual(split_kwargs['settings'], {
            'index.number_of_shards': 6, 'index.number_of_replicas': 0, 'index.blocks.write': None,
            'index.routing.allocation.require._name': 'es2'})
        client.cluster.health.assert_called_with(index=target, wait_for_status='green', timeout='3600s')
        client.options.assert_any_call(max_retries=0)
        client.indices.update_aliases.assert_called_once_with(actions=[
            {'add': {'index': target, 'alias': 'concepts'}},
            {'remove_index': {'index': 'concepts'}},
        ])
        client.indices.delete.assert_not_called()

    def test_dry_run_changes_nothing(self):
        client = self.get_client()

        self.run_command(client, '--shards', '6', '--dry-run')

        client.indices.add_block.assert_not_called()
        client.indices.split.assert_not_called()
        client.indices.update_aliases.assert_not_called()

    @patch('builtins.input', return_value='n')
    def test_asks_before_blocking_writes(self, input_mock):
        client = self.get_client()

        with self.assertRaisesMessage(CommandError, 'nothing changed'):
            self.run_command(client, '--shards', '6')

        input_mock.assert_called_once()
        client.indices.add_block.assert_not_called()
        client.indices.split.assert_not_called()

    def test_preflight_rejects_unsafe_sources(self):
        cases = [
            ({'indices.exists_alias.return_value': True}, 'already an alias'),
            ({'indices.exists.return_value': False}, "doesn't exist"),
            ({'indices.get_settings.return_value': {
                'concepts': {'settings': {'index': {'number_of_shards': '3'}}}}}, 'only 1 is supported'),
            ({'indices.get_settings.return_value': {'concepts': {'settings': {'index': {
                'number_of_shards': '1', 'blocks': {'read_only_allow_delete': 'true'}}}}}}, 'blocks set'),
            ({'cluster.health.return_value': {'status': 'red'}}, 'not green'),
            ({'cat.allocation.return_value': [  # 76 + 10 GB of 100: past 85% while the shards merge apart
                {'node': 'es2', 'disk.used': str(76 * 1024 ** 3), 'disk.total': str(100 * 1024 ** 3)}]},
             'could reach 86% disk'),
        ]
        for overrides, message in cases:
            client = self.get_client(**overrides)
            with self.assertRaisesMessage(CommandError, message):
                self.run_command(client, '--shards', '6', '--yes')
            client.indices.add_block.assert_not_called()

        with self.assertRaisesMessage(CommandError, 'at least 2'):
            self.run_command(self.get_client(), '--shards', '1')

    def test_rolls_back_when_counts_differ(self):
        client = self.get_client()
        client.count.side_effect = [{'count': 42}, {'count': 41}]

        with self.assertRaisesMessage(CommandError, 'Doc counts differ'):
            self.run_command(client, '--shards', '6', '--yes')

        self.assert_rolled_back(client)

    def test_rolls_back_when_split_fails(self):
        client = self.get_client()
        client.indices.split.side_effect = Exception('boom')

        with self.assertRaisesMessage(CommandError, 'rolled back: boom'):
            self.run_command(client, '--shards', '6', '--yes')

        self.assert_rolled_back(client)

    def test_rolls_back_when_target_never_turns_green(self):
        client = self.get_client()
        client.cluster.health.side_effect = [
            {'status': 'green', 'timed_out': False}, {'status': 'yellow', 'timed_out': True}]

        with self.assertRaisesMessage(CommandError, "isn't green"):
            self.run_command(client, '--shards', '6', '--yes')

        self.assert_rolled_back(client)

    def test_clears_a_block_that_applied_but_timed_out(self):
        client = self.get_client()
        client.indices.add_block.side_effect = Exception('ConnectionTimeout')

        with self.assertRaisesMessage(CommandError, 'rolled back: ConnectionTimeout'):
            self.run_command(client, '--shards', '6', '--yes')

        client.indices.split.assert_not_called()
        client.indices.put_settings.assert_called_once_with(index='concepts', settings={'index.blocks.write': None})

    def test_does_not_roll_back_a_swap_that_went_through(self):
        client = self.get_client()
        client.indices.update_aliases.side_effect = Exception('ConnectionTimeout')  # applied, response lost
        client.indices.exists_alias.side_effect = [False, True]  # preflight, then the reconcile check

        stderr = self.run_command(client, '--shards', '6', '--yes')

        target = client.indices.split.call_args[1]['target']
        client.indices.exists_alias.assert_called_with(name='concepts', index=target)
        client.indices.delete.assert_not_called()
        client.indices.put_settings.assert_not_called()
        self.assertIn('swap went through', stderr.write.call_args[0][0])

    def test_keeps_the_target_when_the_swap_state_is_unknown(self):
        client = self.get_client()
        client.indices.update_aliases.side_effect = Exception('ConnectionTimeout')
        client.indices.exists_alias.side_effect = [False, Exception('ES unreachable')]

        with self.assertRaisesMessage(CommandError, 'Rollback incomplete'):
            self.run_command(client, '--shards', '6', '--yes')

        client.indices.delete.assert_not_called()
        client.indices.put_settings.assert_called_once_with(index='concepts', settings={'index.blocks.write': None})

    def test_clears_the_block_even_when_deleting_the_target_fails(self):
        client = self.get_client()
        client.indices.split.side_effect = Exception('boom')
        client.indices.delete.side_effect = Exception('delete timed out')

        with self.assertRaisesMessage(CommandError, 'Rollback incomplete'):
            self.run_command(client, '--shards', '6', '--yes')

        client.indices.put_settings.assert_called_once_with(index='concepts', settings={'index.blocks.write': None})

    @staticmethod
    def assert_rolled_back(client):
        client.indices.update_aliases.assert_not_called()
        target = client.indices.split.call_args[1]['target']
        client.indices.delete.assert_called_once_with(index=target, ignore_unavailable=True)
        client.indices.put_settings.assert_called_once_with(index='concepts', settings={'index.blocks.write': None})
