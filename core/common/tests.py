from unittest.mock import patch, Mock, mock_open

import boto3
from botocore.exceptions import ClientError
from colour_runner.django_runner import ColourRunnerMixin
from django.core.management import call_command
from django.test import TestCase
from django.test.runner import DiscoverRunner
from moto import mock_s3

from core.concepts.models import Concept, LocalizedText
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile
from .services import S3


class CustomTestRunner(ColourRunnerMixin, DiscoverRunner):
    pass


class OCLTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("loaddata", "core/fixtures/base_entities.yaml")

    def tearDown(self):
        Concept.objects.all().delete()
        LocalizedText.objects.all().delete()
        Source.objects.all().delete()
        Organization.objects.exclude(id=1).all().delete()
        UserProfile.objects.exclude(id=1).all().delete()


class S3Test(TestCase):
    @mock_s3
    def test_upload(self):
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='ocl-api-dev')

        S3.upload('some/path', 'content')

        self.assertEqual(
            _conn.Object(
                'ocl-api-dev',
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
            'ocl-api-dev',
            'some/path',
            ExtraArgs={'ACL': 'public-read'},
        )

    def test_upload_file(self):
        with patch("builtins.open", mock_open(read_data="file-content")) as mock_file:
            S3.upload = Mock(return_value=200)
            file_path = "path/to/file.ext"
            res = S3.upload_file(file_path, {'header1': 'val1'})
            self.assertEqual(res, 200)
            S3.upload.assert_called_once_with(file_path, 'file-content', {'header1': 'val1'})
            mock_file.assert_called_once_with(file_path, 'r')


    @mock_s3
    def test_remove(self):
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='ocl-api-dev')

        S3.upload('some/path', 'content')
        self.assertEqual(
            _conn.Object(
                'ocl-api-dev',
                'some/path'
            ).get()['Body'].read().decode("utf-8"),
            'content'
        )

        S3.remove(key='some/path')

        with self.assertRaises(ClientError):
            _conn.Object('ocl-api-dev', 'some/path').get()

    @mock_s3
    def test_url_for(self):
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='ocl-api-dev')

        S3.upload('some/path', 'content')
        _url = S3.url_for('some/path')

        self.assertTrue(
            'https://ocl-api-dev.s3.amazonaws.com/some/path' in _url
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
            S3.public_url_for('some/path'),
            'http://ocl-api-dev.s3.amazonaws.com/some/path'
        )
