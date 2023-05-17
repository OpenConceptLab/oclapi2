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
from rest_framework.response import Response

from core.bundles.serializers import BundleSerializer
from core.client_configs.views import ResourceClientConfigsView
from core.common.constants import HEAD, RELEASED_PARAM, PROCESSING_PARAM, ACCESS_TYPE_NONE
from core.common.exceptions import Http405, Http400
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryCreateMixin, ConceptDictionaryUpdateMixin, \
    ConceptContainerExportMixin, ConceptContainerProcessingMixin
from core.common.permissions import CanViewConceptDictionary, CanEditConceptDictionary, HasAccessToVersionedObject, \
    CanViewConceptDictionaryVersion
from core.common.serializers import TaskSerializer
from core.common.swagger_parameters import q_param, limit_param, sort_desc_param, sort_asc_param, exact_match_param, \
    page_param, verbose_param, include_retired_param, updated_since_param, include_facets_header, compress_header
from core.common.tasks import export_source, index_source_concepts, index_source_mappings, delete_source
from core.common.utils import parse_boolean_query_param, compact_dict_by_values, to_parent_uri
from core.common.views import BaseAPIView, BaseLogoView, ConceptContainerExtraRetrieveUpdateDestroyView, TaskMixin
from core.sources.constants import DELETE_FAILURE, DELETE_SUCCESS, VERSION_ALREADY_EXISTS
from core.sources.documents import SourceDocument
from core.sources.mixins import SummaryMixin
from core.sources.models import Source
from core.sources.search import SourceSearch
from core.sources.serializers import (
    SourceDetailSerializer, SourceListSerializer, SourceCreateSerializer, SourceVersionDetailSerializer,
    SourceVersionListSerializer, SourceVersionExportSerializer, SourceSummaryDetailSerializer,
    SourceVersionSummaryDetailSerializer, SourceMinimalSerializer, SourceSummaryVerboseSerializer,
    SourceVersionSummaryVerboseSerializer, SourceSummaryFieldDistributionSerializer,
    SourceVersionSummaryFieldDistributionSerializer, SourceVersionMinimalSerializer)

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

    def get_detail_serializer(self, obj):
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
    default_filters = {'is_active': True, 'version': HEAD}

    def apply_filters(self, queryset):
        return queryset

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.apply_filters(queryset)
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
        if self.is_brief():
            return SourceMinimalSerializer
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


