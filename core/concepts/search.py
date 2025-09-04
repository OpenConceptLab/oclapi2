from elasticsearch_dsl import TermsFacet, Q, NestedFacet
from pydash import flatten, is_number, compact, get

from core.common.constants import FACET_SIZE, HEAD
from core.common.search import CustomESFacetedSearch, CustomESSearch
from core.common.utils import get_embeddings, is_canonical_uri
from core.concepts.models import Concept


class ConceptFacetedSearch(CustomESFacetedSearch):
    index = 'concepts'
    doc_types = [Concept]
    fields = [
        'datatype', 'concept_class', 'locale', 'retired', 'is_latest_version',
        'source', 'owner', 'owner_type', 'name', 'collection', 'name_types',
        'description_types', 'id', 'synonyms', 'extras', 'updated_by'
    ]

    base_facets = {
        'datatype': TermsFacet(field='datatype', size=100),
        'conceptClass': TermsFacet(field='concept_class', size=100),
        'locale': TermsFacet(field='locale', size=100),
        'retired': TermsFacet(field='retired'),
        'source': TermsFacet(field='source', size=FACET_SIZE),
        'collection': TermsFacet(field='collection', size=FACET_SIZE),
        'owner': TermsFacet(field='owner', size=FACET_SIZE),
        'ownerType': TermsFacet(field='owner_type'),
        'updatedBy': TermsFacet(field='updated_by', size=FACET_SIZE),
        'is_latest_version': TermsFacet(field='is_latest_version'),
        'is_in_latest_source_version': TermsFacet(field='is_in_latest_source_version'),
        'collection_owner_url': TermsFacet(field='collection_owner_url', size=FACET_SIZE),
        'expansion': TermsFacet(field='expansion', size=FACET_SIZE),
        'nameTypes': TermsFacet(field='name_types', size=FACET_SIZE),
        'descriptionTypes': TermsFacet(field='description_types', size=FACET_SIZE),
        'source_version': TermsFacet(field='source_version', size=FACET_SIZE),
        'collection_version': TermsFacet(field='collection_version', size=FACET_SIZE),
        'targetRepo': NestedFacet("mapped_codes", TermsFacet(field="mapped_codes.source", size=FACET_SIZE)),
        'targetRepoMapType': NestedFacet("mapped_codes", TermsFacet(field="mapped_codes.map_type", size=FACET_SIZE)),
    }

    def __init__(self, parent=None, **kwargs):
        facets = {**self.base_facets}
        if parent is not None:
            facets = {
                **facets,
                **self.build_property_facets_from_source(parent)
            }
        self.facets = facets
        super().__init__(**kwargs)

    @staticmethod
    def build_property_facets_from_source(parent):
        return {
            f"properties__{_filter['code']}": TermsFacet(field=f"properties.{_filter['code']}.keyword", size=FACET_SIZE)
            for _filter in (get(parent, 'filters') or [])
        }



