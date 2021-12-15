import logging

from celery_once import AlreadyQueued
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema
from pydash import get
from rest_framework import status
from rest_framework.generics import (
    RetrieveAPIView, ListAPIView, UpdateAPIView, CreateAPIView)
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.client_configs.views import ResourceClientConfigsView
from core.common.constants import HEAD, RELEASED_PARAM, PROCESSING_PARAM, ACCESS_TYPE_NONE
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryCreateMixin, ConceptDictionaryUpdateMixin, \
    ConceptContainerExportMixin, ConceptContainerProcessingMixin, ConceptContainerExtraRetrieveUpdateDestroyView
from core.common.permissions import CanViewConceptDictionary, CanEditConceptDictionary, HasAccessToVersionedObject, \
    CanViewConceptDictionaryVersion
from core.common.serializers import TaskSerializer
from core.common.swagger_parameters import q_param, limit_param, sort_desc_param, sort_asc_param, exact_match_param, \
    page_param, verbose_param, include_retired_param, updated_since_param, include_facets_header, compress_header
from core.common.tasks import export_source, delete_source, index_source_concepts, index_source_mappings
from core.common.utils import parse_boolean_query_param, compact_dict_by_values
from core.common.views import BaseAPIView, BaseLogoView
from core.sources.constants import DELETE_FAILURE, DELETE_SUCCESS, VERSION_ALREADY_EXISTS
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.search import SourceSearch
from core.sources.serializers import (
    SourceDetailSerializer, SourceListSerializer, SourceCreateSerializer, SourceVersionDetailSerializer,
    SourceVersionListSerializer, SourceVersionExportSerializer, SourceSummaryDetailSerializer,
    SourceVersionSummaryDetailSerializer)

logger = logging.getLogger('oclapi')


class SourceBaseView(BaseAPIView):
    lookup_field = 'source'
    pk_field = 'mnemonic'
    model = Source
    permission_classes = (CanViewConceptDictionary,)
    queryset = Source.objects.filter(is_active=True)

    def verify_scope(self):
        has_owner_scope = self.has_owner_scope()
        has_no_kwargs = self.has_no_kwargs()
        if not self.user_is_self:
            if has_no_kwargs:
                if self.request.method not in ['GET', 'HEAD']:
                    raise Http404()
            elif not has_owner_scope:
                raise Http404()

    @staticmethod
    def get_detail_serializer(obj):
        return SourceDetailSerializer(obj)

    def get_filter_params(self, default_version_to_head=True):
        query_params = self.request.query_params.dict()
        version = query_params.get('version', None) or self.kwargs.get('version', None)
        if not version and default_version_to_head:
            version = HEAD

        kwargs = self.kwargs.copy()
        if self.user_is_self and self.request.user.is_authenticated:
            kwargs['user'] = self.request.user.username

        return {**query_params.copy(), **kwargs, 'version': version}

    def get_queryset(self):
        queryset = Source.get_base_queryset(
            compact_dict_by_values(self.get_filter_params())
        ).select_related(
            'user', 'organization'
        )

        if self.is_verbose():
            queryset = queryset.select_related('created_by', 'updated_by')

        return queryset


class SourceVersionBaseView(SourceBaseView):
    def get_filter_params(self, default_version_to_head=False):
        return super().get_filter_params(default_version_to_head)


