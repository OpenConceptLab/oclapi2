from rest_framework import status
from rest_framework.exceptions import APIException

from core.capabilities.constants import (
    CAPABILITY_EXCEEDED_ERROR_CODE, CAPABILITY_NOT_ENTITLED_ERROR_CODE, MAPPER_PROJECTS_CAPABILITY,
)


class UsageRefused(APIException):
    """
    A capability policy refused a use. `detail` is the refusal body, sent as the policy built it: a call site adds
    only its own sentence as `detail`, when the policy gave none.
    An APIException, so a call site that doesn't catch it fails as a 403 rather than a 500.
    """
    status_code = status.HTTP_403_FORBIDDEN
    default_code = 'capability_limit_reached'

    def __init__(self, detail, not_entitled=False):  # pylint: disable=super-init-not-called
        # set directly (as MapProjectCapacityExceeded does), so numbers stay numbers in the JSON
        self.detail = detail
        self.not_entitled = not_entitled


class CapabilityExceeded(UsageRefused):
    """GroupLimitPolicy's refusal: no limit or a blocked one (not entitled), or not enough left."""

    def __init__(self, capability_name, limit, used, requested):
        self.capability_name = capability_name
        self.limit = limit
        self.used = used
        self.requested = requested
        # None = no row anywhere, -1 = blocked: both are "not available", never a count
        not_entitled = limit is None or limit < 0
        super().__init__(
            {
                'error_code': CAPABILITY_NOT_ENTITLED_ERROR_CODE.get(capability_name, 'capability_not_entitled')
                if not_entitled else CAPABILITY_EXCEEDED_ERROR_CODE.get(capability_name, 'capability_limit_reached'),
                'limit': None if not_entitled else limit, 'used': used,
            },
            not_entitled=not_entitled
        )


class MapProjectCapacityExceeded(APIException):
    """
    Raised directly from HasMapProjectCapacity.has_permission rather than
    returning False + BasePermission.message: DRF's normal permission-denied
    path (PermissionDenied(detail=message)) forces every leaf value through
    ErrorDetail/force_str, which would silently turn `limit`/`used` into
    strings in the JSON response. Raising here instead sets `detail` straight
    from __init__, keeping them as real numbers on the wire - matching the
    plain Response(...) the rows/match-operations cap checks still use.
    """
    status_code = status.HTTP_403_FORBIDDEN
    default_code = 'mapper_projects_limit_reached'

    def __init__(self, limit, used):  # pylint: disable=super-init-not-called
        # limit is None means no override and no group row for this user at all - they
        # were never entitled, as opposed to having used up a real configured allowance.
        # -1 = blocked (the kill switch): also "not available", never reported as a count.
        not_entitled = limit is None or limit < 0
        self.detail = {
            'detail': 'You do not have access to create map projects.' if not_entitled else
            'Map project limit reached.',
            'error_code': CAPABILITY_NOT_ENTITLED_ERROR_CODE[MAPPER_PROJECTS_CAPABILITY] if not_entitled else
            CAPABILITY_EXCEEDED_ERROR_CODE[MAPPER_PROJECTS_CAPABILITY],
            'limit': None if not_entitled else limit, 'used': used,
        }
