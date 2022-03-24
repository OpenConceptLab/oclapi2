from django.core.exceptions import ValidationError
from pydash import get
from rest_framework import serializers
from rest_framework.fields import JSONField, CharField, IntegerField

from core.client_configs.constants import HOME_TYPE
from core.client_configs.models import ClientConfig


class ClientConfigAbstractSerializer(serializers.ModelSerializer):
    config = JSONField(required=False)
    url = CharField(source='uri', read_only=True)
    scope = CharField(read_only=True, source='resource_type.name')

    class Meta:
        abstract = True
        model = ClientConfig
        fields = (
            'id', 'name', 'description', 'type', 'is_default', 'config', 'url', 'scope', 'public', 'is_template'
        )

    def __init__(self, *args, **kwargs):
        self.resource = get(kwargs, 'context.view.kwargs.resource', None)
        super().__init__(*args, **kwargs)

    def create(self, validated_data):
        user = self.context['request'].user
        from core.common.utils import get_content_type_from_resource_name
        resource_type = get_content_type_from_resource_name(validated_data.pop('resource_type', None) or self.resource)

        instance = self.get_instance(validated_data, resource_type=resource_type, created_by=user, updated_by=user)
        instance.type = instance.type or HOME_TYPE
        instance.is_default = bool(instance.is_default)

        try:
            instance.full_clean()
            instance.save()
            if instance.id and instance.is_default:
                instance.siblings.filter(is_default=True).update(is_default=False)
        except ValidationError as ex:
            self._errors.update(ex.message_dict)

        if self._errors:
            raise serializers.ValidationError(self._errors)

        return instance

    @staticmethod
    def get_instance(validated_data, **kwargs):
        return ClientConfig(**validated_data, **kwargs)


class ClientConfigTemplateSerializer(ClientConfigAbstractSerializer):
    @staticmethod
    def get_instance(validated_data, **kwargs):
        return ClientConfig(**validated_data, **kwargs, resource_id=0, is_template=True)


class ClientConfigSerializer(ClientConfigAbstractSerializer):
    resource_type = CharField(write_only=True, required=False)
    resource_id = IntegerField(write_only=True, required=False)

    class Meta:
        model = ClientConfig
        fields = ClientConfigAbstractSerializer.Meta.fields + ('resource_type', 'resource_id')

    def update(self, instance, validated_data):
        user = self.context['request'].user
        for attr in ['name', 'description', 'config', 'type']:
            setattr(instance, attr, validated_data.get(attr, get(instance, attr)))
        instance.is_default = bool(validated_data.get('is_default', instance.is_default))
        instance.updated_by = user

        try:
            instance.full_clean()
            instance.save()
            if instance.id and instance.is_default:
                instance.siblings.filter(is_default=True).update(is_default=False)
        except ValidationError as ex:
            self._errors.update(ex.message_dict)

        if self._errors:
            raise serializers.ValidationError(self._errors)

        return instance
