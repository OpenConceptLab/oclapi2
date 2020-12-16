from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import RetrieveAPIView, DestroyAPIView
from rest_framework.response import Response

from core.common.constants import MAX_PINS_ALLOWED
from core.common.permissions import CanViewConceptDictionary
from core.common.views import BaseAPIView
from core.orgs.models import Organization
from core.pins.models import Pin
from core.pins.serializers import PinSerializer
from core.users.models import UserProfile


class PinBaseView(BaseAPIView):
    serializer_class = PinSerializer
    permission_classes = (CanViewConceptDictionary,)

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
        return Pin.objects.filter(
            **self.get_parent_filter()
        ).select_related('organization', 'user').prefetch_related('resource')


class PinListView(PinBaseView):
    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        return Response(self.get_serializer(self.get_queryset(), many=True).data)

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        parent = self.get_parent()
        if not parent:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if parent.pins.count() >= MAX_PINS_ALLOWED:
            return Response(
                dict(error=["Can only keep max {} items pinned".format(MAX_PINS_ALLOWED)]),
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = self.get_serializer(data={**request.data, self.get_parent_type() + '_id': parent.id})
        if serializer.is_valid():
            serializer.save()
            if not serializer.errors:
                return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PinRetrieveDestroyView(PinBaseView, RetrieveAPIView, DestroyAPIView):
    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(id=self.kwargs.get('pin_id'))

    def get_object(self, queryset=None):
        return get_object_or_404(self.get_queryset())
