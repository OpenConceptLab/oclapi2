from rest_framework import serializers


class CapabilitySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Renders one Capability for the user passed in via context['user'] - limit/used
    are per-user, computed at serialization time, not stored on Capability itself.
    """
    name = serializers.CharField()
    limit = serializers.SerializerMethodField()
    used = serializers.SerializerMethodField()

    def get_limit(self, obj):
        return self.context['user'].get_capability_limit(obj.id)

    def get_used(self, obj):
        return self.context['user'].get_capability_usage(obj.id)
