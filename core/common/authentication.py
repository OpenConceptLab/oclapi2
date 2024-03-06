from django.conf import settings
from pydash import get
from rest_framework.authentication import BaseAuthentication, TokenAuthentication


class OCLAuthentication(BaseAuthentication):
    """
    1. configured as settings.DEFAULT_AUTHENTICATION_CLASSES
    2. Uses Django default TokenAuthentication for valid django token request (and for tests)
    3. Uses Auth Service to determine auth class Django/OIDC
    """
    def get_auth_class(self, request):
        from core.services.auth.core import AuthService
        if AuthService.is_valid_django_token(request) or get(settings, 'TEST_MODE', False):
            klass = TokenAuthentication
        else:
            klass = AuthService.get().authentication_class

        return klass()

    def authenticate(self, request):
        return self.get_auth_class(request).authenticate(request)

    def authenticate_header(self, request):
        return self.get_auth_class(request).authenticate_header(request)
