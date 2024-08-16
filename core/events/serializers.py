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

    @staticmethod
    def get_referenced_object(obj):
        referenced_object = obj.referenced_object
        data = None
        if referenced_object:
            data = referenced_object.get_brief_serializer()(referenced_object).data
        return data

    @staticmethod
    def get_object(obj):
        resource_object = obj.object
        data = None
        if resource_object:
            data = resource_object.get_brief_serializer()(resource_object).data
        return data
