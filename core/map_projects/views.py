
from rest_framework import status
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.response import Response

from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryCreateMixin
from core.common.permissions import CanViewConceptDictionary, HasOwnership
from core.common.views import BaseAPIView
from core.map_projects.models import MapProject
from core.map_projects.serializers import MapProjectSerializer, MapProjectCreateUpdateSerializer, \
    MapProjectDetailSerializer


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
