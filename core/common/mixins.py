import logging
from math import ceil
from urllib import parse

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q, F
from django.http import HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404
from django.urls import resolve, Resolver404
from django.utils.functional import cached_property
from pydash import compact, get
from rest_framework import status
from rest_framework.mixins import ListModelMixin, CreateModelMixin
from rest_framework.response import Response

from core.common.constants import HEAD, ACCESS_TYPE_NONE, INCLUDE_FACETS, \
    LIST_DEFAULT_LIMIT, HTTP_COMPRESS_HEADER, CSV_DEFAULT_LIMIT, FACETS_ONLY, INCLUDE_RETIRED_PARAM,\
    SEARCH_STATS_ONLY, INCLUDE_SEARCH_STATS
from core.common.permissions import HasPrivateAccess, HasOwnership, CanViewConceptDictionary, \
    CanViewConceptDictionaryVersion
from .checksums import ChecksumModel
from .utils import write_csv_to_s3, get_csv_from_s3, get_query_params_from_url_string, compact_dict_by_values, \
    to_owner_uri, parse_updated_since_param, get_export_service, to_int

logger = logging.getLogger('oclapi')


class CustomPaginator:
    def __init__(  # pylint: disable=too-many-arguments
            self, request, total_count, queryset, page_size, is_sliced=False, max_score=None, search_scores=None,
            highlights=None
    ):
        self.request = request
        self.queryset = queryset
        self.total = total_count or self.queryset.count()
        self.page_size = int(page_size)
        self.page_number = to_int(request.GET.get('page', '1'), 1)
        if not is_sliced:
            bottom = (self.page_number - 1) * self.page_size
            top = bottom + self.page_size
            if top >= self.total:
                top = self.total
            self.queryset = self.queryset[bottom:top]
        self.queryset.count = None
        self.paginator = Paginator(self.queryset, self.page_size)
        self.page_object = self.paginator.get_page(self.page_number)
        self.page_count = ceil(int(self.total_count) / int(self.page_size))
        self.max_score = max_score
        self.search_scores = search_scores or {}
        self.highlights = highlights or {}

    @property
    def current_page_number(self):
        return self.page_number

    @property
    def current_page_results(self):
        results = self.page_object.object_list
        if self.search_scores or self.max_score or self.highlights:
            for result in results:
                result._score = self.search_scores.get(result.id)  # pylint: disable=protected-access
                result._highlight = self.highlights.get(result.id)  # pylint: disable=protected-access
                if result._score and self.max_score:  # pylint: disable=protected-access
                    result._confidence = f"{round((result._score / self.max_score) * 100, 2)}%"  # pylint: disable=protected-access
        return results

    @cached_property
    def total_count(self):
        return self.total

    def __get_query_params(self):
        return self.request.GET.copy()

    def __get_full_url(self):
        return self.request.build_absolute_uri('?')

    def get_next_page_url(self):
        query_params = self.__get_query_params()
        query_params['page'] = str(self.current_page_number + 1)
        return self.__get_full_url() + '?' + query_params.urlencode()

    def get_current_page_url(self):
        query_params = self.__get_query_params()
        query_params['page'] = str(self.current_page_number)
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
        headers = {
            'num_found': self.total_count,
            'num_returned': len(self.current_page_results),
            'pages': self.page_count,
            'page_number': self.page_number
        }
        if self.has_next():
            headers['next'] = self.get_next_page_url()
        if self.has_previous():
            headers['previous'] = self.get_previous_page_url()

        return headers


