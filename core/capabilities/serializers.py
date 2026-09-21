from rest_framework import serializers


class CapabilitySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    name = serializers.CharField()
    limit = serializers.SerializerMethodField()
    used = serializers.SerializerMethodField()

    def get_limit(self, obj):
        return self.context['user'].get_capability_limit(obj.id)

    def get_used(self, obj):
        return self.context['user'].get_capability_usage(obj.id)


class UserCapabilityOverrideSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    capability = CapabilitySerializer(read_only=True)
    limit = serializers.IntegerField(min_value=0)
