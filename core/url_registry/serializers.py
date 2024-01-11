from rest_framework.fields import CharField, BooleanField, DateTimeField
from rest_framework.serializers import ModelSerializer

from core.url_registry.models import URLRegistry


class URLRegistryBaseSerializer(ModelSerializer):
    class Meta:
        model = URLRegistry
        fields = ['id', 'name', 'url', 'namespace']


class URLRegistryDetailSerializer(URLRegistryBaseSerializer):
    created_by = CharField(source='created_by.username', read_only=True)
    updated_by = CharField(source='updated_by.username', read_only=True)
    is_active = BooleanField(read_only=True)
    created_at = DateTimeField(read_only=True)
    updated_at = DateTimeField(read_only=True)

    class Meta:
        model = URLRegistry
        fields = URLRegistryBaseSerializer.Meta.fields + [
            'created_by', 'updated_by', 'created_at', 'updated_at', 'is_active', 'extras']

    def prepare_object(self, validated_data):
        user = self.context['request'].user
        view = self.context['view']

        url_registry = URLRegistry(
            name=validated_data.get('name'),
            namespace=validated_data.get('namespace'),
            url=validated_data.get('url'),
            created_by=user,
            updated_by=user,
            extras=validated_data.get('extras', {}),
        )
        if view.parent_resource_type and view.parent_resource:
            setattr(url_registry, view.parent_resource_type, view.parent_resource)
        return url_registry

    def create(self, validated_data):
        url_registry = self.prepare_object(validated_data)
        if not url_registry.is_uniq():
            self._errors['non_fields_error'] = ['This entry already exists.']
        if not self._errors:
            url_registry.save()
        return url_registry