class SourceListView(SourceBaseView, ConceptDictionaryCreateMixin, ListWithHeadersMixin):
    serializer_class = SourceListSerializer
    is_searchable = True
    es_fields = Source.es_fields
    document_model = SourceDocument
    facet_class = SourceSearch
    default_filters = dict(is_active=True, version=HEAD)

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if get(user, 'is_staff'):
            return queryset
        if get(user, 'is_anonymous'):
            return queryset.exclude(public_access=ACCESS_TYPE_NONE)

        public_queryset = queryset.exclude(public_access=ACCESS_TYPE_NONE)
        private_queryset = queryset.filter(public_access=ACCESS_TYPE_NONE)
        private_queryset = private_queryset.filter(Q(user_id=user.id) | Q(organization__members__id=user.id))
        return public_queryset.union(private_queryset)

    def get_serializer_class(self):
        if self.request.method == 'GET' and self.is_verbose():
            return SourceDetailSerializer
        if self.request.method == 'POST':
            return SourceCreateSerializer

        return SourceListSerializer

    @swagger_auto_schema(
        manual_parameters=[
            q_param, limit_param, sort_desc_param, sort_asc_param, exact_match_param, page_param, verbose_param,
            include_retired_param, updated_since_param, include_facets_header, compress_header
        ]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def get_csv_rows(self, queryset=None):  # pragma: no cover
        if not queryset:
            queryset = self.get_queryset()

        values = queryset.values('mnemonic', 'name', 'full_name', 'source_type', 'description', 'default_locale',
                                 'supported_locales', 'website', 'external_id', 'updated_at', 'updated_by', 'uri')

        for value in values:
            value['Owner'] = Source.objects.get(uri=value['uri']).parent.mnemonic
            value['Source ID'] = value.pop('mnemonic')
            value['Source Name'] = value.pop('name')
            value['Source Full Name'] = value.pop('full_name')
            value['Source Type'] = value.pop('source_type')
            value['Description'] = value.pop('description')
            value['Default Locale'] = value.pop('default_locale')
            value['Supported Locales'] = ",".join(value.pop('supported_locales'))
            value['Website'] = value.pop('website')
            value['External ID'] = value.pop('external_id')
            value['Last Updated'] = value.pop('updated_at')
            value['Updated By'] = value.pop('updated_by')
            value['URI'] = value.pop('uri')

        values.field_names.extend([
            'Owner', 'Source ID', 'Source Name', 'Source Full Name', 'Source Type', 'Description', 'Default Locale',
            'Supported Locales', 'Website', 'External ID', 'Last Updated', 'Updated By', 'URI'
        ])
        del values.field_names[0:12]
        return values


class SourceLogoView(SourceBaseView, BaseLogoView):
    serializer_class = SourceDetailSerializer

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [CanEditConceptDictionary()]


class SourceRetrieveUpdateDestroyView(SourceBaseView, ConceptDictionaryUpdateMixin, RetrieveAPIView):
    serializer_class = SourceDetailSerializer

    def get_object(self, queryset=None):
        instance = self.get_queryset().filter(is_active=True).order_by('-created_at').first()
        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)
        if not get(settings, 'TEST_MODE', False):
            if instance.active_concepts == 0:
                instance.update_concepts_count()
            if instance.active_mappings == 0:
                instance.update_mappings_count()
            for version in instance.versions.exclude(id=instance.id):
                version.update_children_counts()
        return instance

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [CanEditConceptDictionary()]

    def is_async_requested(self):
        return self.request.query_params.get('async', None) in ['true', True, 'True']

    def delete(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        source = self.get_object()

        if self.is_async_requested():
            task = delete_source.delay(source.id)
            return Response(dict(task=task.id), status=status.HTTP_202_ACCEPTED)

        result = delete_source(source.id)

        if result is True:
            return Response({'detail': DELETE_SUCCESS}, status=status.HTTP_204_NO_CONTENT)

        return Response({'detail': get(result, 'messages', [DELETE_FAILURE])}, status=status.HTTP_400_BAD_REQUEST)


class SourceVersionListView(SourceVersionBaseView, CreateAPIView, ListWithHeadersMixin):
    released_filter = None
    processing_filter = None
    default_qs_sort_attr = '-created_at'

    def get_serializer_class(self):
        if self.request.method in ['GET', 'HEAD'] and self.is_verbose():
            return SourceVersionDetailSerializer
        if self.request.method == 'POST':
            return SourceCreateSerializer

        return SourceVersionListSerializer

    def get(self, request, *args, **kwargs):
        self.released_filter = parse_boolean_query_param(request, RELEASED_PARAM, self.released_filter)
        self.processing_filter = parse_boolean_query_param(request, PROCESSING_PARAM, self.processing_filter)
        return self.list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        head_object = self.get_queryset().first()
        version = request.data.pop('id', None)
        payload = {
            "mnemonic": head_object.mnemonic, "id": head_object.mnemonic, "name": head_object.name, **request.data,
            "organization_id": head_object.organization_id, "user_id": head_object.user_id,
            'version': version
        }
        serializer = self.get_serializer(data=payload)
        if serializer.is_valid():
            try:
                instance = serializer.create_version(payload)
                if serializer.is_valid():
                    serializer = SourceDetailSerializer(instance, context={'request': request})
                    return Response(serializer.data, status=status.HTTP_201_CREATED)
            except IntegrityError as ex:
                return Response(
                    dict(error=str(ex), detail=VERSION_ALREADY_EXISTS.format(version)),
                    status=status.HTTP_409_CONFLICT
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.released_filter is not None:
            queryset = queryset.filter(released=self.released_filter)
        return queryset.order_by('-created_at')


class SourceLatestVersionRetrieveUpdateView(SourceVersionBaseView, RetrieveAPIView, UpdateAPIView):
    serializer_class = SourceVersionDetailSerializer
    permission_classes = (CanViewConceptDictionaryVersion,)

    def get_object(self, queryset=None):
        obj = self.get_queryset().first()
        if not obj:
            raise Http404
        self.check_object_permissions(self.request, obj)
        return obj

    def get_queryset(self):
        queryset = super().get_queryset().filter(released=True)
        return queryset.order_by('-created_at')

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        head = self.object.head
        if not head:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        serializer = self.get_serializer(self.object, data=request.data, partial=True)

        if serializer.is_valid():
            self.object = serializer.save(force_update=True)
            if serializer.is_valid():
                serializer = SourceVersionDetailSerializer(self.object, context={'request': request})
                return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SourceConceptsIndexView(SourceBaseView):
    serializer_class = TaskSerializer

    def get_queryset(self):
        return Source.get_base_queryset(compact_dict_by_values(self.get_filter_params()))

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, instance)
        return instance

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        source = self.get_object()
        result = index_source_concepts.delay(source.id)

        return Response(
            dict(state=result.state, username=self.request.user.username, task=result.task_id, queue='default'),
            status=status.HTTP_202_ACCEPTED
        )


class SourceMappingsIndexView(SourceBaseView):
    serializer_class = TaskSerializer

    def get_queryset(self):
        return Source.get_base_queryset(compact_dict_by_values(self.get_filter_params()))

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, instance)
        return instance

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        source = self.get_object()
        result = index_source_mappings.delay(source.id)

        return Response(
            dict(state=result.state, username=self.request.user.username, task=result.task_id, queue='default'),
            status=status.HTTP_202_ACCEPTED
        )


