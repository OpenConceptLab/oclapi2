from django.http import Http404
from pydash import compact
from rest_framework.permissions import AllowAny

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

    def get_owner(self):
        owner = self.get_owner_from_kwargs()
        if not owner:
            raise Http404()
        return owner

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
