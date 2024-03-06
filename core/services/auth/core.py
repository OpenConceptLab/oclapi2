from django.conf import settings
from pydash import get
from rest_framework.authtoken.models import Token


class AuthService:
    """
    This returns Django or OIDC Auth service based on configured env vars.
    """
    @staticmethod
    def is_sso_enabled():
        return settings.OIDC_SERVER_URL and not get(settings, 'TEST_MODE', False)

    @staticmethod
    def get(**kwargs):
        from core.services.auth.openid import OpenIDAuthService
        from core.services.auth.django import DjangoAuthService

        if AuthService.is_sso_enabled():
            return OpenIDAuthService(**kwargs)
        return DjangoAuthService(**kwargs)

    @staticmethod
    def is_valid_django_token(request):
        authorization_header = request.META.get('HTTP_AUTHORIZATION')
        if authorization_header and authorization_header.startswith('Token '):
            token_key = authorization_header.replace('Token ', '')
            return Token.objects.filter(key=token_key).exists()
        return False


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
