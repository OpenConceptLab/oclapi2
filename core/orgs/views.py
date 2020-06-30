from django.contrib.auth.models import AnonymousUser
from django.db.models import Q
from django.http import HttpResponse
from rest_framework import mixins, status, generics
from rest_framework.generics import RetrieveAPIView, DestroyAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.constants import ACCESS_TYPE_NONE
from core.common.mixins import ListWithHeadersMixin
from core.common.permissions import IsSuperuser
from core.common.views import BaseAPIView
from core.orgs.models import Organization
from core.orgs.serializers import OrganizationDetailSerializer, OrganizationListSerializer, OrganizationCreateSerializer
from core.sources.views import SourceListView
from core.users.models import UserProfile
from core.users.serializers import UserDetailSerializer


class OrganizationListView(BaseAPIView,
                           ListWithHeadersMixin,
                           mixins.CreateModelMixin):
    model = Organization
    queryset = Organization.objects.filter(is_active=True)

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return OrganizationDetailSerializer if self.is_verbose(self.request) else OrganizationListSerializer
        if self.request.method == 'POST':
            return OrganizationCreateSerializer

        return OrganizationListSerializer

    def initial(self, request, *args, **kwargs):
        if request.method == 'POST':
            self.permission_classes = (IsAuthenticated, )  # fixme
        self.related_object_type = kwargs.pop('related_object_type', None)
        self.related_object_kwarg = kwargs.pop('related_object_kwarg', None)
        super().initial(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        if request.user.is_staff:  # /orgs
            return self.list(request, *args, **kwargs)

        if self.related_object_type and self.related_object_kwarg:  # /users/(user)/orgs/
            related_object_key = kwargs.pop(self.related_object_kwarg)
            if UserProfile == self.related_object_type:
                userprofile = UserProfile.objects.get(username=related_object_key)
                self.queryset = userprofile.organizations.all()
        elif self.user_is_self:  # /user/orgs/
            self.queryset = request.user.organizations.all()
        else:  # /orgs
            if isinstance(request.user, AnonymousUser):
                self.queryset = self.queryset.filter(~Q(public_access=ACCESS_TYPE_NONE))
            else:
                self.queryset = request.user.organizations.filter(~Q(public_access=ACCESS_TYPE_NONE))

        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if self.related_object_type or self.related_object_kwarg:
            return HttpResponse(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        response = self.create(request, *args, **kwargs)
        return response

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid():
            instance = serializer.save(force_insert=True)
            if serializer.is_valid():
                request.user.organizations.add(instance)
                headers = self.get_success_headers(serializer.data)
                serializer = OrganizationDetailSerializer(instance, context={'request': request})
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class OrganizationBaseView(BaseAPIView, RetrieveAPIView, DestroyAPIView):
    lookup_field = 'org'

    model = Organization
    queryset = Organization.objects.filter(is_active=True)


class OrganizationDetailView(mixins.UpdateModelMixin, OrganizationBaseView):
    serializer_class = OrganizationDetailSerializer
    queryset = Organization.objects.filter(is_active=True)

    def initial(self, request, *args, **kwargs):
        if request.method == 'DELETE':
            self.permission_classes = (IsSuperuser, )
        if request.method == 'POST':
            self.permission_classes = (IsAuthenticated, )  # fixme
        super().initial(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()

        try:
            obj.delete()
        except Exception as ex:
            return Response({'detail': ex.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Successfully deleted org.'}, status=status.HTTP_200_OK)


class OrganizationMemberView(generics.GenericAPIView):
    userprofile = None
    user_in_org = False
    serializer_class = UserDetailSerializer

    def initial(self, request, *args, **kwargs):
        org_id = kwargs.pop('org')
        self.organization = Organization.objects.get(mnemonic=org_id)
        username = kwargs.pop('user')
        try:
            self.userprofile = UserProfile.objects.get(username=username)
        except UserProfile.DoesNotExist:
            pass
        try:
            self.user_in_org = request.user.is_staff or (
                request.user.is_authenticated and self.organization.is_member(request.user)
            )
        except UserProfile.DoesNotExist:
            pass
        super().initial(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        self.initial(request, *args, **kwargs)
        if not self.user_in_org and not request.user.is_staff:
            return Response(status=status.HTTP_403_FORBIDDEN)
        if self.user_in_org:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_404_NOT_FOUND)

    def put(self, request):
        if not request.user.is_staff and not self.user_in_org:
            return Response(status=status.HTTP_403_FORBIDDEN)
        if not self.userprofile:
            return Response(status=status.HTTP_404_NOT_FOUND)

        self.userprofile.organizations.add(self.organization)

        return Response(status=status.HTTP_204_NO_CONTENT)

    def delete(self, request):
        if not request.user.is_staff and not self.user_in_org:
            return Response(status=status.HTTP_403_FORBIDDEN)

        self.userprofile.organizations.remove(self.organization)

        return Response(status=status.HTTP_204_NO_CONTENT)


class OrganizationSourceListView(SourceListView):
    def get_queryset(self):
        user = UserProfile.objects.get(username=self.kwargs.get('user', None))
        return self.queryset.filter(organization__in=user.organizations.all())
