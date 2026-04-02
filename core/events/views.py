from django.conf import settings
from django.http import Http404
from pydash import compact, get
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.common.mixins import ListWithHeadersMixin
from core.common.permissions import HasOwnership
from core.common.views import BaseAPIView
from core.events.models import Event
from core.events.serializers import EventSerializer


class EventsView(BaseAPIView, ListWithHeadersMixin):
    is_searchable = False
    permission_classes = (AllowAny,)
    default_qs_sort_attr = '-created_at'
    serializer_class = EventSerializer
    owner = None

    def get_owner(self):
        if self.owner:
            return self.owner

        self.owner = self.get_owner_from_kwargs()
        if not self.owner:
            raise Http404()

        return self.owner

    def get_queryset(self):
        owner = self.get_owner()
        return self.apply_permissions(owner, owner.events)

    def should_include_private_events(self, owner):
        return HasOwnership().has_object_permission(self.request, self, owner)

    def apply_permissions(self, owner, queryset):
        if not self.should_include_private_events(owner):
            queryset = queryset.filter(public=True)
        return queryset

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class UserEventsView(EventsView):
    """Events of a User"""
    def get_queryset(self):
        user = self.get_owner()
        scopes = compact(self.request.query_params.get('scopes', '').split(','))
        queryset = Event.objects.none()
        if 'self' in scopes or not scopes:
            queryset = super().get_queryset()
        include_private = self.should_include_private_events(user)
        if 'all' in scopes:
            queryset = queryset.union(Event.get_user_all_events(user, include_private))
        else:
            if 'following' in scopes:
                queryset = queryset.union(Event.get_user_following_events(user, include_private))
            if 'orgs' in scopes:
                queryset = queryset.union(Event.get_user_organization_events(user, include_private))
        return queryset


class GuestEventsView(EventsView):
    def get(self, request, *args, **kwargs):
        if get(self.request, 'user.username'):
            response = Response(status=status.HTTP_303_SEE_OTHER)
            response['URL'] = self.request.user.uri + 'events/'
            return response
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        from core.users.models import UserProfile
        user = UserProfile.objects.filter(username=settings.HIGHLIGHTED_EVENTS_FROM_USERNAME).first()
        events = Event.objects.none()
        if user:
            following_queryset = user.following.exclude(following_type__model='userprofile')
            events = Event.get_events_for_following(
                following_queryset, False, event_type__in=Event.HIGHLIGHT_EVENT_TYPES)
        return events
