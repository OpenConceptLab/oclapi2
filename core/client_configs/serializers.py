from django.core.exceptions import ValidationError
from rest_framework import serializers
from rest_framework.fields import JSONField, CharField, IntegerField

from core.client_configs.constants import HOME_TYPE
from core.client_configs.models import ClientConfig


class ClientConfigSerializer(serializers.ModelSerializer):
    config = JSONField(required=False)
    url = CharField(source='uri', read_only=True)
    resource_type = CharField(write_only=True, required=False)
    resource_id = IntegerField(write_only=True, required=False)

    class Meta:
        model = ClientConfig
        fields = (
            'id', 'name', 'type', 'is_default', 'config', 'url', 'resource_type', 'resource_id'
        )

    def create(self, validated_data):
        user = self.context['request'].user
        from core.common.utils import get_content_type_from_resource_name
        resource_type = get_content_type_from_resource_name(validated_data.pop('resource_type', None))

        instance = ClientConfig(**validated_data, created_by=user, updated_by=user, resource_type=resource_type)

        if not instance.type:
            instance.type = HOME_TYPE
        if instance.is_default is None:
            instance.is_default = False

        try:
            instance.full_clean()
            instance.save()
            if instance.id and instance.is_default:
                instance.siblings.filter(is_default=True).update(is_default=False)
        except ValidationError as ex:
            self._errors.update(ex.message_dict)

        return instance

    def update(self, instance, validated_data):
        user = self.context['request'].user
        instance.name = validated_data.get('name', instance.name)
        instance.config = validated_data.get('config', instance.config)
        instance.type = validated_data.get('type', instance.type)
        instance.is_default = bool(validated_data.get('is_default', instance.is_default))
        instance.updated_by = user

        try:
            instance.full_clean()
            instance.save()
            if instance.id and instance.is_default:
                instance.siblings.filter(is_default=True).update(is_default=False)
        except ValidationError as ex:
            self._errors.update(ex.message_dict)

        return instance
