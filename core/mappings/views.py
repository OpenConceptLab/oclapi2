from django.db.models import F
from django.http import QueryDict, Http404
from drf_yasg.utils import swagger_auto_schema
from pydash import get
from rest_framework import status
from rest_framework.generics import DestroyAPIView, UpdateAPIView, RetrieveAPIView
from rest_framework.mixins import CreateModelMixin
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.common.constants import HEAD, ACCESS_TYPE_NONE
from core.common.exceptions import Http400
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryMixin
from core.common.swagger_parameters import (
    q_param, limit_param, sort_desc_param, page_param, sort_asc_param, verbose_param,
    include_facets_header, updated_since_param, include_retired_param,
    compress_header, include_source_versions_param, include_collection_versions_param, search_from_latest_repo_header)
from core.common.views import SourceChildCommonBaseView, SourceChildExtrasView, \
    SourceChildExtraRetrieveUpdateDestroyView
from core.concepts.permissions import CanEditParentDictionary, CanViewParentDictionary
from core.mappings.constants import PARENT_VERSION_NOT_LATEST_CANNOT_UPDATE_MAPPING
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.search import MappingFacetedSearch
from core.mappings.serializers import MappingDetailSerializer, MappingListSerializer, MappingVersionListSerializer, \
    MappingVersionDetailSerializer, MappingMinimalSerializer


class MappingBaseView(SourceChildCommonBaseView):
    lookup_field = 'mapping'
    model = Mapping
    queryset = Mapping.objects.filter(is_active=True)
    document_model = MappingDocument
    facet_class = MappingFacetedSearch
    es_fields = Mapping.es_fields

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
        method = self.request.method
        is_get = method == 'GET'
        if is_get and self.is_brief():
            return MappingMinimalSerializer

        if (is_get and self.is_verbose()) or method == 'POST':
            return MappingDetailSerializer

        return MappingListSerializer

    def apply_filters(self, queryset):
        return queryset

    def get_queryset(self):
        is_latest_version = 'collection' not in self.kwargs and (
                'version' not in self.kwargs or get(self.kwargs, 'version') == HEAD
        )
        parent = get(self, 'parent_resource')
        if parent:
            queryset = parent.mappings_set if parent.is_head else parent.mappings
            queryset = Mapping.apply_attribute_based_filters(queryset, self.params).filter(is_active=True)
        else:
            queryset = super().get_queryset()

        if is_latest_version:
            queryset = queryset.filter(id=F('versioned_object_id'))

        queryset = self.apply_filters(queryset)

        if not parent:
            user = self.request.user
            if get(user, 'is_anonymous'):
                queryset = queryset.exclude(public_access=ACCESS_TYPE_NONE)
            elif not get(user, 'is_staff'):
                queryset = Mapping.apply_user_criteria(queryset, user)

        return queryset

    @swagger_auto_schema(
        manual_parameters=[
            q_param, limit_param, sort_desc_param, sort_asc_param, page_param, verbose_param,
            include_retired_param, updated_since_param,
            include_facets_header, compress_header, search_from_latest_repo_header
        ]
    )
    def get(self, request, *args, **kwargs):
        self.set_parent_resource(False)
        if self.parent_resource:
            self.check_object_permissions(request, self.parent_resource)
        return self.list(request, *args, **kwargs)

    def set_parent_resource(self, __pop=True):
        from core.sources.models import Source
        source = self.kwargs.pop('source', None) if __pop else self.kwargs.get('source', None)
        collection = self.kwargs.pop('collection', None) if __pop else self.kwargs.get('collection', None)
        container_version = self.kwargs.pop('version', HEAD) if __pop else self.kwargs.get('version', HEAD)
        parent_resource = None
        if 'org' in self.kwargs:
            filters = {'organization__mnemonic': self.kwargs['org']}
        else:
            username = self.request.user.username if self.user_is_self else self.kwargs.get('user')
            filters = {'user__username': username}
        if source:
            parent_resource = Source.get_version(source, container_version or HEAD, filters)
        if collection:
            from core.collections.models import Collection
            parent_resource = Collection.get_version(source, container_version or HEAD, filters)
        self.kwargs['parent_resource'] = self.parent_resource = parent_resource

    def post(self, request, **kwargs):  # pylint: disable=unused-argument
        self.set_parent_resource()
        if not self.parent_resource:
            raise Http404()
        data = request.data.dict() if isinstance(request.data, QueryDict) else request.data
        if isinstance(data, list):
            raise Http400()
        serializer = self.get_serializer(data={
            **data, 'parent_id': self.parent_resource.id
        })
        if serializer.is_valid():
            self.object = serializer.save()
            if serializer.is_valid():
                headers = self.get_success_headers(serializer.data)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MappingRetrieveUpdateDestroyView(MappingBaseView, RetrieveAPIView, UpdateAPIView, DestroyAPIView):
    serializer_class = MappingDetailSerializer

    def is_container_version_specified(self):
        return 'version' in self.kwargs

    def get_object(self, queryset=None):
        queryset = self.get_queryset()
        if not self.is_container_version_specified():
            queryset = queryset.filter(id=F('versioned_object_id'))
        instance = queryset.first()
        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)

        instance.get_checksums()
        for version in instance.versions:
            version.get_checksums()

        return instance

    def get_permissions(self):
        if self.request.method in ['GET']:
            return [CanViewParentDictionary(), ]

        if self.request.method == 'DELETE' and self.is_hard_delete_requested():
            return [IsAdminUser(), ]

        return [CanEditParentDictionary(), ]

    def update(self, request, *args, **kwargs):
        if self.is_container_version_specified():
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        partial = kwargs.pop('partial', True)
        self.object = self.get_object()
        self.parent_resource = self.object.parent

        if not self.parent_resource.is_head:
            return Response(
                {'non_field_errors': PARENT_VERSION_NOT_LATEST_CANNOT_UPDATE_MAPPING},
                status=status.HTTP_400_BAD_REQUEST
            )
        self.object = self.object.clone()
        serializer = self.get_serializer(self.object, data=request.data, partial=partial)
        success_status_code = status.HTTP_200_OK

        if serializer.is_valid():
            self.object = serializer.save()
            if serializer.is_valid():
                return Response(serializer.data, status=success_status_code)

        if Mapping.is_standard_checksum_error(serializer.errors):
            return Response(serializer.errors, status=status.HTTP_208_ALREADY_REPORTED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        if self.is_container_version_specified():
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        mapping = self.get_object()
        parent = mapping.parent
        comment = request.data.get('update_comment', None) or request.data.get('comment', None)
        if self.is_hard_delete_requested():
            mapping.delete()
            parent.update_mappings_count()
            return Response(status=status.HTTP_204_NO_CONTENT)

        errors = mapping.retire(request.user, comment)

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        parent.update_mappings_count()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MappingCollectionMembershipView(MappingBaseView, ListWithHeadersMixin):
    def get_serializer_class(self):
        from core.collections.serializers import CollectionVersionListSerializer
        return CollectionVersionListSerializer

    def get_object(self, queryset=None):
        queryset = Mapping.get_base_queryset(self.params)
        if 'mapping_version' not in self.kwargs:
            queryset = queryset.filter(id=F('versioned_object_id'))
        instance = queryset.first()

        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)

        return instance

    def get_queryset(self):
        instance = self.get_object()

        from core.collections.models import Collection
        return Collection.objects.filter(id__in=instance.expansion_set.filter(
            collection_version__organization_id=instance.parent.organization_id,
            collection_version__user_id=instance.parent.user_id
        ).values_list('collection_version_id', flat=True))

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class MappingReactivateView(MappingBaseView, UpdateAPIView):
    serializer_class = MappingDetailSerializer
    permission_classes = (CanEditParentDictionary, )

    def get_object(self, queryset=None):
        instance = self.get_queryset().filter(id=F('versioned_object_id')).first()
        if not instance:
            raise Http404()
        self.check_object_permissions(self.request, instance)
        return instance

    def update(self, request, *args, **kwargs):
        mapping = self.get_object()
        comment = request.data.get('update_comment', None) or request.data.get('comment', None)
        errors = mapping.unretire(request.user, comment)

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        mapping.parent.update_mappings_count()

        return Response(status=status.HTTP_204_NO_CONTENT)


