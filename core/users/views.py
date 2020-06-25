from rest_framework import mixins, status
from rest_framework.authtoken.models import Token
from rest_framework.generics import RetrieveAPIView, UpdateAPIView
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.common.mixins import ListWithHeadersMixin
from core.common.views import BaseAPIView
from core.orgs.models import Organization
from core.users.serializers import UserDetailSerializer, UserCreateSerializer, UserListSerializer
from .models import UserProfile


class UserBaseView(BaseAPIView):
    lookup_field = 'user'
    pk_field = 'username'
    model = UserProfile
    queryset = UserProfile.objects.filter(is_active=True)
    user_is_self = False

    def initialize(self, request, path_info_segment, **kwargs):
        super().initialize(request, path_info_segment, **kwargs)
        if (request.method == 'DELETE') or (request.method == 'POST' and not self.user_is_self):
            self.permission_classes = (IsAdminUser, )


class UserListView(UserBaseView,
                   ListWithHeadersMixin,
                   mixins.CreateModelMixin):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.related_object_kwarg = kwargs.pop('related_object_kwarg', None)
        self.related_object_type = kwargs.pop('related_object_type', None)

    def initial(self, request, *args, **kwargs):
        if request.method == 'POST':
            self.permission_classes = (IsAdminUser, )
        super().initial(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        self.serializer_class = UserDetailSerializer if self.is_verbose(request) else UserListSerializer
        if self.related_object_type and self.related_object_kwarg:
            related_object_key = kwargs.pop(self.related_object_kwarg)
            if Organization == self.related_object_type:
                organization = Organization.objects.get(mnemonic=related_object_key)
                if not organization.public_can_view:
                    if not request.user.is_staff:
                        if not organization.userprofile_set.filter(id=request.user.id).exists():
                            return Response(status=status.HTTP_403_FORBIDDEN)
                self.queryset = organization.userprofile_set.all()
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if self.related_object_type and self.related_object_kwarg:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        self.serializer_class = UserCreateSerializer
        return self.create(request, *args, **kwargs)


class UserDetailView(UserBaseView, RetrieveAPIView, mixins.UpdateModelMixin):
    serializer_class = UserDetailSerializer

    def get_object(self, queryset=None):
        if self.user_is_self:
            return self.request.user
        return super().get_object(queryset)

    def put(self, request, *args, **kwargs):
        password = request.data.get('password')
        hashed_password = request.data.get('hashed_password')
        if password:
            obj = self.get_object()
            obj.set_password(password)
            obj.save()
        elif hashed_password:
            obj = self.get_object()
            obj.password = hashed_password
            obj.save()
        if obj:
            Token.objects.filter(user=obj).delete()
            Token.objects.create(user=obj)

        return self.partial_update(request, *args, **kwargs)

    def delete(self):
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
