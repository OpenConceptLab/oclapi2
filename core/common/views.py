import base64
from email.mime.image import MIMEImage

import markdown
import requests
from django.conf import settings
from django.core.mail import EmailMessage
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from elasticsearch import RequestError, TransportError
from elasticsearch_dsl import Q
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
    CANONICAL_URL_REQUEST_PARAM, CHECKSUMS_PARAM
from core.common.exceptions import Http400
from core.common.mixins import PathWalkerMixin
from core.common.search import CustomESSearch
from core.common.serializers import RootSerializer
from core.common.swagger_parameters import all_resource_query_param
from core.common.utils import compact_dict_by_values, to_snake_case, parse_updated_since_param, \
    to_int, get_falsy_values, get_truthy_values, format_url_for_search, get_embeddings
from core.concepts.permissions import CanViewParentDictionary, CanEditParentDictionary
from core.orgs.constants import ORG_OBJECT_TYPE
from core.toggles.models import Toggle
from core.users.constants import USER_OBJECT_TYPE

TRUTHY = get_truthy_values()


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
    is_only_searchable = False
    limit = LIST_DEFAULT_LIMIT
    default_filters = {}
    sort_asc_param = 'sortAsc'
    sort_desc_param = 'sortDesc'
    sort_param = 'sort'
    default_qs_sort_attr = '-updated_at'
    facet_class = None
    total_count = 0

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

    def _should_exclude_retired_from_search_results(self):
        if self.is_owner_document_model() or 'expansion' in self.kwargs or self.is_url_registry_document():
            return False

        params = get(self, 'params') or self.request.query_params.dict()
        include_retired = params.get('retired', None) in TRUTHY or params.get(INCLUDE_RETIRED_PARAM, None) in TRUTHY
        return not include_retired

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
        self.limit = params.get(LIMIT_PARAM, None) or params.get(FHIR_LIMIT_PARAM, None) or LIST_DEFAULT_LIMIT

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
            fields = sort_fields.lower().split(',')
            for field in fields.copy():
                field = field.strip()
                is_desc = field.startswith('-')
                field = field.replace('-', '', 1) if is_desc else field
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
                    current_result = {field: order_details}
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

    def get_faceted_criterion(self):
        filters = self.get_faceted_filters()

        def get_query(attr, val):
            not_query = val.startswith('!')
            vals = val.replace('!', '', 1).split(',')
            query = Q('match', **{attr: vals.pop().strip('\"').strip('\'')})
            criteria = ~query if not_query else query  # pylint: disable=invalid-unary-operand-type

            for _val in vals:
                query = Q('match', **{attr: _val.strip('\"').strip('\'')})
                if not_query:
                    criteria &= ~query  # pylint: disable=invalid-unary-operand-type
                else:
                    criteria |= query

            return criteria

        if filters:
            first_filter = filters.popitem()
            criterion = get_query(first_filter[0], first_filter[1])
            for field, value in filters.items():
                criterion &= get_query(field, value)

            return criterion

    def get_faceted_filters(self, split=False):
        faceted_filters = {}
        faceted_fields = self.get_faceted_fields()
        query_params = {to_snake_case(k): v for k, v in self.request.query_params.dict().items()}
        for field in faceted_fields:
            if field in query_params:
                query_value = query_params[field]
                faceted_filters[field] = query_value.split(',') if split else query_value
        return faceted_filters

    def get_faceted_fields(self):
        return [field for field, config in get(self, 'es_fields', {}).items() if config.get('facet', False)]

    def get_facet_filters_from_kwargs(self):
        kwargs = self.kwargs
        filters = {}
        is_collection_specified = 'collection' in self.kwargs
        is_user_specified = 'user' in kwargs
        if is_collection_specified:
            filters['collection'] = kwargs['collection']
            filters['collection_owner_url'] = f'/users/{kwargs["user"]}/' if is_user_specified else \
                f'/orgs/{kwargs["org"]}/'
            if 'expansion' in self.kwargs:
                filters['expansion'] = self.kwargs.get('expansion')
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
            if is_version_specified:
                container_version = self.kwargs['version']
                is_latest_released = container_version == LATEST
                params = {'user__username': self.kwargs.get('user'), 'organization__mnemonic': self.kwargs.get('org')}
                if is_latest_released:
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
                elif is_collection_specified and 'expansion' not in self.kwargs:
                    from core.collections.models import Collection
                    version = Collection.objects.filter(
                        **params, mnemonic=self.kwargs['collection'], version=self.kwargs['version']
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
                if 'expansion' in self.kwargs:
                    filters['expansion'] = self.kwargs.get('expansion')
                elif version:
                    filters['expansion'] = get(version, 'expansion.mnemonic', 'NO_EXPANSION')
                filters['collection_url'] = f"{filters['collection_owner_url']}collections/{self.kwargs['collection']}/"
                if is_version_specified and self.kwargs['version'] != HEAD:
                    filters['collection_url'] += f"{filters['collection_version'] or self.kwargs['version']}/"
            if is_source_specified and not is_version_specified and not self.should_search_latest_repo():
                filters['source_version'] = HEAD
        return filters

    def get_latest_version_filter_field_for_source_child(self):
        query_latest = self.__should_query_latest_version()
        if query_latest:
            return 'is_in_latest_source_version' if self.should_search_latest_repo() else 'is_latest_version'
        if not self.is_global_scope() and (
                self.kwargs.get('version') == HEAD or not self.kwargs.get('version')
        ) and 'collection' not in self.kwargs:
            return 'is_latest_version'
        return None

    def get_facets(self):
        facets = {}

        if self.facet_class:
            if self.is_user_document():
                return facets

            faceted_search = self.facet_class(  # pylint: disable=not-callable
                self.get_search_string(lower=False),
                _search=self.__get_search_results(ignore_retired_filter=True, sort=False, highlight=False, force=True),
            )
            faceted_search.params(request_timeout=ES_REQUEST_TIMEOUT)
            try:
                s = faceted_search.execute()
                facets = s.facets.to_dict()
            except TransportError as ex:  # pragma: no cover
                raise Http400(detail=get(ex, 'info') or get(ex, 'error') or str(ex)) from ex
        if not get(self.request.user, 'is_authenticated'):
            facets.pop('updatedBy', None)
        if self.should_search_latest_repo() and self.is_source_child_document_model() and 'source_version' in facets:
            facets['source_version'] = [facet for facet in facets['source_version'] if facet[0] != 'HEAD']
        is_global_scope = ('org' not in self.kwargs and 'user' not in self.kwargs and not self.user_is_self)
        if is_global_scope:
            facets.pop('source_version', None)
            facets.pop('collection_version', None)
            facets.pop('expansion', None)
            facets.pop('collection_owner_url', None)
        facets.pop('is_in_latest_source_version', None)
        facets.pop('is_latest_version', None)
        return facets

    def get_extras_searchable_fields_from_query_params(self):
        query_params = self.request.query_params.dict()
        result = {}
        for key, value in query_params.items():
            if key.startswith('extras.') and not key.startswith('extras.exists') and not key.startswith('extras.exact'):
                parts = key.split('extras.')
                value = value.replace('/', '\\/').replace('-', '_')
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
        criteria = Q('match', public_can_view=True)
        user = self.request.user

        if user.is_authenticated:
            username = user.username
            from core.orgs.documents import OrganizationDocument
            if self.document_model in [OrganizationDocument]:
                criteria |= (Q('match', public_can_view=False) & Q('match', user=username))
            if self.is_concept_container_document_model() or self.is_source_child_document_model():
                criteria |= (Q('match', public_can_view=False) & Q('match', created_by=username))

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

    def __apply_common_search_filters(self, ignore_retired_filter=False, force=False):
        results = None
        if not force and not self.should_perform_es_search():
            return results

        search_kwargs = {'index': self.document_model.indexes} if get(self.document_model, 'indexes') else {}
        results = self.document_model.search(**search_kwargs)
        default_filters = self.default_filters.copy()
        if self.is_user_document() and self.should_include_inactive():
            default_filters.pop('is_active', None)

        updated_by = self.request.query_params.get(UPDATED_BY_USERNAME_PARAM, None)
        if updated_by:
            results = results.query("terms", updated_by=compact(updated_by.split(',')))
        if self.is_canonical_specified():
            results = results.query(
                'match_phrase',
                _canonical_url=format_url_for_search(self.request.query_params.get(CANONICAL_URL_REQUEST_PARAM))
            )
        if self.is_source_child_document_model():
            latest_attr = self.get_latest_version_filter_field_for_source_child()
            if latest_attr:
                default_filters[latest_attr] = True

        for field, value in default_filters.items():
            results = results.query("match", **{field: value})

        updated_since = parse_updated_since_param(self.request.query_params)
        if updated_since:
            results = results.query('range', last_update={"gte": updated_since})

        if not ignore_retired_filter and self._should_exclude_retired_from_search_results():
            results = results.query('match', retired=False)

        include_private = self._should_include_private()
        if not include_private:
            results = results.query(self.get_public_criteria())

        faceted_criterion = self.get_faceted_criterion()
        if faceted_criterion:
            results = results.query(faceted_criterion)
        return results

    def is_canonical_specified(self):
        return (
                       self.is_concept_container_document_model() or self.is_repo_document_model()
               ) and self.request.query_params.get(CANONICAL_URL_REQUEST_PARAM, None)

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
        results = results.query(self.get_fuzzy_search_criterion())

        min_score = self.request.query_params.get('min_score') or None
        if min_score:
            results = results.extra(min_score=float(min_score))

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

    def __get_search_results(self, ignore_retired_filter=False, sort=True, highlight=True, force=False):  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
        is_semantic = (Toggle.get('SEMANTIC_SEARCH_TOGGLE') and
                       self.is_concept_document() and self.request.query_params.get('semantic', None) == 'true')
        results = self.__apply_common_search_filters(ignore_retired_filter, force)
        if results is None:
            return results
        fields = []
        if not is_semantic:
            exclude_fuzzy = self.request.query_params.get(EXCLUDE_FUZZY_SEARCH_PARAM) in TRUTHY
            exclude_wildcard = self.request.query_params.get(EXCLUDE_WILDCARD_SEARCH_PARAM) in TRUTHY

            extras_fields = self.get_extras_searchable_fields_from_query_params()
            extras_fields_exact = self.get_extras_exact_fields_from_query_params()
            extras_fields_exists = self.get_extras_fields_exists_from_query_params()
            criterion, fields = self.get_exact_search_criterion()

            if not exclude_wildcard:
                wildcard_search_criterion, wildcard_search_fields = self.get_wildcard_search_criterion()
                criterion |= wildcard_search_criterion
                fields += wildcard_search_fields
            if not exclude_fuzzy:
                criterion |= self.get_fuzzy_search_criterion(boost_divide_by=10000, expansions=2)
            results = results.query(criterion)

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

        user = self.request.user
        is_authenticated = user.is_authenticated
        username = user.username

        is_members_view = self.get_view_name() in ['Organization Collection List', 'Organization Source List',
                                                   'Organization Repo List', 'Organization Url Registry List']
        if self.is_owner_document_model():
            kwargs_filters = self.kwargs.copy()
            if self.user_is_self and is_authenticated:
                kwargs_filters.pop('user_is_self', None)
                kwargs_filters['user'] = username
        else:
            kwargs_filters = self.get_kwargs_filters()
            if is_members_view:
                kwargs_filters['ownerType'] = 'Organization'
                kwargs_filters['owner'] = list(
                    user.organizations.values_list('mnemonic', flat=True)) or ['UNKNOWN-ORG-DUMMY']
            elif self.user_is_self and is_authenticated:
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
            if isinstance(value, list):
                criteria = Q('match', **{attr: get(value, '0')})
                for val in value[1:]:
                    criteria |= Q('match', **{attr: val})
                results = results.query(criteria)
            else:
                results = results.query('match', **{attr: value})

        if is_semantic:
            filters = get(results.to_dict(), 'query.bool.must', [])
            embeddings = get_embeddings(self.get_search_string())
            results = results.knn(
                field='_embeddings.vector',
                query_vector=embeddings,
                k=5,
                num_candidates=10,
                filter=filters,
                boost=5
            )
            results = results.knn(
                field='_embeddings.vector',
                query_vector=embeddings,
                k=5,
                num_candidates=10,
                filter=filters,
                boost=1
            )
        elif fields and highlight and self.request.query_params.get(INCLUDE_SEARCH_META_PARAM) in get_truthy_values():
            results = results.highlight(*self.clean_fields_for_highlight(fields))
        return results.sort(*self._get_sort_attribute()) if sort else results

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
            es_search.to_queryset()
            self.total_count = es_search.total - offset
            return es_search.queryset, es_search.scores, es_search.max_score, es_search.highlights
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

    def should_perform_es_search(self):
        sort_field, _ = self.get_sort_and_desc()
        return (
                self.is_only_searchable or
                bool(self.get_search_string()) or
                self.has_searchable_extras_fields() or
                bool(self.get_faceted_filters()) or
                (bool(sort_field) and sort_field != '_score')
        ) or (SEARCH_PARAM in self.request.query_params.dict() and self.should_search_latest_repo())

    def should_search_latest_repo(self):
        return self.is_source_child_document_model() and (
                'version' not in self.kwargs and 'collection' not in self.kwargs
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

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.__set_params()

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
            self.limit = to_int(
                CSV_DEFAULT_LIMIT if self.params.get('csv') else self.params.get(LIMIT_PARAM),
                LIST_DEFAULT_LIMIT
            )


class SourceChildExtrasBaseView:
    default_qs_sort_attr = '-created_at'

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

    @staticmethod
    def get(_):
        return Response(__version__)


class ChangeLogView(APIView):  # pragma: no cover
    permission_classes = (AllowAny, )
    swagger_schema = None

    @staticmethod
    def get(_):
        resp = requests.get('https://raw.githubusercontent.com/OpenConceptLab/oclapi2/master/changelog.md')
        return HttpResponse(markdown.markdown(resp.text), content_type="text/html")


class RootView(BaseAPIView):  # pragma: no cover
    permission_classes = (AllowAny,)
    serializer_class = RootSerializer

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
            if route and name is None:
                name = ('_'.join(route.split('/')[:2])).replace('__', '_').lower()
                if name.endswith('_'):
                    name = name[:-1]
                if name == 'user':
                    name = 'current_user'
            if name == 'core.toggles':
                name = 'toggles'

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
