import base64

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError
from django.conf import settings
from django.core.files.base import ContentFile
from pydash import get

from core.services.storages.cloud.core import CloudStorageServiceInterface


class S3(CloudStorageServiceInterface):
    """
    Configured from settings.EXPORT_SERVICE
    """
    GET = 'get_object'
    PUT = 'put_object'

    def __init__(self):
        super().__init__()
        self.conn = self.__get_connection()

    def upload_file(
            self, key, file_path=None, headers=None, binary=False, metadata=None
    ):  # pylint: disable=too-many-arguments
        """Uploads file object"""
        read_directive = 'rb' if binary else 'r'
        file_path = file_path if file_path else key
        return self._upload(key, open(file_path, read_directive).read(), headers, metadata)

    def upload_base64(  # pylint: disable=too-many-arguments,inconsistent-return-statements
            self, doc_base64, file_name, append_extension=True, public_read=False, headers=None
    ):
        """Uploads via base64 content with file name"""
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

        doc_data = ContentFile(base64.b64decode(_doc_string))
        if public_read:
            self._upload_public(file_name_with_ext, doc_data)
        else:
            self._upload(file_name_with_ext, doc_data, headers)

        return file_name_with_ext

    def url_for(self, file_path):
        return self._generate_signed_url(self.GET, file_path) if file_path else None

    def public_url_for(self, file_path):
        url = f"http://{settings.AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com/{file_path}"
        if settings.ENV != 'development':
            url = url.replace('http://', 'https://')
        return url

    def exists(self, key):
        try:
            self.__resource().meta.client.head_object(Key=key, Bucket=settings.AWS_STORAGE_BUCKET_NAME)
        except (ClientError, NoCredentialsError):
            return False

        return True

    def has_path(self, prefix='/', delimiter='/'):
        return len(self.__fetch_keys(prefix, delimiter)) > 0

    def get_last_key_from_path(self, prefix='/', delimiter='/'):
        keys = self.__fetch_keys(prefix, delimiter, True)
        key = sorted(keys, key=lambda k: k.get('LastModified'), reverse=True)[0] if len(keys) > 1 else get(keys, '0')
        return get(key, 'Key')

    def delete_objects(self, path):  # pragma: no cover
        try:
            keys = self.__fetch_keys(prefix=path)
            if keys:
                self.__resource().meta.client.delete_objects(
                    Bucket=settings.AWS_STORAGE_BUCKET_NAME, Delete={'Objects': keys})
        except:  # pylint: disable=bare-except
            pass

    def remove(self, key):
        try:
            return self.__get_connection().delete_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=key
            )
        except NoCredentialsError:  # pragma: no cover
            pass

        return None

    # private
    def _generate_signed_url(self, accessor, key, metadata=None):
        params = {
            'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
            'Key': key,
            **(metadata or {})
        }
        try:
            return self.__get_connection().generate_presigned_url(
                accessor,
                Params=params,
                ExpiresIn=60 * 60 * 24 * 7,  # a week
            )
        except NoCredentialsError:  # pragma: no cover
            pass

        return None

    def _upload(self, file_path, file_content, headers=None, metadata=None):
        """Uploads via file content with file_path as path + name"""
        url = self._generate_signed_url(self.PUT, file_path, metadata)
        result = None
        if url:
            res = requests.put(
                url, data=file_content, headers=headers
            ) if headers else requests.put(url, data=file_content)
            result = res.status_code

        return result

    def _upload_public(self, file_path, file_content):
        try:
            return self.__get_connection().upload_fileobj(
                file_content,
                settings.AWS_STORAGE_BUCKET_NAME,
                file_path,
                ExtraArgs={
                    'ACL': 'public-read'
                },
            )
        except NoCredentialsError:  # pragma: no cover
            pass

        return None

    # protected
    def __fetch_keys(self, prefix='/', delimiter='/', verbose=False):  # pragma: no cover
        prefix = prefix[1:] if prefix.startswith(delimiter) else prefix
        s3_resource = self.__resource()
        objects = s3_resource.meta.client.list_objects(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Prefix=prefix)
        content = objects.get('Contents', [])
        if verbose:
            return content
        return [{'Key': k} for k in [obj['Key'] for obj in content]]

    def __resource(self):
        return self.__session().resource('s3')

    def __get_connection(self):
        session = self.__session()

        return session.client(
            's3',
            config=Config(region_name=settings.AWS_REGION_NAME, signature_version='s3v4')
        )

    @staticmethod
    def __session():
        return boto3.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION_NAME
        )
