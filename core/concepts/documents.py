from itertools import islice

from django.conf import settings
from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry
from pydash import compact, get

from core.common.utils import jsonify_safe, flatten_extras, drop_version
from core.concepts.embeddings import ConceptVectors, needs_vectors
from core.concepts.models import Concept

# The text a vector encoded, kept in _source only: reuse reads it back, nothing searches it (ocl_online#247)
EMBEDDING_TEXT = {"type": "keyword", "index": False, "doc_values": False}
# What ocl_online#247 adds to an existing concepts index's mapping (the concept_vector_mapping command)
VECTOR_PROVENANCE_MAPPING = {
    '_embeddings': {'type': 'nested', 'properties': {'text': EMBEDDING_TEXT}},
    '_synonyms_embeddings': {'type': 'nested', 'properties': {'text': EMBEDDING_TEXT}},
    '_embeddings_model': {'type': 'keyword'},
}


@registry.register_document
class ConceptDocument(Document):
    class Index:
        name = 'concepts'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    id = fields.TextField(attr='mnemonic')
    id_lowercase = fields.KeywordField(attr='mnemonic', normalizer="lowercase")
    id_raw = fields.KeywordField(attr='mnemonic')
    numeric_id = fields.LongField()
    name = fields.TextField()
    _name = fields.KeywordField()
    last_update = fields.DateField(attr='updated_at')
    updated_by = fields.KeywordField(attr='updated_by.username')
    locale = fields.ListField(fields.KeywordField())
    synonyms = fields.ListField(fields.TextField())
    _synonyms = fields.ListField(fields.KeywordField())
    source = fields.KeywordField(attr='parent_resource', normalizer="lowercase")
    source_text = fields.TextField(attr='parent_resource', fields={'keyword': fields.KeywordField()})
    owner = fields.KeywordField(attr='owner_name', normalizer="lowercase")
    owner_text = fields.TextField(attr='owner_name', fields={'keyword': fields.KeywordField()})
    owner_type = fields.KeywordField(attr='owner_type')
    source_version = fields.ListField(fields.KeywordField())
    collection_version = fields.ListField(fields.KeywordField())
    expansion = fields.ListField(fields.KeywordField())
    expansion_url = fields.ListField(fields.KeywordField())
    collection = fields.ListField(fields.KeywordField())
    collection_url = fields.ListField(fields.KeywordField())
    collection_owner_url = fields.ListField(fields.KeywordField())
    public_can_view = fields.BooleanField(attr='public_can_view')
    datatype = fields.KeywordField(attr='datatype', normalizer="lowercase")
    datatype_text = fields.TextField(attr='datatype', fields={'keyword': fields.KeywordField()})
    concept_class = fields.KeywordField(attr='concept_class', normalizer="lowercase")
    concept_class_text = fields.TextField(attr='concept_class', fields={'keyword': fields.KeywordField()})
    retired = fields.KeywordField(attr='retired')
    is_latest_version = fields.KeywordField(attr='is_latest_version')
    is_in_latest_source_version = fields.KeywordField(attr='is_in_latest_source_version')
    extras = fields.ObjectField(dynamic=True)
    properties = fields.ObjectField(dynamic=True)
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
            },
            "text": EMBEDDING_TEXT,
        }
    )
    _synonyms_embeddings = fields.NestedField(
        properties={
            "vector": {
                "type": "dense_vector",
            },
            "type": {
                "type": "text"
            },
            "text": EMBEDDING_TEXT,
        }
    )
    _embeddings_model = fields.KeywordField()

    VECTOR_CHUNK_SIZE = 100
    _vectors = None  # the ConceptVectors of the chunk being prepared, see _get_actions
    vectors_reused = 0  # over this document instance's _get_actions calls
    texts_encoded = 0
    _source_versions = None  # (version, match_algorithms) of each repo version the row being prepared belongs to

    class Django:
        model = Concept
        fields = [
            'version',
            'external_id',
        ]

    @staticmethod
    def get_match_phrase_attrs():
        return ['_name', '_synonyms', 'name', 'synonyms']

    @staticmethod
    def get_exact_match_attrs():
        return {
            'id': {
                'boost': 15
            },
            'name': {
                'boost': 15
            },
            'synonyms': {
                'boost': 10
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
                'boost': 5
            },
            'name': {
                'boost': 3
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
        return compact(set(instance.active_names.values_list('locale', flat=True)))

    def prepare_source_version(self, instance):
        versions = self._source_versions or instance.sources.values_list('version', 'match_algorithms')
        return [version for version, _ in versions]

    @staticmethod
    def prepare_extras(instance):
        value = {}

        if instance.extras:
            value = jsonify_safe(instance.extras)
            if isinstance(value, dict):
                value = flatten_extras(value)

        return value or {}

    @staticmethod
    def prepare_properties(instance):
        value = {}

        filters = instance.filters
        properties = instance.properties
        for _filter in filters:
            prop = next((prop for prop in properties if prop['code'] == _filter['code']), None)
            if prop:
                value_key = next((key for key in prop if key.startswith('value')), None)
                value[_filter['code']] = prop.get(value_key, None)

        return value

    @staticmethod
    def prepare_name_types(instance):
        return compact(set(instance.active_names.values_list('type', flat=True)))

    @staticmethod
    def prepare_description_types(instance):
        return compact(set(instance.active_descriptions.values_list('type', flat=True)))

    @staticmethod
    def prepare_description(instance):
        return '. '.join(compact(set(instance.active_descriptions.values_list('name', flat=True))))

    def _get_actions(self, object_list, action):
        """
        As django_elasticsearch_dsl's, but prepares 100 docs at a time, so that their vectors are resolved together
        (ConceptVectors): reused where the stored docs already hold them, and the rest encoded in one batched call.
        """
        if action == 'delete':
            yield from super()._get_actions(object_list, action)
            return
        objects = iter(object_list)
        while chunk := list(islice(objects, self.VECTOR_CHUNK_SIZE)):
            self._vectors = ConceptVectors(self._index._name)
            try:
                actions = [self._prepare_action(obj, action) for obj in chunk if self.should_index_object(obj)]
                self._vectors.resolve()
                self.vectors_reused += self._vectors.reused
                self.texts_encoded += self._vectors.encoded
            finally:
                self._vectors = None
            yield from actions

    def prepare(self, instance):
        self._source_versions = list(instance.sources.values_list('version', 'match_algorithms'))
        try:
            data = super().prepare(instance)
        finally:
            versions_match_algorithms = [match_algorithms for _, match_algorithms in self._source_versions]
            self._source_versions = None
        same_as_mapped_codes, other_mapped_codes, verbose_info = self.get_mapped_codes(instance)
        data['same_as_map_codes'] = same_as_mapped_codes
        data['other_map_codes'] = other_mapped_codes
        data['mapped_codes'] = verbose_info

        preferred_locale = instance.preferred_locale
        name = get(preferred_locale, 'name') or ''
        data['_name'] = name.lower()
        data['name'] = name.replace('-', '_')
        synonyms = [n for n in instance.active_names.all() if n.name and n.name != name]
        data['synonyms'] = compact(set(n.name for n in synonyms))
        data['_synonyms'] = data['synonyms']

        if needs_vectors(instance.parent, versions_match_algorithms):
            self.add_vectors(data, instance, name, preferred_locale, synonyms)

        expansions = list(instance.expansion_set.only('mnemonic', 'uri'))
        data['expansion'] = [e.mnemonic for e in expansions]
        data['expansion_url'] = [e.uri for e in expansions]
        data['collection_version'] = list({e.collection_version_name for e in expansions})
        data['collection'] = list({e.collection_version_mnemonic for e in expansions})
        data['collection_url'] = list({e.collection_version_url for e in expansions})
        data['collection_owner_url'] = list({e.owner_url for e in expansions})

        return data

    def add_vectors(self, data, instance, name, preferred_locale, synonyms):  # pylint: disable=too-many-arguments
        """
        Vector entries for the display name and each synonym, each with the text it encodes, and the model on the doc.
        Their vectors come from the chunk's ConceptVectors once every doc in it is prepared -- or, for a doc prepared
        on its own, right away.
        """
        data['_embeddings'] = {
            'vector': None, 'text': name, 'type': get(preferred_locale, 'type'), 'locale': get(preferred_locale, 'locale')
        }
        data['_synonyms_embeddings'] = [
            {'vector': None, 'text': s.name, 'type': get(s, 'type'), 'locale': get(s, 'locale')} for s in synonyms
        ]
        data['_embeddings_model'] = settings.LM_MODEL_NAME
        vectors = self._vectors or ConceptVectors(self._index._name)
        vectors.add(instance, [data['_embeddings'], *data['_synonyms_embeddings']])
        if self._vectors is None:
            vectors.resolve()

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