class ListWithHeadersMixin(ListModelMixin):
    default_filters = {'is_active': True}
    object_list = None
    _max_score = None
    _scores = None
    _highlights = None
    limit = LIST_DEFAULT_LIMIT
    document_model = None

    def head(self, request, **kwargs):  # pylint: disable=unused-argument
        queryset = self.filter_queryset()
        res = Response()
        res['num_found'] = get(self, 'total_count') or queryset.count()
        return res

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

        if self.only_facets():
            return Response({'facets': {'fields': self.get_facets()}})
        if self.only_search_stats() and search_term:
            return Response(self.get_search_stats(get(self, '_source_versions', []), get(self, '_extra_filters', None)))

        if self.object_list is None:
            self.object_list = self.filter_queryset()

        if is_csv and search_string:
            klass = type(self.object_list[0])
            queryset = klass.objects.filter(id__in=self.get_object_ids())
            return self.get_csv(request, queryset)

        # Skip pagination if compressed results are requested
        compress = self.should_compress()

        sorted_list = self.object_list

        headers = {}
        results = sorted_list
        paginator = None
        if not compress:
            if not self.limit or int(self.limit) == 0 or int(self.limit) > 1000:
                self.limit = LIST_DEFAULT_LIMIT
            paginator = CustomPaginator(
                request=request, queryset=sorted_list, page_size=self.limit, total_count=self.total_count,
                is_sliced=self.should_perform_es_search(), max_score=get(self, '_max_score'),
                search_scores=get(self, '_scores'), highlights=get(self, '_highlights')
            )
            headers = paginator.headers
            results = paginator.current_page_results
        data = self.serialize_list(results, paginator)

        response = Response(data)
        for key, value in headers.items():
            response[key] = value
        if not headers:
            response['num_found'] = len(sorted_list)
        return response

    def serialize_list(self, results, paginator=None):
        result_dict = self.get_serializer(results, many=True).data
        if self.should_include_facets():
            data = {
                'results': result_dict,
                'facets': {'fields': self.get_facets()}
            }
        elif self.should_include_search_stats() and self.should_perform_es_search():
            data = {
                'results': result_dict,
                'search_stats': self.get_search_stats(self._source_versions, self._extra_filters)
            }
        elif hasattr(self.__class__, 'bundle_response'):
            data = self.bundle_response(result_dict, paginator)
        else:
            data = result_dict
        return data

    def should_include_facets(self):
        return self.request.META.get(INCLUDE_FACETS, False) in ['true', True]

    def should_include_search_stats(self):
        return self.request.META.get(INCLUDE_SEARCH_STATS, False) in ['true', True]

    def only_facets(self):
        return self.request.query_params.get(FACETS_ONLY, False) in ['true', True]

    def only_search_stats(self):
        return self.request.query_params.get(SEARCH_STATS_ONLY, False) in ['true', True]

    def should_compress(self):
        return self.request.META.get(HTTP_COMPRESS_HEADER, False) in ['true', True]

    def get_object_ids(self):
        self.object_list.limit_iter = False
        return map(lambda o: o.id, self.object_list[0:100])

    def get_csv(self, request, queryset=None):
        filename, url, prepare_new_file, is_member = None, None, True, False

        parent = None  # TODO: fix this for parent (owner)

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
        return self.get_queryset() if is_member else self.get_queryset()[0:CSV_DEFAULT_LIMIT]


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
        data = request.data.copy()
        supported_locales = data.pop('supported_locales', '')
        if isinstance(supported_locales, str):
            supported_locales = compact(supported_locales.split(','))

        data = {
            'mnemonic': data.get('id'),
            'supported_locales': supported_locales,
            'version': HEAD, **data, **{self.parent_resource.resource_type.lower(): self.parent_resource.id}
        }

        serializer = self.get_serializer(data=data)
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
    def sources(self):
        return self.source_set.filter(version=HEAD)

    @property
    def collections(self):
        return self.collection_set.filter(version=HEAD)

    @property
    def all_sources_count(self):
        return self.sources.count()

    @property
    def all_collections_count(self):
        return self.collections.count()

    @property
    def public_sources(self):
        return self.sources.exclude(public_access=ACCESS_TYPE_NONE).count()

    @property
    def public_collections(self):
        return self.collections.exclude(public_access=ACCESS_TYPE_NONE).count()

    @property
    def sources_url(self):
        return self.uri + 'sources/'

    @property
    def collections_url(self):
        return self.uri + 'collections/'


