import dateutil.parser
from django.contrib.syndication.views import Feed
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.feedgenerator import Atom1Feed

from core.common.constants import HEAD
from core.common.utils import reverse_resource
from core.orgs.models import Organization
from core.users.models import UserProfile

DEFAULT_LIMIT = 30
MAX_LIMIT = 1000


class FeedFilterMixin:
    def filter_queryset(self, queryset):
        if self.updated_since:
            updated_since_date = dateutil.parser.parse(self.updated_since)
            queryset = queryset.filter(updated_at__gte=updated_since_date)
        queryset = queryset.order_by('-updated_at')
        if self.limit is None:
            return queryset[:DEFAULT_LIMIT]
        limit = int(self.limit)
        return queryset[:limit if 0 < limit < MAX_LIMIT else MAX_LIMIT]


class ConceptContainerFeed(Feed, FeedFilterMixin):
    feed_type = Atom1Feed
    user = None
    org = None
    updated_since = None
    limit = 0
    entity_name = ''

    def get_object(self, request, *args, **kwargs):
        username = kwargs.get('user')
        org_mnemonic = kwargs.get('org')

        self.user = UserProfile.objects.filter(username=username).first() if username else None
        self.org = Organization.objects.filter(mnemonic=org_mnemonic).first() if org_mnemonic else None

        if not (self.user or self.org):
            raise Http404(f"{self.entity_name} owner does not exist")

        mnemonic = kwargs.get(self.entity_name.lower())
        self.updated_since = request.GET.get('updated_since', None)
        self.limit = request.GET.get('limit', None)

        obj = get_object_or_404(self.model, mnemonic=mnemonic, user=self.user, organization=self.org, version=HEAD)
        # feeds are served without authentication, so only public repos have one
        if not obj.public_can_view:
            raise Http404(f"{self.entity_name} does not exist")
        return obj

    def title(self, obj):
        return f"Updates to {obj.mnemonic}"

    def link(self, obj):
        return reverse_resource(obj, f'{self.entity_name.lower()}-detail')

    def description(self, obj):
        return f"Updates to concepts within {self.entity_name.lower()} {obj.mnemonic}"

    def item_title(self, item):
        return item.mnemonic

    def item_description(self, item):
        return item.display_name

    def item_link(self, item):
        return reverse_resource(item, 'concept-detail')
