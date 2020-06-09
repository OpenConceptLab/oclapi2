from django.contrib.auth.hashers import check_password
from rest_framework import mixins, status, views
from rest_framework.generics import RetrieveAPIView, UpdateAPIView
from rest_framework.permissions import IsAdminUser, AllowAny
from rest_framework.response import Response
from rest_framework.authtoken.models import Token


from core.common.mixins import ListWithHeadersMixin
from core.common.views import BaseAPIView
from core.orgs.models import Organization
from core.users.serializers import UserDetailSerializer, UserCreateSerializer, UserListSerializer
from .models import UserProfile


class UserListView(BaseAPIView,
                   ListWithHeadersMixin,
                   mixins.CreateModelMixin):
    model = UserProfile
    queryset = UserProfile.objects.filter(is_active=True)
    pk_field = 'id'
    lookup_field = 'user'

    def initial(self, request, *args, **kwargs):
        self.related_object_type = kwargs.pop('related_object_type', None)
        self.related_object_kwarg = kwargs.pop('related_object_kwarg', None)
        if request.method == 'POST':
            self.permission_classes = (IsAdminUser, )
        super().initial(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        self.serializer_class = UserDetailSerializer if self.is_verbose(request) else UserListSerializer
        if self.related_object_type and self.related_object_kwarg:
            related_object_key = kwargs.pop(self.related_object_kwarg)
            if Organization == self.related_object_type:
                organization = Organization.objects.get(mnemonic=related_object_key)
                if organization.public_access == 'None':
                    if not request.user.is_staff:
                        if not organization.userprofile_set.filter(id=request.user.id):
                            return Response(status=status.HTTP_403_FORBIDDEN)
                self.queryset = organization.userprofile_set.all()
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if self.related_object_type and self.related_object_kwarg:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        self.serializer_class = UserCreateSerializer
        return self.create(request, *args, **kwargs)


class UserBaseView(BaseAPIView):
    lookup_field = 'id'
    pk_field = 'id'
    model = UserProfile
    queryset = UserProfile.objects.filter(is_active=True)
    user_is_self = False

    def initialize(self, request, path_info_segment, **kwargs):
        super().initialize(request, path_info_segment, **kwargs)
        if (request.method == 'DELETE') or (request.method == 'POST' and not self.user_is_self):
            self.permission_classes = (IsAdminUser, )


class UserDetailView(UserBaseView, RetrieveAPIView, mixins.UpdateModelMixin):
    serializer_class = UserDetailSerializer

    def get_object(self, queryset=None):
        if self.user_is_self:
            return self.request.user
        return super().get_object(queryset)

    def post(self, request, *args, **kwargs):
        password = request.DATA.get('password')
        hashed_password = request.DATA.get('hashed_password')
        if password:
            obj = self.get_object()
            obj.set_password(password)
            obj.save()
            Token.objects.filter(user=obj).delete()
            Token.objects.create(user=obj)
        elif hashed_password:
            obj = self.get_object()
            obj.password = hashed_password
            obj.save()
            Token.objects.filter(user=obj).delete()
            Token.objects.create(user=obj)

        return self.partial_update(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        if self.user_is_self:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        obj = self.get_object()
        obj.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserLoginView(views.APIView):
    permission_classes = (AllowAny,)

    @staticmethod
    def post(request, *args, **kwargs):  # pylint: disable=unused-argument
        errors = {}
        username = request.DATA.get('username')
        if not username:
            errors['username'] = ['This field is required.']
        password = request.DATA.get('password')
        hashed_password = request.DATA.get('hashed_password')
        if not password and not hashed_password:
            errors['password'] = ['This field is required.']
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            profile = UserProfile.objects.get(username=username)
            if check_password(password, profile.password) or hashed_password == profile.password:
                return Response({'token': profile.auth_token.key}, status=status.HTTP_200_OK)
            return Response({'detail': 'No such user or wrong password.'}, status=status.HTTP_401_UNAUTHORIZED)
        except UserProfile.DoesNotExist:
            return Response({'detail': 'No such user or wrong password.'}, status=status.HTTP_401_UNAUTHORIZED)


class UserReactivateView(UserBaseView, UpdateAPIView):
    permission_classes = (IsAdminUser, )
    queryset = UserProfile.objects.filter(is_active=False)

    def update(self, request, *args, **kwargs):
        profile = self.get_object()
        profile.undelete()
        return Response(status=status.HTTP_204_NO_CONTENT)
