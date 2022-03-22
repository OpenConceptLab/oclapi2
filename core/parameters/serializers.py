from rest_framework import serializers
from rest_framework.fields import CharField, SerializerMethodField, BooleanField

from core.common.serializers import ReadSerializerMixin


class ParameterCodingSerializer(ReadSerializerMixin, serializers.Serializer):
    system = CharField()
    code = CharField()


class ParameterSerializer(ReadSerializerMixin, serializers.Serializer):
    name = CharField()
    valueString = CharField(required=False)
    valueCoding = ParameterCodingSerializer(required=False)
    valueBoolean = BooleanField(required=False)


class ParametersSerializer(ReadSerializerMixin, serializers.Serializer):
    resourceType = SerializerMethodField(method_name='get_resource_type')
    parameter = ParameterSerializer(many=True)

    @staticmethod
    def get_resource_type(_):
        return 'Parameters'

    @staticmethod
    def from_concept(concept):
        source = concept.sources.filter(is_latest_version=True).exclude(version='HEAD').first()
        parameters = {
            'parameter': [
                {
                    'name': 'name',
                    'valueString': source.name
                },
                {
                    'name': 'version',
                    'valueString': source.version
                },
                {
                    'name': 'display',
                    'valueString': concept.name if concept.name else concept.display_name
                }
            ]
        }

        return ParametersSerializer(parameters)