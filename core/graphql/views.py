"""
Authenticated GraphQL View

This view integrates header-based authentication for GraphQL endpoints,
similar to how REST endpoints handle authorization tokens.

Supports:
- Django Token authentication (Authorization: Token <token>)
- OIDC Bearer tokens (Authorization: Bearer <token>) - basic support, may need extension for full JWT validation

The authenticated user is passed in the GraphQL context as 'user'.
"""

from asgiref.sync import sync_to_async
from strawberry.django.views import AsyncGraphQLView
from rest_framework.authentication import get_authorization_header
from rest_framework.authtoken.models import Token
from django.contrib.auth.models import AnonymousUser
from django.middleware.csrf import CsrfViewMiddleware
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt


# https://strawberry.rocks/docs/breaking-changes/0.243.0 GraphQL Strawberry needs manually handling CSRF
@method_decorator(csrf_exempt, name='dispatch')
class AuthenticatedGraphQLView(AsyncGraphQLView):
    def dispatch(self, request, *args, **kwargs):
        """Enforce CSRF unless request supplies an auth token."""
        auth_header = get_authorization_header(request).split()
        if not (auth_header and auth_header[0].lower() in (b'token', b'bearer') and len(auth_header) >= 2):
            response = CsrfViewMiddleware().process_view(request, None, (), {})
            if response is not None:
                return response

        return super().dispatch(request, *args, **kwargs)

    async def get_context(self, request, response=None):
        context = await super().get_context(request, response)

        # First, check if user is authenticated via session (e.g., browser login)
        if hasattr(request, 'user') and request.user.is_authenticated:
            context.user = request.user
            context.auth_status = 'valid'
            return context

        # Otherwise, check authorization header
        auth = get_authorization_header(request).split()

        if not auth or auth[0].lower() not in [b'token', b'bearer']:
            context.user = AnonymousUser()
            context.auth_status = 'none'
            return context

        if len(auth) == 1:
            context.user = AnonymousUser()
            context.auth_status = 'invalid'
            return context
        elif len(auth) > 2:
            context.user = AnonymousUser()
            context.auth_status = 'invalid'
            return context

        try:
            token = auth[1].decode()
        except UnicodeError:
            context.user = AnonymousUser()
            context.auth_status = 'invalid'
            return context

        # Handle Django Token authentication
        if auth[0].lower() == b'token':
            try:
                token_obj = await sync_to_async(Token.objects.select_related('user').get)(key=token)
                context.user = token_obj.user
                context.auth_status = 'valid'
            except Token.DoesNotExist:
                context.user = AnonymousUser()
                context.auth_status = 'invalid'
        # Handle OIDC Bearer tokens
        elif auth[0].lower() == b'bearer':
            # For OIDC, basic check - in production, implement full JWT validation
            # using mozilla_django_oidc or similar
            from core.services.auth.core import AuthService
            if await sync_to_async(AuthService.is_sso_enabled)():
                # Placeholder: Assume token is valid if present (extend with proper validation)
                # TODO: Implement JWT decoding and validation for OIDC
                context.user = AnonymousUser()  # For now, treat as anonymous
                context.auth_status = 'invalid'  # Since not implemented
            else:
                context.user = AnonymousUser()
                context.auth_status = 'invalid'

        return context
