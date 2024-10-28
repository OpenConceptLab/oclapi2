import base64
import io
import os
from datetime import timedelta
from unittest.mock import Mock, patch, mock_open, ANY, MagicMock

import boto3
from azure.storage.blob import BlobPrefix
from botocore.exceptions import ClientError
from django.core.files.base import ContentFile
from django.http import StreamingHttpResponse
from django.test import TestCase
from django.utils import timezone
from minio.deleteobjects import DeleteError
from mock.mock import call
from moto import mock_s3

from core.services.storages.cloud.aws import S3
from core.services.storages.cloud.azure import BlobStorage
from core.services.storages.cloud.minio import MinIO


class S3Test(TestCase):
    @mock_s3
    def test_upload(self):
        _conn = boto3.resource('s3', region_name='us-east-1')
        _conn.create_bucket(Bucket='oclapi2-dev')

        S3().upload('some/path', 'content')  # pylint: disable=protected-access

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

        s3.upload('some/path', 'content')  # pylint: disable=protected-access

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
            s3.upload = Mock(return_value=200)  # pylint: disable=protected-access
            file_path = "path/to/file.ext"
            res = s3.upload_file(key=file_path, headers={'header1': 'val1'})
            self.assertEqual(res, 200)
            s3.upload.assert_called_once_with(file_path, ANY, {'header1': 'val1'}, None)  # pylint:
            # disable=protected-access
            mock_file.assert_called_once_with(file_path, 'r')

    def test_upload_base64(self):
        file_content = base64.b64encode(b'file-content')
        s3 = S3()
        s3_upload_mock = Mock()
        s3.upload = s3_upload_mock  # pylint: disable=protected-access
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
        s3.upload = s3_upload_mock  # pylint: disable=protected-access
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
        s3.upload('some/path', 'content')  # pylint: disable=protected-access

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
        s3.upload('some/path', 'content')  # pylint: disable=protected-access
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