class SourceChildMixin(ChecksumModel):
    class Meta:
        abstract = True

    def get_all_checksums(self):
        return {
            **super().get_all_checksums(),
            'repo_versions': self.source_versions_checksum,
        }

    def set_source_versions_checksum(self):
        self.set_specific_checksums('repo_versions', self.source_versions_checksum)

    @property
    def source_versions_checksum(self):
        checksums = [version.checksum for version in self.sources.exclude(version=HEAD)]
        return self.generate_checksum(checksums) if checksums else None

    @staticmethod
    def is_strictly_equal(instance1, instance2):
        return instance1.get_checksums() == instance2.get_checksums()

    @staticmethod
    def is_equal(instance1, instance2):
        return instance1.get_basic_checksums() == instance2.get_basic_checksums()

    @staticmethod
    def apply_user_criteria(queryset, user):
        queryset = queryset.exclude(
            Q(parent__user_id__isnull=False, public_access=ACCESS_TYPE_NONE) & ~Q(parent__user_id=user.id))
        queryset = queryset.exclude(
            Q(parent__organization_id__isnull=False, public_access=ACCESS_TYPE_NONE) &
            ~Q(parent__organization__members__id=user.id)
        )
        return queryset

    @staticmethod
    def apply_attribute_based_filters(queryset, params):
        is_latest = params.get('is_latest', None) in [True, 'true']
        include_retired = params.get(INCLUDE_RETIRED_PARAM, None) in [True, 'true']
        updated_since = parse_updated_since_param(params)
        if is_latest:
            queryset = queryset.filter(is_latest_version=True)
        if not include_retired and not params.get('concept', None) and not params.get('mapping', None):
            queryset = queryset.filter(retired=False)
        if updated_since:
            queryset = queryset.filter(updated_at__gte=updated_since)

        return queryset

    @property
    def source_versions(self):
        return self.sources.exclude(version=HEAD).values_list('uri', flat=True)

    @property
    def collection_versions(self):
        return set(self.expansion_set.exclude(
            collection_version__version=HEAD).values_list('collection_version__uri', flat=True))

    @property
    def versions(self):
        return self.__class__.objects.filter(
            versioned_object_id=self.versioned_object_id).exclude(id=F('versioned_object_id'))

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
        return to_owner_uri(self.uri)

    @property
    def parent_resource(self):
        return get(self.parent, 'mnemonic')

    @property
    def parent_url(self):
        return get(self.parent, 'uri')

    def retire(self, user, comment=None):
        if self.versioned_object.retired:
            return {'__all__': self.ALREADY_RETIRED}

        return self.__update_retire(True, comment or self.WAS_RETIRED, user)

    def unretire(self, user, comment=None):
        if not self.versioned_object.retired:
            return {'__all__': self.ALREADY_NOT_RETIRED}

        return self.__update_retire(False, comment or self.WAS_UNRETIRED, user)

    def __update_retire(self, retired, comment, user):
        latest_version = self.get_latest_version() or self.get_last_version()
        new_version = latest_version.clone()
        new_version.retired = retired
        new_version.comment = comment
        return self.__class__.persist_clone(new_version, user)

    @classmethod
    def from_uri_queryset(cls, uri):  # soon to be deleted
        queryset = cls.objects.none()
        from core.collections.utils import is_concept
        is_concept_uri = is_concept(uri)

        try:
            kwargs = get(resolve(uri.split('?')[0]), 'kwargs', {})
            query_params = get_query_params_from_url_string(uri)  # parsing query parameters
            kwargs.update(query_params)
            if 'concept' in kwargs:
                kwargs['concept'] = parse.unquote(kwargs['concept'])
            if 'collection' in kwargs and 'version' in kwargs:
                from core.collections.models import Collection
                collection_version = Collection.get_base_queryset(kwargs).first()
                if collection_version:
                    if 'expansion' in kwargs:
                        expansion = collection_version.expansions.filter(mnemonic=kwargs['expansion']).first()
                    else:
                        expansion = collection_version.expansion
                    if expansion:
                        queryset = expansion.concepts if is_concept_uri else expansion.mappings
            else:
                queryset = cls.get_base_queryset(kwargs)
                if queryset.count() > 1 and \
                        ('concept_version' not in kwargs or 'mapping_version' not in kwargs) and \
                        ('version' not in kwargs):
                    queryset = queryset.filter(is_latest_version=True)
        except:  # pylint: disable=bare-except
            pass

        return queryset

    @classmethod
    def get_parent_and_owner_filters_from_uri(cls, uri):
        filters = {}
        if not uri:
            return filters

        try:
            resolved_uri = resolve(uri)
            kwargs = resolved_uri.kwargs
            filters = cls.get_parent_and_owner_filters_from_kwargs(kwargs)
        except Resolver404:
            pass

        return compact_dict_by_values(filters)

    @staticmethod
    def get_parent_and_owner_filters_from_kwargs(kwargs):
        filters = {}

        if not kwargs:
            return filters

        filters['parent__mnemonic'] = kwargs.get('source')
        if 'org' in kwargs:
            filters['parent__organization__mnemonic'] = kwargs.get('org')
        if 'user' in kwargs:
            filters['parent__user__username'] = kwargs.get('user')

        return filters

    @classmethod
    def get_filter_by_container_criterion(  # pylint: disable=too-many-arguments
            cls, container_prefix, parent, org, user, container_version, is_latest_released, latest_released_version,
            parent_attr=None
    ):
        parent_attr = parent_attr or container_prefix
        criteria = cls.get_exact_or_criteria(f'{parent_attr}__mnemonic', parent)

        if user:
            criteria &= Q(**{f'{parent_attr}__user__username': user})
        if org:
            criteria &= Q(**{f'{parent_attr}__organization__mnemonic': org})
        if is_latest_released:
            criteria &= Q(**{f'{container_prefix}__version': get(latest_released_version, 'version')})
        if container_version and not is_latest_released:
            criteria &= Q(**{f'{container_prefix}__version': container_version})

        return criteria

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        super().save(force_insert, force_update, using, update_fields)

        if self.is_latest_version and self._counted is False:
            if self.__class__.__name__ == 'Concept':
                self.parent.update_concepts_count()
            else:
                self.parent.update_mappings_count()

            self._counted = True
            self.save(update_fields=['_counted'])

    def collection_references_uris(self, collection):
        ids = self.collection_references(collection).values_list('id', flat=True)
        return [f"{collection.uri}references/{_id}/" for _id in ids]

    def collection_references(self, collection):
        return self.references.filter(collection=collection)


