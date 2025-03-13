from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
from pydash import compact, get
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.swagger_parameters import apps_param, ids_param, resources_body_param, uri_param, filter_param
from core.common.tasks import rebuild_indexes, populate_indexes, batch_index_resources
from core.common.throttling import ThrottleUtil
from core.common.utils import get_resource_class_from_resource_name
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

    @swagger_auto_schema(manual_parameters=[ids_param, uri_param, filter_param, resources_body_param])
    def post(self, _, resource):
        model = get_resource_class_from_resource_name(resource)

        if not model:
            return Response(status=status.HTTP_404_NOT_FOUND)

        ids = self.request.data.get('ids', None)
        uri = self.request.data.get('uri', None)
        _filter = self.request.data.get('filter', None)
        update_indexed = self.request.data.get('update_indexed', False)

        filters = None

        if ids:
            ids = compact([i.strip() for i in compact(ids.split(','))])
            if ids:
                filters = {f"{model.mnemonic_attr}__in": ids}
        elif uri:
            filters = {'uri__icontains': uri}
        elif _filter:
            filters = _filter
        if not filters:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if get(settings, 'TEST_MODE', False):
            batch_index_resources(resource, filters, update_indexed)
        else:
            task = Task.new(queue='indexing', user=self.request.user, name=batch_index_resources.__name__)
            batch_index_resources.apply_async((resource, filters, update_indexed), queue=task.queue, task_id=task.id)

        return Response(status=status.HTTP_202_ACCEPTED)
