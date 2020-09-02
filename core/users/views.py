from pydash import get
from rest_framework import mixins, status
from rest_framework.generics import RetrieveAPIView, UpdateAPIView, DestroyAPIView
from rest_framework.permissions import IsAdminUser, IsAuthenticated
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
        'username': {'sortable': True, 'filterable': True, 'exact': True},
        'date_joined': {'sortable': True, 'default': 'asc', 'filterable': True},
        'company': {'sortable': False, 'filterable': True, 'exact': True},
        'location': {'sortable': False, 'filterable': True, 'exact': True},
    }
    document_model = UserProfileDocument
    is_searchable = True
    default_qs_sort_attr = '-created_at'


class UserListView(UserBaseView,
                   ListWithHeadersMixin,
                   mixins.CreateModelMixin):

    def get_serializer_class(self):
        if self.request.method == 'GET' and self.is_verbose(self.request):
            return UserDetailSerializer
        if self.request.method == 'POST':
            return UserCreateSerializer

        return UserListSerializer

    def get_permissions(self):
        if self.request.method in ['POST', 'DELETE']:
            return [IsAdminUser()]
        return []

    def can_view(self, organization):
        user = self.request.user
        return organization.public_can_view or user.is_staff or organization.is_member(user)

    def get(self, request, *args, **kwargs):
        org = kwargs.pop('org', None)
        if org:
            organization = Organization.objects.filter(mnemonic=org).first()
            if not organization:
                return Response(status=status.HTTP_404_NOT_FOUND)

            if not self.can_view(organization):
                return Response(status=status.HTTP_403_FORBIDDEN)

            self.queryset = organization.members.all()
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)

        data = serializer.data.copy()
        if 'username' in serializer._errors and 'token' not in data and get(  # pylint: disable=protected-access
                serializer, 'instance.token'
        ):
            data['token'] = serializer.instance.token  # for ocl_web

        return Response(data, status=status.HTTP_201_CREATED, headers=headers)


class UserDetailView(UserBaseView, RetrieveAPIView, DestroyAPIView, mixins.UpdateModelMixin):
    serializer_class = UserDetailSerializer

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsAdminUser()]

        return [IsAuthenticated()]

    def get_object(self, queryset=None):
        instance = super().get_object(queryset)
        self.user_is_self = self.request.user.username == instance.username
        return instance

    def put(self, request, *args, **kwargs):
        obj = self.get_object()
        obj.update_password(request.data.get('password'), request.data.get('hashed_password'))

        return self.partial_update(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        if self.user_is_self:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        obj.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserReactivateView(UserBaseView, UpdateAPIView):
    permission_classes = (IsAdminUser, )
    queryset = UserProfile.objects.filter(is_active=False)
    serializer_class = UserDetailSerializer

    def update(self, request, *args, **kwargs):
        profile = self.get_object()
        profile.undelete()
        return Response(status=status.HTTP_204_NO_CONTENT)
