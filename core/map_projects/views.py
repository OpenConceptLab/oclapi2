from django.conf import settings
from rest_framework import status
from rest_framework.generics import RetrieveUpdateDestroyAPIView, RetrieveAPIView, CreateAPIView
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryCreateMixin
from core.common.permissions import CanViewConceptDictionary, HasOwnership
from core.common.utils import get_truthy_values
from core.common.views import BaseAPIView
from core.map_projects.models import MapProject
from core.map_projects.serializers import MapProjectSerializer, MapProjectCreateUpdateSerializer, \
    MapProjectDetailSerializer, MapProjectSummarySerializer, MapProjectLogsSerializer


class MapProjectBaseView(BaseAPIView):
    is_searchable = False
    queryset = MapProject.objects.filter(is_active=True)
    permission_classes = (CanViewConceptDictionary,)
    serializer_class = MapProjectSerializer


class MapProjectListView(MapProjectBaseView, ConceptDictionaryCreateMixin, ListWithHeadersMixin):
    def get_serializer_class(self):
        if self.request.method == 'POST':
            return MapProjectCreateUpdateSerializer
        if self.is_verbose():
            return MapProjectDetailSerializer

        return self.serializer_class

    def get_queryset(self):
        return self.filter_queryset_by_public_access(self.filter_queryset_by_owner(self.queryset))

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def create(self, request, **kwargs):  # pylint: disable=unused-argument
        if not self.parent_resource:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        permission = HasOwnership()
        if not permission.has_object_permission(request, self, self.parent_resource):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = self.get_serializer(data=MapProject.format_request_data(request.data, self.parent_resource))
        if serializer.is_valid():
            instance = serializer.save(force_insert=True)
            if serializer.is_valid():
                headers = self.get_success_headers(serializer.data)
                serializer = self.get_serializer(instance)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MapProjectView(MapProjectBaseView, RetrieveUpdateDestroyAPIView):
    serializer_class = MapProjectDetailSerializer
    lookup_url_kwarg = 'project'
    lookup_field = 'project'
    pk_field = 'id'

    def get_serializer_class(self):
        if self.request.method == 'PUT':
            return MapProjectCreateUpdateSerializer
        return self.serializer_class


class MapProjectRecommendView(MapProjectBaseView):  # pragma: no cover
    serializer_class = MapProjectDetailSerializer
    lookup_url_kwarg = 'project'
    lookup_field = 'project'
    pk_field = 'id'
    permission_classes = (IsAdminUser,)
    swagger_schema = None

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        params = self.request.query_params
        map_project = self.get_object()
        candidates = request.data.get('candidates') or []
        row = request.data.get('row') or {}
        target_repo_url = request.data.get('target_repo_url') or map_project.target_repo_url

        if not candidates or not isinstance(candidates, list) or not row or not isinstance(row, dict):
            return Response(
                {'detail': 'candidates (list) and row (dict) are required.'}, status=status.HTTP_400_BAD_REQUEST
            )
        if not target_repo_url:
            return Response(
                {'detail': 'target_repo_url is required either in the request body or the map project.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        from core.services.litellm import LiteLLMService
        if not settings.ENV or settings.ENV in ['ci', 'development', 'test']:
            return Response(LiteLLMService.mock_response)

        try:
            litellm = LiteLLMService()
            map_project.target_repo_url = target_repo_url
            response = litellm.recommend(
                map_project, row, candidates, params.get('conceptFilterDefault') in get_truthy_values()
            )
            return Response(litellm.to_dict(response), status=status.HTTP_200_OK)
        except Exception as ex:
            return Response({'detail': str(ex)}, status=status.HTTP_400_BAD_REQUEST)


class MapProjectSummaryView(MapProjectBaseView, RetrieveAPIView):
    serializer_class = MapProjectSummarySerializer
    lookup_url_kwarg = 'project'
    lookup_field = 'project'
    pk_field = 'id'


class MapProjectLogsView(MapProjectBaseView, RetrieveAPIView, CreateAPIView):
    serializer_class = MapProjectLogsSerializer
    lookup_url_kwarg = 'project'
    lookup_field = 'project'
    pk_field = 'id'

    def create(self, request, *args, **kwargs):
        map_project = self.get_object()
        new_logs = request.data.get('logs') or {}
        if new_logs:
            map_project.logs = new_logs
            map_project.updated_by = request.user
            map_project.save()
        return Response(status.HTTP_204_NO_CONTENT)
