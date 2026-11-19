from collections import defaultdict
from itertools import islice

from django.conf import settings
from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry
from pydash import compact, get

from core.common.constants import HEAD
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
# Each release's own mapped codes, where they differ from the doc's (OpenConceptLab/ocl_online#703)
RELEASE_MAP_CODES = 'release_map_codes'
MAP_CODE_FIELDS = ('same_as_map_codes', 'other_map_codes')
MAPPED_CODES_PATH = 'mapped_codes'
MAPPING_VALUES = ('map_type', 'to_concept_code', 'to_source_url')


def to_mapped_codes(mappings):
    """(same_as_map_codes, other_map_codes, mapped_codes) of mapping values (MAPPING_VALUES dicts)."""
    same_as_mapped_codes = []
    other_mapped_codes = []
    verbose_info = []
    for value in mappings:
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


def get_codes_key(codes):
    same_as_mapped_codes, other_mapped_codes, verbose_info = codes
    return (sorted(same_as_mapped_codes), sorted(other_mapped_codes),
            sorted((info['source'], info['code'], info['map_type']) for info in verbose_info))


class MappedCodes:
    """
    The mapped-code fields of concept rows' search documents, loaded for many rows at once
    (OpenConceptLab/ocl_online#703). A row unchanged since a release stays one row, so one document serves HEAD and
    every release that holds it:
    - its own fields hold HEAD's codes for HEAD's rows (the versioned object and the latest version, which global
      search reads too) and for a row no release holds (one only a collection holds); otherwise the codes of every
      release that holds it, which collection searches read;
    - each release whose own mappings (the active ones, as Concept.get_version_mappings reads them) give other codes
      is in an entry of release_map_codes (one per distinct set of codes, naming its releases by id), which a search
      in that release reads instead (ConceptDocument.scope_map_codes_to_release).
    Two queries for all the rows, plus HEAD's mappings for each of HEAD's rows.
    """
    def __init__(self, concepts):
        from core.mappings.models import Mapping
        self.releases = defaultdict(list)  # concept id: [source version id]
        self.mappings = defaultdict(dict)  # (source version id, versioned object id): {mapping id: mapping values}
        ids = [concept.id for concept in concepts if concept.id]
        for concept_id, source_id in Concept.sources.through.objects.filter(
                concept_id__in=ids).exclude(source__version=HEAD).order_by('source_id').values_list(
                    'concept_id', 'source_id'):
            self.releases[concept_id].append(source_id)
        if not self.releases:
            return
        source_ids = {source_id for releases in self.releases.values() for source_id in releases}
        versioned_object_ids = {concept.versioned_object_id for concept in concepts if concept.id in self.releases}
        for source_id, versioned_object_id, *values in Mapping.sources.through.objects.filter(
                source_id__in=source_ids, mapping__from_concept__versioned_object_id__in=versioned_object_ids,
                mapping__is_active=True
        ).values_list('source_id', 'mapping__from_concept__versioned_object_id', 'mapping_id',
                      *(f'mapping__{value}' for value in MAPPING_VALUES)):
            self.mappings[(source_id, versioned_object_id)][values[0]] = dict(zip(MAPPING_VALUES, values[1:]))

    def get_release_mappings(self, concept, source_id):
        return self.mappings.get((source_id, concept.versioned_object_id), {})

    def get_fields(self, concept):
        releases = self.releases.get(concept.id, [])
        if concept.is_versioned_object or concept.is_latest_version or not releases:
            codes = to_mapped_codes(concept.get_unidirectional_mappings().values(*MAPPING_VALUES))
        else:  # every release's mappings, once each
            codes = to_mapped_codes({
                mapping_id: values for source_id in releases
                for mapping_id, values in self.get_release_mappings(concept, source_id).items()}.values())
        entries = {}  # one per distinct set of codes, with every release that has it
        own_key = get_codes_key(codes)
        for source_id in releases:
            release_codes = to_mapped_codes(self.get_release_mappings(concept, source_id).values())
            key = get_codes_key(release_codes)
            if key != own_key:
                entries.setdefault(repr(key), {'source_version_id': [], **self.to_fields(release_codes)})[
                    'source_version_id'].append(source_id)
        return {**self.to_fields(codes), RELEASE_MAP_CODES: list(entries.values())}

    @staticmethod
    def to_fields(codes):
        same_as_mapped_codes, other_mapped_codes, verbose_info = codes
        return {
            'same_as_map_codes': same_as_mapped_codes, 'other_map_codes': other_mapped_codes,
            MAPPED_CODES_PATH: verbose_info
        }


