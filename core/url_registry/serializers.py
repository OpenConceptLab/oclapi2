from pydash import get
from rest_framework.fields import CharField, BooleanField, DateTimeField

from core.common.serializers import AbstractResourceSerializer
from core.repos.serializers import RepoListSerializer
from core.url_registry.models import URLRegistry


class URLRegistryBaseSerializer(AbstractResourceSerializer):
    owner = CharField(read_only=True, source='owner.mnemonic', allow_null=True)

    class Meta:
        model = URLRegistry
        fields = AbstractResourceSerializer.Meta.fields + (
            'id', 'name', 'url', 'namespace', 'owner', 'owner_type', 'owner_url')

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['type'] = URLRegistry.OBJECT_TYPE
        return data


class URLRegistryDetailSerializer(URLRegistryBaseSerializer):
    created_by = CharField(source='created_by.username', read_only=True)
    updated_by = CharField(source='updated_by.username', read_only=True)
    is_active = BooleanField(read_only=True)
    created_at = DateTimeField(read_only=True)
    updated_at = DateTimeField(read_only=True)
    repo = RepoListSerializer(read_only=True, allow_null=True)

    class Meta:
        model = URLRegistry
        fields = URLRegistryBaseSerializer.Meta.fields + (
            'created_by', 'updated_by', 'created_at', 'updated_at', 'is_active', 'extras', 'repo'
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if get(data, 'repo.search_meta'):
            data['repo'].pop('search_meta', None)
        return data

    def prepare_object(self, validated_data):
        user = self.context['request'].user
        view = self.context['view']

        url_registry = URLRegistry(
            name=validated_data.get('name'),
            namespace=validated_data.get('namespace') or None,
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
            url_registry.resolve()
        return url_registry

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        if instance.is_active:
            instance.resolve()
        return instance
