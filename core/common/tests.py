import datetime
import os
import uuid
from collections import OrderedDict
from unittest.mock import patch, Mock, ANY

import django
import factory
from colour_runner.django_runner import ColourRunnerMixin
from django.conf import settings
from django.core.files.base import File
from django.core.management import call_command
from django.test import TestCase
from django.test.runner import DiscoverRunner
from mock.mock import call
from requests.auth import HTTPBasicAuth
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase, APITransactionTestCase

from core.collections.models import CollectionReference
from core.common.constants import HEAD
from core.common.tasks import delete_s3_objects, bulk_import_parallel_inline, resources_report, calculate_checksums
from core.common.utils import (
    compact_dict_by_values, to_snake_case, flower_get, task_exists, parse_bulk_import_task_id,
    to_camel_case,
    drop_version, is_versioned_uri, separate_version, to_parent_uri, jsonify_safe, es_get,
    get_resource_class_from_resource_name, flatten_dict, is_csv_file, is_url_encoded_string, to_parent_uri_from_kwargs,
    set_current_user, get_current_user, set_request_url, get_request_url, nested_dict_values, chunks, api_get,
    split_list_by_condition, is_zip_file, get_date_range_label, get_prev_month, from_string_to_date, get_end_of_month,
    get_start_of_month, es_id_in, web_url)
from core.concepts.models import Concept
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory
from .backends import OCLOIDCAuthenticationBackend
from .checksums import Checksum
from .fhir_helpers import translate_fhir_query
from .serializers import IdentifierSerializer
from .validators import URIValidator
from ..code_systems.serializers import CodeSystemDetailSerializer
from ..concepts.tests.factories import ConceptFactory, ConceptNameFactory
from ..sources.tests.factories import OrganizationSourceFactory


class CustomTestRunner(ColourRunnerMixin, DiscoverRunner):
    pass


class SetupTestEnvironment:
    settings.TEST_MODE = True
    settings.ELASTICSEARCH_DSL_AUTOSYNC = True
    settings.ES_SYNC = True


class BaseTestCase(SetupTestEnvironment):
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
        call_command("loaddata", "core/fixtures/toggles.json")
        org = Organization.objects.get(id=1)
        org.members.add(1)