@registry.register_document
class ConceptDocument(Document):
    class Index:
        name = 'concepts'
        settings = {
            'number_of_shards': settings.ES_CONCEPTS_SHARDS,
            'number_of_replicas': settings.ES_CONCEPTS_REPLICAS
        }

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
    release_map_codes = fields.NestedField(
        properties={
            'source_version_id': fields.LongField(),
            'same_as_map_codes': fields.KeywordField(),
            'other_map_codes': fields.KeywordField(),
            'mapped_codes': fields.NestedField(
                properties={
                    'source': fields.KeywordField(),
                    'map_type': fields.KeywordField(),
                    'code': fields.KeywordField(),
                }
            ),
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
    _mapped_codes = None  # the MappedCodes of the chunk being prepared, see _get_actions

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
            self._vectors = ConceptVectors()
            self._mapped_codes = MappedCodes(chunk)
            try:
                # Not self.get_actions, which calls this method. A list, so every doc in the chunk is prepared before
                # resolve() fills in their vectors.
                actions = list(super()._get_actions(chunk, action))
                self._vectors.resolve()
                self.vectors_reused += self._vectors.reused
                self.texts_encoded += self._vectors.encoded
            finally:
                self._vectors = None
                self._mapped_codes = None
            yield from actions

    def prepare(self, instance):
        self._source_versions = list(instance.sources.values_list('version', 'match_algorithms'))
        try:
            data = super().prepare(instance)
        finally:
            versions_match_algorithms = [match_algorithms for _, match_algorithms in self._source_versions]
            self._source_versions = None
        data.update((self._mapped_codes or MappedCodes([instance])).get_fields(instance))

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
        vectors = self._vectors or ConceptVectors()
        vectors.add(instance, [data['_embeddings'], *data['_synonyms_embeddings']])
        if self._vectors is None:
            vectors.resolve()

    @classmethod
    def scope_map_codes_to_release(cls, query, release_id, inner_hit_names=None):
        """
        `query` (a query dict) with each clause on mapped codes reading the own codes of the release (a source version)
        with id `release_id` (OpenConceptLab/ocl_online#703): from the doc's entry for that release where it has one,
        else from the doc's fields. A doc without an entry matches and scores as before.
        inner_hit_names: an iterator of names; given, an entry's matching mapped-code field is highlighted in the
        hit's inner hits under the next name (see CustomESSearch.get_highlight).
        """
        if isinstance(query, list):
            return [cls.scope_map_codes_to_release(clause, release_id, inner_hit_names) for clause in query]
        if not isinstance(query, dict):
            return query
        if cls.is_map_codes_clause(query):
            in_release = {'term': {f'{RELEASE_MAP_CODES}.source_version_id': release_id}}
            release_clause = cls.to_release_map_codes_clause(query)
            entry = {'path': RELEASE_MAP_CODES, 'query': {'bool': {'filter': [in_release], 'must': [release_clause]}}}
            field = next(iter(next(iter(query.values()))))
            if inner_hit_names is not None and field in MAP_CODE_FIELDS:
                entry['inner_hits'] = {'name': next(inner_hit_names), 'size': 1, '_source': False,
                                       'highlight': {'fields': {f'{RELEASE_MAP_CODES}.{field}': {}}}}
            return {'bool': {'minimum_should_match': 1, 'should': [
                {'bool': {'must': [query], 'must_not': [{'nested': {'path': RELEASE_MAP_CODES, 'query': in_release}}]}},
                {'nested': entry},
            ]}}
        return {
            key: cls.scope_map_codes_to_release(value, release_id, inner_hit_names) for key, value in query.items()}

    @staticmethod
    def is_map_codes_clause(query):
        """Whether `query` is a leaf clause on a mapped-code field, or a nested clause on mapped_codes."""
        if len(query) != 1:
            return False
        query_type, body = next(iter(query.items()))
        if not isinstance(body, dict):
            return False
        if query_type == 'nested':
            return body.get('path') == MAPPED_CODES_PATH
        return any(field in body for field in MAP_CODE_FIELDS)

    @classmethod
    def to_release_map_codes_clause(cls, query):
        """A mapped-codes clause, on the same fields of a release_map_codes entry."""
        if isinstance(query, list):
            return [cls.to_release_map_codes_clause(clause) for clause in query]
        if not isinstance(query, dict):
            return query
        clause = {}
        for key, value in query.items():
            if key in MAP_CODE_FIELDS or key.startswith(f'{MAPPED_CODES_PATH}.'):
                key = f'{RELEASE_MAP_CODES}.{key}'
            elif key == 'path' and value == MAPPED_CODES_PATH:
                value = f'{RELEASE_MAP_CODES}.{MAPPED_CODES_PATH}'
            clause[key] = cls.to_release_map_codes_clause(value)
        return clause
