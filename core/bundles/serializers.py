from rest_framework import serializers
from rest_framework.fields import CharField, JSONField, IntegerField


class BundleSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    resource_type = CharField(read_only=True)
    id = CharField(read_only=True)
    meta = JSONField(read_only=True)
    type = CharField(read_only=True)
    total = IntegerField(read_only=True)
    entry = JSONField(read_only=True)

    class Meta:
        fields = (
            'resource_type', 'id', 'meta', 'type', 'total', 'entry'
        )
