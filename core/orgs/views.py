from celery_once import AlreadyQueued
from django.db.models import Count
from django.http import Http404
from drf_yasg.utils import swagger_auto_schema
from pydash import get
from rest_framework import mixins, status, generics
from rest_framework.generics import RetrieveAPIView, DestroyAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from core.client_configs.views import ResourceClientConfigsView
from core.collections.views import CollectionListView
from core.common.constants import NOT_FOUND, MUST_SPECIFY_EXTRA_PARAM_IN_BODY, HEAD
from core.common.mixins import ListWithHeadersMixin
from core.common.permissions import HasPrivateAccess, CanViewConceptDictionary
from core.common.swagger_parameters import org_no_members_param
from core.common.tasks import delete_organization
from core.common.utils import parse_updated_since_param
from core.common.views import BaseAPIView, BaseLogoView
from core.orgs.constants import DELETE_ACCEPTED, NO_MEMBERS
from core.orgs.documents import OrganizationDocument
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
    es_fields = Organization.es_fields
    document_model = OrganizationDocument
    is_searchable = True
    permission_classes = (IsAuthenticatedOrReadOnly, )

    def get_queryset(self):
        username = self.kwargs.get('user')
        no_members = self.request.query_params.get(NO_MEMBERS, False) in ['true', True]

        if not username and self.user_is_self:
            username = get(self.request.user, 'username')

        if username:
            self.queryset = Organization.get_by_username(username)
        elif self.request.user.is_anonymous:
            self.queryset = Organization.get_public()
        elif self.request.user.is_superuser or self.request.user.is_staff:
            self.queryset = Organization.objects.filter(is_active=True)
        else:
            self.queryset = Organization.get_by_username(self.request.user.username) | Organization.get_public()

        updated_since = parse_updated_since_param(self.request.query_params)
        if updated_since:
            self.queryset = self.queryset.filter(updated_at__gte=updated_since)
        if no_members:
            self.queryset = self.queryset.annotate(mem_count=Count('members')).filter(mem_count=0)

        return self.queryset.distinct()

    def get_serializer_class(self):
        if self.request.method == 'GET' and self.is_verbose():
            return OrganizationDetailSerializer
        if self.request.method == 'POST':
            return OrganizationCreateSerializer

        return OrganizationListSerializer

    @swagger_auto_schema(manual_parameters=[org_no_members_param])
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if 'user' in self.kwargs:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        return self.create(request, *args, **kwargs)

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


class OrganizationLogoView(OrganizationBaseView, BaseLogoView):
    serializer_class = OrganizationDetailSerializer

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [HasPrivateAccess(), ]

        return [CanViewConceptDictionary(), ]


