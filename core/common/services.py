import base64
import json

import boto3
import redis
import requests
from botocore.client import Config
from botocore.exceptions import NoCredentialsError, ClientError
from django.conf import settings
from django.core.files.base import ContentFile

from core.settings import REDIS_HOST, REDIS_PORT, REDIS_DB


class S3:
    GET = 'get_object'
    PUT = 'put_object'

    @staticmethod
    def _conn():
        session = S3._session()

        return session.client(
            's3',
            config=Config(region_name=settings.AWS_REGION_NAME, signature_version='s3v4')
        )

    @staticmethod
    def _session():
        return boto3.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

    @classmethod
    def generate_signed_url(cls, accessor, key):
        try:
            _conn = cls._conn()
            return _conn.generate_presigned_url(
                accessor,
                Params={
                    'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                    'Key': key
                },
                ExpiresIn=60*60*24*7,  # a week
            )
        except NoCredentialsError:  # pragma: no cover
            pass

    @classmethod
    def upload(cls, file_path, file_content, headers=None):
        url = cls.generate_signed_url(cls.PUT, file_path)
        result = None
        if url:
            res = requests.put(
                url, data=file_content, headers=headers
            ) if headers else requests.put(url, data=file_content)
            result = res.status_code

        return result

    @classmethod
    def upload_file(cls, key, file_path=None, headers=None, binary=False):
        read_directive = 'rb' if binary else 'r'
        file_path = file_path if file_path else key
        return cls.upload(key, open(file_path, read_directive).read(), headers)

    @classmethod
    def upload_public(cls, file_path, file_content):
        try:
            client = cls._conn()
            return client.upload_fileobj(
                file_content,
                settings.AWS_STORAGE_BUCKET_NAME,
                file_path,
                ExtraArgs={'ACL': 'public-read'},
            )
        except NoCredentialsError:  # pragma: no cover
            pass

    @classmethod
    def upload_base64(  # pylint: disable=too-many-arguments,inconsistent-return-statements
            cls, doc_base64, file_name, append_extension=True, public_read=False, headers=None
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

        doc_data = ContentFile(base64.b64decode(_doc_string))
        if public_read:
            cls.upload_public(file_name_with_ext, doc_data)
        else:
            cls.upload(file_name_with_ext, doc_data, headers)

        return file_name_with_ext

    @classmethod
    def url_for(cls, file_path):
        return cls.generate_signed_url(cls.GET, file_path) if file_path else None

    @classmethod
    def public_url_for(cls, file_path):
        url = "http://{0}.s3.amazonaws.com/{1}".format(
            settings.AWS_STORAGE_BUCKET_NAME,
            file_path,
        )
        if settings.ENV != 'development':
            url = url.replace('http://', 'https://')
        return url

    @classmethod
    def exists(cls, key):
        try:
            cls.resource().meta.client.head_object(Key=key, Bucket=settings.AWS_STORAGE_BUCKET_NAME)
        except (ClientError, NoCredentialsError):
            return False

        return True

    @classmethod
    def __fetch_keys(cls, prefix='/', delimiter='/'):  # pragma: no cover
        prefix = prefix[1:] if prefix.startswith(delimiter) else prefix
        s3_resource = cls.resource()
        objects = s3_resource.meta.client.list_objects(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME, Prefix=prefix
        )
        return [{'Key': k} for k in [obj['Key'] for obj in objects.get('Contents', [])]]

    @classmethod
    def resource(cls):  # pragma: no cover
        return cls._session().resource('s3')

    @classmethod
    def delete_objects(cls, path):  # pragma: no cover
        try:
            s3_resource = cls.resource()
            keys = cls.__fetch_keys(prefix=path)
            if keys:
                s3_resource.meta.client.delete_objects(
                    Bucket=settings.AWS_STORAGE_BUCKET_NAME, Delete=dict(Objects=keys)
                )
        except:  # pylint: disable=bare-except
            pass

    @classmethod
    def missing_objects(cls, objects, prefix_path, sub_paths):  # pragma: no cover
        missing_objects = []

        if not objects:
            return missing_objects

        s3_keys = cls.__fetch_keys(prefix=prefix_path)

        if not s3_keys:
            return objects

        for obj in objects:
            paths = [obj.pdf_path(path) for path in sub_paths]
            if not all([path in s3_keys for path in paths]):
                missing_objects.append(obj)

        return missing_objects

    @classmethod
    def remove(cls, key):
        try:
            _conn = cls._conn()
            return _conn.delete_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=key
            )
        except NoCredentialsError:  # pragma: no cover
            pass


class RedisService:  # pragma: no cover
    def __init__(self):
        self.conn = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)

    def set(self, key, val):
        return self.conn.set(key, val)

    def set_json(self, key, val):
        return self.conn.set(key, json.dumps(val))

    def get_formatted(self, key):
        val = self.get(key)
        if isinstance(val, bytes):
            val = val.decode()

        try:
            val = json.loads(val)
        except:  # pylint: disable=bare-except
            pass

        return val

    def exists(self, key):
        return self.conn.exists(key)

    def get(self, key):
        return self.conn.get(key)

    def keys(self, pattern):
        return self.conn.keys(pattern)

    def get_int(self, key):
        return int(self.conn.get(key).decode('utf-8'))
