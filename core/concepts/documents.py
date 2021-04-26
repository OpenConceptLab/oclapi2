from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry

from core.common.utils import jsonify_safe, flatten_dict
from core.concepts.models import Concept


@registry.register_document
class ConceptDocument(Document):
    class Index:
        name = 'concepts'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    id = fields.KeywordField(attr='mnemonic', normalizer="lowercase")
    name = fields.TextField(attr='display_name')
    _name = fields.KeywordField(attr='display_name', normalizer='lowercase')
    last_update = fields.DateField(attr='updated_at')
    locale = fields.ListField(fields.KeywordField(attr='display_name'))
    source = fields.KeywordField(attr='parent_resource', normalizer="lowercase")
    owner = fields.KeywordField(attr='owner_name', normalizer="lowercase")
    owner_type = fields.KeywordField(attr='owner_type')
    source_version = fields.ListField(fields.KeywordField())
    collection_version = fields.ListField(fields.KeywordField())
    collection = fields.ListField(fields.KeywordField())
    collection_owner_url = fields.ListField(fields.KeywordField())
    public_can_view = fields.BooleanField(attr='public_can_view')
    datatype = fields.KeywordField(attr='datatype', normalizer="lowercase")
    concept_class = fields.KeywordField(attr='concept_class', normalizer="lowercase")
    retired = fields.KeywordField(attr='retired')
    is_active = fields.KeywordField(attr='is_active')
    is_latest_version = fields.KeywordField(attr='is_latest_version')
    extras = fields.ObjectField(dynamic=True)

    class Django:
        model = Concept
        fields = [
            'version',
            'external_id',
        ]

    @staticmethod
    def prepare_locale(instance):
        return list(
            instance.names.filter(locale__isnull=False).distinct('locale').values_list('locale', flat=True)
        )

    @staticmethod
    def prepare_source_version(instance):
        return list(instance.sources.values_list('version', flat=True))

    @staticmethod
    def prepare_collection_version(instance):
        return list(instance.collection_set.values_list('version', flat=True))

    @staticmethod
    def prepare_collection(instance):
        return list(set(list(instance.collection_set.values_list('mnemonic', flat=True))))

    @staticmethod
    def prepare_collection_owner_url(instance):
        return list({coll.parent_url for coll in instance.collection_set.select_related('user', 'organization')})

    @staticmethod
    def prepare_extras(instance):
        value = {}

        if instance.extras:
            value = jsonify_safe(instance.extras)
            if isinstance(value, dict):
                value = flatten_dict(value)

        return value or {}
