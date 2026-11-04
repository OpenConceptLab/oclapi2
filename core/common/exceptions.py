from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException, AuthenticationFailed


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


class SSOSignInRefused(AuthenticationFailed):
    pass


class DeactivatedAccountLoginRefused(SSOSignInRefused):
    """
    Raised when a Keycloak sign-in's username matches a deactivated account but the claims don't carry that
    account's email, verified by Keycloak (ocl_online#339). The account is left as it was; staff can reactivate it.
    """
    default_code = 'deactivated_account'

    def __init__(self):
        super().__init__(
            'This username belongs to a deactivated OCL account. If it is yours, contact '
            f'{settings.COMMUNITY_EMAIL} and we will restore it.'
        )


class SSOIdentityMismatchRefused(SSOSignInRefused):
    """Raised when a Keycloak sign-in's username matches an account already linked to a different Keycloak user."""
    default_code = 'sso_identity_mismatch'

    def __init__(self):
        super().__init__(
            'This username belongs to an OCL account linked to a different sign-in identity. '
            f'Contact {settings.COMMUNITY_EMAIL} for help.'
        )


class SSOUsernameCaseMismatchRefused(SSOSignInRefused):
    """Raised when a Keycloak sign-in's username matches an account only ignoring case, without its verified email."""
    default_code = 'sso_username_case_mismatch'

    def __init__(self):
        super().__init__(
            'This username belongs to an existing OCL account with different capitalization. If it is yours, '
            f'contact {settings.COMMUNITY_EMAIL} and we will link it to your sign-in.'
        )


class SSOUnlinkedAccountRefused(SSOSignInRefused):
    default_code = 'sso_unlinked_account'

    def __init__(self):
        super().__init__(
            'This username belongs to an existing OCL account that is not linked to your sign-in and does not have '
            f'your verified email. If it is yours, contact {settings.COMMUNITY_EMAIL} and we will link it to your '
            'sign-in.'
        )


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
