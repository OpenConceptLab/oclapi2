import base64
import os
import uuid
from unittest.mock import patch, Mock, mock_open

import factory

import boto3
from botocore.exceptions import ClientError
from colour_runner.django_runner import ColourRunnerMixin
from django.conf import settings
from django.core.files.base import ContentFile, File
from django.core.management import call_command
from django.test import TestCase
from django.test.runner import DiscoverRunner
from moto import mock_s3
from requests.auth import HTTPBasicAuth
from rest_framework.test import APITestCase

from core.collections.models import CollectionReference
from core.common.constants import HEAD
from core.common.tasks import delete_s3_objects, bulk_import_parallel_inline, monthly_usage_report
from core.common.utils import (
    compact_dict_by_values, to_snake_case, flower_get, task_exists, parse_bulk_import_task_id,
    to_camel_case,
    drop_version, is_versioned_uri, separate_version, to_parent_uri, jsonify_safe, es_get,
    get_resource_class_from_resource_name, flatten_dict, is_csv_file, is_url_encoded_string, to_parent_uri_from_kwargs,
    set_current_user, get_current_user, set_request_url, get_request_url, nested_dict_values, chunks, api_get,
    split_list_by_condition)
from core.concepts.models import Concept
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory
from .fhir_helpers import translate_fhir_query
from .services import S3, PostgresQL
from ..code_systems.serializers import CodeSystemDetailSerializer


class CustomTestRunner(ColourRunnerMixin, DiscoverRunner):
    pass


class SetupTestEnvironment:
    settings.TEST_MODE = True
    settings.ELASTICSEARCH_DSL_AUTOSYNC = True
    settings.ES_SYNC = True


class BaseTestCase(SetupTestEnvironment):
    @staticmethod
    def create_lookup_concept_classes(user=None, org=None):
        from core.sources.tests.factories import OrganizationSourceFactory
        from core.concepts.tests.factories import ConceptNameFactory, ConceptFactory

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


