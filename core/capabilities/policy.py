import logging

from django.contrib.auth import get_user_model
from django.db import transaction

from core.capabilities.constants import (
    AUTHORING_CAPABILITY_DEFAULT_LIMITS, AUTHORING_CAPABILITY_IDS, CAPABILITY_NAME_BY_ID,
    MAPPER_PROJECTS_CAPABILITY_ID, MAPPER_ROWS_PER_PROJECT_CAPABILITY_ID,
)
from core.capabilities.exceptions import CapabilityExceeded
from core.capabilities.models import GroupCapability, UsageCounter, UsageEvent
from core.users.constants import PREVIEW_GROUP

logger = logging.getLogger('oclapi')


class CapabilityPolicy:
    """
    Decides and records the use of capabilities. settings.CAPABILITY_POLICY names the class in use, and
    get_capability_policy() returns one.
    """

    def get_limit(self, user, capability_id):
        """None = no access, -1 = blocked, 0 = unlimited, N > 0 = the limit."""
        raise NotImplementedError

    def get_usage(self, user, capability_id):
        """`used` (None where nothing accumulates), plus any other fields the policy reports."""
        raise NotImplementedError

    def get_window_start(self, user, capability_id):  # pylint: disable=unused-argument
        """When the usage window counting now began. None is one window that never ends."""
        return None

    def consume(self, user, capability_id, units=1, *, idempotency_key=None, **attribution):
        """
        Checks and records `units` in one transaction. Returns (usage_event, already_consumed). Raises UsageRefused,
        whose `detail` is the refusal body.
        """
        raise NotImplementedError

    def reverse(self, usage_event, units):
        """Takes back up to `units` of an event's use. Returns the units taken back."""
        raise NotImplementedError

    def describe(self, user, capability_id):
        """What usage reports send: `limit`, then every usage field."""
        return {'limit': self.get_limit(user, capability_id), **self.get_usage(user, capability_id)}


class GroupLimitPolicy(CapabilityPolicy):
    """Limits from a per-user override, else from the user's groups."""

    def get_limit(self, user, capability_id):
        """
        There is no implicit-unlimited fallback: None means no override and no group row for this user, and
        consume() treats it as blocked. A new capability, or a new group, needs its limit configured (even to 0).
        Staff and superusers are the deliberate exception.

        A per-user override wins over every group; else the highest of the groups' limits, where a 0 from any one
        group wins outright.

        For the authoring capabilities (bulk import, $clone), which every account has always had, no row means the
        `preview` group's value, not blocked (ocl_online#230).
        """
        if user.is_superuser or user.is_staff:
            return 0
        override = self._get_capability_limit(user, capability_id)
        if override is not None:
            return override
        group_limits = self._get_group_capability_limit(user, capability_id)
        if not group_limits:
            return self._get_default_authoring_limit(capability_id)
        if 0 in group_limits:
            return 0
        return max(group_limits)

    @staticmethod
    def _get_group_capability_limit(user, capability_id):
        return list(GroupCapability.objects.filter(
            group__in=user.groups.all(), capability_id=capability_id
        ).values_list('limit', flat=True))

    @staticmethod
    def _get_capability_limit(user, capability_id):
        return user.capability_overrides.filter(capability_id=capability_id).values_list('limit', flat=True).first()

    @staticmethod
    def _get_default_authoring_limit(capability_id):
        """None (blocked) unless `capability_id` is an authoring capability, which falls back to `preview`."""
        if capability_id not in AUTHORING_CAPABILITY_IDS:
            return None
        limit = GroupCapability.objects.filter(
            group__name=PREVIEW_GROUP, capability_id=capability_id).values_list('limit', flat=True).first()
        if limit is None:
            logger.warning(
                'The %s group has no limit for capability %s; using the built-in default', PREVIEW_GROUP, capability_id)
            return AUTHORING_CAPABILITY_DEFAULT_LIMITS[capability_id]
        return limit

    def get_usage(self, user, capability_id):
        if capability_id in AUTHORING_CAPABILITY_IDS or capability_id == MAPPER_ROWS_PER_PROJECT_CAPABILITY_ID:
            # per-request and per-project limits: nothing accumulates per user
            return {'used': None}
        if capability_id == MAPPER_PROJECTS_CAPABILITY_ID:
            # enforced against the live count (HasMapProjectCapacity), so deleting a project frees its slot
            return {'used': user.map_projects_used}
        return {'used': UsageCounter.get_used(user, capability_id, self.get_window_start(user, capability_id))}

    def consume(self, user, capability_id, units=1, *, idempotency_key=None, **attribution):
        """
        0 is unlimited and always succeeds; -1 is the kill switch (a per-user override blocks just that user).
        With an `idempotency_key` already consumed, consumes nothing and returns that event, even past the limit.
        """
        limit = self.get_limit(user, capability_id)

        with transaction.atomic():
            # the account first, so a user's requests serialize even when they count in different windows. No key
            # update, so a foreign-key check on the user (another transaction inserting a counter) isn't blocked.
            get_user_model().objects.select_for_update(no_key=True).only('id').get(pk=user.pk)
            counter = UsageCounter.get_locked(user, capability_id, self.get_window_start(user, capability_id))

            # under the lock and before the limit check, so a retry after the last unit still succeeds
            event = UsageEvent.find(user, capability_id, idempotency_key)
            if event:
                return event, True

            if limit is None or (limit and counter.used + units > limit):
                raise self.build_refusal(user, capability_id, limit, counter.used, units)

            return counter.consume(units, idempotency_key=idempotency_key, **attribution), False

    def build_refusal(self, user, capability_id, limit, used, units):  # pylint: disable=too-many-arguments,unused-argument
        return CapabilityExceeded(CAPABILITY_NAME_BY_ID.get(capability_id, capability_id), limit, used, units)

    def reverse(self, usage_event, units):
        return usage_event.reverse(units)
