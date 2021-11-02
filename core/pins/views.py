from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView, \
    RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from core.common.constants import MAX_PINS_ALLOWED, INCLUDE_CREATOR_PINS
from core.common.views import BaseAPIView
from core.orgs.models import Organization
from core.pins.models import Pin
from core.pins.serializers import PinSerializer, PinUpdateSerializer
from core.users.models import UserProfile


class PinBaseView(BaseAPIView):
    serializer_class = PinSerializer
    permission_classes = (IsAuthenticatedOrReadOnly,)

    def filter_queryset(self, queryset):
        return queryset

    def should_include_creator_pins(self):
        return self.request.query_params.get(INCLUDE_CREATOR_PINS, None) in ['true', True]

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
            return dict(user=self.request.user)
        if 'user' in self.kwargs:
            return dict(user__username=self.kwargs['user'])
        if 'org' in self.kwargs:
            return dict(organization__mnemonic=self.kwargs['org'])
        return None

    def get_queryset(self):
        filters = self.get_parent_filter()
        criteria = Q(**filters)
        if self.should_include_creator_pins() and 'org' not in self.kwargs:
            criteria |= Q(created_by_id=self.request.user.id)
        return Pin.objects.filter(
            criteria
        ).select_related('organization', 'user').prefetch_related('resource')


class PinListView(PinBaseView, ListAPIView):
    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        parent = self.get_parent()
        if not parent:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if parent.pins.count() >= MAX_PINS_ALLOWED:
            return Response(
                dict(error=[f"Can only keep max {MAX_PINS_ALLOWED} items pinned"]),
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = self.get_serializer(
            data={
                **request.data, self.get_parent_type() + '_id': parent.id, 'created_by_id': self.request.user.id
            }
        )
        if serializer.is_valid():
            serializer.save()
            if not serializer.errors:
                return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PinRetrieveUpdateDestroyView(PinBaseView, RetrieveUpdateDestroyAPIView):
    def get_serializer_class(self):
        if self.request.method == 'PUT':
            return PinUpdateSerializer

        return PinSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(id=self.kwargs.get('pin_id'))

    def get_object(self, queryset=None):
        return get_object_or_404(self.get_queryset())
