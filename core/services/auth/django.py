from django.contrib.auth.backends import ModelBackend
from rest_framework.authentication import TokenAuthentication

from core.services.auth.core import AbstractAuthService


class DjangoAuthService(AbstractAuthService):
    token_type = 'Token'
    authentication_class = TokenAuthentication
    authentication_backend_class = ModelBackend

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
