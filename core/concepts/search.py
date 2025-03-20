from elasticsearch_dsl import TermsFacet, Q
from pydash import flatten, is_number, get, compact

from core.common.constants import FACET_SIZE
from core.common.search import CustomESFacetedSearch, CustomESSearch
from core.common.utils import get_embeddings
from core.concepts.models import Concept


class ConceptFacetedSearch(CustomESFacetedSearch):
    index = 'concepts'
    doc_types = [Concept]
    fields = [
        'datatype', 'concept_class', 'locale', 'retired', 'is_latest_version',
        'source', 'owner', 'owner_type', 'name', 'collection', 'name_types',
        'description_types', 'id', 'synonyms', 'extras', 'updated_by'
    ]

    facets = {
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
        ['same_as_mapped_codes', 0.1],
        ['other_map_codes', 0.1],
        ['concept_class', 'datatype', 0.1],
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
        repo = cls.get_target_repo(repo_url)
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
    def search(  # pylint: disable=too-many-locals,too-many-arguments,too-many-branches
            cls, data, repo_url, repo_params=None, include_retired=False,
            is_semantic=False, num_candidates=5000, k_nearest=5
    ):
        from core.concepts.documents import ConceptDocument
        search = ConceptDocument.search()
        repo_params = repo_params or cls.get_target_repo_params(repo_url)
        for field, value in repo_params.items():
            search = search.query('match', **{field: value})
        if not include_retired:
            search = search.query('match', retired=False)
        for field in cls.filter_fields:
            value = data.get(field, None)
            if value:
                search = search.query('match', **{field: value})
        priority_fields_criteria = []
        fields = cls.semantic_priority_fields if is_semantic else cls.priority_fields
        for field_set in fields:
            boost = field_set[-1]
            for field in field_set[:-1]:
                value = data.get(field, None)
                if value:
                    if isinstance(value, list):
                        for val in value:
                            val = val or ""
                            priority_fields_criteria.append(CustomESSearch.get_match_criteria(field, val, boost))
                    else:
                        priority_fields_criteria.append(CustomESSearch.get_match_criteria(field, value, boost))

        if not is_semantic:
            for field in cls.fuzzy_fields:
                value = data.get(field, None)
                if value:
                    if not isinstance(value, list):
                        value = compact([value])
                    for val in value:
                        val = str(val) if val else ""
                        _search_str = CustomESSearch.get_wildcard_search_string(
                            CustomESSearch.get_search_string(val, decode=True, lower=True)
                        )
                        wildcard_criteria = CustomESSearch.get_wildcard_criteria(field, _search_str, 0.01)
                        priority_fields_criteria.append(wildcard_criteria)
                        criteria = CustomESSearch.fuzzy_criteria(val, field, 0, 3)
                        priority_fields_criteria.append(criteria)
        criterion = None
        for criteria in priority_fields_criteria:
            criterion = criteria if criterion is None else criterion | criteria
        if criterion is not None:
            search = search.query(criterion)

        if is_semantic:
            filters = get(search.to_dict(), 'query.bool.must', [])
            name = data.get('name', None)
            synonyms = data.get('synonyms', None) or []

            def get_kwargs_for_knn(_field, _value, _boost):
                return {
                    'field': _field,
                    'query_vector': get_embeddings(_value),
                    'k': k_nearest,
                    'num_candidates': num_candidates,
                    'filter': [*filters, {"match": {"name": {"query": _value, "fuzziness": "AUTO"}}}],
                    'boost': _boost
                }
            if synonyms and not isinstance(synonyms, list):
                synonyms = [synonyms]
            if name:
                search = search.knn(**get_kwargs_for_knn('_embeddings.vector', name, 5))
                synonyms = {name, *synonyms}
            for synonym in synonyms:
                search = search.knn(**get_kwargs_for_knn('_synonyms_embeddings.vector', synonym, 1))

        highlight = [field for field in flatten([*cls.fuzzy_fields, *cls.priority_fields]) if not is_number(field)]
        search = search.highlight(*highlight)
        return search.sort({'_score': {'order': 'desc'}})

    @classmethod
    def get_search_results(cls, row, repo_url, offset=0, limit=5):
        from core.concepts.documents import ConceptDocument
        search = cls.search(row, repo_url)
        es_search = CustomESSearch(search[offset:limit], ConceptDocument)
        es_search.to_queryset()
        return es_search.queryset, es_search.scores, es_search.max_score, es_search.highlights
