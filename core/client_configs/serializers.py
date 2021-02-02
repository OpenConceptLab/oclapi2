from rest_framework import serializers
from rest_framework.fields import JSONField

from core.client_configs.models import ClientConfig


class ClientConfigSerializer(serializers.ModelSerializer):
    config = JSONField()

    class Meta:
        model = ClientConfig
        fields = (
            'id', 'name', 'type', 'page_size', 'is_default', 'config', 'layout'
        )
