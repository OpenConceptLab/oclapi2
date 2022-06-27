import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, validate_ipv6_address
from django.utils.encoding import punycode
from django.utils.translation import gettext_lazy as _


def validate_non_negative(value):
    if value < 0:
        raise ValidationError(_('%(value)s can not be lesser than 0'), params={'value': value})


class URIValidator(RegexValidator):
    """
    1. Most of the code is taken from django.core.validators.URLValidators, except, scheme validation.
    2. URLValidator accepts any scheme that ends with '://' like http://, ftp://, doesn't work with 'ws:' or 'urn:'.
    3. This Validator assumes a scheme is defined before ':'.
    4. It doesn't validate the actual scheme name, ws:foobar.com is valid and foo:foobar.com is also valid.
    """
    regex = r"\w+:(\/?\/?)[^\s]+"
    message = _("Enter a valid URI.")
    flags = re.IGNORECASE
    unsafe_chars = frozenset("\t\r\n")

    def __call__(self, value):
        if not isinstance(value, str):
            raise ValidationError(self.message, code=self.code, params={"value": value})
        if self.unsafe_chars.intersection(value):
            raise ValidationError(self.message, code=self.code, params={"value": value})

        try:
            super().__call__(value)
        except ValidationError as e:
            # Trivial case failed. Try for possible IDN domain
            if value:
                try:
                    scheme, netloc, path, query, fragment = urlsplit(value)  # pylint: disable=unused-variable
                except ValueError as ex:  # for example, "Invalid IPv6 URL"
                    raise ValidationError(
                        self.message, code=self.code, params={"value": value}
                    ) from ex
                try:
                    new_netloc = punycode(netloc)  # IDN -> ACE
                except UnicodeError as ex:  # invalid domain part
                    raise e from ex
                super().__call__(value.replace(netloc, new_netloc))
            else:
                raise
        else:
            # Now verify IPv6 in the netloc part
            host_match = re.search(r"^\[(.+)\](?::\d{1,5})?$", urlsplit(value).netloc)
            if host_match:
                potential_ip = host_match[1]
                try:
                    validate_ipv6_address(potential_ip)
                except ValidationError as ex:
                    raise ValidationError(
                        self.message, code=self.code, params={"value": value}
                    ) from ex

        # The maximum length of a full host name is 253 characters per RFC 1034
        # section 3.1. It's defined to be 255 bytes or less, but this includes
        # one byte for the length of the name and one byte for the trailing dot
        # that's used to indicate absolute names in DNS.
        if len(urlsplit(value).hostname or '') > 253:
            raise ValidationError(self.message, code=self.code, params={"value": value})
