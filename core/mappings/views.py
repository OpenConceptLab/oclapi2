from django.db.models import F
from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema
from pydash import get
from rest_framework import status
from rest_framework.generics import DestroyAPIView, UpdateAPIView, RetrieveAPIView, ListAPIView, \
    RetrieveUpdateDestroyAPIView
from rest_framework.mixins import CreateModelMixin
from rest_framework.response import Response

from core.common.constants import HEAD
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryMixin
from core.common.swagger_parameters import (
    q_param, limit_param, sort_desc_param, page_param, exact_match_param, sort_asc_param, verbose_param,
    include_facets_header, updated_since_param, include_retired_param
)
from core.common.views import SourceChildCommonBaseView
from core.concepts.permissions import CanEditParentDictionary, CanViewParentDictionary
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.search import MappingSearch
from core.mappings.serializers import MappingDetailSerializer, MappingListSerializer


class MappingBaseView(SourceChildCommonBaseView):
    lookup_field = 'mapping'
    model = Mapping
    queryset = Mapping.objects.filter(is_active=True)
    document_model = MappingDocument
    facet_class = MappingSearch
    es_fields = {
        'last_update': {'sortable': True, 'filterable': False, 'facet': False, 'default': 'desc'},
        'concept': {'sortable': False, 'filterable': True, 'facet': False, 'exact': True},
        'from_concept': {'sortable': False, 'filterable': True, 'facet': False, 'exact': True},
        'to_concept': {'sortable': False, 'filterable': True, 'facet': False, 'exact': True},
        'retired': {'sortable': False, 'filterable': True, 'facet': True},
        'map_type': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'source': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'collection': {'sortable': False, 'filterable': True, 'facet': True},
        'owner': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'concept_source': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'from_concept_source': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'to_concept_source': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'concept_owner': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'from_concept_owner': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'to_concept_owner': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'concept_owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'from_concept_owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'to_concept_owner_type': {'sortable': False, 'filterable': True, 'facet': True},
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
        if 'org' in self.kwargs:
            filters = dict(organization__mnemonic=self.kwargs['org'])
        else:
            filters = dict(user__username=self.kwargs['user'])
        if source:
            parent_resource = Source.get_version(source, source_version, filters)
        self.kwargs['parent_resource'] = self.parent_resource = parent_resource

    def post(self, request, **kwargs):  # pylint: disable=unused-argument
        self.set_parent_resource()
        serializer = self.get_serializer(data={
            **request.data.dict(), 'parent_id': self.parent_resource.id
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
    permission_classes = (CanViewParentDictionary,)

    def get_queryset(self):
        return super().get_queryset().exclude(id=F('versioned_object_id'))

    def get_serializer_class(self):
        return MappingDetailSerializer if self.is_verbose(self.request) else MappingListSerializer

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class MappingVersionRetrieveView(MappingBaseView, RetrieveAPIView):
    serializer_class = MappingDetailSerializer
    permission_classes = (CanViewParentDictionary,)

    def get_object(self, queryset=None):
        return get_object_or_404(self.get_queryset())


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

    @swagger_auto_schema(
        manual_parameters=[
            q_param, limit_param, sort_desc_param, sort_asc_param, exact_match_param, page_param, verbose_param,
            include_retired_param, updated_since_param,
            include_facets_header
        ]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class MappingExtrasBaseView(MappingBaseView):
    default_qs_sort_attr = '-created_at'

    def get_object(self, queryset=None):
        return self.get_queryset().filter(is_latest_version=True).first()


class MappingExtrasView(MappingExtrasBaseView, ListAPIView):
    permission_classes = (CanViewParentDictionary,)
    serializer_class = MappingDetailSerializer

    def list(self, request, *args, **kwargs):
        return Response(get(self.get_object(), 'extras', {}))


class MappingExtraRetrieveUpdateDestroyView(MappingExtrasBaseView, RetrieveUpdateDestroyAPIView):
    serializer_class = MappingDetailSerializer

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewParentDictionary()]

        return [CanEditParentDictionary()]

    def retrieve(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        instance = self.get_object()
        extras = get(instance, 'extras', {})
        if key in extras:
            return Response({key: extras[key]})
        return Response(dict(detail='Not found.'), status=status.HTTP_404_NOT_FOUND)

    def update(self, request, **kwargs):
        key = kwargs.get('extra')
        value = request.data.get(key)
        if not value:
            return Response(
                ['Must specify %s param in body.' % key],
                status=status.HTTP_400_BAD_REQUEST
            )

        new_version = self.get_object().clone()
        new_version.extras[key] = value
        new_version.comment = 'Updated extras: %s=%s.' % (key, value)
        errors = Mapping.persist_clone(new_version, request.user)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        return Response({key: value})

    def delete(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        new_version = self.get_object().clone()
        if key in new_version.extras:
            del new_version.extras[key]
            new_version.comment = 'Deleted extra %s.' % key
            errors = Mapping.persist_clone(new_version, request.user)
            if errors:
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(dict(detail='Not found.'), status=status.HTTP_404_NOT_FOUND)
