from rest_framework import mixins, status
from rest_framework.generics import RetrieveAPIView, UpdateAPIView, DestroyAPIView
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.common.mixins import ListWithHeadersMixin
from core.common.views import BaseAPIView
from core.orgs.models import Organization
from core.users.documents import UserProfileDocument
from core.users.serializers import UserDetailSerializer, UserCreateSerializer, UserListSerializer
from .models import UserProfile


class UserBaseView(BaseAPIView):
    lookup_field = 'user'
    pk_field = 'username'
    model = UserProfile
    queryset = UserProfile.objects.filter(is_active=True)
    user_is_self = False
    es_fields = {
        'username': {'sortable': True, 'filterable': True},
        'dateJoined': {'sortable': True, 'default': 'asc', 'filterable': True},
        'company': {'sortable': False, 'filterable': True},
        'location': {'sortable': False, 'filterable': True},
    }
    document_model = UserProfileDocument
    is_searchable = True
    default_qs_sort_attr = '-created_at'


class UserListView(UserBaseView,
                   ListWithHeadersMixin,
                   mixins.CreateModelMixin):

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return UserDetailSerializer if self.is_verbose(self.request) else UserListSerializer
        if self.request.method == 'POST':
            return UserCreateSerializer

        return UserListSerializer

    def get_permissions(self):
        if self.request.method in ['POST', 'DELETE']:
            return [IsAdminUser()]
        return []

    def get(self, request, *args, **kwargs):
        self.serializer_class = UserDetailSerializer if self.is_verbose(request) else UserListSerializer
        org = kwargs.pop('org', None)
        if org:
            organization = Organization.objects.get(mnemonic=org)
            if not organization.public_can_view:
                if not request.user.is_staff:
                    if not organization.userprofile_set.filter(id=request.user.id).exists():
                        return Response(status=status.HTTP_403_FORBIDDEN)
            self.queryset = organization.userprofile_set.all()
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.serializer_class = UserCreateSerializer
        return self.create(request, *args, **kwargs)


class UserDetailView(UserBaseView, RetrieveAPIView, DestroyAPIView, mixins.UpdateModelMixin):
    serializer_class = UserDetailSerializer

    def get_object(self, queryset=None):
        if self.user_is_self:
            return self.request.user
        return super().get_object(queryset)

    def put(self, request, *args, **kwargs):
        obj = self.get_object()
        obj.update_password(request.data.get('password'), request.data.get('hashed_password'))

        return self.partial_update(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        if self.user_is_self:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        obj = self.get_object()
        obj.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserReactivateView(UserBaseView, UpdateAPIView):
    permission_classes = (IsAdminUser, )
    queryset = UserProfile.objects.filter(is_active=False)

    def update(self, request, *args, **kwargs):
        profile = self.get_object()
        profile.undelete()
        return Response(status=status.HTTP_204_NO_CONTENT)
