"""Reusable permission helpers for GraphQL resolvers."""

from __future__ import annotations

from functools import wraps
from typing import Any, Awaitable, Callable, Optional, Tuple

from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from strawberry.exceptions import GraphQLError

from core.common.constants import ACCESS_TYPE_NONE
from core.common.permissions import user_can_view_concept_dictionary
from core.common.search import apply_document_public_visibility_filter
from core.orgs.constants import ORG_OBJECT_TYPE
from core.users.constants import USER_OBJECT_TYPE

from .constants import AUTHENTICATION_FAILED, FORBIDDEN, build_expected_graphql_error

SOURCE_VERSION_CACHE_ATTR = '_graphql_source_version_cache'


def resolve_owner(org: Optional[str], owner: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Collapse ``(org, owner)`` into ``(value, type)`` shared by ES filters and ownership routing.

    ``org`` takes precedence: when both are provided (callers are expected to validate this
    upstream), the org form wins. Returns ``(None, None)`` when neither is supplied so callers
    can short-circuit global searches.
    """
    if org:
        return org, ORG_OBJECT_TYPE
    if owner:
        return owner, USER_OBJECT_TYPE
    return None, None


async def ensure_can_view_repo(user, source_version) -> None:
    """Raise a GraphQL forbidden error when the repository is not visible to the user."""
    allowed = await sync_to_async(
        user_can_view_concept_dictionary,
        thread_sensitive=True,
    )(user, source_version)

    if not allowed:
        raise build_expected_graphql_error(FORBIDDEN)


def filter_global_queryset(qs, user):
    """Apply the global visibility rules used by the REST concept and mapping list endpoints.

    Behaviour table:

    | User                              | Filter applied                                |
    |-----------------------------------|-----------------------------------------------|
    | Anonymous                         | ``exclude(public_access=ACCESS_TYPE_NONE)``   |
    | Authenticated non-staff           | ``model.apply_user_criteria`` when available, |
    |                                   | otherwise fail-closed to anonymous filter     |
    | Staff / superuser                 | No filter (full visibility)                   |

    The fail-closed branch matters: if a model is wired into global GraphQL queries without
    implementing ``apply_user_criteria``, we still hide private rows instead of leaking them.
    """
    if getattr(user, 'is_anonymous', True):
        return qs.exclude(public_access=ACCESS_TYPE_NONE)
    if getattr(user, 'is_staff', False):
        return qs
    apply_user_criteria = getattr(qs.model, 'apply_user_criteria', None)
    if apply_user_criteria:
        return apply_user_criteria(qs, user)
    # Fail-closed: a model that does not implement apply_user_criteria must not expose private rows.
    return qs.exclude(public_access=ACCESS_TYPE_NONE)


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
    """Deny repository-scoped access early while allowing global queries to continue.

    Assumes ``self`` is a ``PermissionsMixin`` instance (Strawberry always provides the
    declared root type for class-based resolvers), so no fallback factory is needed.
    """

    @wraps(resolver)
    async def wrapper(self, info, *args, **kwargs):
        if getattr(info.context, 'auth_status', 'none') == 'invalid':
            # Reject invalid credentials before repo resolution so private/public repos look the same.
            raise build_expected_graphql_error(AUTHENTICATION_FAILED)
        org = kwargs.get('org')
        owner = kwargs.get('owner')
        source = kwargs.get('source')
        version = kwargs.get('version')

        if source and (org or owner):
            source_version = await self.get_source_version(info, org, owner, source, version)
            user = getattr(info.context, 'user', AnonymousUser())
            await self.ensure_can_view_repo(user, source_version)

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
