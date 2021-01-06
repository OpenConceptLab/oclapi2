import base64
import uuid
from unittest.mock import patch, Mock, mock_open

import boto3
from botocore.exceptions import ClientError
from colour_runner.django_runner import ColourRunnerMixin
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase
from django.test.runner import DiscoverRunner
from moto import mock_s3
from requests.auth import HTTPBasicAuth
from rest_framework.test import APITestCase

from core.collections.models import Collection
from core.common.constants import HEAD, OCL_ORG_ID, SUPER_ADMIN_USER_ID
from core.common.utils import (
    compact_dict_by_values, to_snake_case, flower_get, task_exists, parse_bulk_import_task_id,
    to_camel_case,
    drop_version, is_versioned_uri, separate_version, to_parent_uri)
from core.concepts.models import Concept, LocalizedText
from core.mappings.models import Mapping
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile
from .services import S3


def delete_all():
    Collection.objects.all().delete()
    Mapping.objects.all().delete()
    Concept.objects.all().delete()
    LocalizedText.objects.all().delete()
    Source.objects.all().delete()
    Organization.objects.exclude(id=OCL_ORG_ID).all().delete()
    UserProfile.objects.exclude(id=SUPER_ADMIN_USER_ID).all().delete()


class CustomTestRunner(ColourRunnerMixin, DiscoverRunner):
    pass


class PauseElasticSearchIndex:
    settings.TEST_MODE = True
    settings.ELASTICSEARCH_DSL_AUTOSYNC = False
    settings.ES_SYNC = False


