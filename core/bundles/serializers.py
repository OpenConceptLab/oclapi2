from rest_framework import serializers
from rest_framework.fields import CharField, JSONField, IntegerField, DateTimeField


class BundleSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    resource_type = CharField(read_only=True)
    bundle_type = CharField(read_only=True)
    timestamp = DateTimeField(read_only=True)
    total = IntegerField(read_only=True)
    entry = JSONField(read_only=True)

    class Meta:
        fields = (
            'resource_type', 'bundle_type', 'timestamp', 'total', 'entry'
        )
