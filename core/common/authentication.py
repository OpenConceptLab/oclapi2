from rest_framework.authentication import BaseAuthentication, TokenAuthentication


class OCLAuthentication(BaseAuthentication):
    def get_auth_class(self, request):
        from core.common.services import AuthService
        if AuthService.is_valid_django_token(request):
            klass = TokenAuthentication
        else:
            klass = AuthService.get().authentication_class

        return klass()

    def authenticate(self, request):
        return self.get_auth_class(request).authenticate(request)

    def authenticate_header(self, request):
        return self.get_auth_class(request).authenticate_header(request)
