from rest_framework import generics, status
from rest_framework.generics import RetrieveAPIView, UpdateAPIView, DestroyAPIView
from rest_framework.response import Response

from core.client_configs.serializers import ClientConfigSerializer
from core.common.views import BaseAPIView
from .models import ClientConfig


class ClientConfigBaseView(generics.GenericAPIView):
    swagger_schema = None
    lookup_field = 'id'
    pk_field = 'id'
    queryset = ClientConfig.objects.filter(is_active=True)
    serializer_class = ClientConfigSerializer


class ClientConfigView(ClientConfigBaseView, RetrieveAPIView, UpdateAPIView, DestroyAPIView):
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        serializer.is_valid(raise_exception=True)

        return Response(serializer.data)


class ResourceClientConfigsView(BaseAPIView, RetrieveAPIView):
    swagger_schema = None
    serializer_class = ClientConfigSerializer

    def get(self, request, *args, **kwargs):
        instance = self.get_object()
        configs = instance.client_configs.filter(is_active=True)

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
