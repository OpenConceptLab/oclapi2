from django.db import DatabaseError
from django.http import Http404, HttpResponse
from pydash import get
from rest_framework import response, generics, status

from core.common.constants import SEARCH_PARAM, ES_RESULTS_MAX_LIMIT, LIST_DEFAULT_LIMIT, CSV_DEFAULT_LIMIT, LIMIT_PARAM
from core.common.mixins import PathWalkerMixin
from core.common.utils import compact_dict_by_values
from core.concepts.permissions import CanViewParentDictionary


def get_object_or_404(queryset, **filter_kwargs):
    try:
        return generics.get_object_or_404(queryset, **filter_kwargs)
    except DatabaseError:
        raise Http404


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

    def head(self, _):
        res = HttpResponse()
        res['num_found'] = self.filter_queryset(self.get_queryset()).count()
        return res

    def filter_queryset(self, queryset):
        if self.is_searchable and self.should_perform_es_search():
            return self.get_search_results_qs().filter(id__in=queryset.values_list('id'))

        return super().filter_queryset(queryset)

    def get_searchable_fields(self):
        return [field for field, config in get(self, 'es_fields', dict()).items() if config.get('filterable', False)]

    def get_search_string(self):
        return self.request.query_params.dict().get(SEARCH_PARAM, '')

    def get_wildcard_search_string(self):
        return "*{}*".format(self.get_search_string())

    def get_search_results(self):
        results = None

        if self.should_perform_es_search():
            results = self.document_model.search()
            for field, value in self.default_filters.items():
                results = results.filter("match", **{field: value})
            results = results.filter(
                "query_string", query=self.get_wildcard_search_string(), fields=self.get_searchable_fields()
            )[0:ES_RESULTS_MAX_LIMIT]

        return results

    def get_search_results_qs(self):
        queryset = None
        search_results = self.get_search_results()

        if search_results:
            queryset = search_results.to_queryset()

        return queryset

    def should_perform_es_search(self):
        return bool(
            self.request.query_params.get(SEARCH_PARAM, None) and
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
    default_filters = {'is_active': True, 'is_latest_version': True}

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
        query_params = self.request.query_params.dict().copy()
        kwargs.update(query_params)
        return compact_dict_by_values(kwargs)

    def __set_params(self):
        self.params = self.__get_params()
        if self.params:
            self.limit = CSV_DEFAULT_LIMIT if self.params.get('csv') else int(self.params.get(
                LIMIT_PARAM, LIST_DEFAULT_LIMIT
            ))