class BaseTestCase(PauseElasticSearchIndex):
    @staticmethod
    def create_lookup_concept_classes(user=None, org=None):
        from core.sources.tests.factories import OrganizationSourceFactory
        from core.concepts.tests.factories import LocalizedTextFactory, ConceptFactory

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
            names=[LocalizedTextFactory(name="Diagnosis")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=classes_source, concept_class="Concept Class",
            names=[LocalizedTextFactory(name="Drug")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=classes_source, concept_class="Concept Class",
            names=[LocalizedTextFactory(name="Test")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=classes_source, concept_class="Concept Class",
            names=[LocalizedTextFactory(name="Procedure")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=datatypes_source, concept_class="Datatype",
            names=[LocalizedTextFactory(name="None"), LocalizedTextFactory(name="N/A")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=datatypes_source, concept_class="Datatype",
            names=[LocalizedTextFactory(name="Numeric")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=datatypes_source, concept_class="Datatype",
            names=[LocalizedTextFactory(name="Coded")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=datatypes_source, concept_class="Datatype",
            names=[LocalizedTextFactory(name="Text")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=nametypes_source, concept_class="NameType",
            names=[LocalizedTextFactory(name="FULLY_SPECIFIED"), LocalizedTextFactory(name="Fully Specified")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=nametypes_source, concept_class="NameType",
            names=[LocalizedTextFactory(name="Short"), LocalizedTextFactory(name="SHORT")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=nametypes_source, concept_class="NameType",
            names=[LocalizedTextFactory(name="INDEX_TERM"), LocalizedTextFactory(name="Index Term")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=nametypes_source, concept_class="NameType",
            names=[LocalizedTextFactory(name="None")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=descriptiontypes_source, concept_class="DescriptionType",
            names=[LocalizedTextFactory(name="None")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=descriptiontypes_source, concept_class="DescriptionType",
            names=[LocalizedTextFactory(name="FULLY_SPECIFIED")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=descriptiontypes_source, concept_class="DescriptionType",
            names=[LocalizedTextFactory(name="Definition")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[LocalizedTextFactory(name="SAME-AS"), LocalizedTextFactory(name="Same As")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[LocalizedTextFactory(name="Is Subset of")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[LocalizedTextFactory(name="Different")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[
                LocalizedTextFactory(name="BROADER-THAN"), LocalizedTextFactory(name="Broader Than"),
                LocalizedTextFactory(name="BROADER_THAN")
            ]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[
                LocalizedTextFactory(name="NARROWER-THAN"), LocalizedTextFactory(name="Narrower Than"),
                LocalizedTextFactory(name="NARROWER_THAN")
            ]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[LocalizedTextFactory(name="Q-AND-A")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[LocalizedTextFactory(name="More specific than")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[LocalizedTextFactory(name="Less specific than")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=maptypes_source, concept_class="MapType",
            names=[LocalizedTextFactory(name="Something Else")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[LocalizedTextFactory(name="en")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[LocalizedTextFactory(name="es")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[LocalizedTextFactory(name="fr")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[LocalizedTextFactory(name="tr")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[LocalizedTextFactory(name="Abkhazian")]
        )
        ConceptFactory(
            version=HEAD, updated_by=user, parent=locales_source, concept_class="Locale",
            names=[LocalizedTextFactory(name="English")]
        )


class OCLAPITestCase(APITestCase, BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")

    def tearDown(self):
        super().tearDown()
        delete_all()


class OCLTestCase(TestCase, BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")

    def tearDown(self):
        super().tearDown()
        delete_all()


class S3Test(TestCase):
    @mock_s3
    def test_upload(self):
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='oclapi2-dev')

        S3.upload('some/path', 'content')

        self.assertEqual(
            _conn.Object(
                'oclapi2-dev',
                'some/path'
            ).get()['Body'].read().decode("utf-8"),
            'content'
        )

    @patch('core.common.services.S3._conn')
    def test_upload_public(self, client_mock):
        conn_mock = Mock()
        conn_mock.upload_fileobj = Mock(return_value='success')
        client_mock.return_value = conn_mock

        self.assertEqual(S3.upload_public('some/path', 'content'), 'success')

        conn_mock.upload_fileobj.assert_called_once_with(
            'content',
            'oclapi2-dev',
            'some/path',
            ExtraArgs={'ACL': 'public-read'},
        )

    def test_upload_file(self):
        with patch("builtins.open", mock_open(read_data="file-content")) as mock_file:
            S3.upload = Mock(return_value=200)
            file_path = "path/to/file.ext"
            res = S3.upload_file(key=file_path, headers={'header1': 'val1'})
            self.assertEqual(res, 200)
            S3.upload.assert_called_once_with(file_path, 'file-content', {'header1': 'val1'})
            mock_file.assert_called_once_with(file_path, 'r')

    @patch('core.common.services.S3.upload')
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

    @patch('core.common.services.S3.upload_public')
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

    @patch('core.common.services.S3.upload')
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
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='oclapi2-dev')

        S3.upload('some/path', 'content')
        self.assertEqual(
            _conn.Object(
                'oclapi2-dev',
                'some/path'
            ).get()['Body'].read().decode("utf-8"),
            'content'
        )

        S3.remove(key='some/path')

        with self.assertRaises(ClientError):
            _conn.Object('oclapi2-dev', 'some/path').get()

    @mock_s3
    def test_url_for(self):
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='oclapi2-dev')

        S3.upload('some/path', 'content')
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


class UtilsTest(OCLTestCase):
    def test_compact_dict_by_values(self):
        self.assertEqual(
            compact_dict_by_values(dict()),
            dict()
        )
        self.assertEqual(
            compact_dict_by_values(dict(foo=None)),
            dict()
        )
        self.assertEqual(
            compact_dict_by_values(dict(foo=None, bar=None)),
            dict()
        )
        self.assertEqual(
            compact_dict_by_values(dict(foo=None, bar=1)),
            dict(bar=1)
        )
        self.assertEqual(
            compact_dict_by_values(dict(foo=2, bar=1)),
            dict(foo=2, bar=1)
        )
        self.assertEqual(
            compact_dict_by_values(dict(foo=2, bar='')),
            dict(foo=2)
        )

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
            auth=HTTPBasicAuth(settings.FLOWER_USER, settings.FLOWER_PWD)
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

        task_id = "{}-{}~{}".format(task_uuid, 'username', 'queue')
        self.assertEqual(
            parse_bulk_import_task_id(task_id),
            dict(uuid=task_uuid + '-', username='username', queue='queue')
        )

        task_id = "{}-{}".format(task_uuid, 'username')
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


class BaseModelTest(OCLTestCase):
    def test_model_name(self):
        self.assertEqual(Concept().model_name, 'Concept')
        self.assertEqual(Source().model_name, 'Source')

    def test_app_name(self):
        self.assertEqual(Concept().app_name, 'concepts')
        self.assertEqual(Source().app_name, 'sources')
