from rest_framework import status
from rest_framework.exceptions import APIException

from core.capabilities.constants import CAPABILITY_EXCEEDED_ERROR_CODE, MAPPER_PROJECTS_CAPABILITY


class CapabilityExceeded(Exception):
    def __init__(self, capability_name, limit, used, requested):
        self.capability_name = capability_name
        self.limit = limit
        self.used = used
        self.requested = requested
        super().__init__(f'{capability_name}: {used}+{requested} > {limit}')


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
        self.detail = {
            'detail': 'Map project limit reached.',
            'error_code': CAPABILITY_EXCEEDED_ERROR_CODE[MAPPER_PROJECTS_CAPABILITY],
            'limit': limit, 'used': used,
        }
