from rest_framework import status
from rest_framework.exceptions import APIException
from django.utils.translation import gettext_lazy as _


class Http409(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = _('Conflict.')
    default_code = 'conflict'


class Http405(APIException):
    status_code = status.HTTP_405_METHOD_NOT_ALLOWED
    default_detail = _('Not Allowed.')
    default_code = 'not_allowed'


class Http400(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = _('Bad Request.')
    default_code = 'bad_request'


class Http403(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = _('Forbidden.')
    default_code = 'forbidden'


class UnpaginatedListLimitReached(APIException):
    """403 for `Compress` (an unpaginated list) over the limit without `users.list_unpaginated` (ocl_online#230)."""
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self, limit, requested):  # pylint: disable=super-init-not-called
        self.detail = {
            'detail': f'Unpaginated (Compress) responses are limited to {limit:,} results; this list has '
                      f'{requested:,}. Page through it with limit and page.',
            'error_code': 'list_unpaginated_limit_reached', 'limit': limit, 'requested': requested,
        }
