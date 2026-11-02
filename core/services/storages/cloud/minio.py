import base64
import mimetypes
import os
from io import BytesIO

from django.utils.http import content_disposition_header
from minio import Minio, S3Error
from minio.deleteobjects import DeleteObject
from pydash import get

from core import settings
from core.services.storages.cloud.core import CloudStorageServiceInterface


class MinIO(CloudStorageServiceInterface):
    # Part size used when the upload length is unknown (non seekable stream)
    UNKNOWN_LENGTH_PART_SIZE = 10 * 1024 * 1024

    def __init__(self):
        super().__init__()
        self.endpoint = settings.MINIO_ENDPOINT
        self.external_endpoint = settings.MINIO_EXTERNAL_ENDPOINT or self.endpoint
        self.access_key = settings.MINIO_ACCESS_KEY
        self.secret_key = settings.MINIO_SECRET_KEY
        self.bucket_name = settings.MINIO_BUCKET_NAME
        self.secure = settings.MINIO_SECURE
        self.client = Minio(endpoint=self.endpoint, access_key=self.access_key, secret_key=self.secret_key,
                            secure=self.secure)
        # Dedicated client for presigned URL generation.
        # Uses the external endpoint so the signature carries the publicly accessible host.
        # Region is set explicitly to avoid a network round-trip for region discovery
        # (the external endpoint is not reachable from inside the container).
        # All other operations use self.client (internal endpoint) since they run server-side.
        self._presign_client = Minio(
            endpoint=self.external_endpoint, access_key=self.access_key, secret_key=self.secret_key,
            secure=self.secure, region=settings.MINIO_REGION
        ) if self.external_endpoint != self.endpoint else self.client
        # Ensure the bucket exists
        if not self.client.bucket_exists(self.bucket_name):
            self.client.make_bucket(self.bucket_name)

    @staticmethod
    def __content_type(key, headers, metadata):
        """
        Resolves the content type from the headers, then from the S3 style metadata
        ({'ContentType': ...}) that callers share with S3, then from the key extension.
        """
        header_type = next((v for k, v in (headers or {}).items() if k.lower() == 'content-type'), None)
        return header_type or get(metadata or {}, 'ContentType') or mimetypes.guess_type(key)[0] \
            or 'application/octet-stream'

    @staticmethod
    def __remaining_length(file_content):
        """
        Returns the number of bytes left to read from a seekable file object,
        or None for a non seekable stream (e.g. a raw HTTP response).
        """
        try:
            if not file_content.seekable():
                return None
            position = file_content.tell()
            end = file_content.seek(0, os.SEEK_END)
            file_content.seek(position)
            return end - position
        except (AttributeError, OSError, ValueError):
            return None

    def upload(self, key, file_content, headers=None, metadata=None):
        """
        Uploads a file object to key, streaming it instead of loading it in memory.
        Same contract as the S3 service: returns 204 on success and raises on failure.
        headers/metadata are only used for the content type; the S3 style metadata
        callers pass (e.g. ContentType) is not MinIO user metadata.
        """
        length = self.__remaining_length(file_content)
        # an unknown length (-1) needs an explicit multipart part size (MinIO minimum is 5MiB)
        size_args = {'length': length, 'part_size': 0} if length is not None else {
            'length': -1, 'part_size': self.UNKNOWN_LENGTH_PART_SIZE}
        try:
            self.client.put_object(
                bucket_name=self.bucket_name, object_name=key, data=file_content,
                content_type=self.__content_type(key, headers, metadata), **size_args
            )
            return 204  # Success, no content
        except S3Error as e:
            raise Exception(f"Could not upload {key} to MinIO. Error: {e}") from e  # pylint: disable=broad-exception-raised

    def upload_file(self, key, file_path=None, headers=None, binary=False, metadata=None):  # pylint: disable=too-many-arguments
        """
        Uploads a file to MinIO.
        """
        try:
            result = self.client.fput_object(bucket_name=self.bucket_name, object_name=key, file_path=file_path,
                                             metadata=metadata)
            return result.object_name
        except S3Error as e:
            raise Exception(f"Could not upload file {key} to MinIO. Error: {e}") from e  # pylint: disable=broad-exception-raised

    def upload_base64(self, doc_base64, file_name, append_extension=True, public_read=False, headers=None):  # pylint: disable=too-many-arguments
        """
        Uploads via base64 content with file name
        """
        _format = None
        _doc_string = None
        try:
            _format, _doc_string = doc_base64.split(';base64,')
        except:  # pylint: disable=bare-except # pragma: no cover
            pass

        if not _format or not _doc_string:  # pragma: no cover
            return None

        if append_extension:
            file_name_with_ext = file_name + "." + _format.split('/')[-1]
        else:
            if file_name and file_name.split('.')[-1].lower() not in [
                'pdf', 'jpg', 'jpeg', 'bmp', 'gif', 'png'
            ]:
                file_name += '.jpg'
            file_name_with_ext = file_name

        try:
            # Decode the base64 string
            file_data = base64.b64decode(_doc_string)
            file_size = len(file_data)

            # Guess content type if not provided
            content_type = mimetypes.guess_type(file_name)[0] or 'application/octet-stream'

            # Upload the decoded file to MinIO
            file_stream = BytesIO(file_data)
            self.client.put_object(bucket_name=self.bucket_name, object_name=file_name_with_ext,
                                   data=file_stream, length=file_size, content_type=content_type
                                   )
            return file_name_with_ext
        except S3Error as e:
            raise Exception(f"Could not upload base64 file {file_name_with_ext} to MinIO. Error: {e}") from e  # pylint: disable=broad-exception-raised

    def url_for(self, file_path, filename=None):
        """
        Generates a presigned URL for the given file using the external client so the
        signature is computed with the publicly accessible host (MINIO_EXTERNAL_ENDPOINT).
        Falls back to the internal client when no external endpoint is configured.
        filename, if given, is the download name the response carries.
        """
        response_headers = {
            'response-content-disposition': content_disposition_header(True, filename)
        } if filename else None
        try:
            return self._presign_client.get_presigned_url(
                method='GET', bucket_name=self.bucket_name, object_name=file_path, response_headers=response_headers
            ) if file_path else None
        except S3Error as e:
            raise Exception(f"Could not generate presigned URL for file {file_path}. Error: {e}") from e  # pylint: disable=broad-exception-raised

    def public_url_for(self, file_path):
        """
        Generates a public URL to access the file.
        """
        try:
            # Public URL for MinIO generally follows this format
            url = f"http://{self.endpoint}/{self.bucket_name}/{file_path}"
            if settings.ENV != 'development':
                url = url.replace('http://', 'https://')
            return url
        except S3Error as e:
            raise Exception(f"Could not generate public URL for file {file_path}. Error: {e}") from e  # pylint: disable=broad-exception-raised

    def exists(self, key):
        """
        Check whether a file exists in MinIO.
        """
        try:
            self.client.stat_object(bucket_name=self.bucket_name, object_name=key)
            return True
        except:  # pylint: disable=bare-except
            return False

    def has_path(self, prefix='/', delimiter='/'):
        return len(self.__fetch_keys(prefix, delimiter)) > 0

    def get_last_key_from_path(self, prefix='/', delimiter='/'):
        """
        Fetches the last object key under the given prefix.
        """
        try:
            keys = self.__fetch_keys(prefix, delimiter)
            key = sorted(keys, key=lambda k: k.get('LastModified'), reverse=True)[0] if len(keys) > 1 else get(keys,
                                                                                                               '0')
            return get(key, 'Key')
        except S3Error as e:
            raise Exception(f"Could not fetch last key from path {prefix}. Error: {e}") from e  # pylint: disable=broad-exception-raised

    def delete_objects(self, path):
        """
        Deletes multiple objects from MinIO.
        """
        try:
            delete_object_list = map(
                lambda x: DeleteObject(x.object_name),
                self.client.list_objects(bucket_name=self.bucket_name, prefix=path, recursive=True),
            )
            errors = self.client.remove_objects(bucket_name=self.bucket_name, delete_object_list=delete_object_list)

            for error in errors:
                raise Exception(f"Could not delete object {error.code}. {error.message}. Error: {error}")  # pylint: disable=broad-exception-raised
        except S3Error as e:
            raise Exception(f"Error occurred during delete operation. Error: {e}") from e  # pylint: disable=broad-exception-raised

    def remove(self, key):
        """
        Deletes a file from MinIO.
        """
        try:
            self.client.remove_object(bucket_name=self.bucket_name, object_name=key)
        except S3Error as e:
            raise Exception(f"Could not delete file {key} from MinIO. Error: {e}") from e  # pylint: disable=broad-exception-raised

    def __fetch_keys(self, prefix, delimiter):
        """
        Fetches all object keys under a given prefix.
        """
        try:
            if delimiter and prefix.endswith(delimiter):
                prefix = prefix[:-1]
            objects = self.client.list_objects(bucket_name=self.bucket_name, prefix=prefix, recursive=True)
            return [{'Key': k} for k in [obj.object_name for obj in objects]]
        except S3Error as e:
            raise Exception(f"Could not fetch keys from bucket {self.bucket_name}. Error: {e}") from e  # pylint: disable=broad-exception-raised

    def get_object(self, key):
        """
        Gets an object from MinIO.
        """
        response = self.client.get_object(bucket_name=self.bucket_name, object_name=key)
        return response

    @staticmethod
    def file_iterator(file_obj, chunk_size=8192):
        """
        Generator that yields chunks of the file to be streamed.
        """
        try:
            while True:
                data = file_obj.read(chunk_size)
                if not data:
                    break
                yield data
        finally:
            file_obj.close()
