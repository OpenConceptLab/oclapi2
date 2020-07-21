from rest_framework.mixins import CreateModelMixin
from rest_framework.views import APIView

from core.common.constants import HEAD
from core.common.mixins import ListWithHeadersMixin
from core.common.utils import compact_dict_by_values
from core.common.views import BaseAPIView
from core.concepts.permissions import CanEditParentDictionary, CanViewParentDictionary
from core.mappings.models import Mapping
from core.mappings.serializers import MappingDetailSerializer, MappingListSerializer


class MappingBaseView(BaseAPIView):
    lookup_field = 'mapping'
    pk_field = 'mnemonic'
    model = Mapping
    permission_classes = (CanViewParentDictionary,)
    queryset = Mapping.objects.filter(is_active=True)

    @staticmethod
    def get_detail_serializer(obj, data=None, files=None, partial=False):
        return MappingDetailSerializer(obj, data, files, partial)

    def get_filter_params(self):
        kwargs = self.kwargs.copy()
        query_params = self.request.query_params.copy()
        query_params.update(kwargs)

        return compact_dict_by_values(query_params.update(kwargs))

    def get_queryset(self):
        return Mapping.get_base_queryset(self.get_filter_params())


class MappingListView(MappingBaseView, ListWithHeadersMixin, CreateModelMixin):
    serializer_class = MappingListSerializer

    def get_permissions(self):
        if self.request.method == 'POST':
            return [CanEditParentDictionary(), ]

        return [CanViewParentDictionary(), ]

    def get_serializer_class(self):
        if (self.request.method == 'GET' and self.is_verbose(self.request)) or self.request.method == 'POST':
            return MappingDetailSerializer

        return MappingListSerializer

    def get_queryset(self):
        is_latest_version = 'collection' not in self.kwargs
        queryset = super().get_queryset()
        if is_latest_version:
            queryset = queryset.filter(is_latest_version=True)
        return queryset.select_related(
            'parent__organization', 'parent__user',
        ).prefetch_related('names')

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def set_parent_resource(self):
        from core.sources.models import Source
        source = self.kwargs.pop('source', None)
        source_version = self.kwargs.pop('version', HEAD)
        parent_resource = None
        if source:
            parent_resource = Source.get_version(source, source_version)
        self.kwargs['parent_resource'] = self.parent_resource = parent_resource


class MappingRetrieveUpdateDestroyView(APIView):
    def get(self, request, **kwargs):
        pass
