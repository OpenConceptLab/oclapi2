import base64
import logging

import requests
from django.conf import settings
from django.core.cache import cache
from mozilla_django_oidc.contrib.drf import OIDCAuthentication

from core.common.backends import OCLOIDCAuthenticationBackend
from core.services.auth.core import AbstractAuthService


logger = logging.getLogger('oclapi')


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
    GROUPS_URL = settings.OIDC_SERVER_INTERNAL_URL + f'/admin/realms/{settings.OIDC_REALM}/groups'
    SERVICE_TOKEN_CACHE_KEY = 'oidc_service_account_token'
    ADMIN_TIMEOUT = 15
    PAGE_SIZE = 200
    OIDP_ADMIN_TOKEN_URL = settings.OIDC_SERVER_INTERNAL_URL + '/realms/master/protocol/openid-connect/token'

    @staticmethod
    def get_login_redirect_url(client_id, redirect_uri, state, nonce, code_challenge=None, code_challenge_method=None):  # pylint: disable=line-too-long,too-many-arguments
        # response_type=code only (no id_token): a pure code flow never puts the id_token in the
        # callback URL/access logs. scope=openid is requested explicitly so the token-exchange
        # response still includes id_token in its body, where the frontend now reads it from.
        url = f"{settings.OIDC_OP_AUTHORIZATION_ENDPOINT}?" \
              f"response_type=code&" \
              f"scope=openid profile email&" \
              f"client_id={client_id}&" \
              f"state={state}&" \
              f"nonce={nonce}&" \
              f"redirect_uri={redirect_uri}"
        if code_challenge and code_challenge_method:
            url += f"&code_challenge={code_challenge}&code_challenge_method={code_challenge_method}"
        return url

    @staticmethod
    def get_reset_password_redirect_url(client_id, redirect_uri, code_challenge=None, code_challenge_method=None):
        url = f"{settings.OIDC_OP_AUTHORIZATION_ENDPOINT}?" \
              f"scope=openid&" \
              f"kc_action=UPDATE_PASSWORD&" \
              f"response_type=code&" \
              f"client_id={client_id}&" \
              f"redirect_uri={redirect_uri}"
        if code_challenge and code_challenge_method:
            url += f"&code_challenge={code_challenge}&code_challenge_method={code_challenge_method}"
        return url

    @staticmethod
    def get_registration_redirect_url(client_id, redirect_uri, state, nonce, code_challenge=None, code_challenge_method=None):  # pylint: disable=line-too-long,too-many-arguments
        # See get_login_redirect_url: pure code flow + explicit openid scope, id_token comes
        # from the token-exchange response body instead of the callback URL.
        url = f"{settings.OIDC_OP_REGISTRATION_ENDPOINT}?" \
              f"response_type=code&" \
              f"scope=openid profile email&" \
              f"client_id={client_id}&" \
              f"state={state}&" \
              f"nonce={nonce}&" \
              f"redirect_uri={redirect_uri}"
        if code_challenge and code_challenge_method:
            url += f"&code_challenge={code_challenge}&code_challenge_method={code_challenge_method}"
        return url

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
    def exchange_code_for_token(code, redirect_uri, client_id, client_secret=None, code_verifier=None):
        data = {
            'grant_type': 'authorization_code',
            'client_id': client_id,
            'code': code,
            'redirect_uri': redirect_uri
        }
        if client_secret:
            data['client_secret'] = client_secret
        if code_verifier:
            data['code_verifier'] = code_verifier
        response = requests.post(settings.OIDC_OP_TOKEN_ENDPOINT, data=data)
        return response.json()

    @staticmethod
    def get_admin_headers(**kwargs):
        return {'Authorization': f'Bearer {OpenIDAuthService.get_admin_token(**kwargs)}'}

    @staticmethod
    def is_admin_configured():
        return bool(settings.OIDC_ADMIN_CLIENT_ID and settings.OIDC_ADMIN_CLIENT_SECRET)

    @classmethod
    def get_service_token(cls):
        token = cache.get(cls.SERVICE_TOKEN_CACHE_KEY)
        if token:
            return token
        response = requests.post(
            settings.OIDC_OP_TOKEN_ENDPOINT,
            data={
                'grant_type': 'client_credentials',
                'client_id': settings.OIDC_ADMIN_CLIENT_ID,
                'client_secret': settings.OIDC_ADMIN_CLIENT_SECRET,
            },
            timeout=cls.ADMIN_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        token = data['access_token']
        cache.set(cls.SERVICE_TOKEN_CACHE_KEY, token, max(int(data.get('expires_in', 60)) - 30, 1))
        return token

    @classmethod
    def _admin_request(cls, method, url, **kwargs):
        response = requests.request(
            method, url, headers={'Authorization': f'Bearer {cls.get_service_token()}'},
            timeout=cls.ADMIN_TIMEOUT, **kwargs)
        response.raise_for_status()
        return response

    @classmethod
    def get_keycloak_user(cls, username):
        users = cls._admin_request('GET', cls.USERS_URL, params={'username': username, 'exact': 'true'}).json()
        return next((user for user in users if user.get('username') == username), None)

    @classmethod
    def update_user_profile(cls, username, first_name, last_name, company, location):  # pylint: disable=too-many-arguments
        kc_user = cls.get_keycloak_user(username)
        if not kc_user:
            raise LookupError(f'{username} not found in Keycloak')
        attributes = kc_user.get('attributes') or {}
        for key, value in (('company', company), ('location', location)):
            attributes[key] = [value] if value else []
        kc_user.update({'firstName': first_name, 'lastName': last_name, 'attributes': attributes})
        cls._admin_request('PUT', f"{cls.USERS_URL}/{kc_user['id']}", json=kc_user)

    @classmethod
    def _paginate(cls, url):
        first, results = 0, []
        while True:
            page = cls._admin_request('GET', url, params={'first': first, 'max': cls.PAGE_SIZE}).json()
            results.extend(page)
            if len(page) < cls.PAGE_SIZE:
                return results
            first += cls.PAGE_SIZE

    @classmethod
    def get_group_memberships(cls):
        memberships = {}
        for group in cls._paginate(cls.GROUPS_URL):
            memberships[group['name']] = {
                member['username'] for member in cls._paginate(f"{cls.GROUPS_URL}/{group['id']}/members")}
        return memberships

    @staticmethod
    def create_user(_):
        """In OID auth, user signup needs to happen in OID first"""
        pass  # pylint: disable=unnecessary-pass
