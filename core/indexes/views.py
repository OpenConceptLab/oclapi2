import json

from django.conf import settings
from django.core.exceptions import FieldError, ValidationError
from drf_yasg.utils import swagger_auto_schema
from pydash import compact, get
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.swagger_parameters import apps_param, ids_param, resources_body_param, uri_param, filter_param, \
    update_indexed_param
from core.common.tasks import rebuild_indexes, populate_indexes, batch_index_resources
from core.common.throttling import ThrottleUtil
from core.common.utils import get_resource_class_from_resource_name, get_truthy_values
from core.tasks.models import Task


class BaseESIndexView(APIView):  # pragma: no cover
    permission_classes = (IsAdminUser,)
    parser_classes = (MultiPartParser,)
    task = None

    def get_throttles(self):
        return ThrottleUtil.get_throttles_by_user_plan(self.request.user)

    @swagger_auto_schema(manual_parameters=[apps_param])
    def post(self, request):
        apps = request.data.get('apps', None)
        if apps:
            apps = apps.split(',')
        task = Task.new(queue='indexing', user=request.user, name=self.task.__name__)
        result = self.task.apply_async((apps,), queue=task.queue, task_id=task.id)

        return Response(
            {
                'state': result.state,
                'username': self.request.user.username,
                'task': result.task_id,
                'queue': 'default'
            },
            status=status.HTTP_202_ACCEPTED
        )


class RebuildESIndexView(BaseESIndexView):  # pragma: no cover
    task = rebuild_indexes


class PopulateESIndexView(BaseESIndexView):  # pragma: no cover
    task = populate_indexes


class ResourceIndexView(APIView):
    permission_classes = (IsAdminUser,)
    parser_classes = (MultiPartParser,)

    def get_throttles(self):
        return ThrottleUtil.get_throttles_by_user_plan(self.request.user)

    @swagger_auto_schema(
        operation_description='Indexes the selected rows of one resource type. Send exactly one of ids, uri or '
                              'filter to select the rows; any other combination is a 400.',
        manual_parameters=[ids_param, uri_param, filter_param, update_indexed_param, resources_body_param]
    )
    def post(self, _, resource):
        model = get_resource_class_from_resource_name(resource)

        if not model:
            return Response(status=status.HTTP_404_NOT_FOUND)

        filters, error = self.get_filters(model)
        if error:
            return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)

        update_indexed = self.request.data.get('update_indexed') in get_truthy_values()

        if get(settings, 'TEST_MODE', False):
            batch_index_resources(resource, filters, update_indexed)
        else:
            task = Task.new(queue='indexing', user=self.request.user, name=batch_index_resources.__name__)
            batch_index_resources.apply_async((resource, filters, update_indexed), queue=task.queue, task_id=task.id)

        return Response(status=status.HTTP_202_ACCEPTED)

    def get_filters(self, model):
        """
        Returns (filters, error) for the one selector sent: ids (internal ids, never mnemonics, which repeat across
        repositories), uri (rows whose URI contains it, ignoring case) or filter (a JSON object of field lookups).
        A filter is checked here, not in the task, so an empty one is a 400 instead of a whole-table reindex.
        """
        data = self.request.data
        ids = compact([i.strip() for i in (data.get('ids') or '').split(',')])
        uri = (data.get('uri') or '').strip()
        _filter = (data.get('filter') or '').strip()

        if len(compact([ids, uri, _filter])) != 1:
            return None, 'Send exactly one of ids, uri or filter.'

        if ids:
            try:
                filters = {'id__in': [int(i) for i in ids]}
            except ValueError:
                return None, 'ids takes internal ids, not mnemonics.'
        elif uri:
            filters = {'uri__icontains': uri}
        else:
            try:
                filters = json.loads(_filter)
            except ValueError:
                filters = None
            if not filters or not isinstance(filters, dict):
                return None, 'filter must be a non-empty JSON object.'
            try:
                model.objects.filter(**filters)
            except (FieldError, ValidationError, ValueError, TypeError) as ex:
                return None, f'Invalid filter: {ex}'

        return filters, None
