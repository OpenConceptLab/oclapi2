from pydash import compact
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response

from core.common.constants import HEAD
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
