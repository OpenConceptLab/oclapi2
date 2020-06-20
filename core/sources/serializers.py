from rest_framework.fields import CharField, SerializerMethodField, IntegerField, DateTimeField
from rest_framework.serializers import ModelSerializer

from core.common.constants import DEFAULT_ACCESS_TYPE
from core.settings import DEFAULT_LOCALE
from core.sources.models import Source


class SourceListSerializer(ModelSerializer):
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')

    class Meta:
        model = Source
        fields = (
            'short_code', 'name', 'url', 'owner', 'owner_type', 'owner_url', 'version',
        )


class SourceCreateOrUpdateSerializer(ModelSerializer):
    class Meta:
        model = Source
        lookup_field = 'mnemonic'

    def restore_object(self, attrs, instance=None):
        source = instance if instance else Source()
        source.mnemonic = attrs.get(self.Meta.lookup_field, source.mnemonic)
        source.name = attrs.get('name', source.name)
        source.full_name = attrs.get('full_name', source.full_name)
        source.description = attrs.get('description', source.description)
        source.source_type = attrs.get('source_type', source.source_type)
        source.custom_validation_schema = attrs.get('custom_validation_schema', source.custom_validation_schema)
        source.public_access = attrs.get('public_access', source.public_access or DEFAULT_ACCESS_TYPE)
        source.default_locale = attrs.get('default_locale', source.default_locale or DEFAULT_LOCALE)
        source.website = attrs.get('website', source.website)
        source.supported_locales = attrs.get('supported_locales').split(',') if attrs.get('supported_locales') \
            else source.supported_locales
        source.extras = attrs.get('extras', source.extras)
        source.external_id = attrs.get('external_id', source.external_id)
        return source

    @staticmethod
    def get_active_concepts(obj):
        return obj.get_active_concepts().count()


class SourceDetailSerializer(SourceCreateOrUpdateSerializer):
    type = CharField(source='resource_type')
    uuid = CharField(source='id')
    id = CharField(source='mnemonic')
    short_code = CharField(source='mnemonic')
    active_concepts = SerializerMethodField()
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    versions = IntegerField(source='num_versions')
    created_on = DateTimeField(source='created_at')
    updated_on = DateTimeField(source='updated_at')

    class Meta:
        model = Source
        lookup_field = 'mnemonic'
        fields = (
            'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'source_type',
            'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
            'url', 'active_concepts', 'owner', 'owner_type', 'owner_url', 'versions',
            'created_on', 'updated_on', 'created_by', 'updated_by', 'extras', 'external_id', 'versions_url',
            'version', 'concepts_url',
        )
