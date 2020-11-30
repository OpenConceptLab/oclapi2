import logging
from math import ceil

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q, F
from django.http import HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404
from django.urls import resolve, reverse, Resolver404
from django.utils.functional import cached_property
from pydash import compact, get
from rest_framework import status
from rest_framework.mixins import ListModelMixin, CreateModelMixin
from rest_framework.response import Response

from core.common.constants import HEAD, ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW, ACCESS_TYPE_NONE, INCLUDE_FACETS
from core.common.permissions import HasPrivateAccess, HasOwnership, CanViewConceptDictionary
from core.common.services import S3
from .utils import write_csv_to_s3, get_csv_from_s3, get_query_params_from_url_string, compact_dict_by_values

logger = logging.getLogger('oclapi')


class CustomPaginator:
    def __init__(self, request, total_count, queryset, page_size):
        self.total = total_count
        self.request = request
        self.queryset = queryset
        self.page_size = page_size
        self.page_number = int(request.GET.get('page', '1'))
        self.paginator = Paginator(self.queryset, self.page_size)
        self.page_object = self.paginator.get_page(self.page_number)
        self.page_count = ceil(int(self.total_count) / int(self.page_size))

    @property
    def current_page_number(self):
        return self.page_number

    @property
    def current_page_results(self):
        return self.page_object.object_list

    @cached_property
    def total_count(self):
        return get(self, 'total') or self.queryset.count()

    def __get_query_params(self):
        return self.request.GET.copy()

    def __get_full_url(self):
        return self.request.build_absolute_uri('?')

    def get_next_page_url(self):
        query_params = self.__get_query_params()
        query_params['page'] = str(self.current_page_number + 1)
        return self.__get_full_url() + '?' + query_params.urlencode()

    def get_previous_page_url(self):
        query_params = self.__get_query_params()
        query_params['page'] = str(self.current_page_number - 1)
        return self.__get_full_url() + '?' + query_params.urlencode()

    def has_next(self):
        return self.page_number < self.page_count

    def has_previous(self):
        return self.page_number > 1

    @property
    def headers(self):
        headers = dict(
            num_found=self.total_count, num_returned=len(self.current_page_results),
            pages=self.page_count, page_number=self.page_number
        )
        if self.has_next():
            headers['next'] = self.get_next_page_url()
        if self.has_previous():
            headers['previous'] = self.get_previous_page_url()

        return headers


class ListWithHeadersMixin(ListModelMixin):
    default_filters = {'is_active': True}
    object_list = None

    def list(self, request, *args, **kwargs):  # pylint:disable=too-many-locals
        query_params = request.query_params.dict()
        is_csv = query_params.get('csv', False)
        search_string = query_params.get('type', None)
        exact_match = query_params.get('exact_match', None)
        search_term = query_params.get('q', None)
        if not exact_match and is_csv:
            pattern = search_term
            if pattern:
                query_params._mutable = True  # pylint: disable=protected-access
                query_params['q'] = "*" + search_term + "*"

        if is_csv and not search_string:
            return self.get_csv(request)

        if self.object_list is None:
            self.object_list = self.filter_queryset(self.get_queryset())

        if is_csv and search_string:
            klass = type(self.object_list[0])
            queryset = klass.objects.filter(id__in=self.get_object_ids())
            return self.get_csv(request, queryset)

        # Skip pagination if compressed results are requested
        meta = request._request.META  # pylint: disable=protected-access

        compress = meta.get('HTTP_COMPRESS', False)
        return_all = not self.limit or int(self.limit) == 0
        skip_pagination = compress or return_all

        sorted_list = self.object_list

        headers = dict()
        results = sorted_list
        if not skip_pagination:
            paginator = CustomPaginator(
                request=request, queryset=sorted_list, page_size=self.limit, total_count=self.total_count
            )
            headers = paginator.headers
            results = paginator.current_page_results

        result_dict = self.get_serializer(results, many=True).data
        if self.should_include_facets():
            data = dict(results=result_dict, facets=dict(fields=self.get_facets()))
        else:
            data = result_dict

        response = Response(data)
        for key, value in headers.items():
            response[key] = value
        if not headers:
            response['num_found'] = len(sorted_list)
        return response

    def should_include_facets(self):
        return self.request.META.get(INCLUDE_FACETS, False)

    def get_object_ids(self):
        self.object_list.limit_iter = False
        return map(lambda o: o.id, self.object_list[0:100])

    def get_csv(self, request, queryset=None):
        filename, url, prepare_new_file, is_member = None, None, True, False

        parent = self.get_parent()

        if parent:
            prepare_new_file = False
            user = request.query_params.get('user', None)
            is_member = self._is_member(parent, user)

        try:
            path = request.__dict__.get('_request').path
            filename = '_'.join(compact(path.split('/'))).replace('.', '_')
            kwargs = {
                'filename': filename,
            }
        except Exception:  # pylint: disable=broad-except
            kwargs = {}

        if filename and prepare_new_file:
            url = get_csv_from_s3(filename, is_member)

        if not url:
            queryset = queryset or self._get_query_set_from_view(is_member)
            data = self.get_csv_rows(queryset) if hasattr(self, 'get_csv_rows') else queryset.values()
            url = write_csv_to_s3(data, is_member, **kwargs)

        return Response({'url': url}, status=200)

    @staticmethod
    def _is_member(parent, requesting_user):
        if not parent or type(parent).__name__ in ['UserProfile', 'Organization']:
            return False

        owner = parent.owner
        return owner.members.filter(username=requesting_user).exists() if type(owner).__name__ == 'Organization' else \
            requesting_user == parent.created_by

    def _get_query_set_from_view(self, is_member):
        return self.get_queryset() if is_member else self.get_queryset()[0:100]

    def get_parent(self):
        return get(self, 'parent_resource') or get(self, 'versioned_object') or get(self, 'head')


