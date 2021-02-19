from pydash import get
from rest_framework.fields import CharField, DateTimeField, BooleanField, URLField, JSONField, SerializerMethodField, \
    UUIDField
from rest_framework.serializers import ModelSerializer

from core.common.constants import INCLUDE_INVERSE_MAPPINGS_PARAM, INCLUDE_MAPPINGS_PARAM
from core.concepts.models import Concept, LocalizedText


class LocalizedNameSerializer(ModelSerializer):
    uuid = CharField(read_only=True, source='id')
    name_type = CharField(source='type', required=False, allow_null=True, allow_blank=True)
    type = CharField(source='name_type', required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = LocalizedText
        fields = (
            'uuid', 'name', 'external_id', 'type', 'locale', 'locale_preferred', 'name_type',
        )

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret.update({"type": "ConceptName"})
        return ret


class LocalizedDescriptionSerializer(ModelSerializer):
    uuid = CharField(read_only=True, source='id')
    description = CharField(source='name')
    description_type = CharField(source='type', required=False, allow_null=True, allow_blank=True)
    type = CharField(source='description_type', required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = LocalizedText
        fields = (
            'uuid', 'description', 'external_id', 'type', 'locale', 'locale_preferred', 'description_type'
        )

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret.update({"type": "ConceptDescription"})
        return ret


class ConceptLabelSerializer(ModelSerializer):
    uuid = CharField(read_only=True, source='id')
    external_id = CharField(required=False)
    locale = CharField(required=True)
    locale_preferred = BooleanField(required=False, default=False)

    class Meta:
        model = LocalizedText
        fields = (
            'uuid', 'external_id', 'type', 'locale', 'locale_preferred'
        )

    @staticmethod
    def create(attrs, instance=None):  # pylint: disable=arguments-differ
        concept_desc = instance if instance else LocalizedText()
        concept_desc.name = attrs.get('name', concept_desc.name)
        concept_desc.locale = attrs.get('locale', concept_desc.locale)
        concept_desc.locale_preferred = attrs.get('locale_preferred', concept_desc.locale_preferred)
        concept_desc.type = attrs.get('type', concept_desc.type)
        concept_desc.external_id = attrs.get('external_id', concept_desc.external_id)
        concept_desc.save()
        return concept_desc


class ConceptNameSerializer(ConceptLabelSerializer):
    name = CharField(required=True)
    name_type = CharField(required=False, source='type')

    class Meta:
        model = LocalizedText
        fields = (*ConceptLabelSerializer.Meta.fields, 'name', 'name_type')

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret.update({"type": "ConceptName"})
        return ret


class ConceptDescriptionSerializer(ConceptLabelSerializer):
    description = CharField(required=True, source='name')
    description_type = CharField(required=False, source='type')

    class Meta:
        model = LocalizedText
        fields = (
            *ConceptLabelSerializer.Meta.fields, 'description', 'description_type'
        )

    def to_representation(self, instance):  # used to be to_native
        ret = super().to_representation(instance)
        ret.update({"type": "ConceptDescription"})
        return ret


class ConceptListSerializer(ModelSerializer):
    uuid = CharField(source='id', read_only=True)
    id = CharField(source='mnemonic')
    source = CharField(source='parent_resource')
    owner = CharField(source='owner_name')
    update_comment = CharField(source='comment', required=False, allow_null=True, allow_blank=True)
    locale = SerializerMethodField()
    url = CharField(required=False, source='versioned_object_url')
    version_created_on = DateTimeField(source='created_at', read_only=True)
    version_created_by = DateTimeField(source='created_by.username', read_only=True)
    mappings = SerializerMethodField()

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')
        self.query_params = params.dict() if params else dict()
        self.include_indirect_mappings = self.query_params.get(INCLUDE_INVERSE_MAPPINGS_PARAM) in ['true', True]
        self.include_direct_mappings = self.query_params.get(INCLUDE_MAPPINGS_PARAM) in ['true', True]

        super().__init__(*args, **kwargs)

    class Meta:
        model = Concept
        fields = (
            'uuid', 'id', 'external_id', 'concept_class', 'datatype', 'url', 'retired', 'source',
            'owner', 'owner_type', 'owner_url', 'display_name', 'display_locale', 'version', 'update_comment',
            'locale', 'version_created_by', 'version_created_on', 'mappings', 'is_latest_version', 'versions_url',
            'version_url',
        )

    @staticmethod
    def get_locale(obj):
        return obj.iso_639_1_locale

    def get_mappings(self, obj):
        from core.mappings.serializers import MappingDetailSerializer
        context = get(self, 'context')
        if self.include_direct_mappings:
            return MappingDetailSerializer(obj.get_unidirectional_mappings(), many=True, context=context).data
        if self.include_indirect_mappings:
            return MappingDetailSerializer(obj.get_bidirectional_mappings(), many=True, context=context).data

        return []


class ConceptVersionListSerializer(ConceptListSerializer):
    previous_version_url = CharField(read_only=True, source='prev_version_uri')

    class Meta:
        model = Concept
        fields = ConceptListSerializer.Meta.fields + (
            'previous_version_url',
        )


class ConceptDetailSerializer(ModelSerializer):
    uuid = CharField(source='id', read_only=True)
    version = CharField(read_only=True)
    type = CharField(source='versioned_resource_type', read_only=True)
    id = CharField(source='mnemonic', required=True)
    source = CharField(source='parent_resource', read_only=True)
    parent_id = UUIDField()
    owner = CharField(source='owner_name', read_only=True)
    created_on = DateTimeField(source='created_at', read_only=True)
    updated_on = DateTimeField(source='updated_at', read_only=True)
    names = LocalizedNameSerializer(many=True)
    descriptions = LocalizedDescriptionSerializer(many=True, allow_null=True, required=False)
    external_id = CharField(required=False, allow_blank=True)
    concept_class = CharField(required=True)
    datatype = CharField(required=True)
    display_name = CharField(read_only=True)
    display_locale = CharField(read_only=True)
    retired = BooleanField(required=False)
    owner_type = CharField(read_only=True)
    owner_url = URLField(read_only=True)
    extras = JSONField(required=False, allow_null=True)
    update_comment = CharField(required=False, source='comment', allow_null=True, allow_blank=True)
    mappings = SerializerMethodField()
    url = CharField(required=False, source='versioned_object_url')
    updated_by = DateTimeField(source='updated_by.username', read_only=True)
    created_by = DateTimeField(source='created_by.username', read_only=True)

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')
        self.query_params = params.dict() if params else dict()
        self.include_indirect_mappings = self.query_params.get(INCLUDE_INVERSE_MAPPINGS_PARAM) in ['true', True]
        self.include_direct_mappings = self.query_params.get(INCLUDE_MAPPINGS_PARAM) in ['true', True]

        super().__init__(*args, **kwargs)

    class Meta:
        model = Concept
        fields = (
            'uuid', 'id', 'external_id', 'concept_class', 'datatype', 'url', 'retired', 'source',
            'owner', 'owner_type', 'owner_url', 'display_name', 'display_locale', 'names', 'descriptions',
            'created_on', 'updated_on', 'versions_url', 'version', 'extras', 'parent_id', 'name', 'type',
            'update_comment', 'version_url', 'mappings', 'updated_by', 'created_by'
        )

    def get_mappings(self, obj):
        from core.mappings.serializers import MappingDetailSerializer
        context = get(self, 'context')
        if self.include_indirect_mappings:
            return MappingDetailSerializer(obj.get_bidirectional_mappings(), many=True, context=context).data
        if self.include_direct_mappings:
            return MappingDetailSerializer(obj.get_unidirectional_mappings(), many=True, context=context).data

        return []

    def create(self, validated_data):
        concept = Concept.persist_new(data=validated_data, user=self.context.get('request').user)
        if concept.errors:
            self._errors.update(concept.errors)
        return concept

    def update(self, instance, validated_data):
        errors = Concept.create_new_version_for(instance, validated_data, self.context.get('request').user)
        if errors:
            self._errors.update(errors)
        return instance


class ConceptVersionDetailSerializer(ModelSerializer):
    type = CharField(source='resource_type')
    uuid = CharField(source='id')
    id = CharField(source='mnemonic')
    names = LocalizedNameSerializer(many=True)
    descriptions = LocalizedDescriptionSerializer(many=True, required=False, allow_null=True)
    source = CharField(source='parent_resource')
    source_url = URLField(source='parent_url')
    owner = CharField(source='owner_name')
    created_on = DateTimeField(source='created_at', read_only=True)
    updated_on = DateTimeField(source='updated_at', read_only=True)
    version_created_on = DateTimeField(source='created_at')
    version_created_by = CharField(source='created_by')
    locale = CharField(source='iso_639_1_locale')
    mappings = SerializerMethodField()
    url = CharField(source='versioned_object_url', read_only=True)
    previous_version_url = CharField(source='prev_version_uri', read_only=True)
    update_comment = CharField(source='comment', required=False, allow_null=True, allow_blank=True)

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')

        self.include_indirect_mappings = False
        self.include_direct_mappings = False
        self.query_params = params.dict() if params else dict()
        self.include_indirect_mappings = self.query_params.get(INCLUDE_INVERSE_MAPPINGS_PARAM) == 'true'
        self.include_direct_mappings = self.query_params.get(INCLUDE_MAPPINGS_PARAM) == 'true'

        super().__init__(*args, **kwargs)

    class Meta:
        model = Concept
        fields = (
            'type', 'uuid', 'id', 'external_id', 'concept_class', 'datatype', 'display_name', 'display_locale',
            'names', 'descriptions', 'extras', 'retired', 'source', 'source_url', 'owner', 'owner_name', 'owner_url',
            'version', 'created_on', 'updated_on', 'version_created_on', 'version_created_by', 'update_comment',
            'is_latest_version', 'locale', 'url', 'owner_type', 'version_url', 'mappings', 'previous_version_url'
        )

    def get_mappings(self, obj):
        from core.mappings.serializers import MappingDetailSerializer
        context = get(self, 'context')
        if self.include_direct_mappings:
            return MappingDetailSerializer(obj.get_unidirectional_mappings(), many=True, context=context).data
        if self.include_indirect_mappings:
            return MappingDetailSerializer(obj.get_bidirectional_mappings(), many=True, context=context).data

        return []
