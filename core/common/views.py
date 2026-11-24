import base64
import math
import re
from email.mime.image import MIMEImage
from functools import cached_property

import markdown
import requests
from django.conf import settings
from django.core.mail import EmailMessage
from django.db import models
from django.db.models import F
from django.db.models.functions import Coalesce, Lower
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from elasticsearch import RequestError, TransportError
from elasticsearch_dsl import Q, TermsFacet
from pydash import get, compact
from rest_framework import response, generics, status
from rest_framework.generics import ListAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import __version__
from core.common.checksums import ChecksumModel
from core.common.constants import SEARCH_PARAM, LIST_DEFAULT_LIMIT, CSV_DEFAULT_LIMIT, \
    LIMIT_PARAM, NOT_FOUND, MUST_SPECIFY_EXTRA_PARAM_IN_BODY, INCLUDE_RETIRED_PARAM, VERBOSE_PARAM, HEAD, LATEST, \
    BRIEF_PARAM, ES_REQUEST_TIMEOUT, INCLUDE_INACTIVE, FHIR_LIMIT_PARAM, RAW_PARAM, SEARCH_MAP_CODES_PARAM, \
    INCLUDE_SEARCH_META_PARAM, EXCLUDE_FUZZY_SEARCH_PARAM, EXCLUDE_WILDCARD_SEARCH_PARAM, UPDATED_BY_USERNAME_PARAM, \
    CANONICAL_URL_REQUEST_PARAM, CHECKSUMS_PARAM, ACCESS_TYPE_NONE, HTTP_COMPRESS_HEADER, FACET_SIZE
from core.common.exceptions import Http400
from core.common.mixins import PathWalkerMixin, SourceChildMixin
from core.common.permissions import CanViewConceptDictionary
from core.common.search import CustomESSearch, EXACT_OWNER_FIELD
from core.common.serializers import RootSerializer
from core.common.swagger_parameters import all_resource_query_param
from core.common.throttling import ThrottleUtil
from core.common.utils import compact_dict_by_values, to_snake_case, parse_updated_since_param, \
    to_int, get_falsy_values, get_truthy_values, format_url_for_search, cap_page_size, to_owner_uri
from core.concepts.permissions import CanViewParentDictionary, CanEditParentDictionary
from core.orgs.constants import ORG_OBJECT_TYPE
from core.users.constants import USER_OBJECT_TYPE, LIST_UNPAGINATED_PERMISSION

TRUTHY = get_truthy_values()
FALSY = get_falsy_values()