class PathWalkerMixin:
    """
    A Mixin with methods that help resolve a resource path to a resource object
    """
    path_info = None

    @staticmethod
    def get_parent_in_path(path_info, levels=1):
        last_index = len(path_info) - 1
        last_slash = path_info.rindex('/')
        if last_slash == last_index:
            last_slash = path_info.rindex('/', 0, last_index)
        path_info = path_info[0:last_slash+1]
        if levels > 1:
            i = 1
            while i < levels:
                last_index = len(path_info) - 1
                last_slash = path_info.rindex('/', 0, last_index)
                path_info = path_info[0:last_slash+1]
                i += 1
        return path_info

    @staticmethod
    def get_object_for_path(path_info, request):
        callback, _, callback_kwargs = resolve(path_info)
        view = callback.cls(request=request, kwargs=callback_kwargs)
        view.initialize(request, path_info, **callback_kwargs)
        return view.get_object()


class SubResourceMixin(PathWalkerMixin):
    """
    Base view for a sub-resource.
    Includes a post-initialize step that determines the parent resource,
    and a get_queryset method that applies the appropriate permissions and filtering.
    """
    user = None
    userprofile = None
    user_is_self = False
    parent_path_info = None
    parent_resource = None
    base_or_clause = []

    def initialize(self, request, path_info_segment):
        self.user = request.user
        self.userprofile = self.user
        self.parent_resource = self.userprofile
        if self.user_is_self:
            self.userprofile = self.user
            self.parent_resource = self.userprofile
        else:
            levels = self.get_level()
            self.parent_path_info = self.get_parent_in_path(path_info_segment, levels=levels)
            self.parent_resource = None
            if self.parent_path_info and self.parent_path_info != '/':
                self.parent_resource = self.get_object_for_path(self.parent_path_info, request)

    def get_level(self):
        levels = 1 if isinstance(self, (ListModelMixin, CreateModelMixin)) else 2
        return levels


class ConceptDictionaryMixin(SubResourceMixin):
    base_or_clause = [Q(public_access=ACCESS_TYPE_EDIT), Q(public_access=ACCESS_TYPE_VIEW)]
    permission_classes = (HasPrivateAccess,)


