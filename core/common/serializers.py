from rest_framework.fields import CharField, JSONField
from rest_framework.serializers import Serializer


class RootSerializer(Serializer):  # pylint: disable=abstract-method
    version = CharField()
    routes = JSONField()


class TaskSerializer(Serializer):  # pylint: disable=abstract-method
    pass


class ReadSerializerMixin:
    """ Mixin for serializer which does not update or create resources. """
    def update(self, instance, validated_data):
        pass

    def create(self, validated_data):
        pass
