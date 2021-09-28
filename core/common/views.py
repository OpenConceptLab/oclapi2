import base64
import urllib.parse
from email.mime.image import MIMEImage

import markdown
import requests
from django.conf import settings
from django.core.mail import EmailMessage
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from elasticsearch import RequestError
from elasticsearch_dsl import Q
from pydash import get
from rest_framework import response, generics, status
from rest_framework.generics import ListAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core import __version__
from core.common.constants import SEARCH_PARAM, LIST_DEFAULT_LIMIT, CSV_DEFAULT_LIMIT, \
    LIMIT_PARAM, NOT_FOUND, MUST_SPECIFY_EXTRA_PARAM_IN_BODY, INCLUDE_RETIRED_PARAM, VERBOSE_PARAM, HEAD, LATEST, \
    BRIEF_PARAM
from core.common.exceptions import Http400
from core.common.mixins import PathWalkerMixin
from core.common.serializers import RootSerializer
from core.common.utils import compact_dict_by_values, to_snake_case, to_camel_case, parse_updated_since_param, \
    is_url_encoded_string
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
    sort_param = 'sort'
    default_qs_sort_attr = '-updated_at'
    exact_match = 'exact_match'
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
        if self.is_owner_document_model():
            return False

        params = get(self, 'params') or self.request.query_params.dict()
        include_retired = params.get('retired', None) in [True, 'true'] or params.get(
            INCLUDE_RETIRED_PARAM, None) in [True, 'true']
        return not include_retired

    def _should_include_private(self):
        return self.is_user_document() or self.request.user.is_staff or self.is_user_scope()

    def is_verbose(self):
        return self.request.query_params.get(VERBOSE_PARAM, False) in ['true', True]

    def is_brief(self):
        return self.request.query_params.get(BRIEF_PARAM, False) in ['true', True]

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

    def filter_queryset(self, queryset):
        if self.is_searchable and self.should_perform_es_search():
            return self.get_search_results_qs()

        return super().filter_queryset(queryset).order_by(self.default_qs_sort_attr)

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

    def is_exact_match_on(self):
        return self.request.query_params.dict().get(self.exact_match, None) == 'on'

    def get_searchable_fields(self):
        return [field for field, config in get(self, 'es_fields', {}).items() if config.get('filterable', False)]

    def get_exact_search_fields(self):
        return [field for field, config in get(self, 'es_fields', {}).items() if config.get('exact', False)]

    def get_search_string(self, lower=True):
        search_str = self.request.query_params.dict().get(SEARCH_PARAM, '').strip()
        if self.is_concept_document():
            search_str = search_str.replace('-', '_')
        if lower:
            search_str = search_str.lower()
            search_str = search_str if is_url_encoded_string(search_str) else urllib.parse.quote_plus(search_str)

        return search_str

    def get_wildcard_search_string(self, _str):
        return f"*{_str or self.get_search_string()}*"

    @staticmethod
    def __get_order_by(is_desc):
        return dict(order='desc') if is_desc else dict(order='asc')

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
                    current_result = dict(_score=order_details)
                if self.is_concept_document() and field == 'name':
                    current_result = dict(_name=order_details)
                if self.is_valid_sort(field):
                    current_result = {field: order_details}
                if current_result is not None:
                    result.append(current_result)

        return result

    def get_exact_search_criterion(self):
        search_str = self.get_search_string(False)

        def get_query(attr):
            words = search_str.split(' ')
            criteria = Q('match', **{attr: words[0]})
            for word in words[1:]:
                criteria &= Q('match', **{attr: word})
            return criteria

        exact_search_fields = self.get_exact_search_fields()
        criterion = get_query(exact_search_fields.pop())
        for field in exact_search_fields:
            criterion |= get_query(field)

        return criterion

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

    def get_kwargs_filters(self):
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
            if is_version_specified:
                container_version = self.kwargs['version']
                is_latest_released = container_version == LATEST
                if is_latest_released:
                    params = dict(user__username=self.kwargs.get('user'), organization__mnemonic=self.kwargs.get('org'))
                    if is_source_specified:
                        from core.sources.models import Source
                        latest_released_version = Source.find_latest_released_version_by(
                            {**params, 'mnemonic': self.kwargs['source']})
                        filters['source_version'] = get(latest_released_version, 'version')
                    elif is_collection_specified:
                        from core.collections.models import Collection
                        latest_released_version = Collection.find_latest_released_version_by(
                            {**params, 'mnemonic': self.kwargs['collection']})
                        filters['collection_version'] = get(latest_released_version, 'version')

            if is_collection_specified:
                owner_type = filters.pop('ownerType', None)
                owner = filters.pop('owner', None)
                if owner_type == USER_OBJECT_TYPE:
                    filters['collection_owner_url'] = f"/users/{owner}/"
                if owner_type == ORG_OBJECT_TYPE:
                    filters['collection_owner_url'] = f"/orgs/{owner}/"
                if not is_version_specified:
                    filters['collection_version'] = HEAD
            if is_source_specified and not is_version_specified:
                filters['source_version'] = HEAD
        return filters

    def get_facets(self):
        facets = {}

        if self.facet_class:
            if self.is_user_document():
                return facets
            is_source_child_document_model = self.is_source_child_document_model()
            default_filters = self.default_filters.copy()

            if is_source_child_document_model and 'collection' not in self.kwargs and 'version' not in self.kwargs:
                default_filters['is_latest_version'] = True

            faceted_filters = {to_camel_case(k): v for k, v in self.get_faceted_filters(True).items()}
            filters = {**default_filters, **self.get_facet_filters_from_kwargs(), **faceted_filters, 'retired': False}
            if not self._should_exclude_retired_from_search_results() or not is_source_child_document_model:
                filters.pop('retired')

            is_exact_match_on = self.is_exact_match_on()
            facets = self.facet_class(  # pylint: disable=not-callable
                self.get_search_string(lower=not is_exact_match_on), filters=filters, exact_match=is_exact_match_on
            ).execute().facets.to_dict()

        return facets

    def get_extras_searchable_fields_from_query_params(self):
        query_params = self.request.query_params.dict()

        result = {}

        for key, value in query_params.items():
            if key.startswith('extras.') and not key.startswith('extras.exists') and not key.startswith('extras.exact'):
                parts = key.split('extras.')
                result['extras.' + parts[1].replace('.', '__')] = value

        return result

    def get_extras_exact_fields_from_query_params(self):
        query_params = self.request.query_params.dict()
        result = {}
        for key, value in query_params.items():
            if key.startswith('extras.exact'):
                new_key = key.replace('.exact', '')
                parts = new_key.split('extras.')
                result['extras.' + parts[1].replace('.', '__')] = value

        return result

    def get_extras_fields_exists_from_query_params(self):
        extras_exists_fields = self.request.query_params.dict().get('extras.exists', None)

        if extras_exists_fields:
            return [field.replace('.', '__') for field in extras_exists_fields.split(',')]

        return []

    def is_user_document(self):
        from core.users.documents import UserProfileDocument
        return self.document_model == UserProfileDocument

    def is_concept_document(self):
        from core.concepts.documents import ConceptDocument
        return self.document_model == ConceptDocument

    def is_owner_document_model(self):
        from core.orgs.documents import OrganizationDocument
        from core.users.documents import UserProfileDocument
        return self.document_model in [UserProfileDocument, OrganizationDocument]

    def is_source_child_document_model(self):
        from core.concepts.documents import ConceptDocument
        from core.mappings.documents import MappingDocument
        return self.document_model in [ConceptDocument, MappingDocument]

    def is_concept_container_document_model(self):
        from core.collections.documents import CollectionDocument
        from core.sources.documents import SourceDocument
        return self.document_model in [SourceDocument, CollectionDocument]

    def is_user_scope(self):
        org = self.kwargs.get('org', None)
        user = self.kwargs.get('user', None)

        request_user = self.request.user

        if request_user.is_authenticated:
            if user:
                return user == request_user.username
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

    def __should_query_latest_version(self):
        kwargs = {**self.get_faceted_filters(), **self.kwargs}
        collection = kwargs.get('collection', '')
        version = kwargs.get('version', '')

        return (not collection or collection.startswith('!')) and (not version or version.startswith('!'))

    @property
    def __search_results(self):  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
        results = None

        if self.should_perform_es_search():
            results = self.document_model.search()
            default_filters = self.default_filters.copy()
            if self.is_source_child_document_model() and self.__should_query_latest_version():
                default_filters['is_latest_version'] = True

            for field, value in default_filters.items():
                results = results.query("match", **{field: value})

            faceted_criterion = self.get_faceted_criterion()
            extras_fields = self.get_extras_searchable_fields_from_query_params()
            extras_fields_exact = self.get_extras_exact_fields_from_query_params()
            extras_fields_exists = self.get_extras_fields_exists_from_query_params()

            if faceted_criterion:
                results = results.query(faceted_criterion)

            if self.is_exact_match_on():
                results = results.query(self.get_exact_search_criterion())
            else:
                results = results.query(self.get_wildcard_search_criterion() | self.get_exact_search_criterion())

            updated_since = parse_updated_since_param(self.request.query_params)
            if updated_since:
                results = results.query('range', last_update={"gte": updated_since})

            if extras_fields:
                for field, value in extras_fields.items():
                    value = value.replace('/', '\\/')
                    results = results.filter("query_string", query=value, fields=[field])
            if extras_fields_exists:
                for field in extras_fields_exists:
                    results = results.query("exists", field=f"extras.{field}")
            if extras_fields_exact:
                for field, value in extras_fields_exact.items():
                    value = value.replace('/', '\\/')
                    results = results.query("match", **{field: value}, _expand__to_dot=False)

            if self._should_exclude_retired_from_search_results():
                results = results.query('match', retired=False)

            user = self.request.user
            is_authenticated = user.is_authenticated
            username = user.username

            include_private = self._should_include_private()
            if not include_private:
                results = results.query(self.get_public_criteria())

            if self.is_owner_document_model():
                kwargs_filters = self.kwargs.copy()
                if self.user_is_self and is_authenticated:
                    kwargs_filters.pop('user_is_self', None)
                    kwargs_filters['user'] = username
            else:
                kwargs_filters = self.get_kwargs_filters()
                if self.user_is_self and is_authenticated:
                    kwargs_filters['ownerType'] = 'User'
                    kwargs_filters['owner'] = username

            for key, value in kwargs_filters.items():
                results = results.query('match', **{to_snake_case(key): value})

            sort_by = self.get_sort_attributes()
            if sort_by:
                results = results.sort(*sort_by)
            else:
                results = results.sort(dict(_score=dict(order="desc")))

        return results

    def get_wildcard_search_criterion(self):
        search_string = self.get_search_string()
        name_attr = 'name'
        if self.is_concept_document():
            name_attr = '_name'

        def get_query(_str):
            return Q(
                "wildcard", id=dict(value=_str, boost=2)
            ) | Q(
                "wildcard", **{name_attr: dict(value=_str, boost=5)}
            ) | Q(
                "query_string", query=self.get_wildcard_search_string(_str)
            )

        if not search_string:
            return get_query(search_string)
        words = search_string.split()
        criterion = get_query(words[0])
        for word in words[1:]:
            criterion |= get_query(word)

        return criterion

    def get_search_results_qs(self):
        if not self.should_perform_es_search():
            return None

        search_results = self.__search_results
        self.total_count = search_results.count()

        if isinstance(self.limit, str):
            self.limit = int(self.limit)

        self.limit = self.limit or LIST_DEFAULT_LIMIT

        page = int(self.request.GET.get('page', '1'))
        start = (page - 1) * self.limit
        end = start + self.limit
        try:
            return search_results[start:end].to_queryset()
        except RequestError as ex:
            if get(ex, 'info.error.caused_by.reason', '').startswith('Result window is too large'):
                raise Http400(detail='Only 10000 results are available. Please apply additional filters'
                                     ' or fine tune your query to get more accurate results.') from ex
            raise ex

    def is_head(self):
        return self.request.method.lower() == 'head'

    def should_perform_es_search(self):
        return bool(self.get_search_string()) or self.has_searchable_extras_fields() or bool(self.get_faceted_filters())

    def has_searchable_extras_fields(self):
        return bool(
            self.get_extras_searchable_fields_from_query_params()
        ) or bool(
            self.get_extras_fields_exists_from_query_params()
        ) or bool(
            self.get_extras_exact_fields_from_query_params()
        )


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
    default_filters = dict(is_active=True)

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
            instance = queryset.first()
        else:
            instance = queryset.filter(is_latest_version=True).first()

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
        return Response(dict(detail=NOT_FOUND), status=status.HTTP_404_NOT_FOUND)

    def update(self, request, **kwargs):  # pylint: disable=arguments-differ
        key = kwargs.get('extra')
        value = request.data.get(key)
        if not value:
            return Response([MUST_SPECIFY_EXTRA_PARAM_IN_BODY.format(key)], status=status.HTTP_400_BAD_REQUEST)

        new_version = self.get_object().clone()
        new_version.extras[key] = value
        new_version.comment = f'Updated extras: {key}={value}.'
        errors = self.model.persist_clone(new_version, request.user)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        return Response({key: value})

    def delete(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        new_version = self.get_object().clone()
        if key in new_version.extras:
            del new_version.extras[key]
            new_version.comment = f'Deleted extra {key}.'
            errors = self.model.persist_clone(new_version, request.user)
            if errors:
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(dict(detail=NOT_FOUND), status=status.HTTP_404_NOT_FOUND)


class APIVersionView(APIView):
    permission_classes = (AllowAny,)
    swagger_schema = None

    @staticmethod
    def get(_):
        return Response(__version__)


class ChangeLogView(APIView):
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
        data = dict(version=__version__, routes={})
        for pattern in urlpatterns:
            name = getattr(pattern, 'name', None) or getattr(pattern, 'app_name', None)
            if name in ['admin']:
                continue
            route = str(pattern.pattern)
            if route in ['v1-importers/']:
                continue
            if isinstance(route, str) and route.startswith('admin/'):
                continue
            if route and name is None:
                name = route.split('/', maxsplit=1)[0] + '_urls'
                if name == 'user_urls':
                    name = 'current_user_urls'
            data['routes'][name] = self.get_host_url() + '/' + route

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


class ConceptDuplicateLocalesView(APIView):  # pragma: no cover
    permission_classes = (IsAdminUser,)

    @staticmethod
    def get(request):
        from core.common.tasks import delete_duplicate_locales
        delete_duplicate_locales.delay(int(request.query_params.get('start', 0)))
        return Response(status=status.HTTP_200_OK)


class ConceptDormantLocalesView(APIView):  # pragma: no cover
    permission_classes = (IsAdminUser, )

    @staticmethod
    def get(_, **kwargs):  # pylint: disable=unused-argument
        from core.concepts.models import LocalizedText
        count = LocalizedText.dormants()
        return Response(count, status=status.HTTP_200_OK)

    @staticmethod
    def delete(_, **kwargs):  # pylint: disable=unused-argument
        from core.common.tasks import delete_dormant_locales
        delete_dormant_locales.delay()
        return Response(status=status.HTTP_204_NO_CONTENT)
