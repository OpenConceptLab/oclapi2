from rest_framework import serializers
from rest_framework.fields import CharField, JSONField, IntegerField, DateTimeField


class BundleSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    type = CharField(read_only=True, source='resource_type')
    bundle_type = CharField(read_only=True)
    timestamp = DateTimeField(read_only=True)
    total = IntegerField(read_only=True)
    concepts = IntegerField(read_only=True, source='concepts_count')
    mappings = IntegerField(read_only=True, source='mappings_count')
    entry = JSONField(read_only=True, source='entries')

    class Meta:
        fields = (
            'resource_type', 'bundle_type', 'timestamp', 'total', 'concepts', 'mappings', 'entry'
        )