class OCLAPITestCase(APITestCase, BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")
        org = Organization.objects.get(id=1)
        org.members.add(1)


class OCLTestCase(TestCase, BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")
        call_command("loaddata", "core/fixtures/auth_groups.yaml")

    @staticmethod
    def factory_to_params(factory_klass, **kwargs):
        return {
            **factory.build(dict, FACTORY_CLASS=factory_klass),
            **kwargs
        }


class S3Test(TestCase):
    @mock_s3
    def test_upload(self):
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='oclapi2-dev')

        S3._upload('some/path', 'content')  # pylint: disable=protected-access

        self.assertEqual(
            _conn.Object(
                'oclapi2-dev',
                'some/path'
            ).get()['Body'].read().decode("utf-8"),
            'content'
        )

    @mock_s3
    def test_exists(self):
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='oclapi2-dev')

        self.assertFalse(S3.exists('some/path'))

        S3._upload('some/path', 'content')  # pylint: disable=protected-access

        self.assertTrue(S3.exists('some/path'))

    @patch('core.common.services.S3._conn')
    def test_upload_public(self, client_mock):
        conn_mock = Mock()
        conn_mock.upload_fileobj = Mock(return_value='success')
        client_mock.return_value = conn_mock

        self.assertEqual(S3._upload_public('some/path', 'content'), 'success')  # pylint: disable=protected-access

        conn_mock.upload_fileobj.assert_called_once_with(
            'content',
            'oclapi2-dev',
            'some/path',
            ExtraArgs={'ACL': 'public-read'},
        )

    def test_upload_file(self):
        with patch("builtins.open", mock_open(read_data="file-content")) as mock_file:
            S3._upload = Mock(return_value=200)  # pylint: disable=protected-access
            file_path = "path/to/file.ext"
            res = S3.upload_file(key=file_path, headers={'header1': 'val1'})
            self.assertEqual(res, 200)
            S3._upload.assert_called_once_with(file_path, 'file-content', {'header1': 'val1'}, None)  # pylint: disable=protected-access
            mock_file.assert_called_once_with(file_path, 'r')

    @patch('core.common.services.S3._upload')
    def test_upload_base64(self, s3_upload_mock):
        file_content = base64.b64encode(b'file-content')
        uploaded_file_name_with_ext = S3.upload_base64(
            doc_base64='extension/ext;base64,' + file_content.decode(),
            file_name='some-file-name',
        )

        self.assertEqual(
            uploaded_file_name_with_ext,
            'some-file-name.ext'
        )
        mock_calls = s3_upload_mock.mock_calls
        self.assertEqual(len(mock_calls), 1)
        self.assertEqual(
            mock_calls[0][1][0],
            'some-file-name.ext'
        )
        self.assertTrue(
            isinstance(mock_calls[0][1][1], ContentFile)
        )

    @patch('core.common.services.S3._upload_public')
    def test_upload_base64_public(self, s3_upload_mock):
        file_content = base64.b64encode(b'file-content')
        uploaded_file_name_with_ext = S3.upload_base64(
            doc_base64='extension/ext;base64,' + file_content.decode(),
            file_name='some-file-name',
            public_read=True,
        )

        self.assertEqual(
            uploaded_file_name_with_ext,
            'some-file-name.ext'
        )
        mock_calls = s3_upload_mock.mock_calls
        self.assertEqual(len(mock_calls), 1)
        self.assertEqual(
            mock_calls[0][1][0],
            'some-file-name.ext'
        )
        self.assertTrue(
            isinstance(mock_calls[0][1][1], ContentFile)
        )

    @patch('core.common.services.S3._upload')
    def test_upload_base64_no_ext(self, s3_upload_mock):
        file_content = base64.b64encode(b'file-content')
        uploaded_file_name_with_ext = S3.upload_base64(
            doc_base64='extension/ext;base64,' + file_content.decode(),
            file_name='some-file-name',
            append_extension=False,
        )

        self.assertEqual(
            uploaded_file_name_with_ext,
            'some-file-name.jpg'
        )
        mock_calls = s3_upload_mock.mock_calls
        self.assertEqual(len(mock_calls), 1)
        self.assertEqual(
            mock_calls[0][1][0],
            'some-file-name.jpg'
        )
        self.assertTrue(
            isinstance(mock_calls[0][1][1], ContentFile)
        )

    @mock_s3
    def test_remove(self):
        conn = boto3.resource('s3', region_name='us-east-1')
        conn.create_bucket(Bucket='oclapi2-dev')

        S3._upload('some/path', 'content')  # pylint: disable=protected-access
        self.assertEqual(
            conn.Object(
                'oclapi2-dev',
                'some/path'
            ).get()['Body'].read().decode("utf-8"),
            'content'
        )

        S3.remove(key='some/path')

        with self.assertRaises(ClientError):
            conn.Object('oclapi2-dev', 'some/path').get()

    @mock_s3
    def test_url_for(self):
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='oclapi2-dev')

        S3._upload('some/path', 'content')  # pylint: disable=protected-access
        _url = S3.url_for('some/path')

        self.assertTrue(
            'https://oclapi2-dev.s3.amazonaws.com/some/path' in _url
        )
        self.assertTrue(
            '&X-Amz-Credential=' in _url
        )
        self.assertTrue(
            '&X-Amz-Signature=' in _url
        )
        self.assertTrue(
            'X-Amz-Expires=' in _url
        )

    def test_public_url_for(self):
        self.assertEqual(
            S3.public_url_for('some/path').replace('https://', 'http://'),
            'http://oclapi2-dev.s3.amazonaws.com/some/path'
        )

class FhirHelpersTest(OCLTestCase):
    def test_language_to_default_locale(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'language': 'eng'}
        query_set = Concept.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('"concepts"."default_locale" = eng' in str(query_set.query))

    def test_status_retired(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'status': 'retired'}
        query_set = Concept.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('WHERE "concepts"."retired"' in str(query_set.query))

    def test_status_active(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'status': 'active'}
        query_set = Concept.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('WHERE "concepts"."released"' in str(query_set.query))

    def test_status_draft(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'status': 'draft'}
        query_set = Concept.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('WHERE NOT "concepts"."released"' in str(query_set.query))

    def test_title_to_full_name(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'title': 'some title'}
        query_set = Concept.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('WHERE "concepts"."full_name" = some title' in str(query_set.query))

    def test_other_fields(self):
        query_fields = list(CodeSystemDetailSerializer.Meta.fields)
        query_params = {'version': 'v1', 'id': '2'}
        query_set = Concept.objects.all()

        query_set = translate_fhir_query(query_fields, query_params, query_set)
        self.assertTrue('"concepts"."version" = v1' in str(query_set.query))
        self.assertTrue('"concepts"."id" = 2' in str(query_set.query))

