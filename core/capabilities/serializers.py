from rest_framework import serializers

from core.common.utils import get_capability_policy


class CapabilitySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    name = serializers.CharField()
    limit = serializers.IntegerField(read_only=True, allow_null=True)
    used = serializers.IntegerField(read_only=True, allow_null=True)

    def to_representation(self, instance):
        # every usage field the policy reports, not only `used`
        return {'name': instance.name, **get_capability_policy().describe(self.context['user'], instance.id)}


class UserCapabilityOverrideSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    capability = CapabilitySerializer(read_only=True)
    limit = serializers.IntegerField(min_value=-1)  # 0 = unlimited, -1 = blocked, N > 0 = the limit
