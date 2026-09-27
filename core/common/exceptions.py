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


class BatchIndexingError(Exception):
    """
    Raised once a batched ES indexing run has attempted every batch and one or more failed (ocl_online#241).
    `summary` holds the run's counts; `rejected` is True if ES was still refusing writes (429 / read-only index)
    after the retries, when falling back to a heavier reindex would only fail the same way.
    """
    def __init__(self, message, summary=None, rejected=False):
        super().__init__(message)
        self.summary = summary
        self.rejected = rejected


class UnpaginatedListLimitReached(APIException):
    """403 for `Compress` (an unpaginated list) over the limit without `users.list_unpaginated` (ocl_online#230)."""
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self, limit, requested):  # pylint: disable=super-init-not-called
        self.detail = {
            'detail': f'Unpaginated (Compress) responses are limited to {limit:,} results; this list has '
                      f'{requested:,}. Page through it with limit and page.',
            'error_code': 'list_unpaginated_limit_reached', 'limit': limit, 'requested': requested,
        }
