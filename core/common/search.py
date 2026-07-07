import gc
import re
import threading
import time
import urllib
from collections import OrderedDict

from cid.locals import get_cid
from django.conf import settings
from django.db.models import Case, When, IntegerField
from elasticsearch_dsl import FacetedSearch, Q
from pydash import compact, get, has, set_
from sentence_transformers import CrossEncoder
import torch

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
        if field in ['external_id', '_name', '_synonyms', 'repo_owner'] or field.startswith('_'):
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

    def to_queryset(self, keep_order=True, normalized_score=False, exact_count=True, txt=None, encoder_model=None):  # pylint:disable=too-many-locals,too-many-arguments
        """
        This method return a django queryset from the an elasticsearch result.
        It cost a query to the sql db.
        """
        encoder = bool(txt)
        s, hits, total = self.__get_response(exact_count, encoder)
        max_score = hits.max_score or 1
        cid = get_cid()
        start_time = time.time()
        hits = Reranker(encoder_model).rerank(
            txt=txt, hits=hits.hits, name_key='name', source_attr='_source', should_convert_source_to_dict=True,
            order_results=False
        ) if encoder else hits.hits
        print(f"[{cid}] Cross encoder time: {time.time() - start_time} seconds")
        for result in hits:
            _id = get(result, '_id')
            rerank_score = get(result, 'search_rerank_score')
            raw_score = get(result, '_score') or 0
            self.scores[int(_id)] = {
                'raw': raw_score,
                'rerank': rerank_score,
                'normalized': rerank_score if encoder else (raw_score / max_score)
            } if normalized_score else raw_score
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

    def __get_response(self, exact_count=True, load_fields=False):
        # Do not query again if the es result is already cached
        total = None
        if not hasattr(self._dsl_search, '_response'):
            # We only need the meta fields with the models ids
            s = self._dsl_search.source(
                excludes=['_embeddings', '_synonyms_embeddings']
            ) if load_fields else self._dsl_search.source(False)
            s = s.params(request_timeout=ES_REQUEST_TIMEOUT)
            if exact_count:
                total = s.count()
            s = s.params(track_total_hits=False, request_cache=True)
            s = s.execute()
            hits = s.hits
            self.max_score = hits.max_score
            return s, hits, total
        return self._dsl_search, None, total


