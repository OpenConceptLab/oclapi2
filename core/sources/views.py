from django.db import IntegrityError
from pydash import get
from rest_framework import status, mixins
from rest_framework.generics import (
    DestroyAPIView, RetrieveAPIView, ListAPIView, RetrieveUpdateDestroyAPIView, UpdateAPIView
)
from rest_framework.response import Response

from core.common.constants import HEAD, RELEASED_PARAM, PROCESSING_PARAM
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryCreateMixin, ConceptDictionaryUpdateMixin
from core.common.permissions import CanViewConceptDictionary, CanEditConceptDictionary, HasAccessToVersionedObject
from core.common.utils import parse_boolean_query_param
from core.common.views import BaseAPIView
from core.sources.models import Source
from core.sources.serializers import (
    SourceDetailSerializer, SourceListSerializer, SourceCreateSerializer, SourceVersionDetailSerializer
)


class SourceBaseView(BaseAPIView):
    lookup_field = 'source'
    pk_field = 'mnemonic'
    model = Source
    permission_classes = (CanViewConceptDictionary,)
    queryset = Source.objects.filter(is_active=True)

    @staticmethod
    def get_detail_serializer(obj):
        return SourceDetailSerializer(obj)

    def get_queryset(self):
        query_params = self.request.query_params

        username = query_params.get('user', None) or self.kwargs.get('user', None)
        org = query_params.get('org', None) or self.kwargs.get('org', None)
        version = self.kwargs.get('version', HEAD)
        queryset = self.queryset.filter(version=version)

        if username:
            queryset = queryset.filter(user__username=username)
        if org:
            queryset = queryset.filter(organization__mnemonic=org)
        if 'source' in self.kwargs:
            queryset = queryset.filter(mnemonic=self.kwargs['source'])
        if 'is_latest' in self.kwargs:
            queryset = queryset.filter(is_latest_version=True)

        return queryset.all()


class SourceVersionBaseView(SourceBaseView):
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
        if 'version' in self.kwargs:
            queryset = queryset.filter(version=self.kwargs['version'])
        if 'is_latest' in self.kwargs:
            queryset = queryset.filter(is_latest_version=True)

        return queryset.all()


class SourceListView(SourceBaseView, ConceptDictionaryCreateMixin, ListWithHeadersMixin):
    serializer_class = SourceListSerializer

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return SourceDetailSerializer if self.is_verbose(self.request) else SourceListSerializer
        if self.request.method == 'POST':
            return SourceCreateSerializer

        return SourceListSerializer

    def get(self, request, *args, **kwargs):
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


class SourceRetrieveUpdateDestroyView(SourceBaseView, ConceptDictionaryUpdateMixin, RetrieveAPIView, DestroyAPIView):
    serializer_class = SourceDetailSerializer

    def get_object(self, queryset=None):
        return self.get_queryset().filter(version=HEAD).first()

    def get_permissions(self):
        if self.request.method in ['GET', 'HEAD']:
            return [CanViewConceptDictionary()]

        return [CanEditConceptDictionary()]

    def destroy(self, request, *args, **kwargs):
        source = self.get_object()
        try:
            source.delete()
        except Exception as ex:
            return Response({'detail': ex.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Successfully deleted source.'}, status=status.HTTP_204_NO_CONTENT)


class SourceVersionListView(SourceVersionBaseView, mixins.CreateModelMixin, ListWithHeadersMixin):
    released_filter = None
    processing_filter = None

    def get_serializer_class(self):
        if self.request.method in ['GET', 'HEAD']:
            return SourceVersionDetailSerializer if self.is_verbose(self.request) else SourceListSerializer
        if self.request.method == 'POST':
            return SourceCreateSerializer

        return SourceListSerializer

    def get(self, request, *args, **kwargs):
        self.released_filter = parse_boolean_query_param(request, RELEASED_PARAM, self.released_filter)
        self.processing_filter = parse_boolean_query_param(request, PROCESSING_PARAM, self.processing_filter)
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.create(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        head_object = self.get_queryset().first()
        payload = {
            "mnemonic": head_object.mnemonic, "id": head_object.mnemonic, "name": head_object.name, **request.data,
            "organization_id": head_object.organization_id, "user_id": head_object.user_id,
            'version': request.data.pop('id', None)
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
                    dict(error=str(ex), detail='Source version  \'%s\' already exist. ' % serializer.data.get('id')),
                    status=status.HTTP_409_CONFLICT
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.released_filter is not None:
            queryset = queryset.filter(released=self.released_filter)
        return queryset.order_by('-created_at')


class SourceVersionRetrieveUpdateDestroyView(SourceBaseView, RetrieveAPIView, UpdateAPIView, DestroyAPIView):
    permission_classes = (HasAccessToVersionedObject,)
    serializer_class = SourceDetailSerializer

    def get_object(self, queryset=None):
        return self.get_queryset().first()

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        head = self.object.head
        if not head:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        serializer = self.get_serializer(self.object, data=request.data, partial=True)

        if serializer.is_valid():
            self.object = serializer.save(force_update=True)
            if serializer.is_valid():
                serializer = SourceDetailSerializer(self.object, context={'request': request})
                return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SourceExtrasBaseView(SourceBaseView):
    def get_object(self, queryset=None):
        return self.get_queryset().filter(version=HEAD).first()


class SourceExtrasView(SourceExtrasBaseView, ListAPIView):
    def list(self, request, *args, **kwargs):
        return Response(get(self.get_object(), 'extras', {}))


class SourceExtraRetrieveUpdateDestroyView(SourceExtrasBaseView, RetrieveUpdateDestroyAPIView):
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
            return Response(['Must specify %s param in body.' % key], status=status.HTTP_400_BAD_REQUEST)

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

        return Response(dict(detail='Not found.'), status=status.HTTP_404_NOT_FOUND)
