import re
import urllib

from django.db.models import Case, When, IntegerField
from elasticsearch_dsl import FacetedSearch, Q
from pydash import compact, get

from core.common.constants import ES_REQUEST_TIMEOUT
from core.common.utils import is_url_encoded_string


class CustomESFacetedSearch(FacetedSearch):
    def __init__(self, query=None, filters=None, sort=(), _search=None):  # pylint: disable=dangerous-default-value
        self._search = _search
        super().__init__(query=query, filters=filters or {}, sort=sort)

    @staticmethod
    def format_search_str(search_str):
        return f"{search_str}*".replace('**', '*')

    def query(self, search, query):
        if self._search:
            from_search = self._search.to_dict()
            return search.update_from_dict(from_search)
        if query:
            search_str = self.format_search_str(query)
            if self.fields:
                return search.filter('query_string', fields=self.fields, query=search_str)

            return search.query('multi_match', query=search_str)
        return search

    def params(self, **kwargs):
        self._s = self._s.params(**kwargs)


class CustomESSearch:
    MUST_HAVE_PREFIX = '+'
    MUST_NOT_HAVE_PREFIX = ' -'
    MUST_HAVE_REGEX = fr'\{MUST_HAVE_PREFIX}(\w+)'
    MUST_NOT_HAVE_REGEX = fr'\{MUST_NOT_HAVE_PREFIX}(\w+)'

    def __init__(self, dsl_search, document=None):
        self._dsl_search = dsl_search
        self.document = document
        self.queryset = None
        self.max_score = None
        self.scores = {}
        self.highlights = {}
        self.score_stats = None
        self.score_distribution = None
        self.total = 0

    @classmethod
    def get_must_haves(cls, search_str):
        return set(re.findall(cls.MUST_HAVE_REGEX, search_str))

    @classmethod
    def get_must_not_haves(cls, search_str):
        return set(re.findall(cls.MUST_NOT_HAVE_REGEX, search_str))

    @staticmethod
    def get_wildcard_search_string(_str):
        return f"{_str}*".replace(' ', '*').replace('**', '*')

    @staticmethod
    def get_search_string(search_str, lower=True, decode=True):
        if lower:
            search_str = str(search_str).lower()
        if decode:
            search_str = str(search_str).replace('**', '*')
            starts_with_asterisk = search_str.startswith('*')
            ends_with_asterisk = search_str.endswith('*')
            if starts_with_asterisk:
                search_str = search_str[1:]
            if ends_with_asterisk:
                search_str = search_str[:-1]
            search_str = search_str if is_url_encoded_string(search_str) else urllib.parse.quote_plus(search_str)
            if starts_with_asterisk:
                search_str = f'*{search_str}'
            if ends_with_asterisk:
                search_str = f'{search_str}*'

        return search_str

    @staticmethod
    def get_fuzzy_match_criterion(search_str, fields, boost_divide_by=10, expansions=5):
        criterion = None
        for attr, meta in fields.items():
            criteria = CustomESSearch.fuzzy_criteria(search_str, attr, meta['boost'] / boost_divide_by, expansions)
            criterion = criteria if criterion is None else criterion | criteria
        return criterion

    @staticmethod
    def get_wildcard_match_criterion(search_str, fields):
        cls = CustomESSearch
        criterion = None
        code_fields = ['id', 'same_as_map_codes', 'other_map_codes', 'mnemonic']
        _fields = {k: v for k, v in fields.items() if k not in code_fields} if ' ' in search_str else fields
        _code_fields = {k: v for k, v in fields.items() if k in code_fields}
        for attr, meta in _fields.items():
            lower = meta['lower'] if 'lower' in meta else True
            decode = meta['decode'] if 'decode' in meta else True
            _search_str = cls.get_wildcard_search_string(
                cls.get_search_string(search_str, decode=decode, lower=lower)
            )
            criteria = cls.get_wildcard_criteria(attr, _search_str, meta['boost'])
            criterion = criteria if criterion is None else criterion | criteria
        for attr, meta in _code_fields.items():
            lower = meta['lower'] if 'lower' in meta else True
            decode = meta['decode'] if 'decode' in meta else True
            _search_str = cls.get_wildcard_search_string(
                cls.get_search_string(f"*{search_str}", decode=decode, lower=lower)
            )
            criteria = cls.get_wildcard_criteria(attr, _search_str, meta['boost'])
            criterion = criteria if criterion is None else criterion | criteria
        return criterion

    @staticmethod
    def get_exact_match_criterion(
            search_str, match_phrase_fields_list, match_word_fields_map):
        criterion = None
        if match_phrase_fields_list:
            criterion = CustomESSearch.get_match_phrase_criteria(match_phrase_fields_list[0], search_str, 5)
            for attr in match_phrase_fields_list[1:]:
                criterion |= CustomESSearch.get_match_phrase_criteria(attr, search_str, 5)

        for field, meta in match_word_fields_map.items():
            if ' or ' in search_str.lower():
                criteria = CustomESSearch.get_or_match_criteria(field, search_str, meta['boost'])
            else:
                criteria = CustomESSearch.get_match_criteria(field, search_str, meta['boost'])
            criterion = criteria if criterion is None else criterion | criteria
        return criterion

    @staticmethod
    def get_match_phrase_criteria(field, search_str, boost):
        if field in ['external_id', '_name', '_synonyms'] or field.startswith('_'):
            return CustomESSearch.get_term_match_criteria(field, search_str, boost)

        return Q(
            'match_phrase', **{field: {'query': search_str.lower(), 'boost': boost + 75}}
        ) | CustomESSearch.get_prefix_criteria(field, search_str.lower(), boost + 50)

    @staticmethod
    def get_term_match_criteria(field, search_str, boost):
        return Q('term', **{field: {'value': search_str, 'boost': boost + 100, 'case_insensitive': True}})

    @staticmethod
    def get_prefix_criteria(field, search_str, boost):
        return Q('match_phrase_prefix', **{field: {'query': search_str.lower(), 'boost': boost, "max_expansions": 20}})

    @staticmethod
    def get_match_criteria(field, search_str, boost):
        return Q(
            'match',
            **{
                field: {
                    'query': search_str,
                    'boost': boost,
                    'auto_generate_synonyms_phrase_query': False,
                    'operator': 'AND'
                }
            }
        )

    @staticmethod
    def get_or_match_criteria(field, search_str, boost):
        return Q(
            'match',
            **{
                field: {
                    'query': search_str,
                    'boost': boost,
                    'auto_generate_synonyms_phrase_query': False,
                    'operator': 'OR'
                }
            }
        )

    @staticmethod
    def get_wildcard_criteria(field, search_str, boost):
        return Q("wildcard", **{field: {'value': search_str, 'boost': boost, 'case_insensitive': True}})

    @staticmethod
    def fuzzy_criteria(search_str, field, boost=0, max_expansions=10):
        criterion = CustomESSearch.__fuzzy_criteria(boost, field, max_expansions, search_str)
        words = compact(search_str.split())
        if len(words) > 1:
            for word in words:
                criterion |= CustomESSearch.__fuzzy_criteria(boost, field, max_expansions, word)
        return criterion

    @staticmethod
    def __fuzzy_criteria(boost, field, max_expansions, word):
        return Q(
            {'fuzzy': {field: {'value': word, 'boost': boost, 'fuzziness': 'AUTO', 'max_expansions': max_expansions}}})

    def apply_aggregation_score_histogram(self):
        self._dsl_search.aggs.bucket(
            "distribution", "histogram", script="_score", interval=1, min_doc_count=1)

    def apply_aggregation_score_stats(self):
        self._dsl_search.aggs.bucket("score", "stats", script="_score")

    def to_queryset(self, keep_order=True, normalized_score=False, exact_count=True):  # pylint:disable=too-many-locals
        """
        This method return a django queryset from the an elasticsearch result.
        It cost a query to the sql db.
        """
        s, hits, total = self.__get_response(exact_count)
        max_score = hits.max_score or 1

        for result in hits.hits:
            _id = get(result, '_id')
            self.scores[int(_id)] = {
                'raw': get(result, '_score'),
                'normalized': (get(result, '_score') or 0) / max_score
            } if normalized_score else get(result, '_score')
            highlight = get(result, 'highlight')
            if highlight:
                self.highlights[int(_id)] = highlight.to_dict()
        if self.document and self.document.__name__ == 'RepoDocument':
            from core.sources.models import Source
            from core.collections.models import Collection
            qs = compact([
                (Source if result.meta.index == 'sources' else Collection).objects.filter(
                    id=result.meta.id
                ).first() for result in s
            ])
        else:
            pks = [result.meta.id for result in s]
            if len(pks) == 1:
                qs = self._dsl_search._model.objects.filter(pk=pks[0])  # pylint: disable=protected-access
            else:
                qs = self._dsl_search._model.objects.filter(pk__in=pks)  # pylint: disable=protected-access
            if keep_order:
                preserved_order = Case(
                    *[When(pk=pk, then=pos) for pos, pk in enumerate(pks)],
                    output_field=IntegerField()
                )
                qs = qs.order_by(preserved_order)
        self.queryset = qs
        self.total = total or 0

    def get_aggregations(self, verbose=False, raw=False):
        s, _, total = self.__get_response()

        result = s.aggs.to_dict()
        if raw:
            return result
        self.max_score = result['score']['max']
        self.total = total or 0
        return self._get_score_buckets(
            self.max_score, result['distribution']['buckets'], verbose)

    @staticmethod
    def _get_score_buckets(max_score, buckets, verbose=False):
        high_threshold = max_score * 0.8
        low_threshold = max_score * 0.5

        def get_confidence(threshold):
            return round((threshold/max_score) * 100, 2)

        def build_confidence(_bucket):
            scores = _bucket['scores']
            if scores:
                _bucket['confidence'] = f"~{get_confidence(sum(scores) / len(scores))}%"
            if not verbose:
                _bucket = {k: v for k, v in _bucket.items() if k in ['name', 'threshold', 'total', 'confidence']}
            return _bucket

        def build_bucket(name, confidence_threshold, threshold=None, confidence_prefix='>='):
            threshold = threshold or confidence_threshold
            return {
                'name': name,
                'threshold': round(threshold, 2),
                'scores': [],
                'doc_counts': [],
                'confidence': f"{confidence_prefix}{get_confidence(confidence_threshold)}%",
                'total': 0
            }

        def append_to_bucket(_bucket, _score, count):
            _bucket['scores'].append(_score)
            _bucket['doc_counts'].append(count)
            _bucket['total'] += count

        high = build_bucket('high', high_threshold)
        medium = build_bucket('medium', low_threshold)
        low = build_bucket('low', low_threshold, 0.01, '<')

        for bucket in buckets:
            score = bucket['key']
            doc_count = bucket['doc_count']

            if score >= high_threshold:
                append_to_bucket(high, score, doc_count)
            elif score < low_threshold:
                append_to_bucket(low, score, doc_count)
            else:
                append_to_bucket(medium, score, doc_count)

        return [build_confidence(high), build_confidence(medium), build_confidence(low)]

    def __get_response(self, exact_count=True):
        # Do not query again if the es result is already cached
        total = None
        if not hasattr(self._dsl_search, '_response'):
            # We only need the meta fields with the models ids
            s = self._dsl_search.source(False)
            s = s.params(request_timeout=ES_REQUEST_TIMEOUT)
            if exact_count:
                total = s.count()
            s = s.params(track_total_hits=False, request_cache=True)
            s = s.execute()
            hits = s.hits
            self.max_score = hits.max_score
            return s, hits, total
        return self._dsl_search, None, total
