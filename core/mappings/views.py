from django.db.models import F
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import DestroyAPIView, UpdateAPIView, RetrieveAPIView
from rest_framework.mixins import CreateModelMixin
from rest_framework.response import Response

from core.common.constants import HEAD
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryMixin
from core.common.views import SourceChildCommonBaseView
from core.concepts.permissions import CanEditParentDictionary, CanViewParentDictionary
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.serializers import MappingDetailSerializer, MappingListSerializer


class MappingBaseView(SourceChildCommonBaseView):
    lookup_field = 'mapping'
    model = Mapping
    queryset = Mapping.objects.filter(is_active=True)
    document_model = MappingDocument
    es_fields = {
        'lastUpdate': {'sortable': True, 'filterable': False, 'facet': False},
        'concept': {'sortable': False, 'filterable': True, 'facet': False},
        'fromConcept': {'sortable': False, 'filterable': True, 'facet': False},
        'toConcept': {'sortable': False, 'filterable': True, 'facet': False},
        'retired': {'sortable': False, 'filterable': True, 'facet': True},
        'mapType': {'sortable': False, 'filterable': True, 'facet': True},
        'source': {'sortable': False, 'filterable': True, 'facet': True},
        'collection': {'sortable': False, 'filterable': True, 'facet': True},
        'owner': {'sortable': False, 'filterable': True, 'facet': True},
        'ownerType': {'sortable': False, 'filterable': True, 'facet': True},
        'conceptSource': {'sortable': False, 'filterable': True, 'facet': True},
        'fromConceptSource': {'sortable': False, 'filterable': True, 'facet': True},
        'toConceptSource': {'sortable': False, 'filterable': True, 'facet': True},
        'conceptOwner': {'sortable': False, 'filterable': True, 'facet': True},
        'fromConceptOwner': {'sortable': False, 'filterable': True, 'facet': True},
        'toConceptOwner': {'sortable': False, 'filterable': True, 'facet': True},
        'conceptOwnerType': {'sortable': False, 'filterable': True, 'facet': True},
        'fromConceptOwnerType': {'sortable': False, 'filterable': True, 'facet': True},
        'toConceptOwnerType': {'sortable': False, 'filterable': True, 'facet': True},
    }

    @staticmethod
    def get_detail_serializer(obj, data=None, files=None, partial=False):
        return MappingDetailSerializer(obj, data, files, partial)

    def get_queryset(self):
        return Mapping.get_base_queryset(self.params)


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
            'parent__organization', 'parent__user', 'from_concept__parent', 'to_concept__parent', 'to_source',
            'versioned_object',
        )

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

    def post(self, request, **kwargs):  # pylint: disable=unused-argument
        self.set_parent_resource()
        serializer = self.get_serializer(data={
            **request.data, 'parent_id': self.parent_resource.id
        })
        if serializer.is_valid():
            self.object = serializer.save()
            if serializer.is_valid():
                headers = self.get_success_headers(serializer.data)
                serializer = MappingDetailSerializer(self.object)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MappingRetrieveUpdateDestroyView(MappingBaseView, RetrieveAPIView, UpdateAPIView, DestroyAPIView):
    serializer_class = MappingDetailSerializer

    def get_object(self, queryset=None):
        return get_object_or_404(self.get_queryset(), is_latest_version=True)

    def get_permissions(self):
        if self.request.method in ['GET']:
            return [CanViewParentDictionary(), ]

        return [CanEditParentDictionary(), ]

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        partial = kwargs.pop('partial', True)
        if self.object is None:
            return Response(
                {'non_field_errors': 'Could not find mapping to update'}, status=status.HTTP_404_NOT_FOUND
            )

        self.parent_resource = self.object.parent

        if self.parent_resource != self.parent_resource.head:
            return Response(
                {'non_field_errors': 'Parent version is not the latest. Cannot update mapping.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        self.object = self.object.clone()
        serializer = self.get_serializer(self.object, data=request.data, partial=partial)
        success_status_code = status.HTTP_200_OK

        if serializer.is_valid():
            self.object = serializer.save()
            if serializer.is_valid():
                serializer = MappingDetailSerializer(self.object, context={'request': request})
                return Response(serializer.data, status=success_status_code)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        mapping = self.get_object()
        if not mapping:
            return Response(
                dict(non_field_errors='Could not find mapping to retire'),
                status=status.HTTP_404_NOT_FOUND
            )
        comment = request.data.get('update_comment', None) or request.data.get('comment', None)
        errors = mapping.retire(request.user, comment)

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class MappingVersionsView(MappingBaseView, ConceptDictionaryMixin, ListWithHeadersMixin):
    serializer_class = MappingListSerializer
    permission_classes = (CanViewParentDictionary,)

    def get_queryset(self):
        return super().get_queryset().exclude(id=F('versioned_object_id'))

    def get(self, request, *args, **kwargs):
        self.serializer_class = MappingDetailSerializer if self.is_verbose(request) else MappingListSerializer
        return self.list(request, *args, **kwargs)


class MappingVersionRetrieveView(MappingBaseView, RetrieveAPIView):
    serializer_class = MappingDetailSerializer
    permission_classes = (CanViewParentDictionary,)

    def get_object(self, queryset=None):
        return self.get_queryset().first()


class MappingVersionListAllView(MappingBaseView, ListWithHeadersMixin):
    permission_classes = (CanViewParentDictionary,)

    def get_serializer_class(self):
        if self.is_verbose(self.request):
            return MappingDetailSerializer

        return MappingListSerializer

    def get_queryset(self):
        return Mapping.global_listing_queryset(
            self.get_filter_params(), self.request.user
        ).select_related(
            'parent__organization', 'parent__user',
        )

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)
