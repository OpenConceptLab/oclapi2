from rest_framework import serializers

from core.collections.models import Collection
from core.collections.serializers import CollectionListSerializer
from core.sources.models import Source
from core.sources.serializers import SourceListSerializer


class RepoListSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    def to_representation(self, instance):
        return self.get_data(instance)

    def get_data(self, item):
        if isinstance(item, Source):
            return SourceListSerializer(item, context=self.context).data
        if isinstance(item, Collection):
            return CollectionListSerializer(item, context=self.context).data
        return None
