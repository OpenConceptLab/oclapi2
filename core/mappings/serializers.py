from rest_framework.fields import CharField, JSONField, IntegerField, DateTimeField
from rest_framework.serializers import ModelSerializer

from core.mappings.models import Mapping


class MappingListSerializer(ModelSerializer):
    id = CharField(source='mnemonic', required=False)
    uuid = CharField(source='id', read_only=True)
    source = CharField(source='parent_resource', read_only=True)
    owner = CharField(source='owner_name', read_only=True)
    update_comment = CharField(source='comment', required=False)
    url = CharField(required=False, source='versioned_object_url')
    version = CharField(read_only=True)
    version_created_on = DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = Mapping
        fields = (
            'external_id', 'retired', 'map_type', 'source', 'owner', 'owner_type',
            'from_concept_code', 'from_concept_name', 'from_concept_url',
            'to_concept_code', 'to_concept_name', 'to_concept_url',
            'from_source_owner', 'from_source_owner_type', 'from_source_url', 'from_source_name',
            'to_source_owner', 'to_source_owner_type', 'to_source_url', 'to_source_name',
            'url', 'version', 'id', 'versioned_object_id', 'versioned_object_url',
            'is_latest_version', 'update_comment', 'version_url', 'uuid', 'version_created_on',
            'from_source_version', 'to_source_version'
        )


class MappingVersionListSerializer(MappingListSerializer):
    previous_version_url = CharField(read_only=True, source='prev_version_uri')

    class Meta:
        model = Mapping
        fields = MappingListSerializer.Meta.fields + ('previous_version_url',)


class MappingDetailSerializer(MappingListSerializer):
    type = CharField(source='resource_type', read_only=True)
    uuid = CharField(source='id', read_only=True)
    extras = JSONField(required=False, allow_null=True)
    created_by = CharField(source='created_by.username', read_only=True)
    updated_by = CharField(source='created_by.username', read_only=True)
    parent_id = IntegerField(required=True)
    map_type = CharField(required=True)
    to_concept_url = CharField(required=False)
    from_concept_url = CharField(required=False)
    previous_version_url = CharField(read_only=True, source='prev_version_uri')

    class Meta:
        model = Mapping
        fields = MappingListSerializer.Meta.fields + (
            'type', 'uuid', 'extras', 'created_at', 'updated_at',
            'created_by', 'updated_by', 'parent_id', 'previous_version_url',
        )
        extra_kwargs = {'parent_id': {'write_only': True}}

    def create(self, validated_data):
        mapping = Mapping.persist_new(data=validated_data, user=self.context.get('request').user)
        if mapping.errors:
            self._errors.update(mapping.errors)
        return mapping

    def update(self, instance, validated_data):
        errors = Mapping.create_new_version_for(instance, validated_data, self.context.get('request').user)
        if errors:
            self._errors.update(errors)
        return instance