class SourceVersionRetrieveUpdateDestroyView(SourceVersionBaseView, RetrieveAPIView, UpdateAPIView):
    serializer_class = SourceVersionDetailSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [CanViewConceptDictionaryVersion()]
        return [HasAccessToVersionedObject()]

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, instance)
        if not get(settings, 'TEST_MODE', False):
            if instance.active_concepts == 0:
                instance.update_concepts_count()
            if instance.active_mappings == 0:
                instance.update_mappings_count()
        return instance

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        head = self.object.head
        if not head:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        external_id = get(request.data, 'version_external_id')
        if external_id:
            request.data['external_id'] = external_id
        serializer = self.get_serializer(self.object, data=request.data, partial=True)

        if serializer.is_valid():
            self.object = serializer.save(force_update=True)
            if serializer.is_valid():
                return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, _, **kwargs):  # pylint: disable=unused-argument
        instance = self.get_object()

        try:
            instance.delete()
        except ValidationError as ex:
            return Response(ex.message_dict, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class SourceExtrasBaseView(SourceBaseView):
    def get_object(self, queryset=None):
        return self.get_queryset().filter(version=HEAD).first()


class SourceExtrasView(SourceExtrasBaseView, ListAPIView):
    serializer_class = SourceDetailSerializer

    def list(self, request, *args, **kwargs):
        return Response(get(self.get_object(), 'extras', {}))


class SourceExtraRetrieveUpdateDestroyView(SourceExtrasBaseView, ConceptContainerExtraRetrieveUpdateDestroyView):
    serializer_class = SourceDetailSerializer


class SourceVersionProcessingView(SourceBaseView, ConceptContainerProcessingMixin):
    serializer_class = SourceVersionDetailSerializer
    resource = 'source'


class SourceVersionExportView(ConceptContainerExportMixin, SourceVersionBaseView):
    entity = 'Source'
    permission_classes = (CanViewConceptDictionary,)
    serializer_class = SourceVersionExportSerializer

    def handle_export_version(self):
        version = self.get_object()
        try:
            export_source.delay(version.id)
            return status.HTTP_202_ACCEPTED
        except AlreadyQueued:
            return status.HTTP_409_CONFLICT


class SourceHierarchyView(SourceBaseView, RetrieveAPIView):
    serializer_class = SourceSummaryDetailSerializer
    permission_classes = (CanViewConceptDictionary,)

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, instance)
        return instance

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        params = self.request.query_params.dict()
        limit = 100
        offset = 0
        if 'limit' in params:
            limit = int(params.get('limit'))
        if 'offset' in params:
            offset = int(params.get('offset'))
        return Response(instance.hierarchy(offset=offset, limit=limit))


class SourceSummaryView(SourceBaseView, RetrieveAPIView):
    serializer_class = SourceSummaryDetailSerializer

    def get_permissions(self):
        if self.request.method == 'PUT':
            return [IsAdminUser()]
        return [CanViewConceptDictionary()]

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, instance)
        return instance

    def put(self, request, **kwargs):  # pylint: disable=unused-argument
        self.perform_update()
        return Response(status=status.HTTP_202_ACCEPTED)

    def perform_update(self):
        instance = self.get_object()
        instance.update_children_counts()


class SourceVersionSummaryView(SourceVersionBaseView, RetrieveAPIView):
    serializer_class = SourceVersionSummaryDetailSerializer

    def get_permissions(self):
        if self.request.method == 'PUT':
            return [IsAdminUser()]
        return [CanViewConceptDictionary()]

    def get_object(self, queryset=None):
        instance = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, instance)
        return instance

    def put(self, request, **kwargs):  # pylint: disable=unused-argument
        self.perform_update()
        return Response(status=status.HTTP_202_ACCEPTED)

    def perform_update(self):
        instance = self.get_object()
        instance.update_children_counts()


class SourceLatestVersionSummaryView(SourceVersionBaseView, RetrieveAPIView, UpdateAPIView):
    serializer_class = SourceVersionSummaryDetailSerializer
    permission_classes = (CanViewConceptDictionary,)

    def get_object(self, queryset=None):
        obj = self.get_queryset().first()
        if not obj:
            raise Http404
        self.check_object_permissions(self.request, obj)
        return obj

    def get_queryset(self):
        queryset = super().get_queryset().filter(released=True)
        return queryset.order_by('-created_at')


class SourceClientConfigsView(SourceBaseView, ResourceClientConfigsView):
    lookup_field = 'source'
    model = Source
    queryset = Source.objects.filter(is_active=True, version='HEAD')
    permission_classes = (CanViewConceptDictionary, )
