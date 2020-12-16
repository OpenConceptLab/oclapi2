from django.contrib.auth.models import update_last_login
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema
from pydash import get
from rest_framework import mixins, status
from rest_framework.authtoken.serializers import AuthTokenSerializer
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.generics import RetrieveAPIView, UpdateAPIView, DestroyAPIView
from rest_framework.permissions import IsAdminUser, IsAuthenticated, AllowAny
from rest_framework.response import Response

from core.common.mixins import ListWithHeadersMixin
from core.common.views import BaseAPIView, BaseLogoView
from core.orgs.models import Organization
from core.users.documents import UserProfileDocument
from core.users.serializers import UserDetailSerializer, UserCreateSerializer, UserListSerializer, \
    UserPinnedItemSerializer
from .models import UserProfile, PinnedItem


class TokenAuthenticationView(ObtainAuthToken):
    """Implementation of ObtainAuthToken with last_login update"""

    @swagger_auto_schema(request_body=AuthTokenSerializer)
    def post(self, request, *args, **kwargs):
        result = super().post(request, *args, **kwargs)
        try:
            user = UserProfile.objects.get(username=request.data['username'])
            update_last_login(None, user)
        except:  # pylint: disable=bare-except
            pass

        return result


class UserBaseView(BaseAPIView):
    lookup_field = 'user'
    pk_field = 'username'
    model = UserProfile
    queryset = UserProfile.objects.filter(is_active=True)
    es_fields = {
        'username': {'sortable': True, 'filterable': True, 'exact': True},
        'date_joined': {'sortable': True, 'default': 'asc', 'filterable': True},
        'company': {'sortable': True, 'filterable': True, 'exact': True},
        'location': {'sortable': True, 'filterable': True, 'exact': True},
    }
    document_model = UserProfileDocument
    is_searchable = True
    default_qs_sort_attr = '-created_at'


class UserLogoView(UserBaseView, BaseLogoView):
    serializer_class = UserDetailSerializer
    permission_classes = (IsAuthenticated, )


class UserListView(UserBaseView,
                   ListWithHeadersMixin,
                   mixins.CreateModelMixin):

    def get_serializer_class(self):
        if self.request.method == 'GET' and self.is_verbose():
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
        if self.request.method == 'GET':
            return [AllowAny()]
        if self.request.method == 'DELETE':
            return [IsAdminUser()]

        return [IsAuthenticated()]

    def get_object(self, queryset=None):
        instance = self.request.user if self.kwargs.get('user_is_self') else super().get_object(queryset)
        self.user_is_self = self.request.user.username == instance.username

        if not instance or instance.is_anonymous:
            raise Http404()
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


class UserPinnedItemsView(UserBaseView, mixins.CreateModelMixin):
    serializer_class = UserPinnedItemSerializer
    permission_classes = (IsAuthenticated, )

    def get_object(self, queryset=None):
        instance = self.request.user if self.kwargs.get('user_is_self') else super().get_object(queryset)
        self.user_is_self = self.request.user.username == instance.username

        return instance

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        org_id = request.query_params.get('organization_id', None)
        queryset = self.get_object().pins

        if org_id:
            queryset = queryset.filter(organization_id=org_id)
        else:
            queryset = queryset.filter(organization_id__isnull=True)

        queryset = queryset.prefetch_related('resource').all()

        return Response(self.serializer_class(queryset, many=True).data)

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        user = self.get_object()
        serializer = self.get_serializer(data={**request.data, 'user_id': user.id})
        if serializer.is_valid():
            serializer.save(force_insert=True)
            if not serializer.errors:
                return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserPinnedItemView(RetrieveAPIView, DestroyAPIView):
    serializer_class = UserPinnedItemSerializer

    def get_queryset(self):
        return PinnedItem.objects.filter(
            id=self.kwargs.get('pin_id'), user__username=self.kwargs.get('username')
        )

    def get_object(self):
        return get_object_or_404(self.get_queryset())