class BaseAPIView(generics.GenericAPIView, PathWalkerMixin):
    """
    An extension of generics.GenericAPIView that:
    1. Adds a hook for a post-initialize step
    2. De-couples the lookup field name (in the URL) from the "filter by" field name (in the queryset)
    3. Performs a soft delete on destroy()
    """
    pk_field = 'mnemonic'
    user_is_self = False
    is_searchable = False
    search_checks_access = False  # the search filtered its hits by access, so they load through get_viewable_hits
    is_page_size_capped = False  # concept, mapping and reference lists: `limit` is capped at settings.LIST_MAX_LIMIT
    limit = LIST_DEFAULT_LIMIT
    default_filters = {}
    sort_asc_param = 'sortAsc'
    sort_desc_param = 'sortDesc'
    sort_param = 'sort'
    default_qs_sort_attr = '-updated_at'
    facet_class = None
    total_count = 0
    _latest_release_scopes = None  # exclude_hidden_latest_releases's, read once per request
    # a fuzzy search's targets, which only the concept list sets: without them it keeps to the URL's scope
    _source_versions = None
    _extra_filters = None
    # the facets that keep a search of concepts or mappings to collection members (get_viewable_collection_urls)
    collection_facets = ('collection', 'collection_url', 'collection_owner_url')
    max_viewable_collection_urls = 1000
    _viewable_collection_urls = None  # get_viewable_collection_urls's, read once per request and facet value
    max_collection_facet_names = 3 * FACET_SIZE  # keep_collection_facets_viewable's
    # the facets that name collection versions or expansions, with the index field holding the URLs they come from
    collection_url_facets = {'collection': 'collection_url', 'collection_version': 'collection_url',
                             'expansion': 'expansion_url'}

    def get_throttles(self):
        return ThrottleUtil.get_throttles_by_user_plan(self.request.user)

    def has_no_kwargs(self):
        return len(self.kwargs.values()) == 0

    def has_owner_scope(self):
        kwargs = self.kwargs.keys()
        return 'org' in kwargs or 'user' in kwargs

    def has_concept_container_scope(self):
        kwargs = self.kwargs.keys()
        return 'source' in kwargs or 'collection' in kwargs

    def has_parent_scope(self):
        return self.has_owner_scope() and self.has_concept_container_scope()

    def is_collection_members_list(self):
        """A collection's (or expansion's) concepts or mappings, which include retired ones by default."""
        return 'expansion' in self.kwargs or ('collection' in self.kwargs and self.is_source_child_document_model())

    def _should_exclude_retired_from_search_results(self):
        if self.is_owner_document_model() or self.is_url_registry_document():
            return False

        params = get(self, 'params') or self.request.query_params.dict()
        include_retired = params.get(INCLUDE_RETIRED_PARAM, None)
        retired = {to_snake_case(key): value for key, value in params.items()}.get('retired')  # as get_faceted_filters
        # a retired=true filter lets them in whatever includeRetired says: the OCL Mapper sends both
        if retired in TRUTHY or include_retired in TRUTHY:
            return False
        if self.is_collection_members_list():  # in unless includeRetired=false
            return include_retired in FALSY
        return True

    def get_retired_criterion(self):
        """
        What the results ask of `retired`: the retired rule above and any `retired` filter. The facets count within
        it, all but the `retired` facet, which counts both values so a client can show how many are retired.
        """
        criterion = Q('term', retired=False) if self._should_exclude_retired_from_search_results() else None
        faceted_filters = self.get_faceted_filters()
        retired_filter = self.get_faceted_criterion(
            params={'retired': faceted_filters['retired']}) if 'retired' in faceted_filters else None
        if retired_filter is not None:
            criterion = retired_filter if criterion is None else criterion & retired_filter
        return criterion

    def should_include_inactive(self):
        return self.request.query_params.get(INCLUDE_INACTIVE) in TRUTHY

    def _should_include_private(self):
        return (self.is_user_document() or self.request.user.is_staff or
                self.is_user_scope() or self.is_url_registry_document())

    def is_verbose(self):
        return self.request.query_params.get(VERBOSE_PARAM, False) in TRUTHY

    def is_raw(self):
        return self.request.query_params.get(RAW_PARAM, False) in TRUTHY

    def is_brief(self):
        return self.request.query_params.get(BRIEF_PARAM, False) in TRUTHY

    def is_checksums(self):
        return self.request.query_params.get(CHECKSUMS_PARAM, False) in TRUTHY

    def is_hard_delete_requested(self):
        return self.request.query_params.get('hardDelete', None) in TRUTHY

    def is_async_requested(self):
        return self.request.query_params.get('async', None) in TRUTHY

    def is_inline_requested(self):
        return self.request.query_params.get('inline', None) in TRUTHY

    def verify_scope(self):
        pass

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.initialize(request, request.path_info, **kwargs)
        self.verify_scope()

    def initialize(self, request, path_info_segment, **kwargs):  # pylint: disable=unused-argument
        self.user_is_self = kwargs.pop('user_is_self', False)
        if self.user_is_self and self.request.user.is_anonymous:
            raise Http404()

        params = request.query_params.dict()
        self.validate_pagination_params(request, params)
        self.limit = params.get(LIMIT_PARAM, None) or params.get(FHIR_LIMIT_PARAM, None) or LIST_DEFAULT_LIMIT
        self.limit = self.cap_limit(self.limit)

    def cap_limit(self, limit):
        """`limit`, capped on a capped list, except for checksums and for a `Compress` by someone allowed any size,
         whose results must not be pre-sliced to one page."""
        if not self.is_page_size_capped or (self.is_brief() and self.is_checksums()) or (
                self.request.META.get(HTTP_COMPRESS_HEADER, False) in TRUTHY and self.can_list_unpaginated()):
            return limit
        return cap_page_size(limit)

    def can_list_unpaginated(self):
        """Staff, or `users.list_unpaginated`: `Compress` returns any size of list."""
        user = self.request.user
        return user.is_staff or user.has_perm(LIST_UNPAGINATED_PERMISSION)

    @staticmethod
    def validate_pagination_params(request, params):
        if request.method not in ('GET', 'HEAD'):
            return
        for name in (LIMIT_PARAM, 'page'):
            value = params.get(name)
            if value is None:
                continue
            if not re.fullmatch(r'[0-9]{1,9}', value.strip()) or int(value) < 1:
                raise Http400({'error': f"'{name}' must be a positive integer, got '{value}'."})

    def get_object(self, queryset=None):  # pylint: disable=arguments-differ
        # Determine the base queryset to use.
        if queryset is None:
            queryset = self.filter_queryset(self.get_queryset())
        else:
            pass  # Deprecation warning

        # Perform the lookup filtering.
        lookup = self.kwargs.get(self.lookup_field, None)
        filter_kwargs = {self.pk_field: lookup}
        obj = get_object_or_404(queryset, **filter_kwargs)

        # May raise a permission denied
        self.check_object_permissions(self.request, obj)

        return obj

    def destroy(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        obj = self.get_object()
        obj.soft_delete()
        return response.Response(status=status.HTTP_204_NO_CONTENT)

    def get_host_url(self):
        scheme = self.request.META['wsgi.url_scheme']
        if settings.ENV != 'development':
            scheme += 's'
        return scheme + '://' + self.request.get_host()

    def filter_queryset(self, queryset=None):
        if self.is_searchable and self.should_perform_es_search():
            if self.is_fuzzy_search:
                queryset, self._scores, self._max_score, self._highlights = self.get_fuzzy_search_results_qs(
                    self._source_versions, self._extra_filters)
            else:
                queryset, self._scores, self._max_score, self._highlights = self.get_search_results_qs()
            return queryset

        if queryset is None:
            queryset = self.get_queryset()

        _queryset = super().filter_queryset(queryset)

        if self.default_qs_sort_attr:
            if isinstance(self.default_qs_sort_attr, str):
                _queryset = _queryset.order_by(self.default_qs_sort_attr)
            elif isinstance(self.default_qs_sort_attr, list):
                _queryset = _queryset.order_by(*self.default_qs_sort_attr)
        return _queryset

    def filter_queryset_by_owner(self, queryset):
        if self.user_is_self:
            return queryset.filter(user=self.request.user)
        if 'user' in self.kwargs:
            return queryset.filter(user__username=self.kwargs['user'])
        if 'org' in self.kwargs:
            return queryset.filter(organization__mnemonic=self.kwargs['org'])

        return queryset

    def filter_queryset_by_public_access(self, queryset):
        user = self.request.user

        if get(user, 'is_anonymous'):
            return queryset.exclude(public_access=ACCESS_TYPE_NONE)
        if user.is_staff:
            return queryset

        public_queryset = queryset.exclude(public_access=ACCESS_TYPE_NONE)
        private_queryset = queryset.filter(public_access=ACCESS_TYPE_NONE)
        private_queryset = private_queryset.filter(
            models.Q(user_id=user.id) | models.Q(organization__members__id=user.id))

        return public_queryset.union(private_queryset)

    def get_sort_and_desc(self):
        query_params = self.request.query_params.dict()

        sort_fields = query_params.get(self.sort_desc_param)
        if sort_fields is not None:
            return sort_fields, True

        sort_fields = query_params.get(self.sort_asc_param)
        if sort_fields is not None:
            return sort_fields, False

        sort_fields = query_params.get(self.sort_param)
        if sort_fields is not None:
            return sort_fields, None

        return None, None

    def is_valid_sort(self, field):
        if not self.es_fields or not field:
            return False
        if field in self.es_fields:
            attrs = self.es_fields[field]
            return attrs.get('sortable', False)
        if self.is_concept_document() and field.startswith('properties.'):
            return True

        return False

    def clean_fields(self, fields):
        if self.is_concept_document() and self.request.query_params.get(SEARCH_MAP_CODES_PARAM) in get_falsy_values():
            if isinstance(fields, dict):
                fields = {key: value for key, value in fields.items() if not key.endswith('map_codes')}
            elif isinstance(fields, list):
                fields = [field for field in fields if not field.endswith('map_codes')]
        return fields

    def get_search_string(self, lower=True, decode=True):
        search_str = self.get_raw_search_string().replace('"', '').replace("'", "")
        return CustomESSearch.get_search_string(search_str, lower=lower, decode=decode)

    def get_raw_search_string(self):
        return self.request.query_params.dict().get(SEARCH_PARAM, '').strip()

    @property
    def is_fuzzy_search(self):
        return self.request.query_params.dict().get('fuzzy', None) in get_truthy_values()

    def get_wildcard_search_string(self, _str):
        return CustomESSearch.get_wildcard_search_string(_str or self.get_search_string())

    @staticmethod
    def __get_order_by(is_desc):
        return {'order': 'desc' if is_desc else 'asc'}

    def get_sort_attributes(self):
        sort_fields, desc = self.get_sort_and_desc()
        result = []
        if sort_fields:
            order_by = None if desc is None else self.__get_order_by(desc)
            fields = sort_fields.split(',')
            for original_field in fields.copy():
                original_field = original_field.strip()
                is_desc = original_field.startswith('-')
                original_field = original_field.replace('-', '', 1) if is_desc else original_field
                field = original_field.lower()
                order_details = order_by
                if order_details is None:
                    order_details = self.__get_order_by(is_desc)

                current_result = None
                if field in ['score', '_score', 'best_match', 'best match']:
                    current_result = {'_score': order_details}
                if field == 'name':
                    current_result = {'_name': order_details}
                if field == 'id' and self.is_source_child_document_model():
                    current_result = {'id_lowercase': order_details}
                if field in ['id', 'mnemonic'] and (
                        self.is_org_document() or self.is_concept_container_document_model() or
                        self.is_repo_document_model()
                ):
                    current_result = {'_mnemonic': order_details}
                if field in ['username', 'id'] and self.is_user_document():
                    current_result = {'_username': order_details}
                if self.is_valid_sort(field):
                    sort_field = field
                    if field.startswith('properties.'):
                        # preserve the property code's original case -- it's a dynamic per-source
                        # key, unlike the fixed lowercase 'properties.' prefix or other sort fields
                        sort_field = original_field
                        if not field.endswith('.keyword'):
                            sort_field += '.keyword'
                    current_result = {sort_field: order_details}
                if current_result is not None:
                    result.append(current_result)

        return result

    def get_fuzzy_search_criterion(self, boost_divide_by=10, expansions=5):
        return CustomESSearch.get_fuzzy_match_criterion(
            search_str=self.get_search_string(decode=False),
            fields=self.get_fuzzy_search_fields(),
            boost_divide_by=boost_divide_by,
            expansions=expansions
        )

    def get_wildcard_search_criterion(self, search_str=None):
        fields = self.get_wildcard_search_fields()
        return CustomESSearch.get_wildcard_match_criterion(
            search_str=search_str or self.get_search_string(),
            fields=fields
        ), fields.keys()

    def get_exact_search_criterion(self):
        match_phrase_field_list = self.document_model.get_match_phrase_attrs()
        match_word_fields_map = self.clean_fields(self.document_model.get_exact_match_attrs())
        fields = match_phrase_field_list + list(match_word_fields_map.keys())
        return CustomESSearch.get_exact_match_criterion(
            self.get_search_string(False, False),
            match_phrase_field_list,
            match_word_fields_map,
        ), fields

    def get_declared_property_types(self):
        return get(self, 'parent_resource.property_types') or {}

    @staticmethod
    def get_boolean_property_criterion(property_code, *values):
        """
        A boolean-declared property reaches Elasticsearch as boolean, long or text, depending on
        what the concepts actually store in extras -- `false` may be indexed as `false` or as `0`.
        Lenient match clauses let the literals that do not fit the field's type be skipped instead
        of failing the whole query with a 400. See OpenConceptLab/ocl_issues#2692.
        """
        field = f"properties.{property_code}"
        return Q(
            'bool',
            should=[Q('match', **{field: {'query': value, 'lenient': True}}) for value in values],
            minimum_should_match=1
        )

    def get_faceted_criterion(self, split=False, params=None, **kwargs):
        filters = self.get_faceted_filters(
            split=split,
            params=params,
            additional_fields=['id', 'id_raw'],
            repo_default_filters=kwargs.pop('repo_default_filters', None)
        )

        def get_query_criteria(attr, val):
            is_property = attr.startswith('properties__')
            is_target_repo_filter = attr in ['target_repo', 'target_repo_map_type']
            if is_target_repo_filter:
                path = 'source'
                if attr == 'target_repo_map_type':
                    path = 'map_type'
                return Q(
                    "nested",
                    path="mapped_codes",
                    query=Q("term", **{f"mapped_codes.{path}": val})
                )
            if is_property:
                property_code = attr.split('properties__', 1)[1]

                if not val:
                    new_attr = "properties." + property_code
                    return ~Q("exists", field=new_attr)

                clean_val = val.strip('\"').strip('\'')
                if self.get_declared_property_types().get(property_code) == 'boolean':
                    if clean_val in get_truthy_values():
                        return self.get_boolean_property_criterion(property_code, 'true', '1')
                    if clean_val in get_falsy_values():
                        return self.get_boolean_property_criterion(property_code, 'false', '0')

                return Q('term', **{f"properties.{property_code}.keyword": clean_val})

            clean_val = val.strip('\"').strip('\'')
            if attr == 'retired':  # indexed as 'true'/'false'; other spellings read as the retired rule reads them
                clean_val = 'true' if clean_val in TRUTHY else 'false' if clean_val in FALSY else clean_val
            return Q('term', **{attr: clean_val})

        def get_query(attr, val):
            """
                Constructs a dynamic Elasticsearch DSL query based on value modifiers:
                - Supports `!` prefix for negation
                - Supports `*` suffix for prefix/regex matching
                - Supports multiple comma-separated values
            """
            prefix_query = val.endswith('*')
            not_query = val.startswith('!')
            vals = val.replace('!', '', 1).split(',')
            if prefix_query:
                regexp_field = f"properties.{attr.split('properties__', 1)[1]}.keyword" \
                    if attr.startswith('properties__') else attr
                queries = []
                for _val in vals:
                    clean_val = _val.strip('\"').strip('\'').rstrip('*')
                    queries.append(Q('regexp', **{regexp_field: {'value': f"{re.escape(clean_val)}.*"}}))
            else:
                queries = [
                    get_query_criteria(attr, _val) for _val in vals
                ]
            if attr in self.collection_facets and self.is_source_child_document_model():
                for index, _val in enumerate(vals):
                    clean_val = _val.strip('\"').strip('\'')
                    criterion = self.get_collection_facet_criterion(
                        attr, clean_val.rstrip('*') if prefix_query else clean_val, prefix_query)
                    if criterion is not None:
                        queries[index] = criterion

            return Q('bool', must=[~q for q in queries]) if not_query else Q('bool', should=queries, **kwargs)

        if filters:
            first_filter = filters.popitem()
            criterion = get_query(first_filter[0], first_filter[1])
            for field, value in filters.items():
                criterion &= get_query(field, value)
            return criterion

        return None

    def get_collection_facet_criterion(self, field, value, prefix):
        """
        A collection facet keeps a search to the members of collection versions, any owner's: by name (`collection`),
        by a version's URL (`collection_url`) or by owner URL (`collection_owner_url`); with `prefix`, those starting
        with `value`, as a literal prefix. The index lists every version a concept or mapping is in, those the
        requester can't view too, and those since deleted, so the facet matches through the URLs of the versions they
        can view only (get_viewable_collection_urls). None for staff's exact values: the facet stays as it is.
        """
        urls = self.get_viewable_collection_urls(field, value, prefix)
        if urls is not None:
            return Q('terms', collection_url=urls)
        return Q('prefix', **{field: value}) if prefix else None

    def get_viewable_collection_urls(self, field, value, prefix):
        """
        The URLs of the collection versions a collection facet value matches that the requester can view. None for
        staff, who can view every version. A prefix that matches more than max_viewable_collection_urls they can
        view answers 400 when it matches any they can't, and otherwise stays a prefix (None).
        """
        from core.collections.models import Collection
        viewable = Collection.get_viewable_versions_criteria(self.request.user)
        if viewable is None:
            return None
        if self._viewable_collection_urls is None:
            self._viewable_collection_urls = {}
        cache, key = self._viewable_collection_urls, (field, value, prefix)
        if key not in cache:
            versions = Collection.objects.filter(self.get_collection_facet_versions_criteria(field, value, prefix))
            urls = versions.filter(viewable).values_list('uri', flat=True)
            if not prefix:
                urls = list(urls)
            else:
                limit = self.max_viewable_collection_urls
                urls = list(urls[:limit + 1])
                if len(urls) > limit:
                    if versions.exclude(viewable).exists():
                        raise Http400(detail=f'{field}: more than {limit} collection versions match {value}. '
                                             'Please narrow the filter.')
                    urls = None  # they can view every version it matches
                elif field == 'collection_owner_url':
                    urls = [url for url in urls if to_owner_uri(url).startswith(value)]
            cache[key] = urls
        return cache[key]

    @staticmethod
    def get_collection_facet_versions_criteria(field, value, prefix):
        """The collection versions a collection facet value matches, as a query (for an owner URL prefix, a superset:
        those whose URL starts with it)."""
        if field == 'collection':
            return models.Q(**{'mnemonic__startswith' if prefix else 'mnemonic': value})
        if field == 'collection_url':
            return models.Q(**{'uri__startswith' if prefix else 'uri': value})
        if prefix:  # an owner URL is /orgs/<org>/ or /users/<user>/, which a longer prefix can't start
            return models.Q(uri__startswith=value) if value.count('/') < 3 or (
                value.count('/') == 3 and value.endswith('/')) else models.Q(pk__in=[])
        owner = re.fullmatch(r'/(orgs|users)/([^/]+)/', value)
        if not owner:
            return models.Q(pk__in=[])
        return models.Q(organization__mnemonic=owner[2]) if owner[1] == 'orgs' else models.Q(
            user__username=owner[2])

    @staticmethod
    def merge_filters(filter1, filter2):
        if not filter1 or not filter2:
            return filter1 or filter2 or {}
        merged = {}
        for key in set(filter1) | set(filter2):
            v1, v2 = filter1.get(key), filter2.get(key)
            # Case 1: both values exist
            if v1 is not None and v2 is not None:
                if isinstance(v1, list) and isinstance(v2, list):
                    merged[key] = list(dict.fromkeys(v1 + v2))  # dedup preserve order
                elif isinstance(v1, str) and isinstance(v2, str):
                    if "," in v1 or "," in v2:
                        merged[key] = ",".join(dict.fromkeys((v1.split(",") + v2.split(","))))
                    else:
                        merged[key] = ",".join(dict.fromkeys([v1, v2]))
                else:
                    merged[key] = v1  # prefer value from `a`
            else:
                merged[key] = v1 if v1 is not None else v2
        return merged

    def get_faceted_filters(self, split=False, params=None, additional_fields=None, repo_default_filters=None):
        faceted_filters = {}
        faceted_fields = [*self.get_faceted_fields(), *(additional_fields or [])]
        params = self.request.query_params.dict() if params is None else params

        query_params = {k if k.startswith('properties__') else to_snake_case(k): v for k, v in params.items()}
        repo_default_filters = {
            to_snake_case(k): v for k, v in repo_default_filters.items()
        } if repo_default_filters else {}
        merged_filters = self.merge_filters(repo_default_filters, query_params)

        for field in faceted_fields:
            if field == 'properties':
                property_facets = {
                    key: val.split(',') if split and (val and isinstance(val, str)) else val for key, val in
                    merged_filters.items() if key.startswith('properties__')
                }
                faceted_filters = {**faceted_filters, **property_facets}
            if field in merged_filters or field.replace('_text.keyword', '') in merged_filters:
                query_value = merged_filters.get(
                    field, False) or merged_filters.get(field.replace('_text.keyword', ''))
                faceted_filters[field] = query_value.split(',') if split else query_value
        return faceted_filters

    def get_faceted_fields(self):
        es_fields = get(self, 'es_fields', {})
        return [
            get(
                config, 'facet_field'
            ) or field for field, config in es_fields.items() if config.get('facet', False)
        ]

    @property
    def url_username(self):
        """The user the URL names, the requester on the /user/ shortcut."""
        return self.kwargs.get('user') or (self.request.user.username if self.user_is_self else None)

    def get_facet_filters_from_kwargs(self):
        kwargs = self.kwargs
        filters = {}
        is_collection_specified = 'collection' in self.kwargs
        is_user_specified = 'user' in kwargs
        if is_collection_specified:
            version = kwargs.get('version') or HEAD
            filters['collection'] = kwargs['collection']
            collection_owner_url = f'/orgs/{kwargs["org"]}/' if 'org' in kwargs else f'/users/{self.url_username}/'
            filters['collection_owner_url'] = collection_owner_url
            if 'expansion' in self.kwargs:
                filters['expansion_url'] = (f"{collection_owner_url}collections/{filters['collection']}/{version}/"
                                            f"expansions/{self.kwargs['expansion']}/")
        else:
            if is_user_specified:
                filters['ownerType'] = USER_OBJECT_TYPE
                filters['owner'] = kwargs['user']
            if 'org' in kwargs:
                filters['ownerType'] = ORG_OBJECT_TYPE
                filters['owner'] = kwargs['org']
            if 'source' in kwargs:
                filters['source'] = kwargs['source']

        return filters

    def get_kwargs_filters(self):  # pylint: disable=too-many-branches
        filters = self.get_facet_filters_from_kwargs()
        is_source_child_document_model = self.is_source_child_document_model()
        is_version_specified = 'version' in self.kwargs
        is_collection_specified = 'collection' in self.kwargs
        is_source_specified = 'source' in self.kwargs

        if is_version_specified and is_source_specified:
            filters['source_version'] = self.kwargs['version']
        if is_version_specified and is_collection_specified:
            filters['collection_version'] = self.kwargs['version']

        if is_source_child_document_model:
            version = None
            params = {'user__username': self.url_username, 'organization__mnemonic': self.kwargs.get('org')}
            if self.kwargs.get('version') == LATEST:
                if is_source_specified:
                    from core.sources.models import Source
                    version = Source.find_latest_released_version_by(
                        {**params, 'mnemonic': self.kwargs['source']})
                    filters['source_version'] = get(version, 'version')
                elif is_collection_specified:
                    from core.collections.models import Collection
                    version = Collection.find_latest_released_version_by(
                        {**params, 'mnemonic': self.kwargs['collection']})
                    filters['collection_version'] = get(version, 'version')
            elif is_collection_specified and 'expansion' not in self.kwargs and (
                    is_version_specified or 'reference' not in self.kwargs):
                from core.collections.models import Collection
                version = Collection.objects.filter(  # without a version in the URL: HEAD, as its list
                    **params, mnemonic=self.kwargs['collection'], version=self.kwargs.get('version', HEAD)
                ).first()

            if is_collection_specified:
                owner_type = filters.pop('ownerType', None)
                owner = filters.pop('owner', None)
                if owner_type == USER_OBJECT_TYPE:
                    filters['collection_owner_url'] = f"/users/{owner}/"
                if owner_type == ORG_OBJECT_TYPE:
                    filters['collection_owner_url'] = f"/orgs/{owner}/"
                if not is_version_specified or self.kwargs['version'] == HEAD:
                    filters['collection_version'] = HEAD
                if 'expansion' not in self.kwargs and version:
                    filters['expansion_url'] = get(version, 'expansion.uri', 'NO_EXPANSION')
                filters['collection_url'] = f"{filters['collection_owner_url']}collections/{self.kwargs['collection']}/"
                if is_version_specified and self.kwargs['version'] != HEAD:
                    filters['collection_url'] += f"{filters['collection_version'] or self.kwargs['version']}/"
            if is_source_specified and not is_version_specified and not self.should_search_latest_repo():
                filters['source_version'] = HEAD
        return filters

    @cached_property
    def map_codes_release(self):
        """
        The id of the release (a source version, not HEAD) a concept search runs in, whose own mapped codes it
        matches (OpenConceptLab/ocl_online#703); None if the user can't view it, or for a fuzzy search, whose hits and
        facets read each doc's own codes.
        """
        if not self.is_concept_document() or 'source' not in self.kwargs or 'collection' in self.kwargs or \
                self.is_fuzzy_search:
            return None
        version = self.get_kwargs_filters().get('source_version')
        if version in (None, HEAD):
            return None
        from core.sources.models import Source
        owner = {'organization__mnemonic': self.kwargs['org']} if 'org' in self.kwargs else {
            'user__username': self.kwargs['user'] if 'user' in self.kwargs else self.request.user.username}
        release = Source.objects.filter(**owner, mnemonic=self.kwargs['source'], version=version).first()
        return release.id if release and release.has_view_access(self.request.user) else None

    @staticmethod
    def scope_map_codes(results, release_id):
        """With a release's id, `results` with its mapped-code clauses reading that release's own codes."""
        query = results.to_dict().get('query') if release_id else None
        if query:
            from core.concepts.documents import ConceptDocument
            results = results.update_from_dict(
                {'query': ConceptDocument.scope_map_codes_to_release(query, release_id)})
        return results

    def get_latest_version_filter_field_for_source_child(self):
        query_latest = self.__should_query_latest_version()
        if query_latest:
            return 'is_in_latest_source_version' if self.should_search_latest_repo() else 'is_latest_version'
        if not self.is_global_scope() and (
                self.kwargs.get('version') == HEAD or not self.kwargs.get('version')
        ) and 'collection' not in self.kwargs:
            return 'is_latest_version'
        return None

    def exclude_hidden_latest_releases(self, results, latest_attr):
        """
        A search of latest releases shows a source's concepts and mappings only from a latest release the requester
        can view: of a source whose latest release they can't view, nothing; of one with a release they can't view,
        only the members of its latest release. Hits and facets alike. Other searches are returned as they are.
        """
        if latest_attr != 'is_in_latest_source_version':
            return results
        from core.sources.models import Source
        if self._latest_release_scopes is None:  # hits and facets each build a search
            self._latest_release_scopes = Source.get_latest_release_scopes_for(self.request.user)
        criterion = None
        for mnemonic, org, username, latest_version in self._latest_release_scopes:
            owner_type = ORG_OBJECT_TYPE if org else USER_OBJECT_TYPE
            criteria = Q('term', source=mnemonic) & Q('term', owner=org or username) & Q(
                'term', owner_type=owner_type)
            if latest_version:
                criteria &= ~Q('term', source_version=latest_version)
            criterion = criteria if criterion is None else criterion | criteria
        return results if criterion is None else results.exclude(criterion)

    def get_facets(self, for_requester=True):  # pylint: disable=too-many-branches
        """
        The search's facets. Without for_requester, before get_requester_facets: the same for everyone who can run
        the search, so they can be cached.
        """
        facets = {}
        parent_repo = None
        params = {}

        if self.facet_class:
            if self.is_user_document():
                return facets

            params = {
                'query': self.get_search_string(lower=False),
                '_search': self.get_facets_search(),
                'filters': {'retired': self.get_retired_criterion()}
            }
            if 'source' in self.kwargs and self.is_concept_document():
                parent_repo = params['parent'] = get(self, 'parent_resource')
            if self.map_codes_release:
                params['release'] = self.map_codes_release
            if self.is_source_child_document_model():  # see keep_collection_facets_viewable
                params['extra_facets'] = {
                    '_collection_names': TermsFacet(field='collection', size=self.max_collection_facet_names)}

            faceted_search = self.facet_class(**params) # pylint: disable=not-callable
            faceted_search.params(request_timeout=ES_REQUEST_TIMEOUT)
            try:
                s = faceted_search.execute()
                facets = s.facets.to_dict()
            except TransportError as ex:  # pragma: no cover
                raise Http400(detail=get(ex, 'info') or get(ex, 'error') or str(ex)) from ex
        if self.should_search_latest_repo() and self.is_source_child_document_model() and 'source_version' in facets:
            facets['source_version'] = [facet for facet in facets['source_version'] if facet[0] != 'HEAD']
        is_global_scope = ('org' not in self.kwargs and 'user' not in self.kwargs and not self.user_is_self)

        normalized_facet_concept_class = facets.pop('_conceptClass', None) or []
        normalized_facet_datatype = facets.pop('_datatype', None) or []
        if len(facets.get('conceptClass', [])) == 0 and len(normalized_facet_concept_class) > 0:
            facets['conceptClass'] = normalized_facet_concept_class
        if len(facets.get('datatype', [])) == 0 and len(normalized_facet_datatype) > 0:
            facets['datatype'] = normalized_facet_datatype

        facets.pop('collection_owner_url', None)
        if is_global_scope:
            facets.pop('collection_version', None)
            facets.pop('expansion', None)
        else:
            facets.pop('owner', None)
            facets.pop('ownerType', None)
            if 'source' in self.kwargs:
                facets.pop('source', None)
                facets.pop('source_version', None)
            elif 'collection' in self.kwargs:
                facets.pop('collection', None)
                facets.pop('collection_owner_url', None)
        facets.pop('is_in_latest_source_version', None)
        facets.pop('is_latest_version', None)

        if parent_repo and self.is_concept_document():
            facets = parent_repo.get_ordered_concept_facets_by_filter_order(facets)

        return self.get_requester_facets(facets, get(params, '_search')) if for_requester else facets

    def get_facets_search(self):
        return self.__get_search_results(ignore_retired_filter=True, sort=False, highlight=False, force=True)

    def get_requester_facets(self, facets, search=None):
        """get_facets(for_requester=False)'s facets as this requester sees them; `search`: get_facets_search's."""
        if not get(self.request.user, 'is_authenticated'):
            facets.pop('updatedBy', None)
        self.keep_collection_facets_viewable(facets, search)
        self.keep_repo_facets_viewable(facets, search)
        return facets

    def keep_collection_facets_viewable(self, facets, search=None):
        """
        The collection, collection_version and expansion facets name the collection versions and expansions a match
        is in as the index lists them: those the requester can't view, and those since deleted, too. Here each name
        counts only the matches in the ones they can view, as the collection filter matches them
        (get_collection_facet_criterion), in one more request; a name with none is left out. Staff see them as they
        are. The collections counted are those _collection_names lists: the top max_collection_facet_names among the
        matches. One past them isn't counted, so its names can be missing, never wrong.
        """
        from core.collections.models import Collection
        collections = [name for name, _, _ in facets.pop('_collection_names', [])]
        present = {facet: {name for name, _, _ in facets[facet]} for facet in self.collection_url_facets
                   if facets.get(facet)}
        viewable = Collection.get_viewable_versions_criteria(self.request.user)
        if not present or viewable is None:
            return
        urls = self.get_viewable_collection_facet_urls(present, collections, viewable)
        counts = {}
        if urls:
            search = (search or self.get_facets_search()).extra(size=0).params(request_timeout=ES_REQUEST_TIMEOUT)
            retired_criterion = self.get_retired_criterion()  # the facets count within it (get_facets)
            if retired_criterion is not None:
                search = search.filter(retired_criterion)
            search.aggs.bucket('viewable', 'filters', filters={
                str(i): Q('terms', **{self.collection_url_facets[facet]: sorted(values)})
                for i, ((facet, _), values) in enumerate(urls.items())})
            try:
                buckets = search.execute().aggregations.viewable.buckets
            except TransportError as ex:  # pragma: no cover
                raise Http400(detail=get(ex, 'info') or get(ex, 'error') or str(ex)) from ex
            counts = {key: buckets[str(i)].doc_count for i, key in enumerate(urls)}
        for facet in present:
            facets[facet] = sorted(
                [(name, count, False) for (_facet, name), count in counts.items() if _facet == facet and count],
                key=lambda bucket: (-bucket[1], bucket[0]))[:FACET_SIZE]

    # the facets that name repositories, by what they hold: their names, their owners' names or their URLs
    repo_name_facets = {
        'source': 'mnemonic', 'owner': 'owner', 'fromConceptSource': 'mnemonic', 'toConceptSource': 'mnemonic',
        'fromConceptOwner': 'owner', 'toConceptOwner': 'owner', 'targetRepo': 'uri'}

    def keep_repo_facets_viewable(self, facets, search=None):
        """
        The facets that name repositories, or their owners, name them as the index has them: one made private before
        it was reindexed too, whose hits get_viewable_hits leaves out. Where the search filters by access, a source or
        owner name stays only if it names a repository the requester can view; a name of a mapping's or concept's
        target is left out only if the repositories it names exist and the requester can view none of them, so a
        target outside OCL keeps its name (get_repo_names_by_access). Counts stay the index's. A hit kept as its
        creator's can have its names missing, never wrong. `search`: get_facets_search's, which tells whether the
        search filters by access.
        """
        user = self.request.user
        named = [facet for facet in self.repo_name_facets if facets.get(facet)]
        if not named or get(user, 'is_staff') or not (
                self.search_checks_access if search is not None else self.get_search_visibility_criteria() is not None):
            return
        from core.collections.documents import CollectionDocument
        from core.collections.models import Collection
        from core.sources.models import Source
        owners = [Collection] if self.document_model == CollectionDocument else (
            [Source, Collection] if self.is_repo_document_model() else [Source])
        for facet in named:
            # a concept search's source and owner facets keep the names' case, as the others' facets do but those
            exact = facet not in ('source', 'owner') or self.is_concept_document()
            key = str if exact else lambda name: str(name).lower()
            shown, hidden = self.get_repo_names_by_access(
                user, self.repo_name_facets[facet], {key(name) for name, _, _ in facets[facet]}, exact,
                owners if facet == 'owner' else [Source])
            facets[facet] = [bucket for bucket in facets[facet] if (
                key(bucket[0]) in shown if facet in ('source', 'owner') else key(bucket[0]) not in hidden)]

    @staticmethod
    def get_repo_names_by_access(user, kind, names, exact, repo_models):
        """
        Of these `names` of repositories of `repo_models` (`kind`: their mnemonics, their owners' or their URIs, any
        version's), those of a repository `user` can view, and those whose existing repositories `user` can view none
        of; a name of none is in neither. One query per model, by index when `exact`,
        else names are lowercase and match any case (owners that differ only in case are one name then).
        """
        field = {'mnemonic': F('mnemonic'), 'uri': F('uri'),
                 'owner': Coalesce('organization__mnemonic', 'user__username')}[kind]
        exact_criteria = {
            'mnemonic': models.Q(mnemonic__in=names), 'uri': models.Q(uri__in=names),
            'owner': models.Q(organization__mnemonic__in=names) | models.Q(user__username__in=names)}[kind]
        hidden, shown = set(), set()
        for model in repo_models:
            rows = model.objects.annotate(_name=field if exact else Lower(field))
            rows = rows.filter(exact_criteria) if exact else rows.filter(_name__in=names)
            viewable = models.ExpressionWrapper(
                model.get_viewable_versions_criteria(user), output_field=models.BooleanField())
            for name, is_viewable in rows.annotate(_viewable=viewable).values_list('_name', '_viewable').distinct():
                (shown if is_viewable else hidden).add(name)
        return shown, hidden - shown

    @staticmethod
    def get_viewable_collection_facet_urls(facets, collections, viewable):
        """
        {(facet, name): urls} for these collection facets ({facet: the names it lists}), from these collections'
        versions the requester can view (`viewable`): their URLs by collection and version name, as the collection
        filter matches them, and their expansions' URLs by expansion name. A version or expansion name counts only if
        its facet lists it, so the request stays the facets' size. Names read as the documents read them from their
        expansions' URIs (to_parent_kwargs_from_uri).
        """
        from core.collections.models import Collection, Expansion
        versions = Collection.objects.filter(viewable, mnemonic__in=collections)
        urls = {}
        for uri in versions.values_list('uri', flat=True):
            parts = uri.strip('/').split('/')
            for facet, name in [('collection', parts[3]), ('collection_version', parts[4] if len(parts) > 4 else HEAD)]:
                if facet in facets and (facet == 'collection' or name in facets[facet]):
                    urls.setdefault((facet, name), set()).add(uri)
        if 'expansion' in facets:
            for uri, name in Expansion.objects.filter(
                    collection_version__in=versions, mnemonic__in=facets['expansion']).values_list('uri', 'mnemonic'):
                urls.setdefault(('expansion', name), set()).add(uri)
        return urls

    def get_extras_searchable_fields_from_query_params(self):
        query_params = self.request.query_params.dict()
        result = {}
        for key, value in query_params.items():
            if key.startswith('extras.') and not key.startswith('extras.exists') and not key.startswith('extras.exact'):
                parts = key.split('extras.')
                # escape `:` so the query_string value can only match this extras field
                value = value.replace('\\', '\\\\').replace('/', '\\/').replace(':', '\\:').replace('-', '_')
                result['extras.' + parts[1].replace('.', '__')] = f"*{value}*"

        return result

    def get_extras_exact_fields_from_query_params(self):
        query_params = self.request.query_params.dict()
        result = {}
        for key, value in query_params.items():
            if key.startswith('extras.exact'):
                new_key = key.replace('.exact', '')
                parts = new_key.split('extras.')
                result['extras.' + parts[1].replace('.', '__')] = value.replace('/', '\\/').replace('-', '_')

        return result

    def get_extras_fields_exists_from_query_params(self):
        extras_exists_fields = self.request.query_params.dict().get('extras.exists', None)

        if extras_exists_fields:
            return [field.replace('.', '__') for field in extras_exists_fields.split(',')]

        return []

    def is_user_document(self):
        from core.users.documents import UserProfileDocument
        return self.document_model == UserProfileDocument

    def is_org_document(self):
        from core.orgs.documents import OrganizationDocument
        return self.document_model == OrganizationDocument

    def is_url_registry_document(self):
        from core.url_registry.documents import URLRegistryDocument
        return self.document_model == URLRegistryDocument

    def is_concept_document(self):
        from core.concepts.documents import ConceptDocument
        return self.document_model == ConceptDocument

    def is_owner_document_model(self):
        return self.is_org_document() or self.is_user_document()

    def is_source_child_document_model(self):
        from core.concepts.documents import ConceptDocument
        from core.mappings.documents import MappingDocument
        from core.concepts.search import ConceptFacetedSearch
        from core.mappings.search import MappingFacetedSearch
        return self.document_model in [
            ConceptDocument, MappingDocument] or self.facet_class in [ConceptFacetedSearch, MappingFacetedSearch]

    def is_concept_container_document_model(self):
        from core.collections.documents import CollectionDocument
        from core.sources.documents import SourceDocument
        return self.document_model in [SourceDocument, CollectionDocument]

    def is_repo_document_model(self):
        from core.repos.documents import RepoDocument
        return self.document_model == RepoDocument

    def is_user_scope(self):
        org = self.kwargs.get('org', None)
        user = self.kwargs.get('user', None)

        request_user = self.request.user

        if request_user.is_authenticated:
            if user:
                return user == request_user.username
            if self.user_is_self:
                return True
            if org:
                return request_user.organizations.filter(mnemonic=org).exists()

        return False

    def get_public_criteria(self):
        criteria = Q('term', public_can_view=True)
        user = self.request.user

        if user.is_authenticated:
            username = user.username
            from core.orgs.documents import OrganizationDocument
            if self.document_model in [OrganizationDocument]:
                criteria |= (Q('term', public_can_view=False) & Q('term', user=username))
            if self.is_concept_container_document_model() or self.is_source_child_document_model():
                criteria |= (Q('term', public_can_view=False) & Q('term', created_by=username))

        return criteria

    def is_global_scope(self):
        return self.kwargs.get('org', None) is None and self.kwargs.get('user', None) is None

    def __should_query_latest_version(self):
        kwargs = {**self.get_faceted_filters(), **self.kwargs}
        collection = kwargs.get('collection', '')
        version = kwargs.get('version', '')
        if not version and not self.is_global_scope() and not collection:
            version = HEAD

        return (not collection or collection.startswith('!')) and (not version or version.startswith('!'))

    def is_members_view(self):
        """/user/orgs/<repos>/: lists across all the requester's orgs."""
        return self.get_view_name() in ['Organization Collection List', 'Organization Source List',
                                        'Organization Repo List', 'Organization Url Registry List']

    def get_members_view_org_mnemonics(self):
        """Orgs of the user in the URL (the requester for /user/orgs/<repos>/)."""
        from core.users.models import UserProfile
        username = self.kwargs.get('user') or get(self.request.user, 'username')
        user = UserProfile.objects.filter(username=username).first() if username else None
        if not user:
            raise Http404()
        return list(user.organizations.values_list('mnemonic', flat=True))

    def get_other_members_view_criteria(self):
        """/users/<other>/orgs/<repos>/: public content, plus private content of the orgs shared with the requester."""
        criteria = Q('term', public_can_view=True)
        shared = set(self.get_members_view_org_mnemonics()) & set(
            self.request.user.organizations.values_list('mnemonic', flat=True))
        for mnemonic in shared:
            criteria |= Q('term', owner=mnemonic)
        return criteria

    def is_other_user_members_view(self):
        user = self.request.user
        username = self.kwargs.get('user')
        return bool(
            self.is_members_view() and username and user.is_authenticated and username != user.username and
            not user.is_staff and not self.is_url_registry_document())

    def get_search_visibility_criteria(self):
        """The access filter a search applies to the documents it matches; None where it applies none."""
        if self.is_other_user_members_view():
            return self.get_other_members_view_criteria()
        return self.get_scope_owner_criteria() if self._should_include_private() else self.get_public_criteria()

    def get_scope_owner_criteria(self):
        """Private content in the requester's own scope (their user, or an org of theirs) is shown, but the owner and
        source filters match names in any case, and owner names that differ only in case are other owners: their
        private content is excluded (concepts by exact owner name; for other documents, such as mappings and repos,
        all private content is excluded when such an owner exists). None when there's nothing to exclude."""
        if (not (self.is_source_child_document_model() or self.is_concept_container_document_model() or
                 self.is_repo_document_model()) or
                self.request.user.is_staff or 'collection' in self.kwargs or not self.is_user_scope()):
            return None
        org = self.kwargs.get('org')
        owner = org or self.kwargs.get('user') or self.request.user.username
        public = Q('term', public_can_view=True)
        orgs = self.get_members_view_org_mnemonics() if (
            self.is_members_view()) else ([org] if org else None)
        if self.is_concept_document():
            return public | Q('term', **{EXACT_OWNER_FIELD: owner})
        if orgs is not None:
            from core.orgs.models import Organization
            twin_criteria = models.Q()
            for mnemonic in orgs:
                twin_criteria |= models.Q(mnemonic__iexact=mnemonic)
            has_twin = bool(orgs) and Organization.objects.filter(twin_criteria).exclude(mnemonic__in=orgs).exists()
        else:
            from core.users.models import UserProfile
            has_twin = UserProfile.objects.filter(username__iexact=owner).exclude(username=owner).exists()
        return public if has_twin else None

    def __apply_common_search_filters(self, ignore_retired_filter=False, force=False):
        results = None
        if not force and not self.should_perform_es_search():
            return results

        search_kwargs = {'index': self.document_model.indexes} if get(self.document_model, 'indexes') else {}
        results = self.document_model.search(**search_kwargs)
        default_filters = self.default_filters.copy()
        if ((self.is_concept_container_document_model() or self.is_repo_document_model()) and
                self.request.query_params.get('allVersions', None) in TRUTHY):
            default_filters.pop('version', None)
        if self.is_user_document() and not self.should_include_inactive():
            # excludes rather than requires, so a profile indexed without the field yet still shows
            results = results.exclude('term', is_active=False)

        updated_by = self.request.query_params.get(UPDATED_BY_USERNAME_PARAM, None)
        if updated_by:
            results = results.filter("terms", updated_by=compact(updated_by.split(',')))
        if self.is_canonical_specified():
            results = results.filter(
                'match_phrase',
                _canonical_url=format_url_for_search(self.request.query_params.get(CANONICAL_URL_REQUEST_PARAM))
            )
        if self.is_source_child_document_model():
            latest_attr = self.get_latest_version_filter_field_for_source_child()
            if latest_attr:
                default_filters[latest_attr] = True
            results = self.exclude_hidden_latest_releases(results, latest_attr)

        for field, value in default_filters.items():
            results = results.filter("term", **{field: value})

        updated_since = parse_updated_since_param(self.request.query_params)
        if updated_since:
            results = results.filter('range', last_update={"gte": updated_since})

        if not ignore_retired_filter and self._should_exclude_retired_from_search_results():
            results = results.filter('term', retired=False)

        visibility_criteria = self.get_search_visibility_criteria()
        if visibility_criteria is not None:
            results = results.filter(visibility_criteria)
        # an org search in the requester's own scope reads the index's members, so it's checked as well
        self.search_checks_access = visibility_criteria is not None or self.is_org_document()
        # with ignore_retired_filter, the facets apply the retired rule and the `retired` filter (get_retired_criterion)
        faceted_criterion = self.get_faceted_criterion(params={
            key: value for key, value in self.request.query_params.dict().items() if to_snake_case(key) != 'retired'
        } if ignore_retired_filter else None)
        if faceted_criterion:
            results = results.filter(faceted_criterion)
        return results

    def is_canonical_specified(self):
        return (
                       self.is_concept_container_document_model() or self.is_repo_document_model()
               ) and self.request.query_params.get(CANONICAL_URL_REQUEST_PARAM, None)

    def __apply_scope_filters(self, results):  # pylint: disable=too-many-branches
        """Limits a search to what the URL names: its owner, repository and version (or the requester's orgs, on a
        members view)."""
        user = self.request.user
        is_authenticated = user.is_authenticated
        username = user.username

        is_members_view = self.is_members_view()
        if self.is_owner_document_model():
            kwargs_filters = self.kwargs.copy()
            if self.user_is_self and is_authenticated:
                kwargs_filters.pop('user_is_self', None)
                kwargs_filters['user'] = username
        else:
            kwargs_filters = self.get_kwargs_filters()
            if is_members_view:
                kwargs_filters['ownerType'] = 'Organization'
                kwargs_filters['owner'] = self.get_members_view_org_mnemonics() or ['UNKNOWN-ORG-DUMMY']
            elif self.user_is_self and is_authenticated and 'collection' not in self.kwargs:
                # a collection's concepts can come from any owner's source, so only other routes filter by owner
                kwargs_filters['ownerType'] = 'User'
                kwargs_filters['owner'] = username

        if self.is_url_registry_document() and not is_members_view:
            kwargs_filters = self.kwargs.copy()
            if self.user_is_self and is_authenticated:
                kwargs_filters.pop('user_is_self', None)
                kwargs_filters['ownerType'] = 'User'
                kwargs_filters['owner'] = username
            elif not kwargs_filters:
                kwargs_filters['ownerUrl'] = '/'
            else:
                if 'org' in kwargs_filters:
                    kwargs_filters['ownerType'] = 'Organization'
                    kwargs_filters['owner'] = kwargs_filters.pop('org')
                elif 'user' in kwargs_filters:
                    kwargs_filters['ownerType'] = 'User'
                    kwargs_filters['owner'] = kwargs_filters.pop('user')

        for key, value in kwargs_filters.items():
            attr = to_snake_case(key)
            if value is None:  # a version that doesn't resolve, such as `latest` with no release: nothing matches
                results = results.filter(Q('match_none'))
            elif isinstance(value, list):
                criteria = Q('term', **{attr: get(value, '0')})
                for val in value[1:]:
                    criteria |= Q('term', **{attr: val})
                results = results.filter(criteria)
            else:
                results = results.filter('term', **{attr: value})
        return results

    def __get_fuzzy_search_results(
            self, source_versions=None, other_filters=None, sort=True
    ):
        results = self.__apply_common_search_filters()
        if results is None:
            return results

        for key, value in (other_filters or {}).items():
            results = results.query('match', **{key: value})

        if source_versions:
            results = results.query(
                self.__get_source_versions_es_criterion(source_versions)
            )
        else:
            results = self.__apply_scope_filters(results)
        results = results.query(self.get_fuzzy_search_criterion())

        min_score = self.request.query_params.get('min_score') or None
        if min_score:
            try:
                score = float(min_score)
            except ValueError:
                score = math.nan
            if not math.isfinite(score):
                raise Http400({'error': f"'min_score' must be a number, got '{min_score}'."})
            results = results.extra(min_score=score)

        if sort:
            results = results.sort(*self._get_sort_attribute())

        if self.request.query_params.get(INCLUDE_SEARCH_META_PARAM) in get_truthy_values():
            results = results.highlight(
                *self.clean_fields_for_highlight(set(compact(self.get_wildcard_search_fields().keys()))))

        return results

    def __get_search_aggregations(
            self, source_versions=None, other_filters=None
    ):
        results = self.__get_fuzzy_search_results(
            source_versions=source_versions, other_filters=other_filters, sort=False
        ) if self.is_fuzzy_search else self.__get_search_results()

        results = results.extra(size=0)
        search = CustomESSearch(results)
        search.apply_aggregation_score_stats()
        search.apply_aggregation_score_histogram()
        return search

    def __get_source_versions_es_criterion(self, source_versions):
        criterion = None
        for source_version in (source_versions or []):
            criteria = self.__get_source_version_es_criteria(source_version)
            if criterion is None:
                criterion = criteria
            else:
                criterion |= criteria
        return criterion or Q()

    @staticmethod
    def __get_source_version_es_criteria(source_version):
        criteria = Q('match', source_version=source_version.version)
        criteria &= Q('match', source=source_version.mnemonic)
        criteria &= Q('match', owner=source_version.parent.mnemonic)
        criteria &= Q('match', owner_type=source_version.parent.resource_type)
        return criteria

    def __get_search_results(self, ignore_retired_filter=False, sort=True, highlight=True, force=False):  # pylint: disable=too-many-branches,too-many-locals,too-many-statements,line-too-long
        results = self.__apply_common_search_filters(ignore_retired_filter, force)
        if results is None:
            return results
        exclude_fuzzy = self.request.query_params.get(EXCLUDE_FUZZY_SEARCH_PARAM) in TRUTHY
        exclude_wildcard = self.request.query_params.get(EXCLUDE_WILDCARD_SEARCH_PARAM) in TRUTHY
        has_search_string = bool(self.get_search_string())

        extras_fields = self.get_extras_searchable_fields_from_query_params()
        extras_fields_exact = self.get_extras_exact_fields_from_query_params()
        extras_fields_exists = self.get_extras_fields_exists_from_query_params()

        if has_search_string:
            criterion, fields = self.get_exact_search_criterion()

            if not exclude_wildcard:
                wildcard_search_criterion, wildcard_search_fields = self.get_wildcard_search_criterion()
                criterion |= wildcard_search_criterion
                fields += wildcard_search_fields
            if not exclude_fuzzy:
                criterion |= self.get_fuzzy_search_criterion(boost_divide_by=10000, expansions=2)
            results = results.query(criterion)
        else:
            fields = []

        must_not_have_criterion = self.get_mandatory_exclude_words_criteria()
        must_have_criterion = self.get_mandatory_words_criteria()
        results = results.filter(must_have_criterion) if must_have_criterion is not None else results
        results = results.filter(~must_not_have_criterion) if must_not_have_criterion is not None else results  # pylint: disable=invalid-unary-operand-type

        if extras_fields:
            fields += list(extras_fields.keys())
            for field, value in extras_fields.items():
                results = results.filter("query_string", query=value, fields=[field])
        if extras_fields_exists:
            fields += list(extras_fields_exists)
            for field in extras_fields_exists:
                results = results.query("exists", field=f"extras.{field}")
        if extras_fields_exact:
            fields += list(extras_fields_exact.keys())
            for field, value in extras_fields_exact.items():
                results = results.query("match", **{field: value}, _expand__to_dot=False)

        results = self.__apply_scope_filters(results)

        sort_attrs = self._get_sort_attribute()
        if has_search_string and self.is_concept_document() and (
                not sort_attrs or (
                    '_score' in get(sort_attrs, '0', {}) and
                    get(sort_attrs, '0._score.order') == 'desc'
                )
        ):
            search_str = self.get_search_string(lower=False)
            results = results.extra(
                rescore={
                  "window_size": 400,
                  "query": {
                    "score_mode": "total",
                    "query_weight": 1.0,
                    "rescore_query_weight": 800.0,
                    "rescore_query": {
                      "dis_max": {
                        "tie_breaker": 0.0,
                        "queries": [
                          {
                            "constant_score": {
                              "filter": { "term": { "_name": { "value": search_str, "case_insensitive": True } } },
                              "boost": 10.0
                            }
                          },
                          {
                            "constant_score": {
                              "filter": { "term": { "_synonyms": { "value": search_str, "case_insensitive": True } } },
                              "boost": 8.0
                            }
                          }
                        ]
                      }
                    }
                  }
                }
            )
        if fields and highlight and self.request.query_params.get(INCLUDE_SEARCH_META_PARAM) in get_truthy_values():
            results = results.highlight(*self.clean_fields_for_highlight(fields))
        results = self.scope_map_codes(
            results.source(excludes=['_synonyms_embeddings', '_embeddings']), self.map_codes_release)
        return results.sort(*sort_attrs) if sort else results

    def get_mandatory_words_criteria(self):
        criterion = None
        for must_have in CustomESSearch.get_must_haves(self.get_raw_search_string()):
            criteria, _ = self.get_wildcard_search_criterion(f"{must_have}*")
            criterion = criteria if criterion is None else criterion & criteria
        return criterion

    def get_mandatory_exclude_words_criteria(self):
        criterion = None
        for must_not_have in CustomESSearch.get_must_not_haves(self.get_raw_search_string()):
            criteria, _ = self.get_wildcard_search_criterion(f"{must_not_have}*")
            criterion = criteria if criterion is None else criterion | criteria
        return criterion

    @staticmethod
    def clean_fields_for_highlight(fields):
        return [field for field in set(compact(fields)) if not field.startswith('_')]

    def _get_sort_attribute(self):
        return self.get_sort_attributes() or [{'_score': {'order': 'desc'}}]

    def get_wildcard_search_fields(self):
        return self.clean_fields(self.document_model.get_wildcard_search_attrs() or {})

    def get_fuzzy_search_fields(self):
        return self.document_model.get_fuzzy_search_attrs() or {}

    def __get_queryset_from_search_results(self, search_results):
        offset = max(to_int(self.request.GET.get('offset'), 0), 0)
        self.limit = int(self.limit) or LIST_DEFAULT_LIMIT
        page = max(to_int(self.request.GET.get('page'), 1), 1)
        start = offset or (page - 1) * self.limit
        end = start + self.limit
        try:
            search_results = search_results.params(request_timeout=ES_REQUEST_TIMEOUT)
            es_search = CustomESSearch(search_results[start:end], self.document_model)
            es_search.to_queryset(
                address_duplicates=self.is_source_child_document_model() and (
                        'source' in self.kwargs or 'collection' in self.kwargs),
                brief=self.is_brief()
            )
            self.total_count = es_search.total - offset
            queryset = self.get_viewable_hits(es_search.queryset) if self.search_checks_access else es_search.queryset
            return queryset, es_search.scores, es_search.max_score, es_search.highlights
        except RequestError as ex:  # pragma: no cover
            reason = get(
                ex, 'info.error.caused_by.reason', ''
            ) or get(ex, 'info.error.root_cause.0.reason', '')
            if reason.startswith('Result window is too large'):
                reason = 'Only 10000 results are available. Please apply additional filters or fine tune your query '\
                         ' to get more accurate results.'
            elif 'input automaton is too large' in reason:
                reason = 'Input value is too large.'

            raise Http400(detail=reason or get(ex, 'info') or get(ex, 'error') or str(ex)) from ex
        except TransportError as ex:  # pragma: no cover
            raise Http400(detail=get(ex, 'info') or get(ex, 'error') or str(ex)) from ex

    def get_viewable_hits(self, hits):
        """
        The hits the requester can view, as the database has them now: a hit whose index document is stale, such as a
        repository made private before it was reindexed, is left out, and so is a concept or mapping of a source the
        requester can't view (a source made private commits before its save signal makes them private). One query for
        the page (one per model for the repo search, whose hits are sources and collections). Like the search's access
        filter, it keeps the repositories, concepts and mappings the requester created. The page can then hold fewer
        hits than its limit, while num_found stays the index's count.
        """
        user = self.request.user
        if not isinstance(hits, models.QuerySet):  # the repo search's sources and collections
            viewable_ids = {
                (model, _id) for model in {type(hit) for hit in hits}
                for _id in self.get_viewable_hits(model.objects.filter(
                    id__in=[hit.id for hit in hits if isinstance(hit, model)])).values_list('id', flat=True)
            }
            return [hit for hit in hits if (type(hit), hit.id) in viewable_ids]
        viewable = hits.model.get_viewable(hits, user)
        if issubclass(hits.model, SourceChildMixin):
            from core.sources.models import Source
            parents = Source.get_viewable(Source.objects.filter(id__in=hits.order_by().values('parent_id')), user)
            viewable = viewable.filter(parent_id__in=parents.values('id'))
        if user.is_authenticated and not user.is_staff and (
                self.is_concept_container_document_model() or self.is_source_child_document_model()):
            viewable = hits.filter(models.Q(id__in=viewable.values('id')) | models.Q(created_by_id=user.id))
        return viewable

    def get_search_results_qs(self):
        return self.__get_queryset_from_search_results(self.__get_search_results())

    def get_fuzzy_search_results_qs(
            self, source_versions=None, other_filters=None
    ):
        return self.__get_queryset_from_search_results(self.__get_fuzzy_search_results(source_versions, other_filters))

    def get_search_stats(
            self, source_versions=None, other_filters=None
    ):
        return self.__get_search_aggregations(
            source_versions, other_filters
        ).get_aggregations(self.is_verbose(), self.is_raw())

    def is_repo_version_children_request(self):
        if self.is_source_child_document_model() and self.kwargs and 'source' in self.kwargs:
            parent = get(self, 'parent_resource')
            if parent and not parent.is_head:
                return True
        return False

    def is_repo_version_children_request_without_any_search(self):
        # used for caching repo versions concepts/mappings first page
        if self.is_repo_version_children_request() and not self.get_search_string() and not self.is_verbose():
            page = self.request.query_params.dict().get('page', '').strip()
            sort_field, _ = self.get_sort_and_desc()
            sort = (sort_field or '').strip()
            limit = self.request.query_params.dict().get('limit', '').strip()
            if limit:
                limit = to_int(limit, 25)
            return bool(
                (not limit or limit == 25) and (not page or page in [1, '1']) and (not sort or sort == '_score')
            )
        return False

    def should_perform_es_search(self):
        sort_field, _ = self.get_sort_and_desc()
        return (
                bool(self.get_search_string()) or
                self.has_searchable_extras_fields() or
                bool(self.get_faceted_filters()) or
                (bool(sort_field) and sort_field != '_score')
        ) or (SEARCH_PARAM in self.request.query_params.dict() and self.should_search_latest_repo())

    def is_sliced(self):
        return self.should_perform_es_search()

    def should_search_latest_repo(self):
        return self.is_source_child_document_model() and (
                'version' not in self.kwargs and 'collection' not in self.kwargs and 'source' not in self.kwargs
        ) and self.is_latest_repo_search_header_present()

    def has_searchable_extras_fields(self):
        return bool(
            self.get_extras_searchable_fields_from_query_params()
        ) or bool(
            self.get_extras_fields_exists_from_query_params()
        ) or bool(
            self.get_extras_exact_fields_from_query_params()
        )

    def get_owner_from_kwargs(self):
        from core.orgs.models import Organization
        from core.users.models import UserProfile
        org = self.kwargs.get('org', None)
        user = self.kwargs.get('user', None)

        if not user and self.user_is_self:
            user = self.request.user.username

        if org:
            return Organization.objects.filter(mnemonic=org).first()
        if user:
            return UserProfile.objects.filter(username=user).first()

        return None


class SourceChildCommonBaseView(BaseAPIView):
    lookup_field = None
    model = None
    queryset = None
    params = None
    document_model = None
    es_fields = {}
    pk_field = 'mnemonic'
    permission_classes = (CanViewParentDictionary, )
    is_searchable = True
    default_filters = {}
    viewable_query_versions = None  # see set_viewable_query_versions
    url_versions = ()  # the source versions check_source_version_access read

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.__set_params()
        if request.method in ['GET', 'HEAD']:
            if 'source' in self.kwargs:
                self.check_source_version_access(request)
            self.check_query_source_version_access(request)
            self.check_query_collection_version_access(request)

    def verify_scope(self):
        has_parent_scope = self.has_parent_scope()
        has_no_kwargs = self.has_no_kwargs()
        if not self.user_is_self:
            if has_no_kwargs:
                if self.request.method not in ['GET', 'HEAD']:
                    raise Http404()
            elif not has_parent_scope:
                raise Http404()

    def __get_params(self):
        kwargs = self.kwargs.copy()
        if self.user_is_self and self.request.user.is_authenticated:
            kwargs['user'] = self.request.user.username

        query_params = self.request.query_params.dict().copy()
        kwargs.update(query_params)
        return compact_dict_by_values(kwargs)

    def __set_params(self):
        self.params = self.__get_params()
        if self.params:
            # `limit` as initialize read (and capped) it; `_count` isn't read here
            limit = self.limit if self.params.get(LIMIT_PARAM) else self.cap_limit(LIST_DEFAULT_LIMIT)
            self.limit = to_int(CSV_DEFAULT_LIMIT if self.params.get('csv') else limit, LIST_DEFAULT_LIMIT)

    def get_parent_owner_filters(self, params=None):
        """
        The owner of the repository in the URL, as filters on Source or Collection. Given `params` (the URL's, with
        the query string's over them), the owner they name; none when they name no owner, as the reads under them
        then match every owner's.
        """
        if params is not None:
            if params.get('org'):
                return {'organization__mnemonic': params['org']}
            return {'user__username': params['user']} if params.get('user') else {}
        if 'org' in self.kwargs:
            return {'organization__mnemonic': self.kwargs['org']}
        username = self.request.user.username if self.user_is_self else self.kwargs.get('user')
        return {'user__username': username}

    def check_view_access(self, request, repo_version):
        """401 or 403 unless the requester can view this repository version, as its own lists answer."""
        if not CanViewConceptDictionary().has_object_permission(request, self, repo_version):
            self.permission_denied(request)

    def check_source_version_access(self, request):
        """
        Every read under a source (lists, items, their versions, mappings and hierarchy, searched or not) answers as
        the source version the URL names: 404 when the source or the named version doesn't exist, then 401 or 403
        unless the requester can view the source and that version. For `latest`, that's the newest released
        version, which the reads answer from, as well as any version named `latest`; a source with no released
        version has only its HEAD to check. The URL can name several sources, separated by commas, as the reads
        match each: 404 unless each exists, then 401 or 403 unless the requester can view each. Item-level checks
        still follow.
        """
        from core.sources.models import Source
        mnemonics = list(dict.fromkeys(compact(self.kwargs['source'].split(','))))  # each once
        if not mnemonics:
            raise Http404()
        self.url_versions = []
        for mnemonic in mnemonics:
            repo_versions = Source.get_url_versions(
                mnemonic, self.kwargs.get('version'), self.get_parent_owner_filters())
            if repo_versions is None:
                raise Http404()
            self.url_versions += repo_versions
        for repo_version in self.url_versions:
            self.check_view_access(request, repo_version)

    def get_url_source_version(self, mnemonic, version, owner_filters):
        """Source.get_version, from the versions check_source_version_access read when it's one of them."""
        from core.sources.models import Source
        return next((repo_version for repo_version in self.url_versions if (
            repo_version.mnemonic, repo_version.version) == (mnemonic, version)), None) or Source.get_version(
            mnemonic, version, owner_filters)

    def check_query_source_version_access(self, request):
        """
        The query string can name the source, its owner and its version over the URL's, and the reads then answer
        from those. When it names a version other than HEAD this way, each source it names (commas separate
        several) answers as check_source_version_access does for the URL's: 404 unless the source and that version
        exist under the owner it names, then 401 or 403 unless the requester can view both. A source that doesn't
        exist there has nothing to read (a search reads the name as a facet), so it's skipped. `latest` is the
        newest released version, as the reads resolve it. With no owner (none in the URL, or an empty `org`), see
        set_viewable_query_versions. On a collection's route, the URL's version is the collection's, left to its
        own checks. HEAD's own content carries its access, so reads of it are checked where they're made.
        """
        from core.sources.models import Source
        params = self.params or {}
        version = params.get('version')
        if not params.get('source') or version in [None, HEAD]:
            return
        owner_filters = self.get_parent_owner_filters(params)
        url_repo = 'source' if 'source' in self.kwargs else 'collection'
        if (version, params.get(url_repo), owner_filters) == (
                self.kwargs.get('version'), self.kwargs.get(url_repo), self.get_parent_owner_filters()):
            return  # the URL's own version, checked already
        mnemonics = list(dict.fromkeys(compact(params['source'].split(','))))  # each once
        if not owner_filters:
            self.set_viewable_query_versions(request, mnemonics, version)
            return
        for mnemonic in mnemonics:
            head = Source.get_version(mnemonic, HEAD, owner_filters)
            if not head:
                continue
            repo_version = Source.find_latest_released_version_by(
                {**owner_filters, 'mnemonic': mnemonic}) if version == LATEST else Source.get_version(
                mnemonic, version, owner_filters)
            if not repo_version and version != LATEST:
                raise Http404()
            for viewed in compact([head, repo_version]):
                self.check_view_access(request, viewed)

    def set_viewable_query_versions(self, request, mnemonics, version):
        """
        With no owner named, the reads answer from the version the query string names under every owner's source of
        that name. They keep to the ones the requester can view, leaving out the others (not refusing them, which
        would show that they exist), and a source answers 404 unless one owner's is left. A renamed source's versions
        keep their old name, so they're left out too. `latest` resolves to no release without an owner, so those
        reads find nothing.
        """
        if version == LATEST:
            return
        from core.sources.models import Source
        viewable_versions = []
        for mnemonic in mnemonics:
            viewable = [repo_version for repo_version in Source.objects.filter(mnemonic=mnemonic, version=version)
                        if repo_version.has_view_access(request.user)]
            if not viewable:
                raise Http404()
            viewable_versions += viewable
        self.viewable_query_versions = viewable_versions

    def keep_to_viewable_query_versions(self, queryset):
        """The reads' concepts or mappings, kept to the members of the versions set_viewable_query_versions found."""
        if self.viewable_query_versions is None:
            return queryset
        return queryset.filter(
            id__in=queryset.model.objects.filter(sources__in=self.viewable_query_versions).values('id'))

    def check_query_collection_version_access(self, request):
        """
        The query string can name collections (commas separate several) to keep the reads to their members. With an
        owner (the query string's over the URL's), each that exists under it answers as a source the query string
        names does (check_query_source_version_access): 404 unless the version it names exists, then 401 or 403
        unless the requester can view the collection and that version. Unlike a source's, a collection's HEAD is
        checked too, as it holds other repositories' content. The version is the collection's only where the URL
        names no repository. A collection the URL names keeps its own checks. A value with `!` or `*` names no
        collection, and names with no owner name none in particular: the reads keep to the versions the requester
        can view (get_viewable_query_collection_versions, get_collection_facet_criterion).
        """
        from core.collections.models import Collection
        params = self.params or {}
        owner_filters = self.get_parent_owner_filters(params)
        # every key a search reads as `collection` (`Collection` too); values with `!` or `*` name no collection
        values = [value for key, value in request.query_params.items() if to_snake_case(key) == 'collection' and (
            value and not value.startswith('!') and not value.endswith('*'))]
        if not values or not owner_filters:
            return
        version = None if self.has_concept_container_scope() else params.get('version')
        url_collection = (self.kwargs.get('collection'), self.get_parent_owner_filters())
        names = [name.strip('\"').strip('\'') for value in values for name in value.split(',')]  # as a search does
        for mnemonic in dict.fromkeys(compact(names)):
            if (mnemonic, owner_filters) == url_collection:
                continue
            head = Collection.get_version(mnemonic, HEAD, owner_filters)
            if not head:
                continue  # nothing to read; a search reads the name as a facet
            repo_version = None
            if version not in [None, HEAD]:
                repo_version = Collection.find_latest_released_version_by(
                    {**owner_filters, 'mnemonic': mnemonic}) if version == LATEST else Collection.get_version(
                    mnemonic, version, owner_filters)
                if not repo_version and version != LATEST:
                    raise Http404()
            for viewed in compact([head, repo_version]):
                self.check_view_access(request, viewed)

    def get_viewable_query_collection_versions(self):
        """
        The reads (get_base_queryset) keep to the members of the collections the query string names, any version's
        unless it names one, and any owner's unless it names one: the versions of those the requester can view, for
        the reads to keep to. None for staff, and when the query string names no collection.
        """
        from core.collections.models import Collection
        value = self.request.query_params.get('collection')
        viewable = Collection.get_viewable_versions_criteria(self.request.user)
        if not value or viewable is None:
            return None
        params = self.params or {}
        owner_filters = compact_dict_by_values(
            {'organization__mnemonic': params.get('org'), 'user__username': params.get('user')})
        return Collection.objects.filter(viewable, mnemonic__in=compact(value.split(',')), **owner_filters)


class SourceChildExtrasBaseView:
    default_qs_sort_attr = '-created_at'

    def get_throttles(self):
        return ThrottleUtil.get_throttles_by_user_plan(self.request.user)

    def get_object(self):
        queryset = self.get_queryset()

        if 'concept_version' in self.kwargs or 'mapping_version' in self.kwargs:
            instance = queryset.first()
        else:
            instance = queryset.filter(is_latest_version=True).first()

        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)

        return instance

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewParentDictionary()]

        return [CanEditParentDictionary()]


