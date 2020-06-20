from django.db import IntegrityError
from rest_framework import status, mixins
from rest_framework.generics import DestroyAPIView, RetrieveAPIView
from rest_framework.response import Response

from core.common.constants import HEAD, RELEASED_PARAM, PROCESSING_PARAM
from core.common.mixins import ListWithHeadersMixin
from core.common.permissions import CanViewConceptDictionary, CanEditConceptDictionary
from core.common.utils import parse_boolean_query_param
from core.common.views import BaseAPIView
from core.sources.models import Source
from core.sources.serializers import SourceDetailSerializer, SourceListSerializer, SourceCreateOrUpdateSerializer


class SourceBaseView(BaseAPIView):
    lookup_field = 'source'
    pk_field = 'mnemonic'
    model = Source
    permission_classes = (CanViewConceptDictionary,)
    queryset = Source.objects.filter(is_active=True)

    @staticmethod
    def get_detail_serializer(obj, data=None, files=None, partial=False):
        return SourceDetailSerializer(obj, data, files, partial)

    def get_queryset(self):
        query_params = self.request.query_params

        username = query_params.get('user', None) or self.kwargs.get('user', None)
        org = query_params.get('org', None) or self.kwargs.get('org', None)
        queryset = self.queryset

        if username:
            queryset = queryset.filter(user__username=username)
        if org:
            queryset = queryset.filter(organization__mnemonic=org)
        if 'source' in self.kwargs:
            queryset = queryset.filter(mnemonic=self.kwargs['source'])

        return queryset.all()


class SourceListView(SourceBaseView, ListWithHeadersMixin):
    serializer_class = SourceListSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(version=HEAD)

    def get(self, request, *args, **kwargs):
        self.serializer_class = SourceDetailSerializer if self.is_verbose(request) else SourceListSerializer
        return self.list(request, *args, **kwargs)

    def get_csv_rows(self, queryset=None):
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


class SourceRetrieveUpdateDestroyView(SourceBaseView, RetrieveAPIView, DestroyAPIView):
    serializer_class = SourceDetailSerializer

    def initialize(self, request, path_info_segment, **kwargs):
        if request.method in ['GET', 'HEAD']:
            self.permission_classes = (CanViewConceptDictionary,)
        else:
            self.permission_classes = (CanEditConceptDictionary,)
        super().initialize(request, path_info_segment, **kwargs)

    def destroy(self, request, *args, **kwargs):
        source = self.get_object()
        try:
            source.delete()
        except Exception as ex:
            return Response({'detail': ex.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Successfully deleted source.'}, status=status.HTTP_204_NO_CONTENT)


class SourceVersionListView(SourceBaseView, mixins.CreateModelMixin, ListWithHeadersMixin):
    released_filter = None
    processing_filter = None
    permission_classes = (CanViewConceptDictionary,)

    def get(self, request, *args, **kwargs):
        self.serializer_class = SourceDetailSerializer if self.is_verbose(request) else SourceListSerializer
        self.released_filter = parse_boolean_query_param(request, RELEASED_PARAM, self.released_filter)
        self.processing_filter = parse_boolean_query_param(request, PROCESSING_PARAM, self.processing_filter)
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.serializer_class = SourceCreateOrUpdateSerializer
        return self.create(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        if not self.versioned_object:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        serializer = self.get_serializer(data=request.DATA, files=request.FILES)
        if serializer.is_valid():
            self.pre_save(serializer.object)
            try:
                self.object = serializer.save(force_insert=True, versioned_object=self.versioned_object)
                if serializer.is_valid():
                    self.post_save(self.object, created=True)
                    headers = self.get_success_headers(serializer.data)
                    serializer = SourceDetailSerializer(self.object, context={'request': request})
                    return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
            except IntegrityError as ex:
                return Response(
                    dict(error=str(ex), detail='Source version  \'%s\' already exist. ' % serializer.data.get('id')),
                    status=status.HTTP_409_CONFLICT
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.released_filter is not None:
            queryset = queryset.filter(released=self.released_filter)
        return queryset.order_by('-created_at')
