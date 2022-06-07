from django.contrib.syndication.views import Feed
from django.http import Http404
from django.utils.feedgenerator import Atom1Feed

from core.common.feeds import FeedFilterMixin
from core.concepts.models import Concept


class ConceptFeed(Feed, FeedFilterMixin):
    feed_type = Atom1Feed
    user = None
    org = None
    source = None
    updated_since = None
    limit = 0

    def get_object(self, request, *args, **kwargs):
        concept = Concept.get_base_queryset(kwargs).first()
        if not concept:
            raise Http404()

        self.source = concept.parent
        self.updated_since = request.GET.get('updated_since', None)
        self.limit = request.GET.get('limit', None)
        return concept

    def title(self, obj):
        return f"Updates to {self.source.mnemonic}:{obj.mnemonic}"

    def link(self, obj):
        return obj.url

    def description(self, obj):
        return f"Updates to concept {obj.mnemonic} in source {self.source.mnemonic}"

    def items(self, obj):
        return self.filter_queryset(Concept.objects.filter(versioned_object_id=obj.id))

    def item_author_name(self, item):
        return item.created_by.username

    def item_title(self, item):
        return item.mnemonic

    def item_description(self, item):
        return item.comment

    def item_link(self, item):
        return item.url

    def item_pubdate(self, item):
        return item.created_at