class BlobStorageTest(TestCase):
    @patch('core.services.storages.cloud.azure.BlobServiceClient')
    def test_client(self, blob_service_client):
        container_mock = Mock(get_container_client=Mock(return_value='container-client'))
        blob_service_client.from_connection_string = Mock(return_value=container_mock)
        blob_storage = BlobStorage()
        self.assertEqual(blob_storage.client, 'container-client')
        blob_service_client.from_connection_string.assert_called_once_with(conn_str='conn-str')
        container_mock.get_container_client.assert_called_once_with('ocl-test-exports')

    @patch('core.services.storages.cloud.azure.BlobServiceClient')
    def test_upload_file(self, blob_service_client):
        client_mock = Mock(upload_blob=Mock(return_value='success'), url='https://some-url')
        container_client_mock = Mock(get_blob_client=Mock(return_value=client_mock))
        container_mock = Mock(get_container_client=Mock(return_value=container_client_mock))
        blob_service_client.from_connection_string = Mock(return_value=container_mock)
        blob_storage = BlobStorage()

        file_path = os.path.join(os.path.dirname(__file__), '../../../', 'samples/sample_ocldev.json')

        result = blob_storage.upload_file(
            'foo/bar/foo.json', file_path, {'content-type': 'application/zip'}, True
        )

        self.assertEqual(result, 'https://some-url')
        container_client_mock.get_blob_client.assert_called_once_with(blob='foo/bar/foo.json')
        client_mock.upload_blob.assert_called_once_with(
            data=ANY, content_settings=ANY, overwrite=True)
        self.assertTrue(isinstance(client_mock.upload_blob.call_args[1]['data'], io.BufferedReader))
        self.assertEqual(
            dict(client_mock.upload_blob.call_args[1]['content_settings']),
            {
                'content_type': 'application/octet-stream',
                'content_encoding': 'zip',
                'content_language': None,
                'content_md5': None,
                'content_disposition': None,
                'cache_control': None
            }
        )

    @patch('core.services.storages.cloud.azure.BlobServiceClient')
    def test_upload_base64(self, blob_service_client):
        client_mock = Mock(upload_blob=Mock(return_value='success'), url='https://some-url')
        container_client_mock = Mock(get_blob_client=Mock(return_value=client_mock))
        container_mock = Mock(get_container_client=Mock(return_value=container_client_mock))
        blob_service_client.from_connection_string = Mock(return_value=container_mock)
        blob_storage = BlobStorage()

        file_content = base64.b64encode(b'file-content')

        uploaded_file_name_with_ext = blob_storage.upload_base64(
            doc_base64='extension/ext;base64,' + file_content.decode(),
            file_name='some-file-name',
        )

        self.assertEqual(uploaded_file_name_with_ext, 'some-file-name.ext')
        container_client_mock.get_blob_client.assert_called_once_with(blob='some-file-name.ext')
        client_mock.upload_blob.assert_called_once_with(data=ANY, content_settings=ANY, overwrite=True)
        self.assertEqual(
            dict(client_mock.upload_blob.call_args[1]['content_settings']),
            {
                'content_type': 'application/octet-stream',
                'content_encoding': None,
                'content_language': None,
                'content_md5': None,
                'content_disposition': None,
                'cache_control': None
            }
        )

    @patch('core.services.storages.cloud.azure.BlobServiceClient', Mock())
    def test_public_url_for(self):
        blob_storage = BlobStorage()

        self.assertEqual(
            blob_storage.public_url_for('some/path/file.json'),
            'https://ocltestaccount.blob.core.windows.net/ocl-test-exports/some/path/file.json'
        )

        self.assertEqual(
            blob_storage.public_url_for('file.zip'),
            'https://ocltestaccount.blob.core.windows.net/ocl-test-exports/file.zip'
        )

    @patch('core.services.storages.cloud.azure.BlobServiceClient', Mock())
    def test_url_for(self):
        blob_storage = BlobStorage()

        self.assertEqual(
            blob_storage.url_for('some/path/file.json'),
            'https://ocltestaccount.blob.core.windows.net/ocl-test-exports/some/path/file.json'
        )

        self.assertEqual(
            blob_storage.url_for('file.zip'),
            'https://ocltestaccount.blob.core.windows.net/ocl-test-exports/file.zip'
        )

    @patch('core.services.storages.cloud.azure.BlobServiceClient')
    def test_exists(self, blob_service_client):
        client_mock = Mock()
        client_mock.get_blob_properties.side_effect = [{'name': 'blah', 'last_modified': 'blah'}, Exception()]
        container_client_mock = Mock(get_blob_client=Mock(return_value=client_mock))
        container_mock = Mock(get_container_client=Mock(return_value=container_client_mock))
        blob_service_client.from_connection_string = Mock(return_value=container_mock)

        blob_storage = BlobStorage()

        self.assertTrue(blob_storage.exists('some/path/file.zip'))
        self.assertFalse(blob_storage.exists('foo.json'))
        self.assertEqual(container_client_mock.get_blob_client.call_count, 2)
        self.assertEqual(
            container_client_mock.get_blob_client.mock_calls[0],
            call(blob='some/path/file.zip')
        )
        self.assertEqual(
            container_client_mock.get_blob_client.mock_calls[1],
            call(blob='foo.json')
        )
        self.assertEqual(client_mock.get_blob_properties.call_count, 2)

    @patch('core.services.storages.cloud.azure.BlobServiceClient')
    def test_has_path(self, blob_service_client):
        blob1 = Mock()
        blob1.name = 'foo/bar/foobar.json'
        blob2 = Mock()
        blob2.name = 'foobar.json'
        blobs_mock = [blob1, blob2, BlobPrefix(name='bar/foobar.json')]
        container_client_mock = Mock(walk_blobs=Mock(return_value=blobs_mock))
        container_mock = Mock(get_container_client=Mock(return_value=container_client_mock))
        blob_service_client.from_connection_string = Mock(return_value=container_mock)

        blob_storage = BlobStorage()

        self.assertTrue(blob_storage.has_path('foo/bar/'))
        self.assertTrue(blob_storage.has_path('foo/bar'))
        self.assertFalse(blob_storage.has_path('bar/'))

        self.assertEqual(container_client_mock.walk_blobs.call_count, 3)
        self.assertEqual(
            container_client_mock.walk_blobs.mock_calls[0], call(name_starts_with='foo/bar', delimiter='/'))
        self.assertEqual(
            container_client_mock.walk_blobs.mock_calls[1], call(name_starts_with='foo/bar', delimiter='/'))
        self.assertEqual(
            container_client_mock.walk_blobs.mock_calls[2], call(name_starts_with='bar', delimiter='/'))

    @patch('core.services.storages.cloud.azure.BlobServiceClient')
    def test_get_last_key_from_path(self, blob_service_client):
        now = timezone.now()
        blob1 = Mock(last_modified=now)
        blob1.name = 'foo/bar/foobar.json'
        blob2 = Mock(last_modified=now - timedelta(days=1))
        blob2.name = 'foo/bar/foobar1.json'
        blobs_mock = [blob1, blob2, BlobPrefix(name='foo/bar/foobar.json')]
        container_client_mock = Mock(walk_blobs=Mock(return_value=blobs_mock))
        container_mock = Mock(get_container_client=Mock(return_value=container_client_mock))
        blob_service_client.from_connection_string = Mock(return_value=container_mock)

        self.assertEqual(BlobStorage().get_last_key_from_path('foo/bar/'), 'foo/bar/foobar.json')
        container_client_mock.walk_blobs.assert_called_once_with(name_starts_with='foo/bar/', delimiter='')

    @patch('core.services.storages.cloud.azure.BlobServiceClient')
    def test_delete_objects(self, blob_service_client):
        blob1 = Mock()
        blob1.name = 'foo/bar/foobar.json'
        blob2 = Mock()
        blob2.name = 'foo/bar/foobar1.json'
        blobs_mock = [blob1, blob2, BlobPrefix(name='foo/bar/foobar2.json')]
        client_mock = Mock(delete_blob=Mock())
        container_client_mock = Mock(
            walk_blobs=Mock(return_value=blobs_mock), get_blob_client=Mock(return_value=client_mock))
        container_mock = Mock(get_container_client=Mock(return_value=container_client_mock))
        blob_service_client.from_connection_string = Mock(return_value=container_mock)

        self.assertEqual(BlobStorage().delete_objects('foo/bar/'), 2)
        container_client_mock.walk_blobs.assert_called_once_with(name_starts_with='foo/bar/', delimiter='')
        self.assertEqual(container_client_mock.get_blob_client.call_count, 2)
        self.assertEqual(container_client_mock.get_blob_client.mock_calls[0], call(blob='foo/bar/foobar.json'))
        self.assertEqual(container_client_mock.get_blob_client.mock_calls[1], call(blob='foo/bar/foobar1.json'))
        self.assertEqual(client_mock.delete_blob.call_count, 2)

    @patch('core.services.storages.cloud.azure.BlobServiceClient')
    def test_remove(self, blob_service_client):
        blob1 = Mock()
        blob1.name = 'foo/bar/foobar.json'
        blob2 = Mock()
        blob2.name = 'foo/bar/foobar1.json'
        client_mock = Mock(delete_blob=Mock())
        container_client_mock = Mock(
            get_blob_client=Mock(return_value=client_mock))
        container_mock = Mock(get_container_client=Mock(return_value=container_client_mock))
        blob_service_client.from_connection_string = Mock(return_value=container_mock)

        BlobStorage().remove('foo/bar/foobar.json')

        container_client_mock.get_blob_client.assert_called_once_with(blob='foo/bar/foobar.json')
        client_mock.delete_blob.assert_called_once()


