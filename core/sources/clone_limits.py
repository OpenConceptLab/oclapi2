"""
Launch guardrails for $clone (OpenConceptLab/ocl_online#230): a per-call budget from the caller's groups
(`clone.resources_per_call`; 0 = unlimited, -1 = blocked, no row = the `preview` value), and one clone at a
time for limited users - a clone runs inside a web worker and can hold it for minutes.
"""
import logging
from contextlib import contextmanager

from django.core.cache import cache
from rest_framework import status
from rest_framework.exceptions import APIException

from core.capabilities.constants import CLONE_RESOURCES_PER_CALL_CAPABILITY_ID

logger = logging.getLogger('oclapi')
CLONE_LOCK_SECONDS = 660  # a little over gunicorn's 600 s worker timeout, so a killed worker's lock expires


class CloneGuardrailError(APIException):
    """`detail` is set directly so numbers stay numbers in the JSON."""
    def __init__(self, detail, status_code=status.HTTP_403_FORBIDDEN):  # pylint: disable=super-init-not-called
        self.detail = detail
        self.status_code = status_code


def get_clone_budget(user):
    """Concepts + mappings one $clone call may create: None = unlimited. Raises CloneGuardrailError if blocked."""
    limit = user.get_capability_limit(CLONE_RESOURCES_PER_CALL_CAPABILITY_ID)
    if limit is None or limit < 0:
        raise CloneGuardrailError({
            'detail': 'You do not have access to clone concepts.',
            'error_code': 'clone_resources_per_call_not_entitled', 'limit': None,
        })
    return limit or None


def clone_limit_error_detail(limit, requested):
    return {
        'detail': f'This clone would create more than {limit:,} concepts and mappings. Your limit is {limit:,} per '
                  f'clone: clone a narrower concept, or fewer cascade levels.',
        'error_code': 'clone_resources_per_call_limit_reached', 'limit': limit, 'requested': requested,
    }


@contextmanager
def clone_lock(user, budget):
    """One clone in flight per limited user (budget not None). Fails open if the cache is unavailable."""
    if budget is None:
        yield
        return
    key = f'clone_in_progress:{user.id}'
    try:
        acquired = cache.add(key, 1, timeout=CLONE_LOCK_SECONDS)
    except Exception:  # pylint: disable=broad-except
        logger.warning('clone lock unavailable; cloning without it', exc_info=True)
        acquired, key = True, None
    if not acquired:
        raise CloneGuardrailError({
            'detail': 'Another clone is still running for your account. Try again when it finishes.',
            'error_code': 'clone_in_progress',
        }, status.HTTP_409_CONFLICT)
    try:
        yield
    finally:
        if key:
            try:
                cache.delete(key)
            except Exception:  # pylint: disable=broad-except
                logger.warning('clone lock release failed', exc_info=True)
