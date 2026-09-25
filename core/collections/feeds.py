from core.collections.models import Collection
from core.common.feeds import ConceptContainerFeed


class CollectionFeed(ConceptContainerFeed):
    model = Collection
    entity_name = 'Collection'

    def items(self, obj):
        return self.filter_queryset(obj.get_concepts_queryset())
