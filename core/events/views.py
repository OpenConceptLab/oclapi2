from django.http import Http404
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

    def get_queryset(self):
        owner = self.get_owner_from_kwargs()
        if not owner:
            raise Http404()

        queryset = Event.objects.filter(object_url=owner.uri)

        permission = HasOwnership()
        if not permission.has_object_permission(self.request, self, owner):
            queryset = queryset.filter(public=True)

        return queryset

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)