class MappingVersionsView(MappingBaseView, ConceptDictionaryMixin, ListWithHeadersMixin):
    permission_classes = (CanViewParentDictionary,)

    def get_queryset(self):
        instance = super().get_queryset().filter(id=F('versioned_object_id')).first()
        if not instance:
            raise Http404()
        self.check_object_permissions(self.request, instance)

        return instance.versions

    def get_serializer_class(self):
        return MappingVersionDetailSerializer if self.is_verbose() else MappingVersionListSerializer

    @swagger_auto_schema(
        manual_parameters=[
            include_source_versions_param, include_collection_versions_param
        ]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class MappingVersionRetrieveView(MappingBaseView, RetrieveAPIView, DestroyAPIView):
    serializer_class = MappingVersionDetailSerializer

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsAdminUser()]

        return [CanViewParentDictionary(), ]

    def get_object(self, queryset=None):
        instance = self.get_queryset().first()
        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)
        return instance

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if self.is_hard_delete_requested():
            obj.delete()
        else:
            obj.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MappingExtrasView(SourceChildExtrasView, MappingBaseView):
    serializer_class = MappingVersionDetailSerializer


class MappingExtraRetrieveUpdateDestroyView(SourceChildExtraRetrieveUpdateDestroyView, MappingBaseView):
    serializer_class = MappingVersionDetailSerializer
    model = Mapping