class ConceptDictionaryCreateMixin(ConceptDictionaryMixin):
    """
    Concrete view for creating a model instance.
    """
    def post(self, request, **kwargs):
        self.set_parent_resource()
        return self.create(request, **kwargs)

    def set_parent_resource(self):
        from core.orgs.models import Organization
        from core.users.models import UserProfile
        org = self.kwargs.get('org', None)
        user = self.kwargs.get('user', None)
        if not user and self.user_is_self:
            user = self.request.user.username
        parent_resource = None
        if org:
            parent_resource = Organization.objects.filter(mnemonic=org).first()
        if user:
            parent_resource = UserProfile.objects.filter(username=user).first()

        self.kwargs['parent_resource'] = self.parent_resource = parent_resource

    def create(self, request, **kwargs):  # pylint: disable=unused-argument
        if not self.parent_resource:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        permission = HasOwnership()
        if not permission.has_object_permission(request, self, self.parent_resource):
            return Response(status=status.HTTP_403_FORBIDDEN)
        supported_locales = request.data.pop('supported_locales', '')
        if isinstance(supported_locales, str):
            supported_locales = compact(supported_locales.split(','))

        serializer = self.get_serializer(
            data={
                'mnemonic': request.data.get('id'),
                'supported_locales': supported_locales,
                'version': HEAD, **request.data, **{self.parent_resource.resource_type.lower(): self.parent_resource.id}
            }
        )
        if serializer.is_valid():
            instance = serializer.save(force_insert=True)
            if serializer.is_valid():
                headers = self.get_success_headers(serializer.data)
                serializer = self.get_detail_serializer(instance)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def get_success_headers(data):
        try:
            return {'Location': data['url']}
        except (TypeError, KeyError):
            return {}


class ConceptDictionaryUpdateMixin(ConceptDictionaryMixin):

    """
    Concrete view for updating a model instance.
    """
    def put(self, request, **kwargs):  # pylint: disable=unused-argument
        super().initialize(request, request.path_info)
        return self.update(request)

    def update(self, request):
        if not self.parent_resource:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        self.object = self.get_object()
        save_kwargs = {'force_update': True, 'parent_resource': self.parent_resource}
        success_status_code = status.HTTP_200_OK

        supported_locales = request.data.pop('supported_locales', '')
        if isinstance(supported_locales, str):
            supported_locales = compact(supported_locales.split(','))

        request.data['supported_locales'] = supported_locales
        serializer = self.get_serializer(self.object, data=request.data, partial=True)

        if serializer.is_valid():
            self.object = serializer.save(**save_kwargs)
            if serializer.is_valid():
                serializer = self.get_detail_serializer(self.object)
                return Response(serializer.data, status=success_status_code)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SourceContainerMixin:
    @property
    def public_sources(self):
        return self.source_set.exclude(public_access=ACCESS_TYPE_NONE).filter(version=HEAD).count()

    @property
    def public_collections(self):
        return self.collection_set.exclude(public_access=ACCESS_TYPE_NONE).filter(version=HEAD).count()

    @property
    def sources_url(self):
        return reverse('source-list', kwargs={self.get_url_kwarg(): self.mnemonic})

    @property
    def collections_url(self):
        return reverse('collection-list', kwargs={self.get_url_kwarg(): self.mnemonic})


