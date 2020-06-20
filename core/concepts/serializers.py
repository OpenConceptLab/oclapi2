from rest_framework.fields import CharField, DateTimeField
from rest_framework.serializers import ModelSerializer

from core.concepts.models import Concept, LocalizedText


class LocalizedTextSerializer(ModelSerializer):
    class Meta:
        model = LocalizedText
        fields = (
            'id', 'name', 'external_id', 'type', 'locale', 'locale_preferred'
        )


class ConceptListSerializer(ModelSerializer):
    id = CharField(source='mnemonic')
    source = CharField(source='parent_resource')
    owner = CharField(source='owner_name')

    class Meta:
        model = Concept
        fields = (
            'id', 'external_id', 'concept_class', 'datatype', 'url', 'retired', 'source',
            'owner', 'owner_type', 'owner_url', 'display_name', 'display_locale'
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
            'created_on', 'updated_on',
        )
