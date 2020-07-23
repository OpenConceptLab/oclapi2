from core.common.feeds import ConceptContainerFeed
from core.sources.models import Source


class SourceFeed(ConceptContainerFeed):
    model = Source
    entity_name = 'Source'

    def link(self, obj):
        return obj.url

    def items(self, obj):
        return self.filter_queryset(obj.concepts_set)

    def item_description(self, item):
        item = item.get_latest_version()
        return item.update_comment

    def item_link(self, item):
        return item.url
