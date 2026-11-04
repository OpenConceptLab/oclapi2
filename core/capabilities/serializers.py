from django.utils.functional import cached_property
from rest_framework import serializers

from core.capabilities.policy import get_capability_policy


class CapabilitySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    name = serializers.CharField()
    limit = serializers.IntegerField(read_only=True, allow_null=True)
    used = serializers.IntegerField(read_only=True, allow_null=True)

    @cached_property
    def policy(self):
        # one for a whole list (many=True serializes every item with one of these), so it's one snapshot
        return get_capability_policy()

    def to_representation(self, instance):
        # every usage field the policy reports, not only `used`
        return {'name': instance.name, **self.policy.describe(self.context['user'], instance.id)}


class UserCapabilityOverrideSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    capability = CapabilitySerializer(read_only=True)
    limit = serializers.IntegerField(min_value=-1)  # 0 = unlimited, -1 = blocked, N > 0 = the limit