class OCLAPITestCase(APITestCase, BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")
        call_command("loaddata", "core/fixtures/toggles.json")
        org = Organization.objects.get(id=1)
        org.members.add(1)


class OCLTestCase(TestCase, BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")
        call_command("loaddata", "core/fixtures/auth_groups.yaml")
        call_command("loaddata", "core/fixtures/toggles.json")

    @staticmethod
    def factory_to_params(factory_klass, **kwargs):
        return {
            **factory.build(dict, FACTORY_CLASS=factory_klass),
            **kwargs
        }


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

    @patch('core.common.utils.flower_get')
    def test_task_exists(self, flower_get_mock):
        flower_get_mock.return_value = None

        self.assertFalse(task_exists('task-id'))
        flower_get_mock.assert_called_with('api/task/info/task-id')

        flower_get_mock.return_value = Mock(status_code=400)

        self.assertFalse(task_exists('task-id'))
        flower_get_mock.assert_called_with('api/task/info/task-id')

        flower_get_mock.return_value = Mock(status_code=200, text=None)

        self.assertFalse(task_exists('task-id'))
        flower_get_mock.assert_called_with('api/task/info/task-id')

        flower_get_mock.return_value = Mock(status_code=200, text='Success')

        self.assertTrue(task_exists('task-id'))
        flower_get_mock.assert_called_with('api/task/info/task-id')

    def test_parse_bulk_import_task_id(self):
        task_uuid = str(uuid.uuid4())

        task_id = f"{task_uuid}-username~queue"
        self.assertEqual(
            parse_bulk_import_task_id(task_id),
            {'uuid': task_uuid + '-', 'username': 'username', 'queue': 'queue'}
        )

        task_id = f"{task_uuid}-username"
        self.assertEqual(
            parse_bulk_import_task_id(task_id),
            {'uuid': task_uuid + '-', 'username': 'username', 'queue': 'default'}
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


class BaseModelTest(OCLTestCase):
    def test_model_name(self):
        self.assertEqual(Concept().model_name, 'Concept')
        self.assertEqual(Source().model_name, 'Source')

    def test_app_name(self):
        self.assertEqual(Concept().app_name, 'concepts')
        self.assertEqual(Source().app_name, 'sources')


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
        self.assertEqual(call_args['to'], ['reports@openconceptlab.org'])
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

        self.backend.update_user(user, self.claim)

        self.assertEqual(user.first_name, 'Bruce')
        self.assertEqual(user.last_name, 'Wayne')
        self.assertEqual(user.email, 'batman@gotham.com')

        user.save.assert_called_once()

    def test_filter_users_by_claims(self):
        batman = UserProfileFactory(username='batman')
        UserProfileFactory(username='superman@not-gotham.com')

        users = self.backend.filter_users_by_claims(self.claim)

        self.assertEqual(users.count(), 1)
        self.assertEqual(users.first(), batman)

        self.assertEqual(self.backend.filter_users_by_claims({**self.claim, 'preferred_username': None}).count(), 0)


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
        self.assertNotEqual(Checksum.generate({'a': 1}), Checksum.generate({'a': 1.0}))
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
        checksum_generate_mock.assert_not_called()

    @patch('core.common.checksums.Checksum.generate')
    def test_post_200_concept(self, checksum_generate_mock):
        checksum_generate_mock.return_value = 'checksum'

        response = self.client.post(
            '/$checksum/standard/?resource=concept_version',
            data={'foo': 'bar', 'concept_class': 'foobar', 'extras': {}},
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'checksum')
        checksum_generate_mock.assert_called_once_with({'concept_class': 'foobar', 'names': [], 'descriptions': []})

    @patch('core.common.checksums.Checksum.generate')
    def test_post_200_mapping_standard(self, checksum_generate_mock):
        checksum_generate_mock.side_effect = ['checksum1', 'checksum2', 'checksum3']

        response = self.client.post(
            '/$checksum/standard/?resource=mapping',
            data=[
                {
                    'id': 'bar',
                    'map_type': 'foobar',
                    'from_concept_url': '/foo/',
                    'to_source_url': '/bar/',
                    'from_concept_code': 'foo',
                    'to_concept_code': 'bar',
                    'from_concept_name': 'fooName',
                    'to_concept_name': 'barName',
                    'retired': False,
                    'external_id': 'EX123',
                    'extras': {
                        'foo': 'bar'
                    }
                },
                {
                    'id': 'barbara',
                    'map_type': 'foobarbara',
                    'from_concept_url': '/foobara/',
                    'to_source_url': '/barbara/',
                    'from_concept_code': 'foobara',
                    'to_concept_code': 'barbara',
                    'from_concept_name': 'foobaraName',
                    'to_concept_name': 'barbaraName',
                    'retired': True,
                    'extras': {
                        'foo': 'barbara'
                    }
                }
            ],
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'checksum3')
        self.assertEqual(checksum_generate_mock.call_count, 3)
        self.assertEqual(
            checksum_generate_mock.mock_calls,
            [
                call({
                    'map_type': 'foobar',
                    'from_concept_code': 'foo',
                    'to_concept_code': 'bar',
                    'from_concept_name': 'fooName',
                    'to_concept_name': 'barName',
                    'extras': {'foo': 'bar'},
                    'external_id': 'EX123'
                }),
                call({
                    'map_type': 'foobarbara',
                    'from_concept_code': 'foobara',
                    'to_concept_code': 'barbara',
                    'from_concept_name': 'foobaraName',
                    'to_concept_name': 'barbaraName',
                    'extras': {'foo': 'barbara'},
                    'retired': True
                }),
                call(['checksum1', 'checksum2'])
            ]
        )

    @patch('core.common.checksums.Checksum.generate')
    def test_post_200_mapping_smart(self, checksum_generate_mock):
        checksum_generate_mock.side_effect = ['checksum1', 'checksum2', 'checksum3']

        response = self.client.post(
            '/$checksum/smart/?resource=mapping',
            data=[
                {
                    'id': 'bar',
                    'map_type': 'foobar',
                    'from_concept_url': '/foo/',
                    'to_source_url': '/bar/',
                    'from_concept_code': 'foo',
                    'to_concept_code': 'bar',
                    'from_concept_name': 'fooName',
                    'to_concept_name': 'barName',
                    'retired': False,
                    'extras': {'foo': 'bar'}
                },
                {
                    'id': 'barbara',
                    'map_type': 'foobarbara',
                    'from_concept_url': '/foobara/',
                    'to_source_url': '/barbara/',
                    'from_concept_code': 'foobara',
                    'to_concept_code': 'barbara',
                    'from_concept_name': 'foobaraName',
                    'to_concept_name': 'barbaraName',
                    'retired': True,
                    'extras': {'foo': 'barbara'}
                }
            ],
            HTTP_AUTHORIZATION=f"Token {self.token}",
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, 'checksum3')
        self.assertEqual(checksum_generate_mock.call_count, 3)
        self.assertEqual(
            checksum_generate_mock.mock_calls,
            [
                call({
                      'map_type': 'foobar',
                      'from_concept_code': 'foo',
                      'to_concept_code': 'bar',
                      'from_concept_name': 'fooName',
                      'to_concept_name': 'barName'
                }),
                call({
                      'map_type': 'foobarbara',
                      'from_concept_code': 'foobara',
                      'to_concept_code': 'barbara',
                      'from_concept_name': 'foobaraName',
                      'to_concept_name': 'barbaraName',
                      'retired': True
                }),
                call(['checksum1', 'checksum2'])
            ]
        )
