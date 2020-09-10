from django.http import Http404
from pydash import get
from rest_framework import mixins, status, generics
from rest_framework.generics import RetrieveAPIView, DestroyAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from core.collections.views import CollectionListView
from core.common.mixins import ListWithHeadersMixin
from core.common.permissions import HasPrivateAccess
from core.common.views import BaseAPIView
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
    es_fields = {
        'name': {'sortable': True, 'filterable': True, 'exact': True},
        'last_update': {'sortable': True, 'default': 'desc', 'filterable': True},
        'company': {'sortable': False, 'filterable': True, 'exact': True},
        'location': {'sortable': False, 'filterable': True, 'exact': True},
    }
    document_model = OrganizationDocument
    is_searchable = True
    permission_classes = (IsAuthenticatedOrReadOnly, )

    def get_queryset(self):
        username = self.kwargs.get('user')
        if username:
            return Organization.get_by_username(username)
        if self.request.user.is_anonymous:
            return Organization.get_public()
        if self.request.user.is_superuser or self.request.user.is_staff:
            return Organization.objects.filter(is_active=True)

        queryset = Organization.get_by_username(self.request.user.username) | Organization.get_public()
        return queryset.distinct()

    def get_serializer_class(self):
        if self.request.method == 'GET' and self.is_verbose(self.request):
            return OrganizationDetailSerializer
        if self.request.method == 'POST':
            return OrganizationCreateSerializer

        return OrganizationListSerializer

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


class OrganizationDetailView(OrganizationBaseView, mixins.UpdateModelMixin, mixins.CreateModelMixin):
    queryset = Organization.objects.filter(is_active=True)

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [HasPrivateAccess(), ]

        return [IsAuthenticated(), ]

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
                serializer = OrganizationDetailSerializer(instance, context={'request': request})
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()

        try:
            obj.delete()
        except Exception as ex:  # pragma: no cover
            return Response({'detail': ex.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Successfully deleted org.'}, status=status.HTTP_204_NO_CONTENT)


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
        if not request.user.is_staff:
            return Response(status=status.HTTP_403_FORBIDDEN)
        if not self.userprofile:
            return Response(status=status.HTTP_404_NOT_FOUND)

        self.userprofile.organizations.add(self.organization)

        return Response(status=status.HTTP_204_NO_CONTENT)

    def delete(self, request, **kwargs):  # pylint: disable=unused-argument
        if not request.user.is_staff and not self.user_in_org:
            return Response(status=status.HTTP_403_FORBIDDEN)

        self.userprofile.organizations.remove(self.organization)

        return Response(status=status.HTTP_204_NO_CONTENT)


class OrganizationSourceListView(SourceListView):  # pragma: no cover
    def get_queryset(self):
        user = UserProfile.objects.get(username=self.kwargs.get('user', None))
        return self.queryset.filter(organization__in=user.organizations.all())


class OrganizationCollectionListView(CollectionListView):  # pragma: no cover
    def get_queryset(self):
        user = UserProfile.objects.get(username=self.kwargs.get('user', None))
        return self.queryset.filter(organization__in=user.organizations.all())


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

        return Response(dict(detail='Not found.'), status=status.HTTP_404_NOT_FOUND)

    def update(self, request, **kwargs):
        key = kwargs.get('extra')
        value = request.data.get(key)
        if not value:
            return Response(['Must specify %s param in body.' % key], status=status.HTTP_400_BAD_REQUEST)

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

        return Response(dict(detail='Not found.'), status=status.HTTP_404_NOT_FOUND)
