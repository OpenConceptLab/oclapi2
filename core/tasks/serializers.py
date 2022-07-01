from rest_framework.fields import CharField, JSONField
from rest_framework.serializers import Serializer


class FlowerTaskSerializer(Serializer):  # pylint: disable=abstract-method
    task_id = CharField(read_only=True, source='task-id')
    state = CharField(read_only=True)
    result = JSONField(allow_null=True, read_only=True)