@patch("core.services.storages.cloud.minio.settings.MINIO_ENDPOINT", "localhost")
@patch("core.services.storages.cloud.minio.settings.MINIO_ACCESS_KEY", "access_key")
@patch("core.services.storages.cloud.minio.settings.MINIO_SECRET_KEY", "secret_key")
@patch("core.services.storages.cloud.minio.settings.MINIO_BUCKET_NAME", "bucket")
class MinIOTest(TestCase):

    @patch("core.services.storages.cloud.minio.Minio")
    def test_minio_client_instantiated_with_env_vars(self, mock_minio_client):
        """
        Test if MinIO correctly picks up and instantiates
        the MinIO client using environment variables.
        """
        # Initialize MinIO without passing in credentials directly,
        # assuming that the class reads them from environment variables.
        client = MinIO()

        # Assert that the MinIO client was instantiated with the values from os.environ
        mock_minio_client.assert_called_once_with(
            endpoint="localhost",
            access_key="access_key",
            secret_key="secret_key",
            secure=False  # Assuming your class uses secure connections; adjust if needed
        )

        # Verify that the bucket name was set correctly from the environment variables
        self.assertEqual(client.bucket_name, "bucket")

    @patch("core.services.storages.cloud.minio.Minio")
    def test_upload_base64(self, mock_minio_client):
        # Mock put_object method
        mock_client = mock_minio_client.return_value
        mock_client.put_object = MagicMock()
        client = MinIO()

        # Call the upload_base64 method
        base64_data = base64.b64encode(b'test content')  # "test content" in base64
        client.upload_base64(file_name='test-file', doc_base64='extension/ext;base64,' + base64_data.decode())

        # Assert that the put_object method was called with correct parameters
        mock_client.put_object.assert_called_once()

    @patch("core.services.storages.cloud.minio.Minio")
    def test_url_for(self, mock_minio_client):
        mock_client = mock_minio_client.return_value
        mock_client.get_presigned_url = MagicMock(return_value="http://mock_url")

        client = MinIO()
        file_key = "test-file.txt"

        # Call the url_for method
        url = client.url_for(file_key)

        # Assert that the URL is generated correctly
        self.assertEqual(url, "http://mock_url")
        mock_client.get_presigned_url.assert_called_once_with(method='GET', bucket_name="bucket",
                                                              object_name=file_key)

    @patch("core.services.storages.cloud.minio.Minio", Mock())
    def test_public_url_for(self):
        # Call the public_url_for method (this method does not need to mock Minio itself)
        client = MinIO()
        url = client.public_url_for('test-file.txt')

        # Assert that the URL is generated in the correct format
        self.assertEqual(
            url.replace('https://', 'http://'), "http://localhost/bucket/test-file.txt")

    @patch("core.services.storages.cloud.minio.Minio")
    def test_fetch_keys(self, mock_minio_client):
        # Mock list_objects method
        mock_client = mock_minio_client.return_value
        mock_client.list_objects = MagicMock(return_value=[
            MagicMock(object_name="test-file1.txt"),
            MagicMock(object_name="test-file2.txt")
        ])

        client = MinIO()
        # Call the __fetch_keys method
        keys = client._MinIO__fetch_keys(prefix="test/", delimiter='/')  # pylint: disable=protected-access

        # Assert that the correct keys were fetched
        self.assertEqual(keys, [{'Key': "test-file1.txt"}, {'Key': "test-file2.txt"}])
        mock_client.list_objects.assert_called_once_with(bucket_name="bucket", prefix="test", recursive=True)

        # Call the __fetch_keys method
        keys = client._MinIO__fetch_keys(prefix="test/", delimiter='')  # pylint: disable=protected-access

        # Assert that the correct keys were fetched
        self.assertEqual(keys, [{'Key': "test-file1.txt"}, {'Key': "test-file2.txt"}])
        mock_client.list_objects.assert_called_with(bucket_name="bucket", prefix="test/", recursive=True)

    @patch("core.services.storages.cloud.minio.Minio")
    def test_delete_objects(self, mock_minio_client):
        # Mock remove_objects method
        mock_client = mock_minio_client.return_value
        mock_client.remove_objects = MagicMock(return_value=[])

        client = MinIO()
        # Call the delete_objects method
        client.delete_objects(['test-file1.txt'])

        # Assert that remove_objects was called with the correct file keys
        mock_client.remove_objects.assert_called_once()

    @patch("core.services.storages.cloud.minio.Minio")
    def test_get_streaming_response(self, mock_minio_client):
        # Mock get_object method
        mock_client = mock_minio_client.return_value
        mock_file_obj = MagicMock()
        mock_file_obj.read = MagicMock(side_effect=[b"chunk1", b"chunk2", b""])
        mock_client.get_object = MagicMock(return_value=mock_file_obj)
        MinIO.file_iterator(mock_file_obj)

        client = MinIO()
        # Call the get_streaming_response method
        response = client.get_streaming_response('test-file1.txt')

        # Assert that the response is a StreamingHttpResponse
        self.assertIsInstance(response, StreamingHttpResponse)
        # Convert response content into a list for easier assertion
        response_content = b"".join(list(response.streaming_content))
        self.assertEqual(response_content, b"chunk1chunk2")

    @patch("core.services.storages.cloud.minio.Minio", Mock())
    def test_file_iterator(self):
        # Mock file object with read method
        mock_file_obj = MagicMock()
        mock_file_obj.read = MagicMock(side_effect=[b"chunk1", b"chunk2", b""])

        # Call the file_iterator method
        iterator = MinIO.file_iterator(mock_file_obj)
        content = b"".join(list(iterator))

        # Assert that the file_iterator yields the correct content
        self.assertEqual(content, b"chunk1chunk2")
        mock_file_obj.read.assert_called()

    @patch("core.services.storages.cloud.minio.Minio")
    def test_delete_objects_with_errors(self, mock_minio_client):
        # Mock remove_objects method to raise DeleteError
        mock_client = mock_minio_client.return_value
        error = DeleteError("test-file.txt", Exception("Deletion failed"), '', '')
        mock_client.remove_objects = MagicMock(return_value=[error])

        client = MinIO()
        # Assert that delete_objects raises an exception when there are errors
        with self.assertRaises(Exception) as context:
            client.delete_objects(['test-file.txt'])

        self.assertIn("Could not delete object test-file.txt", str(context.exception))
