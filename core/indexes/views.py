from drf_yasg.utils import swagger_auto_schema
from pydash import compact
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.swagger_parameters import apps_param, ids_param, resources_body_param
from core.common.tasks import rebuild_indexes, populate_indexes
from core.common.utils import get_resource_class_from_resource_name


class BaseESIndexView(APIView):  # pragma: no cover
    permission_classes = (IsAdminUser,)
    parser_classes = (MultiPartParser,)
    task = None

    @swagger_auto_schema(manual_parameters=[apps_param])
    def post(self, request):
        apps = request.data.get('apps', None)
        if apps:
            apps = apps.split(',')
        result = self.task.delay(apps)

        return Response(
            dict(state=result.state, username=self.request.user.username, task=result.task_id, queue='default'),
            status=status.HTTP_202_ACCEPTED
        )


class RebuildESIndexView(BaseESIndexView):  # pragma: no cover
    task = rebuild_indexes


class PopulateESIndexView(BaseESIndexView):  # pragma: no cover
    task = populate_indexes


class ResourceIndexView(APIView):
    permission_classes = (IsAdminUser,)
    parser_classes = (MultiPartParser,)

    @swagger_auto_schema(manual_parameters=[ids_param, resources_body_param])
    def post(self, _, resource):
        model = get_resource_class_from_resource_name(resource)

        if not model:
            return Response(status=status.HTTP_404_NOT_FOUND)

        ids = self.request.data.get('ids', None)
        if ids:
            ids = compact([i.strip() for i in compact(ids.split(','))])

        if not ids:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        for instance in model.objects.filter(**{"{}__in".format(model.mnemonic_attr): ids}):
            instance.save()

        return Response(status=status.HTTP_202_ACCEPTED)
