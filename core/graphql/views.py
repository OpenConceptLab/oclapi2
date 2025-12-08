"""
Authenticated GraphQL View

This view integrates header-based authentication for GraphQL endpoints,
using the same DRF authentication stack as REST (Django tokens or OIDC).

The authenticated user is passed in the GraphQL context as 'user'.
"""

from asgiref.sync import sync_to_async
from strawberry.django.views import AsyncGraphQLView
from rest_framework.authentication import get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request
from django.contrib.auth.models import AnonymousUser
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from core.common.authentication import OCLAuthentication


# https://strawberry.rocks/docs/breaking-changes/0.243.0 GraphQL Strawberry needs manually handling CSRF
@method_decorator(csrf_exempt, name='dispatch')
class AuthenticatedGraphQLView(AsyncGraphQLView):
    async def dispatch(self, request, *args, **kwargs):
        """Enforce CSRF unless request supplies an auth token; ensure GraphiQL GET sets a CSRF cookie."""
        auth_header = get_authorization_header(request).split()
        csrf_middleware = CsrfViewMiddleware(lambda req: None)
        require_csrf = not (auth_header and auth_header[0].lower() in (b'token', b'bearer') and len(auth_header) >= 2)

        # For anonymous GraphiQL usage, make sure a CSRF cookie is issued on GET.
        if request.method == 'GET':
            get_token(request)

        if require_csrf:
            # CsrfViewMiddleware expects a get_response callable
            response = await sync_to_async(
                csrf_middleware.process_view,
                thread_sensitive=True
            )(request, None, (), {})
            if response is not None:
                # Still set the cookie if we primed it above.
                response = await sync_to_async(
                    csrf_middleware.process_response,
                    thread_sensitive=True
                )(request, response)
                return response

        response = await super().dispatch(request, *args, **kwargs)

        if require_csrf or request.method == 'GET':
            response = await sync_to_async(
                csrf_middleware.process_response,
                thread_sensitive=True
            )(request, response)

        return response

    async def get_context(self, request, response=None):
        context = await super().get_context(request, response)

        # First, check if user is authenticated via session (e.g., browser login)
        if hasattr(request, 'user') and request.user.is_authenticated:
            context.user = request.user
            context.auth_status = 'valid'
            return context

        # Otherwise, check authorization header
        auth_header = get_authorization_header(request)
        if not auth_header:
            context.user = AnonymousUser()
            context.auth_status = 'none'
            return context

        # Reuse DRF's combined Django/OIDC auth stack for GraphQL requests
        authenticator = OCLAuthentication()
        drf_request = Request(request)

        try:
            auth_result = await sync_to_async(authenticator.authenticate)(drf_request)
        except AuthenticationFailed:
            context.user = AnonymousUser()
            context.auth_status = 'invalid'
            return context

        if not auth_result:
            context.user = AnonymousUser()
            context.auth_status = 'invalid'
            return context

        user, auth = auth_result
        context.user = user
        context.auth = auth
        context.auth_status = 'valid' if getattr(user, 'is_authenticated', False) else 'invalid'

        return context
