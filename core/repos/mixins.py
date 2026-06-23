from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.permissions import CanViewConceptDictionaryVersion
from core.common.utils import get_export_service
from core.repos.serializers import RepoExternalExportSerializer


class RepoExternalExportMixin:
    permission_classes = (CanViewConceptDictionaryVersion, IsAuthenticated)

    def get_object(self):
        queryset = self.get_queryset()
        if 'version' not in self.kwargs:
            queryset = queryset.filter(is_latest_version=True)

        instance = queryset.first()

        if not instance:
            raise Http404()

        self.check_object_permissions(self.request, instance)

        return instance

    def get_external_export(self, version, required=True):
        instance = version.external_exports.filter(key=self.kwargs.get('external_export_key')).first()

        if required and not instance:
            raise Http404()

        return instance

    @staticmethod
    def is_permitted(user, version):
        return user.is_staff or user.is_superuser or user.is_admin_for(version)

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        version = self.get_object()
        instance = self.get_external_export(version)

        export_url = instance.file_url
        if not export_url:
            return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return redirect(export_url)

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        version = self.get_object()
        user = request.user

        if not self.is_permitted(user, version):
            return HttpResponseForbidden()

        uploaded_file = request.data.get('file')
        if not uploaded_file:
            return Response({'file': ['This field is required.']}, status=status.HTTP_400_BAD_REQUEST)

        instance, is_create = version.upload_external_export(
            self.kwargs.get('external_export_key'), uploaded_file, user, request.data.get('description'))

        serializer = RepoExternalExportSerializer(instance)
        return Response(serializer.data, status=status.HTTP_201_CREATED if is_create else status.HTTP_200_OK)

    def delete(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        version = self.get_object()
        user = request.user

        if not self.is_permitted(user, version):
            return HttpResponseForbidden()

        instance = self.get_external_export(version)
        get_export_service().remove(instance.file_path)
        instance.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
