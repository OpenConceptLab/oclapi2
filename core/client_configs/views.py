from django.db import models
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveAPIView, UpdateAPIView, DestroyAPIView, ListAPIView, CreateAPIView
from rest_framework.response import Response

from core.client_configs.serializers import ClientConfigSerializer, ClientConfigTemplateSerializer
from core.common.views import BaseAPIView
from .models import ClientConfig
from ..common.throttling import ThrottleUtil


class ClientConfigBaseView(generics.GenericAPIView):
    lookup_field = 'id'
    pk_field = 'id'
    queryset = ClientConfig.objects.filter(is_active=True)
    serializer_class = ClientConfigSerializer

    def get_throttles(self):
        return ThrottleUtil.get_throttles_by_user_plan(self.request.user)


class ClientConfigView(ClientConfigBaseView, RetrieveAPIView, UpdateAPIView, DestroyAPIView):
    def perform_destroy(self, instance: ClientConfig):
        if not self.request.user.is_staff:
            if instance.created_by != self.request.user:
                raise PermissionDenied()

        super().perform_destroy(instance)


class ResourceClientConfigsView(BaseAPIView, RetrieveAPIView):
    swagger_schema = None
    serializer_class = ClientConfigSerializer

    def get(self, request, *args, **kwargs):
        instance = self.get_object()
        configs = instance.client_configs.filter(is_active=True, is_template=False)

        return Response(self.get_serializer(configs, many=True).data, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        instance = self.get_object()
        serializer = self.get_serializer(
            data={**request.data, 'resource_type': instance.__class__.__name__, 'resource_id': instance.id}
        )
        if serializer.is_valid():
            serializer.save()
            if serializer.is_valid():
                return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ResourceTemplatesView(ListAPIView, CreateAPIView):
    serializer_class = ClientConfigTemplateSerializer

    def get_throttles(self):
        return ThrottleUtil.get_throttles_by_user_plan(self.request.user)

    def get_queryset(self):
        user = self.request.user
        return ClientConfig.objects.filter(
            is_template=True, is_active=True, resource_type__app_label=self.kwargs.get('resource')
        ).filter(models.Q(public=True) | models.Q(created_by=user))
