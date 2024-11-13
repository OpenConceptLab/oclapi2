from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry
from pydash import get

from core.common.utils import jsonify_safe, flatten_dict
from core.mappings.models import Mapping


@registry.register_document
class MappingDocument(Document):
    class Index:
        name = 'mappings'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    class Django:
        model = Mapping
        fields = [
            'external_id'
        ]

    last_update = fields.DateField(attr='updated_at')
    updated_by = fields.KeywordField(attr='updated_by.username')
    owner = fields.KeywordField(attr='owner_name', normalizer="lowercase")
    owner_type = fields.KeywordField(attr='owner_type')
    source = fields.KeywordField(attr='source', normalizer="lowercase")
    retired = fields.KeywordField(attr='retired')
    is_latest_version = fields.KeywordField(attr='is_latest_version')
    is_in_latest_source_version = fields.KeywordField(attr='is_in_latest_source_version')
    map_type = fields.KeywordField(attr='map_type', normalizer="lowercase")
    from_concept = fields.ListField(fields.TextField())
    to_concept = fields.ListField(fields.TextField())
    from_concept_owner = fields.KeywordField(attr='from_source_owner')
    to_concept_owner = fields.KeywordField(attr='to_source_owner')
    from_concept_owner_type = fields.KeywordField(attr='from_source_owner_type')
    to_concept_owner_type = fields.KeywordField(attr='to_source_owner_type')
    from_concept_source = fields.KeywordField(attr='from_source_name')
    to_concept_source = fields.KeywordField(attr='to_source_name')
    source_version = fields.ListField(fields.KeywordField())
    collection_version = fields.ListField(fields.KeywordField())
    expansion = fields.ListField(fields.KeywordField())
    collection = fields.ListField(fields.KeywordField())
    collection_url = fields.ListField(fields.KeywordField())
    collection_owner_url = fields.ListField(fields.KeywordField())
    public_can_view = fields.BooleanField(attr='public_can_view')
    id_lowercase = fields.KeywordField(attr='mnemonic', normalizer="lowercase")
    id = fields.TextField(attr='mnemonic')
    extras = fields.ObjectField(dynamic=True)
    created_by = fields.KeywordField(attr='created_by.username')

    @staticmethod
    def get_match_phrase_attrs():
        return ['from_concept', 'to_concept']

    @staticmethod
    def get_exact_match_attrs():
        return {
            'id': {
                'boost': 4
            },
            'to_concept': {
                'boost': 3.5,
            },
            'from_concept': {
                'boost': 3,
            },
            'external_id': {
                'boost': 2.5
            }
        }

    @staticmethod
    def get_wildcard_search_attrs():
        return {
            'id': {
                'boost': 1
            },
            'from_concept': {
                'boost': 0.8,
                'lower': False
            },
            'to_concept': {
                'boost': 0.6,
                'lower': False
            }
        }

    @staticmethod
    def get_fuzzy_search_attrs():
        return {
            'from_concept': {
                'boost': 0.8,
            },
            'to_concept': {
                'boost': 0.6,
            }
        }

    @staticmethod
    def prepare_from_concept(instance):
        from_concept_name = get(instance, 'from_concept_name') or get(instance, 'from_concept.display_name')
        return [instance.from_concept_code, from_concept_name]

    @staticmethod
    def prepare_to_concept(instance):
        return [instance.get_to_concept_code(), instance.get_to_concept_name()]

    @staticmethod
    def prepare_source_version(instance):
        return list(instance.sources.values_list('version', flat=True))

    @staticmethod
    def prepare_collection_version(instance):
        return list(set(instance.expansion_set.values_list('collection_version__version', flat=True)))

    @staticmethod
    def prepare_expansion(instance):
        return list(instance.expansion_set.values_list('mnemonic', flat=True))

    @staticmethod
    def prepare_collection(instance):
        return list(set(instance.expansion_set.values_list('collection_version__mnemonic', flat=True)))

    @staticmethod
    def prepare_collection_url(instance):
        return list(set(list(instance.expansion_set.values_list('collection_version__uri', flat=True))))

    @staticmethod
    def prepare_collection_owner_url(instance):
        return list(set(expansion.owner_url for expansion in instance.expansion_set.all()))

    @staticmethod
    def prepare_extras(instance):
        value = {}

        if instance.extras:
            value = jsonify_safe(instance.extras)
            if isinstance(value, dict):
                value = flatten_dict(value)

        return value or {}
