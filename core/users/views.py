import uuid

from django.contrib.auth.models import update_last_login
from django.http import Http404
from drf_yasg.utils import swagger_auto_schema
from pydash import get
from rest_framework import mixins, status
from rest_framework.authtoken.serializers import AuthTokenSerializer
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveAPIView, UpdateAPIView, DestroyAPIView
from rest_framework.permissions import IsAdminUser, IsAuthenticated, AllowAny
from rest_framework.response import Response

from core.common.mixins import ListWithHeadersMixin
from core.common.utils import parse_updated_since_param
from core.common.views import BaseAPIView, BaseLogoView
from core.orgs.models import Organization
from core.users.constants import VERIFICATION_TOKEN_MISMATCH, VERIFY_EMAIL_MESSAGE
from core.users.documents import UserProfileDocument
from core.users.search import UserProfileSearch
from core.users.serializers import UserDetailSerializer, UserCreateSerializer, UserListSerializer
from .models import UserProfile


class TokenAuthenticationView(ObtainAuthToken):
    """Implementation of ObtainAuthToken with last_login update"""

    @swagger_auto_schema(request_body=AuthTokenSerializer)
    def post(self, request, *args, **kwargs):
        result = super().post(request, *args, **kwargs)
        try:
            user = UserProfile.objects.get(username=request.data['username'])
            if not user.verified:
                user.send_verification_email()
                return Response(
                    {'detail': VERIFY_EMAIL_MESSAGE, 'email': user.email}, status=status.HTTP_401_UNAUTHORIZED
                )
            update_last_login(None, user)
        except:  # pylint: disable=bare-except
            pass

        return result


class UserBaseView(BaseAPIView):
    lookup_field = 'user'
    pk_field = 'username'
    model = UserProfile
    queryset = UserProfile.objects.filter(is_active=True)
    es_fields = UserProfile.es_fields
    document_model = UserProfileDocument
    facet_class = UserProfileSearch
    is_searchable = True
    default_qs_sort_attr = '-date_joined'

    def get_queryset(self):
        updated_since = parse_updated_since_param(self.request.query_params)
        if updated_since:
            self.queryset = self.queryset.filter(updated_at__gte=updated_since)
        return self.queryset


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

        if serializer.errors:
            return Response(serializer.data, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.data.copy()
        if 'username' in serializer.errors and 'token' not in data and get(serializer, 'instance.token'):
            data['token'] = serializer.instance.token  # for ocl_web

        return Response(data, status=status.HTTP_201_CREATED, headers=headers)


class UserSignup(UserBaseView, mixins.CreateModelMixin):
    serializer_class = UserCreateSerializer
    permission_classes = (AllowAny, )

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        serializer = self.get_serializer(data={**request.data, 'verified': False})
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        if serializer.errors:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        headers = self.get_success_headers(serializer.data)
        return Response({'detail': VERIFY_EMAIL_MESSAGE}, status=status.HTTP_201_CREATED, headers=headers)


class UserEmailVerificationView(UserBaseView):
    serializer_class = UserDetailSerializer
    permission_classes = (AllowAny, )

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        user = self.get_object()
        if not user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        result = user.mark_verified(kwargs.get('verification_token'))
        if result:
            update_last_login(None, user)
            return Response({'token': user.get_token()}, status=status.HTTP_200_OK)

        return Response(dict(detail=VERIFICATION_TOKEN_MISMATCH), status=status.HTTP_401_UNAUTHORIZED)


class UserPasswordResetView(UserBaseView):
    serializer_class = UserDetailSerializer
    permission_classes = (AllowAny, )

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument,no-self-use
        """Sends reset password mail"""

        email = request.data.get('email')
        if not email:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        user = UserProfile.objects.filter(email=email).first()
        if not user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        user.verification_token = uuid.uuid4()
        user.save()
        user.send_reset_password_email()
        return Response(status=status.HTTP_200_OK)

    def put(self, request, *args, **kwargs):  # pylint: disable=unused-argument,no-self-use
        """Resets password"""

        token = request.data.get('token', None)
        password = request.data.get('new_password', None)
        if not token or not password:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        user = UserProfile.objects.filter(verification_token=token).first()

        if not user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        result = user.update_password(password=password)
        if get(result, 'errors'):
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_200_OK)


class UserDetailView(UserBaseView, RetrieveAPIView, DestroyAPIView, mixins.UpdateModelMixin):
    serializer_class = UserDetailSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        if self.kwargs.get('user_is_self'):
            return queryset.filter(username=self.request.user.username)
        return queryset.filter(username=self.kwargs['user'])

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsAdminUser()]

        return [IsAuthenticated()]

    def get_object(self, queryset=None):
        instance = self.request.user if self.kwargs.get('user_is_self') else super().get_object(queryset)
        self.user_is_self = self.request.user.username == instance.username

        if not instance or instance.is_anonymous:
            raise Http404()

        is_self = self.kwargs.get('user_is_self') or self.user_is_self
        is_admin = self.request.user.is_staff

        if not is_self and not is_admin:
            raise PermissionDenied(detail='You do not have permission to perform this action.')

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
