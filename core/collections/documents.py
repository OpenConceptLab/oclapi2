from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry
from pydash import get

from core.collections.models import Collection


@registry.register_document
class CollectionDocument(Document):
    class Index:
        name = 'collections'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    last_update = fields.DateField(attr='updated_at')
    public_can_view = fields.TextField(attr='public_can_view')
    locale = fields.ListField(fields.KeywordField())
    owner = fields.KeywordField(attr='parent_resource', normalizer='lowercase')
    owner_type = fields.KeywordField(attr='parent_resource_type')
    collection_type = fields.KeywordField(attr='collection_type', normalizer='lowercase')
    is_active = fields.KeywordField(attr='is_active')
    version = fields.KeywordField(attr='version')
    name = fields.KeywordField(attr='name', normalizer='lowercase')

    class Django:
        model = Collection
        fields = [
            'full_name',
            'custom_validation_schema',
        ]

    @staticmethod
    def prepare_locale(instance):
        return get(instance.supported_locales, [])
