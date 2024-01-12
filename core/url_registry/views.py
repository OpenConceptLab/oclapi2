from django.http import Http404
from drf_yasg.utils import swagger_auto_schema
from rest_framework.generics import CreateAPIView, RetrieveUpdateDestroyAPIView, get_object_or_404
from rest_framework.permissions import IsAuthenticatedOrReadOnly

from core.common.mixins import ListWithHeadersMixin
from core.common.swagger_parameters import q_param, limit_param, sort_desc_param, sort_asc_param, page_param
from core.common.views import BaseAPIView
from core.url_registry.documents import URLRegistryDocument
from core.url_registry.models import URLRegistry
from core.url_registry.serializers import URLRegistryDetailSerializer


class URLRegistryBaseView(BaseAPIView):
    permission_classes = (IsAuthenticatedOrReadOnly,)
    serializer_class = URLRegistryDetailSerializer
    queryset = URLRegistry.objects.filter(is_active=True)
    parent_resource = None
    parent_resource_type = None

    def set_parent_resource(self):
        from core.orgs.models import Organization
        from core.users.models import UserProfile
        org = self.kwargs.get('org', None)
        user = self.kwargs.get('user', None)
        if not user and self.user_is_self:
            user = self.request.user.username
        if org:
            self.parent_resource = Organization.objects.filter(mnemonic=org).first()
            self.parent_resource_type = 'organization'
        elif user:
            self.parent_resource = UserProfile.objects.filter(username=user).first()
            self.parent_resource_type = 'user'

        self.kwargs['parent_resource'] = self.parent_resource
        self.kwargs['parent_resource_type'] = self.parent_resource_type

    def get_queryset(self):
        self.set_parent_resource()

        queryset = self.queryset
        if self.parent_resource_type:
            queryset = queryset.filter(
                **{f"{self.parent_resource_type}__{self.parent_resource.mnemonic_attr}": self.parent_resource.mnemonic}
            )
        else:
            queryset = queryset.filter(organization__isnull=True, user__isnull=True)

        return queryset


class URLRegistriesView(URLRegistryBaseView, ListWithHeadersMixin, CreateAPIView):
    document_model = URLRegistryDocument
    es_fields = URLRegistry.es_fields
    is_searchable = True

    @swagger_auto_schema(
        manual_parameters=[
            q_param, limit_param, sort_desc_param, sort_asc_param, page_param,
        ]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        self.set_parent_resource()
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save()
        serializer.is_valid(raise_exception=True)


class UserOrgURLRegistriesView(URLRegistriesView):
    def get_queryset(self):
        self.set_parent_resource()

        if not self.parent_resource:
            raise Http404()

        return self.queryset.filter(organization__members__username=self.parent_resource.username)


class URLRegistryView(URLRegistryBaseView, RetrieveUpdateDestroyAPIView):
    def get_object(self, queryset=None):
        queryset = self.get_queryset()
        instance = get_object_or_404(queryset.filter(id=self.kwargs['id']))
        self.check_object_permissions(self.request, instance)
        return instance
