import base64
import json

import boto3
import redis
import requests
from botocore.client import Config
from botocore.exceptions import NoCredentialsError, ClientError
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import connection
from django.http import Http404
from pydash import get

from core.settings import REDIS_HOST, REDIS_PORT, REDIS_DB


class S3:
    """
    Configured from settings.EXPORT_SERVICE
    """
    GET = 'get_object'
    PUT = 'put_object'

    @classmethod
    def upload_file(
            cls, key, file_path=None, headers=None, binary=False, metadata=None
    ):  # pylint: disable=too-many-arguments
        """Uploads file object"""
        read_directive = 'rb' if binary else 'r'
        file_path = file_path if file_path else key
        return cls._upload(key, open(file_path, read_directive).read(), headers, metadata)

    @classmethod
    def upload_base64(  # pylint: disable=too-many-arguments,inconsistent-return-statements
            cls, doc_base64, file_name, append_extension=True, public_read=False, headers=None
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
            cls._upload_public(file_name_with_ext, doc_data)
        else:
            cls._upload(file_name_with_ext, doc_data, headers)

        return file_name_with_ext

    @classmethod
    def url_for(cls, file_path):
        return cls._generate_signed_url(cls.GET, file_path) if file_path else None

    @classmethod
    def public_url_for(cls, file_path):
        url = f"http://{settings.AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com/{file_path}"
        if settings.ENV != 'development':
            url = url.replace('http://', 'https://')
        return url

    @classmethod
    def exists(cls, key):
        try:
            cls.__resource().meta.client.head_object(Key=key, Bucket=settings.AWS_STORAGE_BUCKET_NAME)
        except (ClientError, NoCredentialsError):
            return False

        return True

    @classmethod
    def delete_objects(cls, path):  # pragma: no cover
        try:
            s3_resource = cls.__resource()
            keys = cls.__fetch_keys(prefix=path)
            if keys:
                s3_resource.meta.client.delete_objects(
                    Bucket=settings.AWS_STORAGE_BUCKET_NAME, Delete=dict(Objects=keys)
                )
        except:  # pylint: disable=bare-except
            pass

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

        return None

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
    def _generate_signed_url(cls, accessor, key, metadata=None):
        params = {
            'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
            'Key': key,
            **(metadata or {})
        }
        try:
            _conn = cls._conn()
            return _conn.generate_presigned_url(
                accessor,
                Params=params,
                ExpiresIn=60*60*24*7,  # a week
            )
        except NoCredentialsError:  # pragma: no cover
            pass

        return None

    @classmethod
    def _upload(cls, file_path, file_content, headers=None, metadata=None):
        """Uploads via file content with file_path as path + name"""
        url = cls._generate_signed_url(cls.PUT, file_path, metadata)
        result = None
        if url:
            res = requests.put(
                url, data=file_content, headers=headers
            ) if headers else requests.put(url, data=file_content)
            result = res.status_code

        return result

    @classmethod
    def _upload_public(cls, file_path, file_content):
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

        return None

    @classmethod
    def __fetch_keys(cls, prefix='/', delimiter='/'):  # pragma: no cover
        prefix = prefix[1:] if prefix.startswith(delimiter) else prefix
        s3_resource = cls.__resource()
        objects = s3_resource.meta.client.list_objects(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Prefix=prefix)
        return [{'Key': k} for k in [obj['Key'] for obj in objects.get('Contents', [])]]

    @classmethod
    def __resource(cls):
        return cls._session().resource('s3')


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


class PostgresQL:
    @staticmethod
    def create_seq(seq_name, owned_by, min_value=0, start=1):
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE SEQUENCE IF NOT EXISTS {seq_name} MINVALUE {min_value} START {start} OWNED BY {owned_by};")

    @staticmethod
    def update_seq(seq_name, start):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT setval('{seq_name}', {start}, true);")

    @staticmethod
    def drop_seq(seq_name):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SEQUENCE IF EXISTS {seq_name};")

    @staticmethod
    def next_value(seq_name):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT nextval('{seq_name}');")
            return cursor.fetchone()[0]

    @staticmethod
    def last_value(seq_name):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT last_value from {seq_name};")
            return cursor.fetchone()[0]


class AbstractAuthService:
    def __init__(self, username=None, password=None, user=None):
        self.username = username
        self.password = password
        self.user = user
        if self.user:
            self.username = self.user.username
        elif self.username:
            self.set_user()

    def set_user(self):
        from core.users.models import UserProfile
        self.user = UserProfile.objects.filter(username=self.username).first()

    def get_token(self):
        pass

    def mark_verified(self, **kwargs):
        return self.user.mark_verified(**kwargs)

    def update_password(self, password):
        return self.user.update_password(password=password)


class DjangoAuthService(AbstractAuthService):
    token_type = 'Token'

    def get_token(self, check_password=True):
        if check_password:
            if not self.user.check_password(self.password):
                return False
        return self.token_type + ' ' + self.user.get_token()

    @staticmethod
    def create_user(_):
        return True

    def logout(self, _):
        pass


class OIDCAuthService(AbstractAuthService):
    token_type = 'Bearer'
    USERS_URL = settings.OIDC_SERVER_INTERNAL_URL + f'/admin/realms/{settings.OIDC_REALM}/users'
    OIDP_ADMIN_TOKEN_URL = settings.OIDC_SERVER_INTERNAL_URL + '/realms/master/protocol/openid-connect/token'

    @staticmethod
    def credential_representation_from_hash(hash_, temporary=False):
        algorithm, hashIterations, salt, hashedSaltedValue = hash_.split('$')

        return {
            'type': 'password',
            'hashedSaltedValue': hashedSaltedValue,
            'algorithm': algorithm.replace('_', '-'),
            'hashIterations': int(hashIterations),
            'salt': base64.b64encode(salt.encode()).decode('ascii').strip(),
            'temporary': temporary
        }

    @classmethod
    def add_user(cls, user, username=None, password=None):
        response = requests.post(
            cls.USERS_URL,
            json=dict(
                enabled=True,
                emailVerified=user.verified,
                firstName=user.first_name,
                lastName=user.last_name,
                email=user.email,
                username=user.username,
                credentials=[cls.credential_representation_from_hash(hash_=user.password)]
            ),
            verify=False,
            headers=OIDCAuthService.get_admin_headers(username=username, password=password)
        )
        if response.status_code == 201:
            return True

        return response.json()

    def get_token(self):
        token = self.user.get_oidc_token(self.password)
        if token is False:
            return token
        return self.token_type + ' ' + get(token, 'access_token')

    @staticmethod
    def get_admin_token(username=None, password=None):
        response = requests.post(
            OIDCAuthService.OIDP_ADMIN_TOKEN_URL,
            data=dict(
                grant_type='password',
                username=username or settings.KEYCLOAK_ADMIN,
                password=password or settings.KEYCLOAK_ADMIN_PASSWORD,
                client_id='admin-cli'
            ),
            verify=False,
        )
        return response.json().get('access_token')

    def logout(self, token):
        user_info = self.get_user_info(token)
        user_id = get(user_info, 'sub')
        if user_id:
            requests.post(
                f"{self.USERS_URL}/{user_id}/logout",
                json=dict(user=user_id, realm=settings.OIDC_REALM),
                headers=self.get_admin_headers()
            )

        return user_info

    @staticmethod
    def get_user_info(token):
        response = requests.post(
            settings.OIDC_OP_USER_ENDPOINT,
            verify=False,
            headers=dict(Authorization=token)
        )
        return response.json()

    @staticmethod
    def exchange_code_for_token(code, redirect_uri):
        response = requests.post(
            settings.OIDC_OP_TOKEN_ENDPOINT,
            data=dict(
                grant_type='authorization_code',
                client_id=settings.OIDC_RP_CLIENT_ID,
                client_secret=settings.OIDC_RP_CLIENT_SECRET,
                code=code,
                redirect_uri=redirect_uri
            )

        )
        return response.json()

    @staticmethod
    def get_admin_headers(**kwargs):
        return dict(Authorization=f'Bearer {OIDCAuthService.get_admin_token(**kwargs)}')

    def get_user_headers(self):
        return dict(Authorization=self.get_token())

    @staticmethod
    def create_user(data):
        response = requests.post(
            OIDCAuthService.USERS_URL,
            json=dict(
                enabled=True,
                emailVerified=False,
                firstName=data.get('first_name'),
                lastName=data.get('last_name'),
                email=data.get('email'),
                username=data.get('username'),
                credentials=[dict(type='password', value=data.get('password'), temporary=False)]
            ),
            verify=False,
            headers=OIDCAuthService.get_admin_headers()
        )
        if response.status_code == 201:
            return True

        return response.json()

    def __get_all_users(self, headers=None):
        response = requests.get(
            self.USERS_URL,
            verify=False,
            headers=headers or self.get_admin_headers()
        )
        return response.json()

    def __get_user_info(self, headers=None):
        users = self.__get_all_users(headers)
        return next((user for user in users if user['username'] == self.username), None)

    def __get_user_id(self, headers=None):
        user_info = self.__get_user_info(headers)
        return get(user_info, 'id')

    def mark_verified(self, **kwargs):
        response = self.update_user(dict(emailVerified=True))
        if response.status_code < 300:
            return super().mark_verified(**kwargs)
        return response.json()

    def update_user(self, data):
        admin_headers = self.get_admin_headers()
        oid_user_id = self.__get_user_id(admin_headers)
        if not oid_user_id:
            raise Http404()
        response = requests.put(
            f"{self.USERS_URL}/{oid_user_id}",
            json=data,
            verify=False,
            headers=admin_headers
        )
        return response

    def update_password(self, password):
        admin_headers = self.get_admin_headers()
        oid_user_id = self.__get_user_id(admin_headers)
        if not oid_user_id:
            raise Http404()

        requests.put(
            f"{self.USERS_URL}/{oid_user_id}/disable-credential-types",
            json=['password'],
            verify=False,
            headers=admin_headers
        )

        response = requests.put(
            f"{self.USERS_URL}/{oid_user_id}/reset-password",
            json=dict(type='password', temporary=False, value=password),
            verify=False,
            headers=admin_headers
        )
        if response.status_code < 300:
            return super().update_password(password)
        return response.json()


class AuthService:
    @staticmethod
    def get(**kwargs):
        if get(settings, 'OIDC_SERVER_URL'):
            return OIDCAuthService(**kwargs)
        return DjangoAuthService(**kwargs)