class ConceptContainerExportMixin:
    permission_classes = (CanViewConceptDictionaryVersion, )

    def get_object(self):
        queryset = self.get_queryset()
        if 'version' not in self.kwargs:
            queryset = queryset.filter(is_latest_version=True)

        instance = queryset.first()

        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)

        return instance

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        version = self.get_object()
        logger.debug(
            'Export requested for %s version %s - Requesting AWS-S3 key', self.entity.lower(), version.version
        )
        if version.is_head and not request.user.is_staff:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        if version.is_exporting:
            return Response(status=status.HTTP_208_ALREADY_REPORTED)

        if version.has_export():
            export_url = version.get_export_url()

            no_redirect = request.query_params.get('noRedirect', False) in ['true', 'True', True]
            if no_redirect:
                return Response({'url': export_url}, status=status.HTTP_200_OK)

            response = Response(status=status.HTTP_303_SEE_OTHER)
            response['Location'] = export_url

            # Set headers to ensure sure response is not cached by a client
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
            response['Last-Updated'] = version.get_last_child_update_from_export_url(export_url)
            response['Last-Updated-Timezone'] = settings.TIME_ZONE_PLACE
            return response

        return Response(status=status.HTTP_204_NO_CONTENT)

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        version = self.get_object()

        if version.is_head and not request.user.is_staff:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        logger.debug('%s Export requested for version %s (post)', self.entity, version.version)

        if version.is_exporting:
            return Response(status=status.HTTP_208_ALREADY_REPORTED)

        force_export = request.query_params.get('force', False) in ['true', 'True', True]

        if force_export or not version.has_export():
            status_code = self.handle_export_version()
            return Response(status=status_code)

        no_redirect = request.query_params.get('noRedirect', False) in ['true', 'True', True]
        if no_redirect:
            return Response(status=status.HTTP_204_NO_CONTENT)

        response = Response(status=status.HTTP_303_SEE_OTHER)
        response['URL'] = version.uri + 'export/'
        return response

    def delete(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        user = request.user
        version = self.get_object()

        if version.is_head and not user.is_staff:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        permitted = user.is_staff or user.is_superuser or user.is_admin_for(version)

        if not permitted:
            return HttpResponseForbidden()

        if version.has_export():
            get_export_service().remove(version.export_path)
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(status=status.HTTP_404_NOT_FOUND)


class ConceptContainerProcessingMixin:
    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasOwnership(), ]

        return [CanViewConceptDictionary(), ]

    def get_object(self, queryset=None):  # pylint: disable=unused-argument
        return get_object_or_404(self.get_queryset())

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        version = self.get_object()
        is_debug = request.query_params.get('debug', None) in ['true', True]

        if is_debug:
            return Response({'is_processing': version.is_processing, 'process_ids': version._background_process_ids})  # pylint: disable=protected-access

        logger.debug('Processing flag requested for %s version %s', self.resource, version)

        response = Response(status=200)
        response.content = version.is_processing
        return response

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        version = self.get_object()
        logger.debug('Processing flag clearance requested for %s version %s', self.resource, version)

        version.clear_processing()

        return Response(status=status.HTTP_200_OK)
