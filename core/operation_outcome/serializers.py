from rest_framework import serializers
from rest_framework.fields import SerializerMethodField, CharField, ChoiceField

from core.common.serializers import ReadSerializerMixin


class OperationOutcomeIssueSerializer(ReadSerializerMixin, serializers.Serializer):
    severity = ChoiceField(choices=['fatal', 'error', 'warning', 'information'])
    code = CharField()
    diagnostics = CharField(required=False)
    expression = CharField(required=False, many=True)


class OperationOutcomeTextSerializer(ReadSerializerMixin, serializers.Serializer):
    status = CharField()
    div = CharField()


class OperationOutcomeSerializer(ReadSerializerMixin, serializers.Serializer):
    """ https://www.hl7.org/fhir/operationoutcome.html """
    resourceType = SerializerMethodField(method_name='get_resource_type')
    id = CharField(required=False)
    text = OperationOutcomeTextSerializer(required=False)
    issue = OperationOutcomeIssueSerializer(many=True)

    @staticmethod
    def get_resource_type(_):
        return 'Parameters'
