from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry
from elasticsearch_dsl import DenseVector
from pydash import compact, get

from core.common.utils import jsonify_safe, flatten_dict, get_embeddings, drop_version
from core.concepts.models import Concept


@registry.register_document
class ConceptDocument(Document):
    class Index:
        name = 'concepts'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    id = fields.TextField(attr='mnemonic')
    id_lowercase = fields.KeywordField(attr='mnemonic', normalizer="lowercase")
    numeric_id = fields.LongField()
    name = fields.TextField()
    _name = fields.KeywordField()
    last_update = fields.DateField(attr='updated_at')
    updated_by = fields.KeywordField(attr='updated_by.username')
    locale = fields.ListField(fields.KeywordField())
    synonyms = fields.ListField(fields.TextField())
    _synonyms = fields.ListField(fields.KeywordField())
    source = fields.KeywordField(attr='parent_resource', normalizer="lowercase")
    owner = fields.KeywordField(attr='owner_name', normalizer="lowercase")
    owner_type = fields.KeywordField(attr='owner_type')
    source_version = fields.ListField(fields.KeywordField())
    collection_version = fields.ListField(fields.KeywordField())
    expansion = fields.ListField(fields.KeywordField())
    collection = fields.ListField(fields.KeywordField())
    collection_url = fields.ListField(fields.KeywordField())
    collection_owner_url = fields.ListField(fields.KeywordField())
    public_can_view = fields.BooleanField(attr='public_can_view')
    datatype = fields.KeywordField(attr='datatype', normalizer="lowercase")
    concept_class = fields.KeywordField(attr='concept_class', normalizer="lowercase")
    retired = fields.KeywordField(attr='retired')
    is_latest_version = fields.KeywordField(attr='is_latest_version')
    is_in_latest_source_version = fields.KeywordField(attr='is_in_latest_source_version')
    extras = fields.ObjectField(dynamic=True)
    created_by = fields.KeywordField(attr='created_by.username')
    name_types = fields.ListField(fields.KeywordField())
    description_types = fields.ListField(fields.KeywordField())
    description = fields.TextField()
    same_as_map_codes = fields.ListField(fields.KeywordField())
    other_map_codes = fields.ListField(fields.KeywordField())
    mapped_codes = fields.NestedField(
        properties={
            'source': fields.KeywordField(),
            'map_type': fields.KeywordField(),
            'code': fields.KeywordField(),
        }
    )
    _embeddings = fields.NestedField(
        properties={
            "vector": {
                "type": "dense_vector",
            },
            "type": {
                "type": "text"
            }
        }
    )
    _synonyms_embeddings = fields.NestedField(
        properties={
            "vector": {
                "type": "dense_vector",
            },
            "type": {
                "type": "text"
            }
        }
    )

    class Django:
        model = Concept
        fields = [
            'version',
            'external_id',
        ]

    @staticmethod
    def get_match_phrase_attrs():
        return ['_name']

    @staticmethod
    def get_exact_match_attrs():
        return {
            'id': {
                'boost': 50
            },
            'name': {
                'boost': 15
            },
            'external_id': {
                'boost': 6
            },
            'same_as_map_codes': {
                'boost': 5.5,
            },
            'other_map_codes': {
                'boost': 5,
            },
        }

    @staticmethod
    def get_wildcard_search_attrs():
        return {
            'id': {
                'boost': 35
            },
            'name': {
                'boost': 23
            },
            'synonyms': {
                'boost': 0.3,
                'wildcard': True,
                'lower': False
            },
            'same_as_map_codes': {
                'boost': 0.2,
                'wildcard': True,
                'lower': True
            },
            'other_map_codes': {
                'boost': 0.1,
                'wildcard': True,
                'lower': True
            },
            'description': {
                'boost': 0,
                'wildcard': True,
                'lower': False
            },
        }

    @staticmethod
    def get_fuzzy_search_attrs():
        return {
            'name': {
                'boost': 10
            },
            'synonyms': {
                'boost': 0.3,
            },
        }

    @staticmethod
    def prepare_numeric_id(instance):
        if len(instance.mnemonic) > 19:  # long (-9223372036854775808 - 9223372036854775807)
            return 0
        try:
            return int(instance.mnemonic)
        except:  # pylint: disable=bare-except
            return 0

    @staticmethod
    def prepare_locale(instance):
        return compact(set(instance.names.values_list('locale', flat=True)))

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

    @staticmethod
    def prepare_name_types(instance):
        return compact(set(instance.names.values_list('type', flat=True)))

    @staticmethod
    def prepare_description_types(instance):
        return compact(set(instance.descriptions.values_list('type', flat=True)))

    @staticmethod
    def prepare_description(instance):
        return '. '.join(compact(set(instance.descriptions.values_list('name', flat=True))))

    def prepare(self, instance):
        data = super().prepare(instance)

        same_as_mapped_codes, other_mapped_codes, verbose_info = self.get_mapped_codes(instance)
        data['same_as_map_codes'] = same_as_mapped_codes
        data['other_map_codes'] = other_mapped_codes
        data['mapped_codes'] = verbose_info

        preferred_locale = instance.preferred_locale
        name = get(preferred_locale, 'name') or ''
        data['_name'] = name.lower()
        data['name'] = name.replace('-', '_')
        data['_embeddings'] = {
            'vector': get_embeddings(name),
            'type': get(preferred_locale, 'type'),
            'locale': get(preferred_locale, 'locale')
        }

        synonyms = instance.names.exclude(name=name).exclude(name='')
        data['synonyms'] = compact(set(synonyms.values_list('name', flat=True)))
        data['_synonyms'] = data['synonyms']
        data['_synonyms_embeddings'] = [
            {
                'vector': get_embeddings(s.name),
                'type': get(s, 'type'),
                'locale': get(s, 'locale')
            } for s in synonyms
        ]
        return data

    @staticmethod
    def get_mapped_codes(instance):
        mappings = instance.get_unidirectional_mappings()
        same_as_mapped_codes = []
        other_mapped_codes = []
        verbose_info = []
        for value in mappings.values('map_type', 'to_concept_code', 'to_source_url'):
            to_concept_code = value['to_concept_code']
            map_type = value['map_type']
            if to_concept_code and map_type:
                to_source_url = drop_version(value['to_source_url']) if value['to_source_url'] else None
                if to_source_url:
                    verbose_info.append(
                        {'source': to_source_url, 'code': to_concept_code, 'map_type': map_type})
                if map_type.lower().startswith('same'):
                    same_as_mapped_codes.append(to_concept_code)
                else:
                    other_mapped_codes.append(to_concept_code)
        return same_as_mapped_codes, other_mapped_codes, verbose_info