class SourceRetrieveUpdateDestroyView(SourceBaseView, ConceptDictionaryUpdateMixin, RetrieveAPIView, TaskMixin):
    serializer_class = SourceDetailSerializer

    def get_object(self, queryset=None):
        instance = self.get_queryset().filter(is_active=True).order_by('-created_at').first()
        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)
        if not get(settings, 'TEST_MODE', False):
            if instance.should_set_active_concepts:
                instance.update_concepts_count()
            if instance.should_set_active_mappings:
                instance.update_mappings_count()
            for version in instance.versions.exclude(id=instance.id):
                if version.should_set_active_mappings:
                    version.update_concepts_count()
                if version.should_set_active_mappings:
                    version.update_mappings_count()
        return instance

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [CanEditConceptDictionary()]

    def delete(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        source = self.get_object()
        result = self.perform_task(delete_source, (source.id, ))

        if isinstance(result, Response):
            return result

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
        if self.is_brief():
            return SourceVersionMinimalSerializer

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
                    {
                        'error': str(ex),
                        'detail': VERSION_ALREADY_EXISTS.format(version)
                    },
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
        try:
            result = index_source_concepts.delay(source.id)
        except AlreadyQueued:
            return Response({'detail': 'Already Queued'}, status=status.HTTP_409_CONFLICT)

        return Response(
            {
                'state': result.state,
                'username': self.request.user.username,
                'task': result.task_id,
                'queue': 'default'
            },
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
        try:
            result = index_source_mappings.delay(source.id)
        except AlreadyQueued:
            return Response({'detail': 'Already Queued'}, status=status.HTTP_409_CONFLICT)

        return Response(
            {
                'state': result.state,
                'username': self.request.user.username,
                'task': result.task_id,
                'queue': 'default'
            },
            status=status.HTTP_202_ACCEPTED
        )


class SourceConceptsCloneView(SourceBaseView):
    serializer_class = BundleSerializer

    def post(self, request, **kwargs):  # pylint: disable=unused-argument, too-many-locals
        """
        body:
            {
                “expressions”: [“/orgs/CIEL/sources/CIEL/concepts/123/”], (cloneFrom)
                “parameters”: { ….same as cascade… }
            }
        """
        expressions = request.data.get('expressions')
        parameters = request.data.get('parameters') or {}
        if not expressions:
            raise Http400()
        instance = self.get_object()
        results = {}
        parent_resources = {}
        is_verbose = self.is_verbose()
        for expression in expressions:
            from core.concepts.models import Concept
            result = {}
            concept_to_clone = Concept.objects.filter(uri=expression).first()
            if concept_to_clone:
                parent_uri = to_parent_uri(expression)
                if parent_uri not in parent_resources:
                    parent_resources[parent_uri] = Source.objects.filter(uri=parent_uri).first()
                parent_resource = parent_resources[parent_uri]
                from core.bundles.models import Bundle
                bundle = Bundle.clone(
                    concept_to_clone, parent_resource, instance, request.user,
                    self.request.get_full_path(), is_verbose, **parameters
                )
                result['status'] = status.HTTP_200_OK
                result['bundle'] = BundleSerializer(bundle, context={'request': request}).data
            else:
                result['status'] = status.HTTP_404_NOT_FOUND
                result['errors'] = [f'Concept to clone with expression {expression} not found.']
            results[expression] = result

        return Response(results, status.HTTP_200_OK)


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
            if instance.should_set_active_concepts:
                instance.update_concepts_count()
            if instance.should_set_active_mappings:
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
        return get_object_or_404(self.get_queryset(), version=HEAD)


class SourceExtrasView(SourceExtrasBaseView, ListAPIView):
    serializer_class = SourceDetailSerializer

    def list(self, request, *args, **kwargs):
        return Response(get(self.get_object(), 'extras', {}))


class SourceVersionExtrasView(SourceBaseView, ListAPIView):
    serializer_class = SourceDetailSerializer

    def list(self, request, *args, **kwargs):
        instance = get_object_or_404(self.get_queryset(), version=self.kwargs['version'])
        return Response(get(instance, 'extras', {}))


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


class SourceSummaryView(SummaryMixin, SourceBaseView, RetrieveAPIView):
    serializer_class = SourceSummaryDetailSerializer
    permission_classes = (CanViewConceptDictionary,)

    def get_serializer_class(self):
        if self.is_verbose():
            if self.request.query_params.get('distribution'):
                return SourceSummaryFieldDistributionSerializer
            return SourceSummaryVerboseSerializer
        return SourceSummaryDetailSerializer


class SourceVersionSummaryView(SummaryMixin, SourceVersionBaseView, RetrieveAPIView):
    serializer_class = SourceVersionSummaryDetailSerializer
    permission_classes = (CanViewConceptDictionary,)

    def get_serializer_class(self):
        if self.is_verbose():
            if self.request.query_params.get('distribution'):
                return SourceVersionSummaryFieldDistributionSerializer
            return SourceVersionSummaryVerboseSerializer
        return SourceVersionSummaryDetailSerializer


class SourceLatestVersionSummaryView(SourceVersionBaseView, RetrieveAPIView, UpdateAPIView):
    serializer_class = SourceVersionSummaryDetailSerializer
    permission_classes = (CanViewConceptDictionary,)

    def get_serializer_class(self):
        if self.is_verbose():
            if self.request.query_params.get('distribution'):
                return SourceVersionSummaryFieldDistributionSerializer
            return SourceVersionSummaryVerboseSerializer
        return SourceVersionSummaryDetailSerializer

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
    queryset = Source.objects.filter(is_active=True, version=HEAD)
    permission_classes = (CanViewConceptDictionary, )


class SourceMappedSourcesListView(SourceListView):
    is_searchable = False

    def get_object(self, queryset=None):
        instance = super().get_queryset().order_by('-created_at').first()
        if not instance:
            raise Http404()
        self.check_object_permissions(self.request, instance)
        return instance

    def get_queryset(self):
        instance = self.get_object()
        return instance.get_mapped_sources()

    def post(self, request, **kwargs):
        raise Http405()
