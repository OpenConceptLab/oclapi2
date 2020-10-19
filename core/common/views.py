from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from elasticsearch_dsl import Q
from pydash import get
from rest_framework import response, generics, status
from rest_framework.generics import ListAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.response import Response

from core.common.constants import SEARCH_PARAM, ES_RESULTS_MAX_LIMIT, LIST_DEFAULT_LIMIT, CSV_DEFAULT_LIMIT, \
    LIMIT_PARAM, NOT_FOUND, MUST_SPECIFY_EXTRA_PARAM_IN_BODY
from core.common.mixins import PathWalkerMixin
from core.common.utils import compact_dict_by_values, to_snake_case, to_camel_case
from core.concepts.permissions import CanViewParentDictionary, CanEditParentDictionary
from core.orgs.constants import ORG_OBJECT_TYPE
from core.users.constants import USER_OBJECT_TYPE


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
    limit = LIST_DEFAULT_LIMIT
    default_filters = dict(is_active=True)
    sort_asc_param = 'sortAsc'
    sort_desc_param = 'sortDesc'
    default_qs_sort_attr = '-updated_at'
    exact_match = 'exact_match'
    facet_class = None

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.initialize(request, request.path_info, **kwargs)

    def initialize(self, request, path_info_segment, **kwargs):  # pylint: disable=unused-argument
        self.user_is_self = kwargs.pop('user_is_self', False)
        self.limit = request.query_params.dict().get(LIMIT_PARAM, LIST_DEFAULT_LIMIT)

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
        return self.request.META['wsgi.url_scheme'] + '://' + self.request.get_host()

    def head(self, request, **kwargs):  # pylint: disable=unused-argument
        res = HttpResponse()
        res['num_found'] = self.filter_queryset(self.get_queryset()).count()
        return res

    def filter_queryset(self, queryset):
        if self.is_searchable and self.should_perform_es_search():
            return self.get_search_results_qs().filter(id__in=queryset.values_list('id'))

        return super().filter_queryset(queryset).order_by(self.default_qs_sort_attr)

    def get_sort_and_desc(self):
        query_params = self.request.query_params.dict()

        sort_field = query_params.get(self.sort_desc_param)
        if sort_field is not None:
            return sort_field, True

        sort_field = query_params.get(self.sort_asc_param)
        if sort_field is not None:
            return sort_field, False

        return None, None

    def is_valid_sort(self, field):
        if not self.es_fields or not field:
            return False
        if field in self.es_fields:
            attrs = self.es_fields[field]
            return attrs.get('sortable', False)

        return False

    def is_exact_match_on(self):
        return self.request.query_params.dict().get(self.exact_match, None) == 'on'

    def get_default_sort(self):
        for field in self.es_fields:
            attrs = self.es_fields[field]
            if 'sortable' in attrs and 'default' in attrs:
                prefix = '-' if attrs['default'] == 'desc' else ''
                return prefix + field
        return None

    def get_searchable_fields(self):
        return [field for field, config in get(self, 'es_fields', dict()).items() if config.get('filterable', False)]

    def get_exact_search_fields(self):
        return [field for field, config in get(self, 'es_fields', dict()).items() if config.get('exact', False)]

    def get_search_string(self):
        return self.request.query_params.dict().get(SEARCH_PARAM, '')

    def get_wildcard_search_string(self):
        return "*{}*".format(self.get_search_string())

    def get_sort_attr(self):
        sort_field, desc = self.get_sort_and_desc()
        if self.is_valid_sort(sort_field):
            if desc:
                sort_field = '-' + sort_field
            return sort_field

        return dict(_score=dict(order="desc"))

    def get_exact_search_criterion(self):
        search_str = self.get_search_string()

        def get_query(attr):
            return Q('match', **{attr: search_str})

        exact_search_fields = self.get_exact_search_fields()
        criterion = get_query(exact_search_fields.pop())
        for field in exact_search_fields:
            criterion |= get_query(field)

        return criterion

    def get_faceted_criterion(self):
        filters = self.get_faceted_filters()

        def get_query(attr, val):
            vals = val.split(',')
            criteria = Q('match', **{attr: vals.pop()})
            for _val in vals:
                criteria |= Q('match', **{attr: _val})
            return criteria

        if filters:
            first_filter = filters.popitem()
            criterion = get_query(first_filter[0], first_filter[1])
            for field, value in filters.items():
                criterion &= get_query(field, value)

            return criterion

    def get_faceted_filters(self, split=False):
        faceted_filters = dict()
        faceted_fields = self.get_faceted_fields()
        query_params = {to_snake_case(k): v for k, v in self.request.query_params.dict().items()}
        for field in faceted_fields:
            if field in query_params:
                query_value = query_params[field]
                faceted_filters[field] = query_value.split(',') if split else query_value
        return faceted_filters

    def get_faceted_fields(self):
        return [field for field, config in get(self, 'es_fields', dict()).items() if config.get('facet', False)]

    def get_facet_filters_from_kwargs(self):
        kwargs = self.kwargs
        filters = dict()
        if 'user' in kwargs:
            filters['ownerType'] = USER_OBJECT_TYPE
            filters['owner'] = kwargs['user']
        if 'org' in kwargs:
            filters['ownerType'] = ORG_OBJECT_TYPE
            filters['owner'] = kwargs['org']
        if 'source' in kwargs:
            filters['source'] = kwargs['source']
        if 'collection' in kwargs:
            filters['collection'] = kwargs['collection']

        return filters

    def get_facets(self):
        facets = dict()
        if self.should_include_facets() and self.facet_class:
            faceted_filters = {to_camel_case(k): v for k, v in self.get_faceted_filters(True).items()}
            filters = {**self.default_filters, **self.get_facet_filters_from_kwargs(), **faceted_filters}
            searcher = self.facet_class(  # pylint: disable=not-callable
                self.get_search_string(), filters=filters, exact_match=self.is_exact_match_on()
            )
            facets = searcher.execute().facets.to_dict()

        return facets

    def get_search_results(self):
        results = None

        if self.should_perform_es_search():
            results = self.document_model.search()
            for field, value in self.default_filters.items():
                results = results.filter("match", **{field: value})

            faceted_criterion = self.get_faceted_criterion()
            if faceted_criterion:
                results = results.query(faceted_criterion)

            if self.is_exact_match_on():
                results = results.query(self.get_exact_search_criterion())
            else:
                results = results.filter(
                    "query_string", query=self.get_wildcard_search_string(), fields=self.get_searchable_fields()
                )
            results = results[0:ES_RESULTS_MAX_LIMIT]

            sort_field = self.get_sort_attr()
            if sort_field:
                results = results.sort(sort_field)

        return results

    def get_search_results_qs(self):
        queryset = None
        search_results = self.get_search_results()

        if search_results:
            queryset = search_results.to_queryset()

        return queryset

    def should_perform_es_search(self):
        return bool(
            SEARCH_PARAM in self.request.query_params and
            self.document_model and
            self.get_searchable_fields()
        )


