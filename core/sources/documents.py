from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry
from pydash import get

from core.common.utils import jsonify_safe
from core.sources.models import Source


@registry.register_document
class SourceDocument(Document):
    class Index:
        name = 'sources'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    locale = fields.ListField(fields.KeywordField())
    last_update = fields.DateField(attr='updated_at')
    owner = fields.KeywordField(attr='parent_resource', normalizer='lowercase')
    owner_type = fields.KeywordField(attr='parent_resource_type')
    public_can_view = fields.TextField(attr='public_can_view')
    source_type = fields.KeywordField(attr='source_type', normalizer='lowercase')
    is_active = fields.KeywordField(attr='is_active')
    version = fields.KeywordField(attr='version')
    name = fields.KeywordField(attr='name', normalizer='lowercase')
    canonical_url = fields.KeywordField(attr='canonical_url', normalizer='lowercase')
    mnemonic = fields.KeywordField(attr='mnemonic', normalizer='lowercase')
    extras = fields.ObjectField()
    identifier = fields.ObjectField()
    jurisdiction = fields.ObjectField()
    publisher = fields.KeywordField(attr='publisher', normalizer='lowercase')
    content_type = fields.KeywordField(attr='content_type', normalizer='lowercase')

    class Django:
        model = Source
        fields = [
            'full_name',
            'custom_validation_schema',
            'revision_date',
            'retired',
        ]

    @staticmethod
    def prepare_locale(instance):
        return get(instance.supported_locales, [])

    @staticmethod
    def prepare_extras(instance):
        value = {}

        if instance.extras:
            value = jsonify_safe(instance.extras)

        return value or {}

    @staticmethod
    def prepare_identifier(instance):
        value = {}

        if instance.identifier:
            value = jsonify_safe(instance.identifier)

        return value or {}

    @staticmethod
    def prepare_jurisdiction(instance):
        value = {}
        if instance.jurisdiction:
            value = jsonify_safe(instance.jurisdiction)

        return value or {}
