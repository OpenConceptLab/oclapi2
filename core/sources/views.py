import logging

from celery_once import AlreadyQueued
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema
from pydash import get
from rest_framework import status, mixins
from rest_framework.generics import (
    RetrieveAPIView, ListAPIView, RetrieveUpdateDestroyAPIView, UpdateAPIView
)
from rest_framework.response import Response

from core.common.constants import HEAD, RELEASED_PARAM, PROCESSING_PARAM, NOT_FOUND, MUST_SPECIFY_EXTRA_PARAM_IN_BODY
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryCreateMixin, ConceptDictionaryUpdateMixin, \
    ConceptContainerExportMixin, ConceptContainerProcessingMixin
from core.common.permissions import CanViewConceptDictionary, CanEditConceptDictionary, HasAccessToVersionedObject, \
    CanViewConceptDictionaryVersion
from core.common.swagger_parameters import q_param, limit_param, sort_desc_param, sort_asc_param, exact_match_param, \
    page_param, verbose_param, include_retired_param, updated_since_param, include_facets_header, compress_header
from core.common.tasks import export_source
from core.common.utils import parse_boolean_query_param, compact_dict_by_values
from core.common.views import BaseAPIView, BaseLogoView, ResourceIndexView
from core.sources.constants import DELETE_FAILURE, DELETE_SUCCESS, VERSION_ALREADY_EXISTS
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.search import SourceSearch
from core.sources.serializers import (
    SourceDetailSerializer, SourceListSerializer, SourceCreateSerializer, SourceVersionDetailSerializer,
    SourceVersionListSerializer, SourceVersionExportSerializer)

logger = logging.getLogger('oclapi')


class SourceBaseView(BaseAPIView):
    lookup_field = 'source'
    pk_field = 'mnemonic'
    model = Source
    permission_classes = (CanViewConceptDictionary,)
    queryset = Source.objects.filter(is_active=True)

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
    es_fields = {
        'source_type': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'mnemonic': {'sortable': True, 'filterable': True, 'exact': True},
        'name': {'sortable': True, 'filterable': True, 'exact': True},
        'last_update': {'sortable': True, 'filterable': True, 'default': 'desc'},
        'locale': {'sortable': False, 'filterable': True, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'custom_validation_schema': {'sortable': False, 'filterable': True},
        'canonical_url': {'sortable': True, 'filterable': True},
    }
    document_model = SourceDocument
    facet_class = SourceSearch
    default_filters = dict(is_active=True, version=HEAD)

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


class SourceRetrieveUpdateDestroyView(SourceBaseView, ConceptDictionaryUpdateMixin):
    serializer_class = SourceDetailSerializer

    def get_object(self, queryset=None):
        return self.get_queryset().filter(is_active=True).order_by('-created_at').first()

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [CanEditConceptDictionary()]

    def delete(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        source = self.get_object()
        try:
            source.delete()
        except Exception as ex:
            return Response({'detail': get(ex, 'messages', [DELETE_FAILURE])}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': DELETE_SUCCESS}, status=status.HTTP_204_NO_CONTENT)

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        instance = self.get_object()

        if not instance:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = self.get_serializer(instance)
        return Response(serializer.data)


class SourceVersionListView(SourceVersionBaseView, mixins.CreateModelMixin, ListWithHeadersMixin):
    released_filter = None
    processing_filter = None
    default_qs_sort_attr = 'created_at'

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

    def post(self, request, *args, **kwargs):
        return self.create(request, *args, **kwargs)

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
                    data = serializer.data
                    version_id = data.get('uuid')
                    export_source.delay(version_id)
                    return Response(data, status=status.HTTP_201_CREATED)
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

    def get_filter_params(self, default_version_to_head=False):
        params = super().get_filter_params(default_version_to_head)
        params['is_latest'] = True
        return params

    def get_object(self, queryset=None):
        obj = get_object_or_404(self.get_queryset(), released=True)
        self.check_object_permissions(self.request, obj)
        return obj

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


class SourceVersionRetrieveUpdateDestroyView(SourceVersionBaseView, RetrieveAPIView, UpdateAPIView):
    permission_classes = (HasAccessToVersionedObject,)
    serializer_class = SourceVersionDetailSerializer

    def get_object(self, queryset=None):
        return get_object_or_404(self.get_queryset())

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


class SourceExtraRetrieveUpdateDestroyView(SourceExtrasBaseView, RetrieveUpdateDestroyAPIView):
    serializer_class = SourceDetailSerializer

    def retrieve(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        instance = self.get_object()
        extras = get(instance, 'extras', {})
        if key in extras:
            return Response({key: extras[key]})

        return Response(dict(detail=NOT_FOUND), status=status.HTTP_404_NOT_FOUND)

    def update(self, request, **kwargs):
        key = kwargs.get('extra')
        value = request.data.get(key)
        if not value:
            return Response([MUST_SPECIFY_EXTRA_PARAM_IN_BODY.format(key)], status=status.HTTP_400_BAD_REQUEST)

        instance = self.get_object()
        instance.extras = get(instance, 'extras', {})
        instance.extras[key] = value
        instance.comment = 'Updated extras: %s=%s.' % (key, value)
        head = instance.get_head()
        head.extras = get(head, 'extras', {})
        head.extras.update(instance.extras)
        instance.save()
        head.save()
        return Response({key: value})

    def delete(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        instance = self.get_object()
        instance.extras = get(instance, 'extras', {})
        if key in instance.extras:
            del instance.extras[key]
            instance.comment = 'Deleted extra %s.' % key
            head = instance.get_head()
            head.extras = get(head, 'extras', {})
            del head.extras[key]
            instance.save()
            head.save()
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(dict(detail=NOT_FOUND), status=status.HTTP_404_NOT_FOUND)


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


class SourcesIndexView(ResourceIndexView):
    serializer_class = SourceListSerializer
    model = Source