class SourceChildExtrasView(SourceChildExtrasBaseView, ListAPIView):
    def list(self, request, *args, **kwargs):
        return Response(get(self.get_object(), 'extras', {}))


class SourceChildExtraRetrieveUpdateDestroyView(SourceChildExtrasBaseView, RetrieveUpdateDestroyAPIView):
    def retrieve(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        instance = self.get_object()
        extras = get(instance, 'extras', {})
        if key in extras:
            return Response({key: extras[key]})
        return Response({'detail': NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

    def update(self, request, **kwargs):  # pylint: disable=arguments-differ
        key = kwargs.get('extra')
        value = request.data.get(key)
        if not value:
            return Response([MUST_SPECIFY_EXTRA_PARAM_IN_BODY.format(key)], status=status.HTTP_400_BAD_REQUEST)

        new_version = self.get_object().clone()
        new_version.extras[key] = value
        new_version.comment = f'Updated extras: {key}={value}.'
        errors = new_version.save_as_new_version(request.user)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        return Response({key: value})

    def delete(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        new_version = self.get_object().clone()
        if key in new_version.extras:
            del new_version.extras[key]
            new_version.comment = f'Deleted extra {key}.'
            errors = new_version.save_as_new_version(request.user)
            if errors:
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response({'detail': NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)


class APIVersionView(APIView):  # pragma: no cover
    permission_classes = (AllowAny,)
    swagger_schema = None

    def get_throttles(self):
        return []

    @staticmethod
    def get(_):
        return Response(__version__)


class ChangeLogView(APIView):  # pragma: no cover
    permission_classes = (AllowAny, )
    swagger_schema = None

    def get_throttles(self):
        return []

    @staticmethod
    def get(_):
        resp = requests.get('https://raw.githubusercontent.com/OpenConceptLab/oclapi2/master/changelog.md')
        return HttpResponse(markdown.markdown(resp.text), content_type="text/html")


class RootView(BaseAPIView):  # pragma: no cover
    permission_classes = (AllowAny,)
    serializer_class = RootSerializer

    def get_throttles(self):
        return []

    def get(self, _):
        from core.urls import urlpatterns
        data = {'version': __version__, 'routes': {}}
        main_host_url = self.get_host_url()

        for pattern in urlpatterns:
            name = getattr(pattern, 'name', None) or getattr(pattern, 'app_name', None)
            if name in ['admin']:
                continue
            route = str(pattern.pattern)
            if isinstance(route, str):
                if any(route.startswith(path) for path in ['admin/', 'manage/bulkimport/', 'oidc/']):
                    continue
                if route.startswith('^\\'):
                    route = route.replace('^\\', '')
            if name == 'schema-json':
                route = 'swagger.json'
            if route and name is None:
                name = ('_'.join(route.split('/')[:2])).replace('__', '_').lower()
                if name.endswith('_'):
                    name = name[:-1]
                if name == 'user':
                    name = 'current_user'
            if name == 'core.toggles':
                name = 'toggles'
            if not name:
                continue
            host_url = main_host_url.replace('://api.', '://fhir.') if 'fhir' in name else main_host_url
            data['routes'][name] = host_url + '/' + route

        data['routes'].pop('root')

        return Response(data)


class BaseLogoView:
    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        data = request.data
        obj = self.get_object()
        obj.upload_base64_logo(data.get('base64'), 'logo.png')

        return Response(self.get_serializer_class()(obj).data, status=status.HTTP_200_OK)


class FeedbackView(APIView):  # pragma: no cover
    permission_classes = (AllowAny, )

    def get_throttles(self):
        return ThrottleUtil.get_throttles_by_user_plan(self.request.user)

    @staticmethod
    @swagger_auto_schema(request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            'description': openapi.Schema(type=openapi.TYPE_STRING, description='Feedback/Suggestion/Complaint'),
            'url': openapi.Schema(type=openapi.TYPE_STRING, description='Specific URL to point'),
        }
    ))
    def post(request):
        message = request.data.get('description', '') or ''
        url = request.data.get('url', False)
        name = request.data.get('name', None)
        email = request.data.get('email', None)

        if not message and not url:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if url:
            message += '\n\n' + 'URL: ' + url

        user = request.user

        if user.is_authenticated:
            username = user.username
            email = user.email
        else:
            username = name or 'Guest'
            email = email or None

        message += '\n\n' + 'Reported By: ' + username
        subject = f"[{settings.ENV.upper()}] [FEEDBACK] From: {username}"

        mail = EmailMessage(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[settings.COMMUNITY_EMAIL],
            cc=[email] if email else [],
        )

        image = request.data.get('image', False)

        if image:
            ext, img_data = image.split(';base64,')
            extension = ext.split('/')[-1]
            image_name = 'feedback.' + extension

            img = MIMEImage(base64.b64decode(img_data), extension)
            img.add_header("Content-Disposition", "inline", filename=image_name)
            mail.attach(img)

        mail.send()

        return Response(status=status.HTTP_200_OK)


class ConceptContainerExtraRetrieveUpdateDestroyView(RetrieveUpdateDestroyAPIView):
    def retrieve(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        instance = self.get_object()
        extras = get(instance, 'extras', {})
        if key in extras:
            return Response({key: extras[key]})

        return Response({'detail': NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

    def update(self, request, **kwargs):  # pylint: disable=arguments-differ
        key = kwargs.get('extra')
        value = request.data.get(key)
        if not value:
            return Response([MUST_SPECIFY_EXTRA_PARAM_IN_BODY.format(key)], status=status.HTTP_400_BAD_REQUEST)

        instance = self.get_object()
        instance.extras = get(instance, 'extras', {})
        instance.extras[key] = value
        instance.comment = f'Updated extras: {key}={value}.'
        instance.save()
        instance.set_checksums()
        return Response({key: value})

    def delete(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        instance = self.get_object()
        instance.extras = get(instance, 'extras', {})
        if key in instance.extras:
            del instance.extras[key]
            instance.comment = f'Deleted extra {key}.'
            instance.save()
            instance.set_checksums()
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response({'detail': NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)


class AbstractChecksumView(APIView):
    permission_classes = (IsAuthenticated,)
    smart = False

    def get_throttles(self):
        return ThrottleUtil.get_throttles_by_user_plan(self.request.user)

    @swagger_auto_schema(
        manual_parameters=[all_resource_query_param],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            description='Data to generate checksum',
        ),
        responses={
            200: openapi.Response(
                'MD5 checksum of the request body for a resource',
                openapi.Schema(type=openapi.TYPE_STRING),
            )
        },
    )
    def post(self, request):
        resource = request.query_params.get('resource')
        data = request.data
        if not resource or not data:
            return Response({'error': 'resource and data are both required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            checksum = ChecksumModel.generate_checksum_from_many(
                resource, request.data, 'smart' if self.smart else 'standard')
        except ValueError as ex:
            return Response({'error': str(ex)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(checksum)


class StandardChecksumView(AbstractChecksumView):
    smart = False


class SmartChecksumView(AbstractChecksumView):
    smart = True
