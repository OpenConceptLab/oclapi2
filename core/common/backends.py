from celery_once.backends import Redis
from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from pydash import get
from redis import Sentinel


class QueueOnceRedisSentinelBackend(Redis):
    def __init__(self, backend_settings):
        # pylint: disable=super-init-not-called
        self._sentinel = Sentinel(backend_settings['sentinels'])
        self._sentinel_master = backend_settings['sentinels_master']
        self.blocking_timeout = backend_settings.get("blocking_timeout", 1)
        self.blocking = backend_settings.get("blocking", False)

    @property
    def redis(self):
        return self._sentinel.master_for(self._sentinel_master)


class OCLOIDCAuthenticationBackend(OIDCAuthenticationBackend):
    """
    1. overrides Default OIDCAuthenticationBackend
    2. creates/updates user from OID to django on successful auth
    """
    def create_user(self, claims):
        """Return object for a newly created user account."""
        # {
        #     'sub': '<str:uuid>',
        #     'email_verified': <boolean>,
        #     'realm_access': {
        #         'roles': ['offline_access', 'default-roles-ocl', 'uma_authorization']
        #     },
        #     'name': 'Inactive User',
        #     'preferred_username': 'inactive',
        #     'given_name': 'Inactive',
        #     'family_name': 'User',
        #     'email': 'inactive@user.com'
        # }
        from core.users.models import UserProfile
        return UserProfile.objects.create_user(
            claims.get('preferred_username'),
            email=claims.get('email'),
            first_name=claims.get('given_name'),
            last_name=claims.get('family_name'),
            verified=claims.get('email_verified')
        )

    def update_user(self, user, claims):
        user.first_name = claims.get('given_name') or user.first_name
        user.last_name = claims.get('family_name') or user.last_name
        user.email = claims.get('email') or user.email
        user.save()
        return user

    def filter_users_by_claims(self, claims):
        from core.users.models import UserProfile

        username = claims.get('preferred_username')

        if not username:
            return UserProfile.objects.none()

        return UserProfile.objects.filter(username=username)


class OCLAuthenticationBackend(ModelBackend):
    """
    1. authentication backend defined in settings.AUTHENTICATION_BACKENDS.
    2. switches between Django/OID Auth Backends based on type of request
    3. switches to django auth if a valid django token is used in request
    """

    def get_auth_backend(self, request=None):
        if get(self, '_authentication_backend'):
            return get(self, '_authentication_backend')

        from core.common.services import AuthService
        if AuthService.is_valid_django_token(request) or get(settings, 'TEST_MODE', False):
            klass = ModelBackend
        else:
            klass = AuthService.get().authentication_backend_class

        self._authentication_backend = klass()

        return self._authentication_backend

    def authenticate(self, request, username=None, password=None, **kwargs):
        return self.get_auth_backend(request).authenticate(
            request=request, username=username, password=password, **kwargs)

    def get_user(self, user_id):
        return self.get_auth_backend().get_user(user_id=user_id)