class ConceptFuzzySearch:  # pragma: no cover
    filter_fields = []
    priority_fields = [
        ['id', 0.3],
        ['name', 0.3],
        ['synonyms', 0.1],
        ['same_as_mapped_codes', 0.1],
        ['other_map_codes', 0.1],
        ['concept_class', 'datatype', 0.1],
        ['description', 0]
    ]
    semantic_priority_fields = [
        ['id', 0.3],
        ['_name', 0],
        ['_synonyms', 0],
        ['name', 0],
        ['synonyms', 0],
        ['same_as_mapped_codes', 0.1],
        ['other_map_codes', 0.1],
        ['concept_class', 'datatype', 0.001],
    ]
    fuzzy_fields = ['name', 'synonyms']

    @staticmethod
    def get_target_repo(repo_url):
        from core.sources.models import Source
        repo, _ = Source.resolve_reference_expression(repo_url)
        if repo.id:
            return repo
        return None

    @classmethod
    def get_target_repo_params(cls, repo_url):
        return cls.get_repo_params(cls.get_target_repo(repo_url))

    @classmethod
    def get_repo_params(cls, repo):
        if repo:
            return {
                'owner': repo.parent.mnemonic,
                'owner_type': repo.parent.resource_type,
                'source_version': repo.version,
                'source': repo.mnemonic
            }
        return {}

    @staticmethod
    def get_exact_and_contains_criteria(field, value, boost=0, add_boost=True):
        return (CustomESSearch.get_match_criteria(field, value, boost) |
                Q('match_phrase', **{
                    field: {
                        'query': value,
                        'boost': 1 + boost if add_boost else boost
                    }
                }))

    @classmethod
    def search(  # pylint: disable=too-many-locals,too-many-arguments,too-many-branches,too-many-statements
            cls, data, repo_url, repo_params=None, include_retired=False,
            is_semantic=False, num_candidates=5000, k_nearest=5, map_config=None, additional_filter_criterion=None
    ):
        from core.concepts.documents import ConceptDocument
        map_config = map_config or []
        filter_query = cls.get_filter_criteria(
            data, include_retired, repo_params, repo_url, additional_filter_criterion)
        or_clauses = []

        priority_criteria = []
        fields = cls.semantic_priority_fields if is_semantic else cls.priority_fields
        for field_set in fields:
            boost = field_set[-1]
            for field in field_set[:-1]:
                value = data.get(field, None)
                if value:
                    values = value if isinstance(value, list) else [value]
                    for val in values:
                        val = val or ""
                        priority_criteria.append(CustomESSearch.get_or_match_criteria(field, val, boost))

        knn_queries = []
        name = None
        synonyms = []
        if is_semantic:
            name = data.get('name', None)
            synonyms = data.get('synonyms')
            if synonyms and not isinstance(synonyms, list):
                synonyms = compact([synonyms])
            synonyms = synonyms or []
            def get_knn_query(_field, _value, _boost):
                return {
                        "field": _field,
                        "query_vector": get_embeddings(_value),
                        "k": k_nearest,
                        "num_candidates": num_candidates,
                        "filter": filter_query,
                        "boost": _boost
                }
            if name:
                knn_queries.append(get_knn_query("_embeddings.vector", name, 0.3))
                knn_queries.append(get_knn_query("_synonyms_embeddings.vector", name, 0.275))
            for synonym in synonyms:
                if synonym is not None:
                    knn_queries.append(get_knn_query("_synonyms_embeddings.vector", synonym, 0.125))
                    knn_queries.append(get_knn_query("_embeddings.vector", synonym, 0.15))
        else:
            for field in cls.fuzzy_fields:
                value = data.get(field, None)
                if value:
                    values = value if isinstance(value, list) else [value]
                    for val in compact(values):
                        val = str(val) or ""
                        _search_str = CustomESSearch.get_wildcard_search_string(
                            CustomESSearch.get_search_string(val, decode=True, lower=True)
                        )
                        priority_criteria.append(CustomESSearch.get_wildcard_criteria(field, _search_str, 0.01))
                        priority_criteria.append(CustomESSearch.fuzzy_criteria(val, field, 0, 3))

        if priority_criteria:
            combined_or = None
            for criteria in priority_criteria:
                combined_or = criteria if combined_or is None else combined_or | criteria
            or_clauses.append(combined_or)

        nested_mapped_codes_queries = cls.get_mapped_code_queries(data, map_config)

        if nested_mapped_codes_queries:
            or_clauses.append(Q("bool", should=nested_mapped_codes_queries, minimum_should_match=1, boost=0.1))

        wrapped_clauses = [
            Q("bool", must=[Q(filter_query), clause])
            for clause in or_clauses
        ]

        search = ConceptDocument.search()
        for knn_query in knn_queries:
            search = search.knn(**knn_query)
        if wrapped_clauses:
            search = search.query(Q("bool", should=wrapped_clauses, minimum_should_match=1))
        else:
            search = search.query(Q("bool", must=[Q(filter_query)]))

        if is_semantic:
            rescore_query = []
            if name:
                rescore_query.append(Q("term", _name={"value": name, "case_insensitive": True, "boost": 3}))
                synonyms = [name, *synonyms]
            for synonym in (synonyms or []):
                rescore_query.append(Q("term", _synonyms={"value": synonym, "case_insensitive": True, "boost": 1}))
            if rescore_query:
                search = search.extra(rescore={
                    "window_size": 500,
                    "query": {
                        "score_mode": "total",
                        "query_weight": 1.0,
                        "rescore_query_weight": 20.0,
                        "rescore_query": {
                            "bool": {
                                "should": rescore_query
                            }
                        },
                    },
                })

        highlight = [field for field in flatten([*cls.fuzzy_fields, *fields]) if not is_number(field)]
        search = search.highlight(*highlight)
        search = search.sort({'_score': {'order': 'desc'}})
        return search

    @classmethod
    def get_mapped_code_queries(cls, data, map_config):
        mapped_codes = cls.get_mapped_codes(data, map_config)
        nested_mapped_codes_queries = []
        for mapped_code in mapped_codes:
            source = mapped_code.get('source', None)
            code = mapped_code.get('code', None)
            map_type = mapped_code.get('map_type', None)
            queries = []
            if source:
                queries.append(Q("term", **{"mapped_codes.source": source}))
            if code:
                queries.append(Q("term", **{"mapped_codes.code": code}))
            if map_type:
                queries.append(Q("term", **{"mapped_codes.map_type": map_type}))
            if queries:
                nested_mapped_codes_queries.append(Q("nested", path="mapped_codes", query=Q("bool", must=queries)))
        return nested_mapped_codes_queries

    @classmethod
    def get_mapped_codes(cls, data, map_config):  # pylint: disable=too-many-locals
        from core.sources.models import Source

        mapped_codes = []
        for config in map_config:
            config_type = config.get('type')
            column = config.get('input_column')
            target_urls = config.get('target_urls') or []
            target_source_url = config.get('target_source_url') or None
            delimiter = config.get('delimiter') or ','
            separator = config.get('separator') or ':'
            is_list = config_type == 'mapping-list'

            if not config_type or not column:
                continue
            if (is_list and not target_urls) or (not is_list and not target_source_url):
                continue
            value = data.get(column) or None
            if not value:
                continue

            mapped_code = {'source': None, 'code': None}
            if is_list:
                values = {}
                for val in value.split(delimiter):
                    parts = val.strip().split(separator)
                    values[parts[0].strip().lower()] = parts[1].strip() if len(parts) > 1 else None
                for source_code, url in target_urls.items():
                    if url and is_canonical_uri(url):
                        repo, _ = Source.resolve_reference_expression(url, version=HEAD)
                        url = repo.uri if repo and repo.id else None
                    mapped_code['source'] = url
                    mapped_code['code'] = values.get(source_code.strip().lower()) or None
            else:
                if is_canonical_uri(target_source_url):
                    repo, _ = Source.resolve_reference_expression(target_source_url, version=HEAD)
                    target_source_url = repo.uri if repo and repo.id else None
                mapped_code['source'] = target_source_url
                mapped_code['code'] = value

            if mapped_code['source'] and mapped_code['code']:
                mapped_codes.append(mapped_code)
        return mapped_codes

    @classmethod
    def get_filter_criteria(cls, data, include_retired, repo_params, repo_url, additional_filter_criterion=None):  # pylint: disable=too-many-arguments
        must_clauses = []
        repo_params = repo_params or cls.get_target_repo_params(repo_url)
        for field, value in repo_params.items():
            must_clauses.append(Q('match', **{field: value}))
        if not include_retired:
            must_clauses.append(Q('match', retired=False))
        for field in cls.filter_fields:
            value = data.get(field)
            if value:
                must_clauses.append(Q('match', **{field: value}))

        if additional_filter_criterion:
            must_clauses.append(additional_filter_criterion)

        return Q("bool", must=must_clauses)

    @classmethod
    def get_search_results(cls, row, repo_url, offset=0, limit=5):
        from core.concepts.documents import ConceptDocument
        search = cls.search(row, repo_url)
        es_search = CustomESSearch(search[offset:limit], ConceptDocument)
        es_search.to_queryset()
        return es_search.queryset, es_search.scores, es_search.max_score, es_search.highlights
