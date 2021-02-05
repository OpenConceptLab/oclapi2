from rest_framework import generics
from rest_framework.generics import RetrieveAPIView, UpdateAPIView, DestroyAPIView
from rest_framework.response import Response

from core.client_configs.serializers import ClientConfigSerializer
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