class OrganizationDetailView(OrganizationBaseView, mixins.UpdateModelMixin, mixins.CreateModelMixin):
    def get_queryset(self):
        return super().get_queryset().filter(mnemonic=self.kwargs['org'])

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [HasPrivateAccess(), ]

        return [CanViewConceptDictionary(), ]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return OrganizationCreateSerializer  # pragma: no cover

        return OrganizationDetailSerializer

    def put(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    # TODO: should not be needed
    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument  # pragma: no cover
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid():
            instance = serializer.save(force_insert=True)
            if serializer.is_valid():
                request.user.organizations.add(instance)
                headers = self.get_success_headers(serializer.data)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()

        inline = request.query_params.get('inline') in ['true', True]
        if inline:
            delete_organization(obj.id)
            return Response(status=status.HTTP_204_NO_CONTENT)

        try:
            delete_organization.delay(obj.id)
        except AlreadyQueued:  # pragma: no cover
            return Response({'detail': 'Already Queued'}, status=status.HTTP_409_CONFLICT)

        return Response({'detail': DELETE_ACCEPTED}, status=status.HTTP_202_ACCEPTED)


class OrganizationClientConfigsView(ResourceClientConfigsView):
    lookup_field = 'org'
    model = Organization
    queryset = Organization.objects.filter(is_active=True)
    permission_classes = (CanViewConceptDictionary, )


class OrganizationMemberView(generics.GenericAPIView):
    userprofile = None
    user_in_org = False
    serializer_class = UserDetailSerializer

    def initial(self, request, *args, **kwargs):
        org_id = kwargs.pop('org')
        self.organization = Organization.objects.filter(mnemonic=org_id).first()
        if not self.organization:
            return
        username = kwargs.pop('user')
        try:
            self.userprofile = UserProfile.objects.get(username=username)
        except UserProfile.DoesNotExist:
            pass
        try:
            self.user_in_org = request.user.is_staff or (
                request.user.is_authenticated and self.organization.is_member(request.user)
            )
        except UserProfile.DoesNotExist:  # pragma: no cover
            pass
        super().initial(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        self.initial(request, *args, **kwargs)
        if not self.organization:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not self.user_in_org and not request.user.is_staff:
            return Response(status=status.HTTP_403_FORBIDDEN)
        if self.user_in_org:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_404_NOT_FOUND)  # pragma: no cover

    def put(self, request, **kwargs):  # pylint: disable=unused-argument
        if not self.userprofile:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not self.user_in_org:
            return Response(status=status.HTTP_403_FORBIDDEN)

        self.userprofile.organizations.add(self.organization)
        # ES Index
        self.organization.save()
        self.userprofile.save()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def delete(self, request, **kwargs):  # pylint: disable=unused-argument
        if not request.user.is_staff and not self.user_in_org:
            return Response(status=status.HTTP_403_FORBIDDEN)

        self.userprofile.organizations.remove(self.organization)
        # ES Index
        self.organization.save()
        self.userprofile.save()

        return Response(status=status.HTTP_204_NO_CONTENT)


class OrganizationResourceAbstractListView:
    def get_queryset(self):
        username = self.kwargs.get('user', None)
        if not username and self.user_is_self:
            username = get(self.request.user, 'username')

        user = UserProfile.objects.filter(username=username).first()
        if not user:
            raise Http404()

        return self.queryset.filter(organization__in=user.organizations.all(), version=HEAD)


class OrganizationSourceListView(OrganizationResourceAbstractListView, SourceListView):
    pass


class OrganizationCollectionListView(OrganizationResourceAbstractListView, CollectionListView):
    pass


class OrganizationExtrasBaseView(APIView):
    def get_object(self):
        instance = Organization.objects.filter(is_active=True, mnemonic=self.kwargs['org']).first()

        if not instance:
            raise Http404()
        return instance


class OrganizationExtrasView(OrganizationExtrasBaseView):
    serializer_class = OrganizationDetailSerializer

    def get(self, request, org):  # pylint: disable=unused-argument
        return Response(get(self.get_object(), 'extras', {}))


class OrganizationExtraRetrieveUpdateDestroyView(OrganizationExtrasBaseView, RetrieveUpdateDestroyAPIView):
    serializer_class = OrganizationDetailSerializer

    def retrieve(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        instance = self.get_object()
        extras = get(instance, 'extras', {})
        if key in extras:
            return Response({key: extras[key]})

        return Response(dict(detail=NOT_FOUND), status=status.HTTP_404_NOT_FOUND)

    def update(self, request, **kwargs):  # pylint: disable=arguments-differ
        key = kwargs.get('extra')
        value = request.data.get(key)
        if not value:
            return Response([MUST_SPECIFY_EXTRA_PARAM_IN_BODY.format(key)], status=status.HTTP_400_BAD_REQUEST)

        instance = self.get_object()
        instance.extras = get(instance, 'extras', {})
        instance.extras[key] = value
        instance.save()
        return Response({key: value})

    def delete(self, request, *args, **kwargs):
        key = kwargs.get('extra')
        instance = self.get_object()
        instance.extras = get(instance, 'extras', {})
        if key in instance.extras:
            del instance.extras[key]
            instance.save()
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(dict(detail=NOT_FOUND), status=status.HTTP_404_NOT_FOUND)
