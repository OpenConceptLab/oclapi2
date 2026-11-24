from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.capabilities.constants import WRITE_ACTION_EDIT
from core.capabilities.writes import check_write, get_write_target
from core.common.permissions import CanViewConceptDictionaryVersion
from core.common.utils import get_export_service
from core.repos.serializers import RepoExternalExportSerializer


class RepoListCSVMixin:
    """CSV export of a source or collection list: a row per listed repository, under display headers."""
    csv_type_field = None  # 'source_type' or 'collection_type'

    def get_csv_label(self):
        return self.get_queryset().model.__name__

    def get_csv_headers(self):
        label = self.get_csv_label()
        return [
            'Owner', f'{label} ID', f'{label} Name', f'{label} Full Name', f'{label} Type', 'Description',
            'Default Locale', 'Supported Locales', 'Website', 'External ID', 'Last Updated', 'Updated By', 'URI'
        ]

    def get_csv_rows(self, queryset):
        """Read again by id, as the list may be a union."""
        model = self.get_queryset().model
        repos = model.objects.filter(id__in=[repo.id for repo in queryset]).select_related(
            'organization', 'user', 'updated_by').order_by('id')
        return [
            dict(zip(self.get_csv_headers(), [
                repo.parent.mnemonic, repo.mnemonic, repo.name, repo.full_name, getattr(repo, self.csv_type_field),
                repo.description, repo.default_locale, ','.join(repo.supported_locales or []), repo.website,
                repo.external_id, repo.updated_at, repo.updated_by.username, repo.uri
            ])) for repo in repos
        ]


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
        check_write(user, get_write_target(version), WRITE_ACTION_EDIT)

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
        check_write(user, get_write_target(version), WRITE_ACTION_EDIT)

        instance = self.get_external_export(version)
        get_export_service().remove(instance.file_path)
        instance.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