class UtilsTest(OCLTestCase):
    def test_set_and_get_current_user(self):
        set_current_user(lambda self: 'foo')
        self.assertEqual(get_current_user(), 'foo')

    def test_set_and_get_request_url(self):
        set_request_url(lambda self: 'https://foobar.org/foo')
        self.assertEqual(get_request_url(), 'https://foobar.org/foo')

    def test_compact_dict_by_values(self):
        self.assertEqual(compact_dict_by_values({}), {})
        self.assertEqual(compact_dict_by_values(dict(foo=None)), {})
        self.assertEqual(compact_dict_by_values(dict(foo=None, bar=None)), {})
        self.assertEqual(compact_dict_by_values(dict(foo=None, bar=1)), dict(bar=1))
        self.assertEqual(compact_dict_by_values(dict(foo=2, bar=1)), dict(foo=2, bar=1))
        self.assertEqual(compact_dict_by_values(dict(foo=2, bar='')), dict(foo=2))

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
            headers=dict(Authorization=f'Token {user.get_token()}')
        )

    @patch('core.common.utils.requests.get')
    def test_es_get(self, http_get_mock):
        http_get_mock.return_value = 'dummy-response'

        self.assertEqual(es_get('some-url', timeout=1), 'dummy-response')

        http_get_mock.assert_called_once_with('http://es:9200/some-url', timeout=1)

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
            dict(uuid=task_uuid + '-', username='username', queue='queue')
        )

        task_id = f"{task_uuid}-username"
        self.assertEqual(
            parse_bulk_import_task_id(task_id),
            dict(uuid=task_uuid + '-', username='username', queue='default')
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
        self.assertEqual(jsonify_safe(dict(a=1)), dict(a=1))
        self.assertEqual(jsonify_safe('foobar'), 'foobar')
        self.assertEqual(jsonify_safe('{"foo": "bar"}'), dict(foo='bar'))

    def test_get_resource_class_from_resource_name(self):
        self.assertEqual(get_resource_class_from_resource_name('mappings').__name__, 'Mapping')
        self.assertEqual(get_resource_class_from_resource_name('sources').__name__, 'Source')
        self.assertEqual(get_resource_class_from_resource_name('source').__name__, 'Source')
        self.assertEqual(get_resource_class_from_resource_name('collections').__name__, 'Collection')
        self.assertEqual(get_resource_class_from_resource_name('collection').__name__, 'Collection')
        for name in ['orgs', 'organizations', 'org', 'ORG']:
            self.assertEqual(get_resource_class_from_resource_name(name).__name__, 'Organization')
        for name in ['user', 'USer', 'user_profile', 'USERS']:
            self.assertEqual(get_resource_class_from_resource_name(name).__name__, 'UserProfile')

    def test_flatten_dict(self):
        self.assertEqual(flatten_dict(dict(foo='bar')), dict(foo='bar'))
        self.assertEqual(flatten_dict(dict(foo=1)), dict(foo='1'))
        self.assertEqual(flatten_dict(dict(foo=1.1)), dict(foo='1.1'))
        self.assertEqual(flatten_dict(dict(foo=True)), dict(foo='True'))
        self.assertEqual(
            flatten_dict(dict(foo=True, bar=dict(tao=dict(te='ching')))),
            dict(foo='True', bar__tao__te='ching')
        )
        self.assertEqual(
            flatten_dict(dict(foo=True, bar=dict(tao=dict(te='tao-te-ching')))),
            dict(foo='True', bar__tao__te='tao_te_ching')
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

    def test_is_url_encoded_string(self):
        self.assertTrue(is_url_encoded_string('foo'))
        self.assertFalse(is_url_encoded_string('foo/bar'))
        self.assertTrue(is_url_encoded_string('foo%2Fbar'))

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
        self.assertEqual(list(nested_dict_values(dict(a=1))), [1])
        self.assertEqual(list(nested_dict_values(dict(a=1, b='foobar'))), [1, 'foobar'])
        self.assertEqual(
            list(nested_dict_values(dict(a=1, b='foobar', c=dict(a=1, b='foobar')))),
            [1, 'foobar', 1, 'foobar']
        )
        self.assertEqual(
            list(
                nested_dict_values(
                    dict(a=1, b='foobar', c=dict(a=1, b='foobar', c=dict(d=[dict(a=1), dict(b='foobar')])))
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

        self.assertEqual(result, dict(error='Invalid JSON (Expecting property name enclosed in double quotes)'))
        import_run_mock.assert_not_called()

    @patch('core.importers.models.BulkImportParallelRunner.run')
    def test_bulk_import_parallel_inline_invalid_without_resource_type(self, import_run_mock):
        content = open(
            os.path.join(os.path.dirname(__file__), '..', 'samples/invalid_import_without_type.json'), 'r').read()

        result = bulk_import_parallel_inline(to_import=content, username='ocladmin', update_if_exists=False)  # pylint: disable=no-value-for-parameter

        self.assertEqual(result, dict(error='Invalid Input ("type" should be present in each line)'))
        import_run_mock.assert_not_called()

    @patch('core.importers.models.BulkImportParallelRunner.run')
    def test_bulk_import_parallel_inline_valid_json(self, import_run_mock):
        import_run_mock.return_value = 'Import Result'
        content = open(os.path.join(os.path.dirname(__file__), '..', 'samples/sample_ocldev.json'), 'r').read()

        result = bulk_import_parallel_inline(to_import=content, username='ocladmin', update_if_exists=False)  # pylint: disable=no-value-for-parameter

        self.assertEqual(result, 'Import Result')
        import_run_mock.assert_called_once()

    @patch('core.common.tasks.EmailMessage')
    def test_monthly_usage_report(self, email_message_mock):
        email_message_instance_mock = Mock(send=Mock(return_value=1))
        email_message_mock.return_value = email_message_instance_mock
        res = monthly_usage_report()

        email_message_mock.assert_called_once()
        email_message_instance_mock.send.assert_called_once()

        self.assertEqual(res, 1)
        call_args = email_message_mock.call_args[1]
        self.assertTrue("Monthly usage report" in call_args['subject'])
        self.assertEqual(call_args['to'], ['reports@openconceptlab.org'])
        self.assertTrue('</html>' in call_args['body'])
        self.assertTrue('concepts' in call_args['body'])
        self.assertTrue('sources' in call_args['body'])
        self.assertTrue('collections' in call_args['body'])
        self.assertEqual(email_message_instance_mock.content_subtype, 'html')


class PostgresQLTest(OCLTestCase):
    @patch('core.common.services.connection')
    def test_create_seq(self, db_connection_mock):
        cursor_context_mock = Mock(execute=Mock())
        cursor_mock = Mock()
        cursor_mock.__enter__ = Mock(return_value=cursor_context_mock)
        cursor_mock.__exit__ = Mock(return_value=None)
        db_connection_mock.cursor = Mock(return_value=cursor_mock)

        self.assertEqual(PostgresQL.create_seq('foobar_seq', 'sources.uri', 1, 100), None)

        db_connection_mock.cursor.assert_called_once()
        cursor_context_mock.execute.assert_called_once_with(
            'CREATE SEQUENCE IF NOT EXISTS foobar_seq MINVALUE 1 START 100 OWNED BY sources.uri;')

    @patch('core.common.services.connection')
    def test_update_seq(self, db_connection_mock):
        cursor_context_mock = Mock(execute=Mock())
        cursor_mock = Mock()
        cursor_mock.__enter__ = Mock(return_value=cursor_context_mock)
        cursor_mock.__exit__ = Mock(return_value=None)
        db_connection_mock.cursor = Mock(return_value=cursor_mock)

        self.assertEqual(PostgresQL.update_seq('foobar_seq', 1567), None)

        db_connection_mock.cursor.assert_called_once()
        cursor_context_mock.execute.assert_called_once_with("SELECT setval('foobar_seq', 1567, true);")

    @patch('core.common.services.connection')
    def test_drop_seq(self, db_connection_mock):
        cursor_context_mock = Mock(execute=Mock())
        cursor_mock = Mock()
        cursor_mock.__enter__ = Mock(return_value=cursor_context_mock)
        cursor_mock.__exit__ = Mock(return_value=None)
        db_connection_mock.cursor = Mock(return_value=cursor_mock)

        self.assertEqual(PostgresQL.drop_seq('foobar_seq'), None)

        db_connection_mock.cursor.assert_called_once()
        cursor_context_mock.execute.assert_called_once_with("DROP SEQUENCE IF EXISTS foobar_seq;")

    @patch('core.common.services.connection')
    def test_next_value(self, db_connection_mock):
        cursor_context_mock = Mock(execute=Mock(), fetchone=Mock(return_value=[1568]))
        cursor_mock = Mock()
        cursor_mock.__enter__ = Mock(return_value=cursor_context_mock)
        cursor_mock.__exit__ = Mock(return_value=None)
        db_connection_mock.cursor = Mock(return_value=cursor_mock)

        self.assertEqual(PostgresQL.next_value('foobar_seq'), 1568)

        db_connection_mock.cursor.assert_called_once()
        cursor_context_mock.execute.assert_called_once_with("SELECT nextval('foobar_seq');")

    @patch('core.common.services.connection')
    def test_last_value(self, db_connection_mock):
        cursor_context_mock = Mock(execute=Mock(), fetchone=Mock(return_value=[1567]))
        cursor_mock = Mock()
        cursor_mock.__enter__ = Mock(return_value=cursor_context_mock)
        cursor_mock.__exit__ = Mock(return_value=None)
        db_connection_mock.cursor = Mock(return_value=cursor_mock)

        self.assertEqual(PostgresQL.last_value('foobar_seq'), 1567)

        db_connection_mock.cursor.assert_called_once()
        cursor_context_mock.execute.assert_called_once_with("SELECT last_value from foobar_seq;")
