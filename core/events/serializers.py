from pydash import get
from rest_framework import serializers

from core.events.models import Event
from core.users.serializers import UserListSerializer


class EventSerializer(serializers.ModelSerializer):
    actor = UserListSerializer(read_only=True)
    referenced_object = serializers.SerializerMethodField()
    object = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = (
            'id', 'type', 'event_type', 'description', 'object', 'referenced_object',
            'actor', 'public', 'url', 'created_at'
        )

    def can_view(self, instance):
        """An event's flag is set when it's recorded; the object shown is live, so its access is checked now."""
        from core.users.models import UserProfile
        return UserProfile.can_user_view(get(self, 'context.request.user'), instance)

    def get_referenced_object(self, obj):
        referenced_object = obj.referenced_object
        if referenced_object:
            data = referenced_object.get_brief_serializer()(referenced_object).data if self.can_view(
                referenced_object) else None
        else:
            data = obj._referenced_object
        return data

    def get_object(self, obj):
        resource_object = obj.object
        data = None
        if resource_object and self.can_view(resource_object):
            data = resource_object.get_brief_serializer()(resource_object).data
        return data
