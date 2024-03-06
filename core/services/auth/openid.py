import base64

import requests
from django.conf import settings
from mozilla_django_oidc.contrib.drf import OIDCAuthentication

from core.common.backends import OCLOIDCAuthenticationBackend
from core.services.auth.core import AbstractAuthService


class OpenIDAuthService(AbstractAuthService):
    """
    Service that interacts with OIDP for:
    1. exchanging auth_code with token
    2. migrating user from django to OIDP
    """
    token_type = 'Bearer'
    authentication_class = OIDCAuthentication
    authentication_backend_class = OCLOIDCAuthenticationBackend
    USERS_URL = settings.OIDC_SERVER_INTERNAL_URL + f'/admin/realms/{settings.OIDC_REALM}/users'
    OIDP_ADMIN_TOKEN_URL = settings.OIDC_SERVER_INTERNAL_URL + '/realms/master/protocol/openid-connect/token'

    @staticmethod
    def get_login_redirect_url(client_id, redirect_uri, state, nonce):
        return f"{settings.OIDC_OP_AUTHORIZATION_ENDPOINT}?" \
               f"response_type=code id_token&" \
               f"client_id={client_id}&" \
               f"state={state}&" \
               f"nonce={nonce}&" \
               f"redirect_uri={redirect_uri}"

    @staticmethod
    def get_registration_redirect_url(client_id, redirect_uri, state, nonce):
        return f"{settings.OIDC_OP_REGISTRATION_ENDPOINT}?" \
               f"response_type=code id_token&" \
               f"client_id={client_id}&" \
               f"state={state}&" \
               f"nonce={nonce}&" \
               f"redirect_uri={redirect_uri}"

    @staticmethod
    def get_logout_redirect_url(id_token_hint, redirect_uri):
        return f"{settings.OIDC_OP_LOGOUT_ENDPOINT}?" \
               f"id_token_hint={id_token_hint}&" \
               f"post_logout_redirect_uri={redirect_uri}"

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
    def add_user(cls, user, username, password):
        response = requests.post(
            cls.USERS_URL,
            json={
                'enabled': True,
                'emailVerified': user.verified,
                'firstName': user.first_name,
                'lastName': user.last_name,
                'email': user.email,
                'username': user.username,
                'credentials': [cls.credential_representation_from_hash(hash_=user.password)]
            },
            verify=False,
            headers=OpenIDAuthService.get_admin_headers(username=username, password=password)
        )
        if response.status_code == 201:
            return True

        return response.json()

    @staticmethod
    def get_admin_token(username, password):
        response = requests.post(
            OpenIDAuthService.OIDP_ADMIN_TOKEN_URL,
            data={
                'grant_type': 'password',
                'username': username,
                'password': password,
                'client_id': 'admin-cli'
            },
            verify=False,
        )
        return response.json().get('access_token')

    @staticmethod
    def exchange_code_for_token(code, redirect_uri, client_id, client_secret):
        response = requests.post(
            settings.OIDC_OP_TOKEN_ENDPOINT,
            data={
                'grant_type': 'authorization_code',
                'client_id': client_id,
                'client_secret': client_secret,
                'code': code,
                'redirect_uri': redirect_uri
            }
        )
        return response.json()

    @staticmethod
    def get_admin_headers(**kwargs):
        return {'Authorization': f'Bearer {OpenIDAuthService.get_admin_token(**kwargs)}'}

    @staticmethod
    def create_user(_):
        """In OID auth, user signup needs to happen in OID first"""
        pass  # pylint: disable=unnecessary-pass
