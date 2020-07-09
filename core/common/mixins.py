from django.db.models import Q
from django.urls import resolve, reverse
from pydash import compact
from rest_framework import status
from rest_framework.mixins import ListModelMixin, CreateModelMixin
from rest_framework.response import Response

from core.common.constants import HEAD, ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW, ACCESS_TYPE_NONE
from core.common.permissions import HasPrivateAccess, HasOwnership
from .utils import write_csv_to_s3, get_csv_from_s3


class ListWithHeadersMixin(ListModelMixin):
    verbose_param = 'verbose'
    default_filters = {'is_active': True}
    object_list = None

    def is_verbose(self, request):
        return request.query_params.get(self.verbose_param, False)

    def list(self, request, *args, **kwargs):  # pylint:disable=too-many-locals
        is_csv = request.query_params.get('csv', False)
        search_string = request.query_params.get('type', None)
        exact_match = request.query_params.get('exact_match', None)
        if not exact_match and is_csv:
            pattern = request.query_params.get('q', None)
            if pattern:
                request.query_params._mutable = True  # pylint: disable=protected-access
                request.query_params['q'] = "*" + request.query_params['q'] + "*"

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
        return_all = False  # self.get_paginate_by() == 0
        skip_pagination = compress or return_all

        # Switch between paginated or standard style responses
        sorted_list = self.prepend_head(self.object_list) if len(self.object_list) > 0 else self.object_list

        if not skip_pagination:
            page = self.paginate_queryset(sorted_list)
            if page is not None:
                serializer = self.get_pagination_serializer(page)
                results = serializer.data
                return Response(results, headers=serializer.headers)

        return Response(self.get_serializer(sorted_list, many=True).data)

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
        if hasattr(self, 'parent_resource'):
            parent = self.parent_resource
        elif hasattr(self, 'versioned_object'):
            parent = self.versioned_object
        else:
            parent = None

        return parent

    @staticmethod
    def prepend_head(objects):
        if len(objects) > 0 and hasattr(objects[0], 'mnemonic'):
            head_el = [el for el in objects if hasattr(el, 'mnemonic') and el.mnemonic == HEAD]
            if head_el:
                objects = head_el + [el for el in objects if el.mnemonic != HEAD]

        return objects

    @staticmethod
    def _reduce_func(prev, current):
        prev_version_ids = map(lambda v: v.versioned_object_id, prev)
        if current.versioned_object_id not in prev_version_ids:
            prev.append(current)
        return prev


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
        serializer = self.get_serializer(
            data={
                'mnemonic': request.data.get('id'),
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
    def sources_url(self):
        return reverse('source-list', kwargs={self.get_url_kwarg(): self.mnemonic})
