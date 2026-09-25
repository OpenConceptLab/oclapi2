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


class UnpaginatedListNotEntitled(APIException):
    """403 for the `Compress` header (an unpaginated list) without `users.list_unpaginated` (ocl_online#230)."""
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self):  # pylint: disable=super-init-not-called
        self.detail = {
            'detail': 'Unpaginated (Compress) responses are not available on your plan. '
                      'Page through results with limit and page.',
            'error_code': 'list_unpaginated_not_entitled',
        }
