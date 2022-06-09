import json

from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry
from pydash import get

from core.collections.models import Collection
from core.common.utils import jsonify_safe, flatten_dict


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
    canonical_url = fields.KeywordField(attr='canonical_url', normalizer='lowercase')
    mnemonic = fields.KeywordField(attr='mnemonic', normalizer='lowercase')
    extras = fields.ObjectField(dynamic=True)
    identifier = fields.ObjectField()
    publisher = fields.KeywordField(attr='publisher', normalizer='lowercase')
    immutable = fields.KeywordField(attr='immutable')
    custom_validation_schema = fields.KeywordField(attr='custom_validation_schema', normalizer='lowercase')
    created_by = fields.KeywordField()

    class Django:
        model = Collection
        fields = [
            'full_name',
            'revision_date',
            'retired',
            'experimental',
            'locked_date',
            'external_id',
        ]

    @staticmethod
    def get_boostable_search_attrs():
        return dict(mnemonic=dict(boost=5), name=dict(boost=4), canonical_url=dict(boost=3, lower=True, wildcard=True))

    @staticmethod
    def prepare_locale(instance):
        return get(instance.supported_locales, [])

    @staticmethod
    def prepare_extras(instance):
        value = {}

        if instance.extras:
            value = jsonify_safe(instance.extras)
            if isinstance(value, dict):
                value = flatten_dict(value)

        if value:
            value = json.loads(json.dumps(value).replace('-', '_'))
        return value or {}

    @staticmethod
    def prepare_identifier(instance):
        value = {}

        if instance.identifier:
            value = jsonify_safe(instance.identifier)
            if isinstance(value, dict):
                value = flatten_dict(value)
            if isinstance(value, str):
                value = dict(value=value)

        return value or {}

    @staticmethod
    def prepare_created_by(instance):
        return instance.created_by.username
