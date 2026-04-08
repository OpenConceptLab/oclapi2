"""Reusable permission helpers for GraphQL resolvers."""

from __future__ import annotations

from functools import wraps
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional

from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from strawberry.exceptions import GraphQLError

from core.common.constants import ACCESS_TYPE_NONE
from core.common.permissions import CanViewConceptDictionary
from core.common.search import apply_document_public_visibility_filter

from .constants import AUTHENTICATION_FAILED, FORBIDDEN, build_expected_graphql_error

SOURCE_VERSION_CACHE_ATTR = '_graphql_source_version_cache'


def get_permission_target(instance, resolver):
    """Return a resolver helper instance even when Strawberry passes a null root value."""
    if instance is not None:
        return instance

    owner_name = resolver.__qualname__.split('.', 1)[0]
    owner_class = resolver.__globals__.get(owner_name)
    if owner_class is None:
        raise GraphQLError('Resolver permission target is not available.')
    return owner_class()


async def ensure_can_view_repo(user, source_version) -> None:
    """Raise a GraphQL forbidden error when the repository is not visible to the user."""
    request = SimpleNamespace(user=user)
    permission = CanViewConceptDictionary()
    allowed = await sync_to_async(
        permission.has_object_permission,
        thread_sensitive=True,
    )(request, None, source_version)

    if not allowed:
        raise build_expected_graphql_error(FORBIDDEN)


def filter_global_queryset(qs, user):
    """Apply the same global visibility rules used by the REST concept and mapping APIs."""
    if getattr(user, 'is_anonymous', True):
        return qs.exclude(public_access=ACCESS_TYPE_NONE)
    if not getattr(user, 'is_staff', False):
        apply_user_criteria = getattr(qs.model, 'apply_user_criteria', None)
        if apply_user_criteria:
            return apply_user_criteria(qs, user)
    return qs


def apply_es_visibility_filter(search, user):
    """Mirror REST visibility rules in Elasticsearch so totals stay aligned with the DB."""
    return apply_document_public_visibility_filter(
        search,
        user,
        include_owner_private_access=True,
        include_organization_memberships=True,
    )


def check_user_permission(
    resolver: Callable[..., Awaitable[Any]]
) -> Callable[..., Awaitable[Any]]:
    """Deny repository-scoped access early while allowing global queries to continue."""

    @wraps(resolver)
    async def wrapper(self, info, *args, **kwargs):
        permission_target = get_permission_target(self, resolver)
        if getattr(info.context, 'auth_status', 'none') == 'invalid':
            # Reject invalid credentials before repo resolution so private/public repos look the same.
            raise build_expected_graphql_error(AUTHENTICATION_FAILED)
        org = kwargs.get('org')
        owner = kwargs.get('owner')
        source = kwargs.get('source')
        version = kwargs.get('version')

        if source and (org or owner):
            source_version = await permission_target.get_source_version(info, org, owner, source, version)
            user = getattr(info.context, 'user', AnonymousUser())
            await permission_target.ensure_can_view_repo(user, source_version)

        return await resolver(self, info, *args, **kwargs)

    return wrapper


class PermissionsMixin:
    """Provide cached source resolution and shared permission helpers to resolvers."""

    async def resolve_source_version_for_permissions(
        self,
        org: Optional[str],
        owner: Optional[str],
        source: str,
        version: Optional[str],
    ):
        """Allow GraphQL query types to plug in their own source-version resolver."""
        raise NotImplementedError

    async def get_source_version(
        self,
        info,
        org: Optional[str],
        owner: Optional[str],
        source: str,
        version: Optional[str],
    ):
        """Resolve and cache the source version for the current GraphQL request."""
        cache = getattr(info.context, SOURCE_VERSION_CACHE_ATTR, None) or {}
        cache_key = (org, owner, source, version)
        if cache_key in cache:
            return cache[cache_key]

        source_version = await self.resolve_source_version_for_permissions(org, owner, source, version)
        cache[cache_key] = source_version
        setattr(info.context, SOURCE_VERSION_CACHE_ATTR, cache)
        return source_version

    async def ensure_can_view_repo(self, user, source_version) -> None:
        """Delegate repository permission checks to the shared helper."""
        await ensure_can_view_repo(user, source_version)

    def filter_global_queryset(self, qs, user):
        """Delegate global queryset visibility rules to the shared helper."""
        return filter_global_queryset(qs, user)

    def apply_es_visibility_filter(self, search, user):
        """Delegate Elasticsearch visibility rules to the shared helper."""
        return apply_es_visibility_filter(search, user)