class SourceChildCommonBaseView(BaseAPIView):
    lookup_field = None
    model = None
    queryset = None
    params = None
    document_model = None
    es_fields = dict()
    pk_field = 'mnemonic'
    permission_classes = (CanViewParentDictionary, )
    is_searchable = True
    default_filters = dict(is_active=True, is_latest_version=True)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.__set_params()

    def get_filter_params(self):
        if self.params:
            return self.params

        self.__set_params()
        return self.params

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
            self.limit = CSV_DEFAULT_LIMIT if self.params.get('csv') else int(self.params.get(
                LIMIT_PARAM, LIST_DEFAULT_LIMIT
            ))


class SourceChildExtrasBaseView:
    default_qs_sort_attr = '-created_at'

    def get_object(self):
        queryset = self.get_queryset()

        if 'concept_version' in self.kwargs or 'mapping_version' in self.kwargs:
            return queryset.first()

        return queryset.filter(is_latest_version=True).first()

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
        return Response(dict(detail=NOT_FOUND), status=status.HTTP_404_NOT_FOUND)

    def update(self, request, **kwargs):
        key = kwargs.get('extra')
        value = request.data.get(key)
        if not value:
            return Response([MUST_SPECIFY_EXTRA_PARAM_IN_BODY.format(key)], status=status.HTTP_400_BAD_REQUEST)

        new_version = self.get_object().clone()
        new_version.extras[key] = value
        new_version.comment = 'Updated extras: %s=%s.' % (key, value)
        errors = self.model.persist_clone(new_version, request.user)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        return Response({key: value})

    def delete(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        new_version = self.get_object().clone()
        if key in new_version.extras:
            del new_version.extras[key]
            new_version.comment = 'Deleted extra %s.' % key
            errors = self.model.persist_clone(new_version, request.user)
            if errors:
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(dict(detail=NOT_FOUND), status=status.HTTP_404_NOT_FOUND)
