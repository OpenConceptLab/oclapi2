from rest_framework import serializers
from rest_framework.fields import CharField, SerializerMethodField, BooleanField, IntegerField

from core.common.serializers import ReadSerializerMixin

# The R4 value types of operation inputs that are read from a valueString too: some clients send a code or a URI
# as a plain string.
STRING_TOLERANT_VALUE_TYPES = ('valueCode', 'valueUri')


class ParameterCodingSerializer(ReadSerializerMixin, serializers.Serializer):
    system = CharField()
    code = CharField()


class PartParameterSerializer(ReadSerializerMixin, serializers.Serializer):
    name = CharField()
    valueString = CharField(required=False)
    valueCoding = ParameterCodingSerializer(required=False)
    valueBoolean = BooleanField(required=False)
    valueUri = CharField(required=False)
    valueCode = CharField(required=False)


class ParameterSerializer(ReadSerializerMixin, serializers.Serializer):
    name = CharField()
    part = PartParameterSerializer(many=True, required=False)
    valueString = CharField(required=False)
    valueCoding = ParameterCodingSerializer(required=False)
    valueBoolean = BooleanField(required=False)
    valueUri = CharField(required=False)
    valueCode = CharField(required=False)
    valueInteger = IntegerField(required=False)


class ParametersSerializer(ReadSerializerMixin, serializers.Serializer):
    resourceType = SerializerMethodField(method_name='get_resource_type')
    parameter = ParameterSerializer(many=True)
    allowed_input_parameters = {}

    @staticmethod
    def get_resource_type(_):
        return 'Parameters'

    @classmethod
    def parse_query_params(cls, query_params):
        parameters = []
        for key, value in query_params.items():
            if key in cls.allowed_input_parameters:
                parameters.append(
                    {
                        'name': key,
                        cls.allowed_input_parameters[key]: value
                    }
                )

        if parameters:
            return cls(data={'parameter': parameters})
        return cls(data={'parameter': []})

    def to_internal_value(self, data):
        parameters = {}

        for parameter in data.get('parameter', []):
            name = parameter.get('name', None)
            value_type = self.allowed_input_parameters.get(name, None) if name else None
            if value_type:
                value = parameter.get(value_type, None)
                if value is None and value_type in STRING_TOLERANT_VALUE_TYPES:
                    value = parameter.get('valueString', None)
                if value:
                    parameters[name] = value

        return {'parameters': parameters}
