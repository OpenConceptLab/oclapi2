from rest_framework.fields import CharField

from core.common.utils import decode_string, is_url_encoded_string, encode_string


class EncodedDecodedCharField(CharField):
    def to_internal_value(self, data):
        string = super().to_internal_value(data)
        return string if is_url_encoded_string(string) else encode_string(string, safe=' ')

    def to_representation(self, value):
        string = super().to_representation(value)
        return decode_string(string) if string else value
