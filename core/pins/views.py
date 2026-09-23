from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView, \
    RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from core.common.constants import MAX_PINS_ALLOWED, INCLUDE_CREATOR_PINS
from core.common.permissions import CanViewConceptDictionary
from core.common.utils import get_truthy_values
from core.common.views import BaseAPIView
from core.orgs.models import Organization
from core.pins.models import Pin
from core.pins.permissions import CanEditPins
from core.pins.serializers import PinSerializer, PinUpdateSerializer
from core.users.models import UserProfile


TRUTHY = get_truthy_values()


class PinBaseView(BaseAPIView):
    serializer_class = PinSerializer
    permission_classes = (IsAuthenticatedOrReadOnly, CanEditPins)

    def filter_queryset(self, queryset=None):
        return queryset

    def should_include_creator_pins(self):
        return self.request.query_params.get(INCLUDE_CREATOR_PINS, None) in TRUTHY

    def get_parent_type(self):
        if self.kwargs.get('user_is_self') or 'user' in self.kwargs:
            return 'user'
        if 'org' in self.kwargs:
            return 'organization'
        return None

    def get_parent(self):
        if self.kwargs.get('user_is_self'):
            return self.request.user
        if 'user' in self.kwargs:
            return UserProfile.objects.filter(username=self.kwargs['user']).first()
        if 'org' in self.kwargs:
            return Organization.objects.filter(mnemonic=self.kwargs['org']).first()
        return None

    def get_parent_filter(self):
        if self.kwargs.get('user_is_self'):
            return {'user': self.request.user}
        if 'user' in self.kwargs:
            return {'user__username': self.kwargs['user']}
        if 'org' in self.kwargs:
            return {'organization__mnemonic': self.kwargs['org']}
        return None

    def get_queryset(self):
        filters = self.get_parent_filter()
        criteria = Q(**filters)
        if self.should_include_creator_pins() and 'org' not in self.kwargs:
            criteria |= Q(created_by_id=self.request.user.id, user_id__isnull=True)
        return Pin.objects.filter(
            criteria
        ).select_related('organization', 'user').prefetch_related('resource')


class PinListView(PinBaseView, ListAPIView):
    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        parent = self.get_parent()
        if not parent:
            return Response(status=status.HTTP_404_NOT_FOUND)
        self.check_object_permissions(request, parent)
        if parent.pins.count() >= MAX_PINS_ALLOWED:
            return Response(
                {'error': [f"Can only keep max {MAX_PINS_ALLOWED} items pinned"]},
                status=status.HTTP_404_NOT_FOUND
            )

        # The pin always belongs to the user or org in the URL.
        data = {key: value for key, value in request.data.items() if key not in ['user_id', 'organization_id']}
        serializer = self.get_serializer(
            data={
                **data, self.get_parent_type() + '_id': parent.id, 'created_by_id': self.request.user.id
            }
        )
        if serializer.is_valid():
            resource = Pin.get_resource(
                serializer.validated_data['resource_type'], serializer.validated_data['resource_id'])
            if resource and not CanViewConceptDictionary().has_object_permission(request, self, resource):
                self.permission_denied(request)
            serializer.save()
            if not serializer.errors:
                return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PinRetrieveUpdateDestroyView(PinBaseView, RetrieveUpdateDestroyAPIView):
    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return PinUpdateSerializer

        return PinSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(id=self.kwargs.get('pin_id'))

    def get_object(self, queryset=None):
        pin = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, pin.parent)
        return pin
