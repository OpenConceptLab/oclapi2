from django.db import IntegrityError
from pydash import compact
from rest_framework.fields import CharField, DateTimeField, BooleanField, URLField, JSONField
from rest_framework.serializers import ModelSerializer

from core.concepts.models import Concept, LocalizedText


class LocalizedTextSerializer(ModelSerializer):
    class Meta:
        model = LocalizedText
        fields = (
            'id', 'name', 'external_id', 'type', 'locale', 'locale_preferred'
        )


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
        models = LocalizedText
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
    id = CharField(source='mnemonic')
    source = CharField(source='parent_resource')
    owner = CharField(source='owner_name')

    class Meta:
        model = Concept
        fields = (
            'id', 'external_id', 'concept_class', 'datatype', 'url', 'retired', 'source',
            'owner', 'owner_type', 'owner_url', 'display_name', 'display_locale', 'version',
        )


class ConceptDetailSerializer(ModelSerializer):
    id = CharField(source='mnemonic', required=True)
    source = CharField(source='parent_resource', read_only=True)
    parent_id = CharField()
    owner = CharField(source='owner_name', read_only=True)
    created_on = DateTimeField(source='created_at', read_only=True)
    updated_on = DateTimeField(source='updated_at', read_only=True)
    names = LocalizedTextSerializer(many=True)
    descriptions = LocalizedTextSerializer(many=True)
    external_id = CharField(required=False)
    concept_class = CharField(required=False)
    datatype = CharField(required=False)
    display_name = CharField(read_only=True)
    display_locale = CharField(read_only=True)
    retired = BooleanField(required=False)
    url = URLField(read_only=True)
    owner_type = CharField(read_only=True)
    owner_url = URLField(read_only=True)
    extras = JSONField(required=False)

    class Meta:
        model = Concept
        fields = (
            'id', 'external_id', 'concept_class', 'datatype', 'url', 'retired', 'source',
            'owner', 'owner_type', 'owner_url', 'display_name', 'display_locale', 'names', 'descriptions',
            'created_on', 'updated_on', 'versions_url', 'version', 'extras', 'parent_id'
        )

    def create(self, validated_data):
        names = [LocalizedText(**name) for name in validated_data.pop('names', [])]
        descriptions = [LocalizedText(**name) for name in validated_data.pop('descriptions', [])]
        try:
            instance = super().create(validated_data)
        except IntegrityError as ex:
            self._errors.update(dict(__all__=ex.args))
            return Concept()

        if instance.id:
            instance.cloned_names = names
            instance.cloned_descriptions = descriptions
            instance.set_locales()
            instance.sources.set([instance.parent, instance.parent.head])

        return instance

    def update(self, instance, validated_data):
        instance.concept_class = validated_data.get('concept_class', instance.concept_class)
        instance.datatype = validated_data.get('datatype', instance.datatype)
        instance.extras = validated_data.get('extras', instance.extras)
        instance.external_id = validated_data.get('external_id', instance.external_id)
        instance.comment = validated_data.get('update_comment') or validated_data.get('comment')
        instance.retired = validated_data.get('retired', instance.retired)

        new_names = [LocalizedText(**name) for name in validated_data.get('names', [])]
        new_descriptions = [LocalizedText(**desc) for desc in validated_data.get('descriptions', [])]

        instance.cloned_names = compact([*instance.cloned_names, *new_names])
        instance.cloned_descriptions = compact([*instance.cloned_descriptions, *new_descriptions])
        errors = Concept.persist_clone(instance, self.context.get('request').user)
        if errors:
            self._errors.update(errors)
        return instance


class ConceptVersionDetailSerializer(ModelSerializer):
    type = CharField(source='resource_type')
    uuid = CharField(source='id')
    id = CharField(source='mnemonic')
    names = LocalizedTextSerializer(many=True)
    descriptions = LocalizedTextSerializer(many=True)
    source = CharField(source='parent_resource')
    source_url = URLField(source='owner_url')
    owner = CharField(source='owner_name')
    created_on = DateTimeField(source='created_at', read_only=True)
    updated_on = DateTimeField(source='updated_at', read_only=True)
    version_created_on = DateTimeField(source='created_at')
    version_created_by = CharField(source='created_by')
    locale = CharField(source='iso_639_1_locale')

    class Meta:
        model = Concept
        fields = (
            'type', 'uuid', 'id', 'external_id', 'concept_class', 'datatype', 'display_name', 'display_locale',
            'names', 'descriptions', 'extras', 'retired', 'source', 'source_url', 'owner', 'owner_name', 'owner_url',
            'version', 'created_on', 'updated_on', 'version_created_on', 'version_created_by', 'extras',
            'is_latest_version', 'locale'
        )
