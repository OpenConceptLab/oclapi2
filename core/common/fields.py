from django.db.models import URLField
from django import forms
from django.utils.translation import gettext_lazy as _
from rest_framework.fields import CharField

from core.common.utils import decode_string, is_url_encoded_string, encode_string
from core.common.validators import URIValidator


class EncodedDecodedCharField(CharField):
    def to_internal_value(self, data):
        string = super().to_internal_value(data)
        return string if is_url_encoded_string(string) else encode_string(string, safe=' ')

    def to_representation(self, value):
        string = super().to_representation(value)
        return decode_string(string) if string else value


class URIField(URLField):
    default_validators = [URIValidator()]
    description = _("URI")

    def formfield(self, **kwargs):
        return super().formfield(
            **{
                "form_class": forms.CharField,
                **kwargs,
            }
        )
