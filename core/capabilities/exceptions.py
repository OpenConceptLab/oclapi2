from rest_framework import status
from rest_framework.exceptions import APIException

from core.capabilities.constants import (
    CAPABILITY_EXCEEDED_ERROR_CODE, CAPABILITY_NOT_ENTITLED_ERROR_CODE, MAPPER_PROJECTS_CAPABILITY,
)


class CapabilityExceeded(APIException):
    """
    An APIException (not a plain Exception) so that a call site which forgets to
    catch this - there is more than one check_and_consume_capability() caller -
    fails as a 403 under DRF's default exception handler instead of an uncaught 500.
    Existing callers still catch it explicitly to build their own structured
    {detail, error_code, limit, used} response; this only changes the failure mode
    for whichever call site doesn't.
    """
    status_code = status.HTTP_403_FORBIDDEN
    default_code = 'capability_limit_reached'

    def __init__(self, capability_name, limit, used, requested):  # pylint: disable=super-init-not-called
        self.capability_name = capability_name
        self.limit = limit
        self.used = used
        self.requested = requested
        self.detail = f'{capability_name}: {used}+{requested} > {limit}'


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
        not_entitled = limit is None
        self.detail = {
            'detail': 'You do not have access to create map projects.' if not_entitled else
            'Map project limit reached.',
            'error_code': CAPABILITY_NOT_ENTITLED_ERROR_CODE[MAPPER_PROJECTS_CAPABILITY] if not_entitled else
            CAPABILITY_EXCEEDED_ERROR_CODE[MAPPER_PROJECTS_CAPABILITY],
            'limit': limit, 'used': used,
        }
