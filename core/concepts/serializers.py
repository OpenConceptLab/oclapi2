from rest_framework.fields import CharField, DateTimeField, BooleanField
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
    id = CharField(source='mnemonic')
    source = CharField(source='parent_resource')
    owner = CharField(source='owner_name')
    created_on = DateTimeField(source='created_at')
    updated_on = DateTimeField(source='updated_at')
    names = LocalizedTextSerializer(many=True)
    descriptions = LocalizedTextSerializer(many=True)

    class Meta:
        model = Concept
        fields = (
            'id', 'external_id', 'concept_class', 'datatype', 'url', 'retired', 'source',
            'owner', 'owner_type', 'owner_url', 'display_name', 'display_locale', 'names', 'descriptions',
            'created_on', 'updated_on', 'versions_url', 'version',
        )
