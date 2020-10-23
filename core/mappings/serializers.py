from rest_framework.fields import CharField, JSONField, IntegerField, SerializerMethodField, DateTimeField
from rest_framework.serializers import ModelSerializer

from core.mappings.models import Mapping


class MappingListSerializer(ModelSerializer):
    id = CharField(source='mnemonic', required=False)
    uuid = CharField(source='id', read_only=True)
    source = CharField(source='parent_resource', read_only=True)
    owner = CharField(source='owner_name', read_only=True)
    update_comment = CharField(source='comment', required=False)
    url = CharField(source='versioned_object_url', read_only=True)
    version = CharField(read_only=True)
    to_concept_code = SerializerMethodField()
    to_concept_name = SerializerMethodField()
    version_created_on = DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = Mapping
        fields = (
            'external_id', 'retired', 'map_type', 'source', 'owner', 'owner_type',
            'from_concept_code', 'from_concept_name', 'from_concept_url',
            'to_concept_code', 'to_concept_name', 'to_concept_url',
            'from_source_owner', 'from_source_owner_type', 'from_source_url', 'from_source_name',
            'to_source_owner', 'to_source_owner_type', 'to_source_url', 'to_source_name',
            'url', 'version', 'id', 'versioned_object_id', 'is_latest_version', 'update_comment', 'version_url',
            'uuid', 'version_created_on', 'version_url'
        )

    @staticmethod
    def get_to_concept_code(obj):
        return obj.get_to_concept_code()

    @staticmethod
    def get_to_concept_name(obj):
        return obj.get_to_concept_name()


class MappingVersionListSerializer(MappingListSerializer):
    previous_version_url = CharField(read_only=True, source='prev_version_uri')

    class Meta:
        model = Mapping
        fields = MappingListSerializer.Meta.fields + ('previous_version_url', )


class MappingDetailSerializer(MappingListSerializer):
    type = CharField(source='resource_type', read_only=True)
    uuid = CharField(source='id', read_only=True)
    extras = JSONField(required=False, allow_null=True)
    created_by = CharField(source='created_by.username', read_only=True)
    updated_by = CharField(source='created_by.username', read_only=True)
    parent_id = IntegerField(required=True)
    map_type = CharField(required=True)
    to_concept_url = CharField(required=True)
    from_concept_url = CharField(required=True)
    previous_version_url = CharField(read_only=True, source='prev_version_uri')

    class Meta:
        model = Mapping
        fields = MappingListSerializer.Meta.fields + (
            'type', 'uuid', 'extras', 'created_at', 'updated_at',
            'created_by', 'updated_by', 'parent_id', 'previous_version_url'
        )
        extra_kwargs = {'parent_id': {'write_only': True}}

    def create(self, validated_data):
        mapping = Mapping.persist_new(data=validated_data, user=self.context.get('request').user)
        self._errors.update(mapping.errors)
        return mapping

    def update(self, instance, validated_data):
        from core.concepts.models import Concept
        from core.sources.models import Source

        instance.extras = validated_data.get('extras', instance.extras)
        instance.external_id = validated_data.get('external_id', instance.external_id)
        instance.comment = validated_data.get('update_comment') or validated_data.get('comment')
        instance.retired = validated_data.get('retired', instance.retired)
        from_concept_url = validated_data.get('from_concept_url', None)
        to_concept_url = validated_data.get('to_concept_url', None)
        to_source_url = validated_data.get('to_source_url', None)
        if from_concept_url:
            instance.from_concept = Concept.from_uri_queryset(from_concept_url).first()
        if to_concept_url:
            instance.to_concept = Concept.from_uri_queryset(to_concept_url).first()
        if to_source_url:
            instance.to_source = Source.head_from_uri(to_source_url).first()

        instance.mnemonic = validated_data.get('mnemonic', instance.mnemonic)
        instance.map_type = validated_data.get('map_type', instance.map_type)
        instance.to_concept_code = validated_data.get('to_concept_code', instance.to_concept_code)
        instance.to_concept_name = validated_data.get('to_concept_name', instance.to_concept_name)

        errors = Mapping.persist_clone(instance, self.context.get('request').user)
        if errors:
            self._errors.update(errors)

        return instance
