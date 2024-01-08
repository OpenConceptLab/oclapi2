import base64
from unittest.mock import Mock, patch, mock_open

import boto3
from botocore.exceptions import ClientError
from django.core.files.base import ContentFile
from django.test import TestCase
from moto import mock_s3

from core.services.storages.cloud.aws import S3


class S3Test(TestCase):
    @mock_s3
    def test_upload(self):
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='oclapi2-dev')

        S3()._upload('some/path', 'content')  # pylint: disable=protected-access

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
        s3 = S3()
        self.assertFalse(s3.exists('some/path'))

        s3._upload('some/path', 'content')  # pylint: disable=protected-access

        self.assertTrue(s3.exists('some/path'))

    def test_upload_public(self):
        conn_mock = Mock(upload_fileobj=Mock(return_value='success'))

        s3 = S3()
        s3._S3__get_connection = Mock(return_value=conn_mock)  # pylint: disable=protected-access
        self.assertEqual(s3._upload_public('some/path', 'content'), 'success')  # pylint: disable=protected-access

        conn_mock.upload_fileobj.assert_called_once_with(
            'content',
            'oclapi2-dev',
            'some/path',
            ExtraArgs={'ACL': 'public-read'},
        )

    def test_upload_file(self):
        with patch("builtins.open", mock_open(read_data="file-content")) as mock_file:
            s3 = S3()
            s3._upload = Mock(return_value=200)  # pylint: disable=protected-access
            file_path = "path/to/file.ext"
            res = s3.upload_file(key=file_path, headers={'header1': 'val1'})
            self.assertEqual(res, 200)
            s3._upload.assert_called_once_with(file_path, 'file-content', {'header1': 'val1'}, None)  # pylint: disable=protected-access
            mock_file.assert_called_once_with(file_path, 'r')

    def test_upload_base64(self):
        file_content = base64.b64encode(b'file-content')
        s3 = S3()
        s3_upload_mock = Mock()
        s3._upload = s3_upload_mock  # pylint: disable=protected-access
        uploaded_file_name_with_ext = s3.upload_base64(
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

    def test_upload_base64_public(self):
        file_content = base64.b64encode(b'file-content')
        s3 = S3()
        s3_upload_mock = Mock()
        s3._upload_public = s3_upload_mock  # pylint: disable=protected-access
        uploaded_file_name_with_ext = s3.upload_base64(
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

    def test_upload_base64_no_ext(self):
        s3_upload_mock = Mock()
        s3 = S3()
        s3._upload = s3_upload_mock  # pylint: disable=protected-access
        file_content = base64.b64encode(b'file-content')
        uploaded_file_name_with_ext = s3.upload_base64(
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

        s3 = S3()
        s3._upload('some/path', 'content')  # pylint: disable=protected-access

        self.assertEqual(
            conn.Object(
                'oclapi2-dev',
                'some/path'
            ).get()['Body'].read().decode("utf-8"),
            'content'
        )

        s3.remove(key='some/path')

        with self.assertRaises(ClientError):
            conn.Object('oclapi2-dev', 'some/path').get()

    @mock_s3
    def test_url_for(self):
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='oclapi2-dev')

        s3 = S3()
        s3._upload('some/path', 'content')  # pylint: disable=protected-access
        _url = s3.url_for('some/path')

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
            S3().public_url_for('some/path').replace('https://', 'http://'),
            'http://oclapi2-dev.s3.amazonaws.com/some/path'
        )
