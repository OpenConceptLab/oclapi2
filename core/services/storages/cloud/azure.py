import base64

from azure.storage.blob import BlobServiceClient, ContentSettings, BlobPrefix
from django.conf import settings
from django.core.files.base import ContentFile
from pydash import get

from core.services.storages.cloud.core import CloudStorageServiceInterface


class BlobStorage(CloudStorageServiceInterface):
    def __init__(self):
        super().__init__()
        self.account_name = settings.AZURE_STORAGE_ACCOUNT_NAME
        self.container_name = settings.AZURE_STORAGE_CONTAINER_NAME
        self.connection_string = settings.AZURE_STORAGE_CONNECTION_STRING
        self.client = self.__get_container_client()

    def public_url_for(self, file_path):
        return f"https://{self.account_name}.blob.core.windows.net/{self.container_name}/{file_path}"

    def url_for(self, file_path):
        return self.public_url_for(file_path)

    def exists(self, key):
        try:
            self.__get_blob_client(key).get_blob_properties()
            return True
        except:  # pylint: disable=bare-except
            return False

    def has_path(self, prefix='/', delimiter='/'):
        try:
            blobs = self._fetch_blobs(prefix, delimiter)
            return any(blob.name.startswith(prefix) for blob in blobs if not isinstance(blob, BlobPrefix))
        except:  # pylint: disable=bare-except
            return False

    def get_last_key_from_path(self, prefix='/', delimiter=''):
        try:
            if delimiter and not prefix.endswith(delimiter):
                prefix = prefix + delimiter
            blobs = self._fetch_blobs(prefix, delimiter)
            blob_names = [[blob.name, blob.last_modified] for blob in blobs if not isinstance(blob, BlobPrefix)]
            return sorted(
                blob_names, key=lambda x: x[1], reverse=True)[0][0] if len(blob_names) > 1 else blob_names[0][0]
        except:  # pylint: disable=bare-except
            return None

    def delete_objects(self, path):
        count_deleted = 0
        try:
            for blob in self._fetch_blobs(path, ''):
                if not isinstance(blob, BlobPrefix):
                    self._remove(blob.name)
                    count_deleted += 1
            return count_deleted
        except:  # pylint: disable=bare-except
            return count_deleted

    def remove(self, key):
        try:
            return self._remove(key)
        except:  # pylint: disable=bare-except
            pass

        return None

    def upload_file(self, key, file_path=None, headers=None, binary=False, metadata=None):  # pylint: disable=too-many-arguments
        return self._upload(
            blob_name=key,
            file_path=file_path or key,
            read_directive='rb' if binary else 'r',
            metadata=headers or metadata
        )

    def upload_base64(  # pylint: disable=too-many-arguments,inconsistent-return-statements
            self, doc_base64, file_name, append_extension=True, public_read=False, headers=None
    ):
        _format = None
        _doc_string = None
        try:
            _format, _doc_string = doc_base64.split(';base64,')
        except:  # pylint: disable=bare-except # pragma: no cover
            pass

        if not _format or not _doc_string:  # pragma: no cover
            return

        if append_extension:
            file_name_with_ext = file_name + "." + _format.split('/')[-1]
        else:
            if file_name and file_name.split('.')[-1].lower() not in [
                    'pdf', 'jpg', 'jpeg', 'bmp', 'gif', 'png'
            ]:
                file_name += '.jpg'
            file_name_with_ext = file_name

        self._upload(
            blob_name=file_name_with_ext,
            file_content=ContentFile(base64.b64decode(_doc_string)),
            metadata=headers
        )

        return file_name_with_ext

    def _upload(self, blob_name, file_content=None, file_path=None, read_directive=None, metadata=None):  # pylint: disable=too-many-arguments
        if not file_path and not file_content:
            return None
        try:
            content_settings = ContentSettings(content_type='application/octet-stream')
            content_type = get(metadata, 'content-type') or get(metadata, 'ContentType')
            if content_type and 'application/' in content_type:
                content_settings.content_encoding = content_type.split('application/')[1]

            blob_client = self.__get_blob_client(blob_name)
            if file_content:
                self.__upload_content(blob_client, content_settings, file_content)
            else:
                with open(file_path, read_directive or 'r') as data:
                    self.__upload_content(blob_client, content_settings, data)

            return blob_client.url
        except:  # pylint: disable=bare-except
            return None

    def _fetch_blobs(self, prefix, delimiter):
        if delimiter and prefix.endswith(delimiter):
            prefix = prefix[:-1]
        return self.client.walk_blobs(name_starts_with=prefix, delimiter=delimiter)

    def _remove(self, blob_name):
        return self.__get_blob_client(blob_name).delete_blob()

    @staticmethod
    def __upload_content(blob_client, content_settings, file_content):
        blob_client.upload_blob(data=file_content, content_settings=content_settings, overwrite=True)

    def __get_blob_client(self, blob_name):
        return self.client.get_blob_client(blob=blob_name)

    def __get_container_client(self):
        try:
            return BlobServiceClient.from_connection_string(
                conn_str=self.connection_string
            ).get_container_client(self.container_name)
        except:  # pylint: disable=bare-except
            return None
