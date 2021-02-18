import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

REGEX = "(?=.*[0-9])(?=.*[a-zA-Z])(?=\S+$)."


class AlphaNumericPasswordValidator:
    """
    Validate whether the password is alphanumeric.
    """
    def validate(self, password, user=None):
        if re.match(REGEX, password) is None:
            raise ValidationError(
                _("This password is not alphanumeric."),
                code='password_not_alphanumeric',
            )

    def get_help_text(self):
        return _('Your password has to be alphanumeric.')