class SourceChildMixin:
    @property
    def versions(self):
        if self.is_versioned_object:
            self.versions_set.exclude(id=F('versioned_object_id')).all()
        return self.versioned_object.versions_set.exclude(id=F('versioned_object_id')).all()

    @property
    def is_versioned_object(self):
        return self.id == self.versioned_object_id

    @property
    def version_url(self):
        if self.is_versioned_object:
            return self.get_latest_version().uri
        return self.uri

    @property
    def head(self):
        return self.versioned_object

    @property
    def is_head(self):
        return self.is_versioned_object

    @property
    def owner(self):
        return get(self, 'parent.parent')

    @property
    def owner_name(self):
        return str(self.owner or '')

    @property
    def owner_type(self):
        return get(self.owner, 'resource_type')

    @property
    def owner_url(self):
        return get(self.owner, 'url')

    @property
    def parent_resource(self):
        return get(self.parent, 'mnemonic')

    def retire(self, user, comment=None):
        if self.versioned_object.retired:
            return {'__all__': self.ALREADY_RETIRED}

        return self.__update_retire(True, comment or self.WAS_RETIRED, user)

    def unretire(self, user, comment=None):
        if not self.versioned_object.retired:
            return {'__all__': self.ALREADY_NOT_RETIRED}

        return self.__update_retire(False, comment or self.WAS_UNRETIRED, user)

    def __update_retire(self, retired, comment, user):
        latest_version = self.get_latest_version()
        new_version = latest_version.clone()
        new_version.retired = retired
        new_version.comment = comment
        return self.__class__.persist_clone(new_version, user)

    @classmethod
    def from_uri_queryset(cls, uri):
        queryset = cls.objects.none()

        try:
            kwargs = get(resolve(uri), 'kwargs', dict())
            query_params = get_query_params_from_url_string(uri)  # parsing query parameters
            kwargs.update(query_params)
            queryset = cls.get_base_queryset(kwargs)
            if queryset.count() > 1 and \
                    ('concept_version' not in kwargs or 'mapping_version' not in kwargs) and \
                    ('concept' in kwargs or 'mapping' in kwargs):
                queryset = queryset.filter(is_latest_version=True)
        except:  # pylint: disable=bare-except
            pass

        return queryset

    @classmethod
    def global_listing_queryset(cls, params, user):
        queryset = cls.get_base_queryset(params).filter(is_latest_version=True, is_active=True)
        if not user.is_staff:
            queryset = queryset.exclude(public_access=ACCESS_TYPE_NONE)
        return queryset

    @staticmethod
    def get_parent_and_owner_filters_from_uri(uri):
        filters = dict()
        if not uri:
            return filters

        try:
            resolved_uri = resolve(uri)
            kwargs = resolved_uri.kwargs
            filters['parent__mnemonic'] = kwargs.get('source')
            if 'org' in kwargs:
                filters['parent__organization__mnemonic'] = kwargs.get('org')
            if 'user' in kwargs:
                filters['parent__user__username'] = kwargs.get('user')
        except Resolver404:
            pass

        return compact_dict_by_values(filters)


class ConceptContainerExportMixin:
    def get_object(self):
        queryset = self.get_queryset()
        if 'version' not in self.kwargs:
            queryset = queryset.filter(is_latest_version=True)

        instance = queryset.first()

        if not instance:
            raise Http404()

        return instance

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        version = self.get_object()
        logger.debug(
            'Export requested for %s version %s - Requesting AWS-S3 key', self.entity.lower(), version.version
        )
        if version.is_head:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        key = version.export_path
        url = S3.url_for(key)

        if url:
            logger.debug('   URL and Key retrieved for %s version %s', self.entity.lower(), version.version)
        else:
            logger.debug('   Key does not exist for %s version %s', self.entity.lower(), version.version)
            return Response(status=status.HTTP_204_NO_CONTENT)

        response = Response(status=status.HTTP_303_SEE_OTHER)
        response['Location'] = url

        # Set headers to ensure sure response is not cached by a client
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        response['Last-Updated'] = version.last_child_update.isoformat()
        response['Last-Updated-Timezone'] = settings.TIME_ZONE_PLACE
        return response

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        version = self.get_object()

        if version.is_head:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        logger.debug('%s Export requested for version %s (post)', self.entity, version.version)
        status_code = status.HTTP_303_SEE_OTHER

        if not S3.exists(version.export_path):
            status_code = self.handle_export_version()
            return Response(status=status_code)

        response = Response(status=status_code)
        response['URL'] = version.uri + 'export/'
        return response

    def delete(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        user = request.user
        version = self.get_object()

        permitted = user.is_staff or user.is_superuser or user.is_admin_for(version.head)

        if not permitted:
            return HttpResponseForbidden()
        if version.has_export():
            S3.remove(version.export_path)
            return Response(status=status.HTTP_200_OK)

        return Response(status=status.HTTP_204_NO_CONTENT)


class ConceptContainerProcessingMixin:
    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasOwnership(), ]

        return [CanViewConceptDictionary(), ]

    def get_object(self, queryset=None):  # pylint: disable=unused-argument
        return get_object_or_404(self.get_queryset())

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        version = self.get_object()
        logger.debug('Processing flag requested for %s version %s', self.resource, version)

        response = Response(status=200)
        response.content = version.is_processing
        return response

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        version = self.get_object()
        logger.debug('Processing flag clearance requested for %s version %s', self.resource, version)

        version.clear_processing()

        return Response(status=status.HTTP_200_OK)