class Reranker:
    """Rerank semantic search hits with model-specific score normalization."""

    ENCODERS = [
        # Best and Fastest overall lightweight medical reranker
        # Size: ~110M
        # Speed: similar to MiniLM CrossEncoder
        # Training: includes clinical, medical, question-answering datasets
        # Output: positive similarity scores (not raw logits!)
        # 0.6B params
        # https://huggingface.co/BAAI/bge-reranker-v2-m3
        "BAAI/bge-reranker-v2-m3",

        # Model: jinhybr/OA-MedBERT-cross-encoder or similar
        # Size: ~110M
        # Domain: PubMed abstracts, biomedical QA
        # Type: binary classifier (logits)
        # Not huggin face model -- ???
        # "jinhybr/OA-MedBERT-cross-encoder",

        # Model: microsoft/BioLinkBERT-base
        # Type: CrossEncoder
        # Size: ~120M
        # Domain: UMLS, PubMed, MeSH, SNOMED (closest to OCL)
        # Not huggin face model -- doesn't work with sentence_transformers
        # "microsoft/BioLinkBERT-base",

        # 22.7M params
        # https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2
        # doesn't work with logits, so not between 0-1
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
    ]
    SCORE_KEY = 'search_rerank_score'
    MISSING_SCORE = -1000000.0
    CUSTOM_ENCODER_CACHE = OrderedDict()
    CUSTOM_ENCODER_CACHE_LOCK = threading.Lock()
    CUSTOM_ENCODER_LOAD_LOCKS = {}
    DEFAULT_ENCODER_PREDICT_LOCK = threading.Lock()

    def __init__(self, model_name=None):
        self.model_name = model_name
        self.encoder_state = self._get_encoder_state(self.model_name)
        self.encoder = self.encoder_state['encoder']
        self.encoder_predict_lock = self.encoder_state['predict_lock']

    def rerank(  # pylint: disable=too-many-arguments
            self, hits, txt, name_key='name', source_attr=None, should_convert_source_to_dict=True,
            score_key=None, order_results=True):
        scores = self._predict_scores(hits, txt, name_key, source_attr, should_convert_source_to_dict)
        return self._assign_score(hits, scores, score_key, order_results)

    @property
    def default_model(self):
        return self._get_default_model_name()

    @classmethod
    def _get_default_model_name(cls):
        """Return the default boot-time reranker model name."""
        return settings.ENCODER_MODEL_NAME

    # private
    def _predict_scores(self, hits, txt, name_key, source_attr, should_convert_source_to_dict):  # pylint: disable=too-many-arguments
        if not hits or not txt:
            return []
        # Keep unscorable candidates sortable while remaining JSON-safe.
        scores_full = [self.MISSING_SCORE] * len(hits)
        if not isinstance(txt, str) or not txt.strip():
            return scores_full

        docs = [get(self._get_source(hit, source_attr, should_convert_source_to_dict), name_key) for hit in hits]
        valid = []
        for i, d in enumerate(docs):
            if isinstance(d, str) and d.strip():
                valid.append((i, d.strip()))
        if not valid:
            return scores_full
        with self.encoder_predict_lock:
            scores = self.encoder.predict([(txt, d) for _, d in valid], **self._get_predict_kwargs())
        for (i, _), s in zip(valid, scores):
            scores_full[i] = float(s)

        return scores_full

    def _get_activation_fn(self):
        """Return the score activation required by the configured reranker model."""
        model_name = self.model_name or self.default_model
        if isinstance(model_name, str) and self._is_sigmoid_model(model_name):
            return torch.nn.Sigmoid()
        return None

    @staticmethod
    def _is_sigmoid_model(model_name):
        return any(model_name == prefix or model_name.startswith(prefix)
                   for prefix in settings.RERANKER_SIGMOID_MODEL_PREFIXES)

    def _get_predict_kwargs(self):
        """Return extra kwargs for CrossEncoder.predict for the configured model."""
        activation_fn = self._get_activation_fn()
        if activation_fn is None:
            return {}
        return {'activation_fn': activation_fn}

    def _assign_score(self, hits, scores, score_key, order_results):
        score_key = score_key or self.SCORE_KEY
        key_to_set = score_key

        for hit, score in zip(hits, scores):
            key_to_set = f'search_meta.{score_key}' if has(hit, 'search_meta') else score_key
            set_(hit, key_to_set, float(score))
            set_(hit, 'search_meta.search_normalized_score', float(score) * 100)

        return self._order(hits, key_to_set) if order_results and key_to_set else hits

    @staticmethod
    def _order(hits, key_to_order):
        return sorted(hits, key=lambda hit: get(hit, key_to_order), reverse=True)

    @classmethod
    def _get_encoder_state(cls, model_name):
        if model_name and model_name != cls._get_default_model_name():
            return cls._get_custom_encoder_state(model_name)
        return cls._load_default_encoder_state()

    @classmethod
    def _get_custom_encoder_state(cls, model_name):
        """Return a bounded cached custom encoder to avoid repeated large-model loads."""
        now = time.time()
        with cls.CUSTOM_ENCODER_CACHE_LOCK:
            cls._evict_expired_custom_encoders(now)
            cached_encoder_state = cls.CUSTOM_ENCODER_CACHE.get(model_name)
            if cached_encoder_state:
                cls.CUSTOM_ENCODER_CACHE.move_to_end(model_name)
                cached_encoder_state['expires_at'] = now + cls._get_custom_encoder_cache_ttl()
                return cached_encoder_state
            load_lock = cls.CUSTOM_ENCODER_LOAD_LOCKS.setdefault(model_name, threading.Lock())

        with load_lock:
            with cls.CUSTOM_ENCODER_CACHE_LOCK:
                cls._evict_expired_custom_encoders(time.time())
                cached_encoder_state = cls.CUSTOM_ENCODER_CACHE.get(model_name)
                if cached_encoder_state:
                    cls.CUSTOM_ENCODER_CACHE.move_to_end(model_name)
                    cached_encoder_state['expires_at'] = time.time() + cls._get_custom_encoder_cache_ttl()
                    return cached_encoder_state

            loaded_encoder = cls._load_encoder(model_name)
            loaded_encoder_state = {
                'encoder': loaded_encoder,
                'predict_lock': threading.Lock(),
            }

            cached_encoder_state = None
            with cls.CUSTOM_ENCODER_CACHE_LOCK:
                cls._evict_expired_custom_encoders(time.time())
                cached_encoder_state = cls.CUSTOM_ENCODER_CACHE.get(model_name)
                if cached_encoder_state:
                    cls.CUSTOM_ENCODER_CACHE.move_to_end(model_name)
                    cached_encoder_state['expires_at'] = time.time() + cls._get_custom_encoder_cache_ttl()
                else:
                    cls._evict_custom_encoders_for_capacity()
                    # Cache size bounds cache references only; in-flight requests may still
                    # keep an evicted encoder alive briefly via their own instance state.
                    loaded_encoder_state['expires_at'] = time.time() + cls._get_custom_encoder_cache_ttl()
                    cls.CUSTOM_ENCODER_CACHE[model_name] = loaded_encoder_state

            if cached_encoder_state:
                del loaded_encoder
                del loaded_encoder_state
                cls._release_memory()
                return cached_encoder_state
            return loaded_encoder_state

    @staticmethod
    def _load_encoder(model_name):
        return CrossEncoder(model_name, device="cpu", max_length=128)

    @staticmethod
    def _load_default_encoder_state():
        return {
            'encoder': settings.ENCODER,
            'predict_lock': Reranker.DEFAULT_ENCODER_PREDICT_LOCK,
        }

    @staticmethod
    def _get_source(data, source_attr, should_convert_source_to_dict):
        source = get(data, source_attr) if source_attr else data
        if should_convert_source_to_dict and source:
            source = dict(source)
        return source

    @classmethod
    def _get_custom_encoder_cache_size(cls):
        """Return the max number of custom encoders that may stay loaded per process."""
        return max(1, settings.RERANKER_CUSTOM_ENCODER_CACHE_SIZE)

    @classmethod
    def _get_custom_encoder_cache_ttl(cls):
        """Return the idle TTL for custom encoders in seconds."""
        return max(1, settings.RERANKER_CUSTOM_ENCODER_CACHE_TTL)

    @classmethod
    def _evict_custom_encoders_for_capacity(cls):
        """Evict least-recently-used custom encoders before loading another large model."""
        while len(cls.CUSTOM_ENCODER_CACHE) >= cls._get_custom_encoder_cache_size():
            cls.CUSTOM_ENCODER_CACHE.popitem(last=False)
            cls._release_memory()

    @classmethod
    def _evict_expired_custom_encoders(cls, now=None):
        """Remove expired custom encoders so idle large models do not stay resident forever."""
        now = now or time.time()
        expired_models = [
            model_name for model_name, cached_encoder_state in cls.CUSTOM_ENCODER_CACHE.items()
            if cached_encoder_state['expires_at'] <= now
        ]
        for model_name in expired_models:
            if cls.CUSTOM_ENCODER_CACHE.pop(model_name, None):
                cls._release_memory()

    @staticmethod
    def _release_memory():
        """Reclaim memory after the last cache reference to an evicted encoder has been dropped."""
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
