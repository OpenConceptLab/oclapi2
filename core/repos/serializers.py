from rest_framework import serializers

from core.collections.models import Collection
from core.collections.serializers import CollectionListSerializer
from core.sources.models import Source
from core.sources.serializers import SourceListSerializer


class RepoListSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    def to_representation(self, instance):
        return self.get_data(instance)

    def get_data(self, item):
        repo = None
        if isinstance(item, Source):
            repo = SourceListSerializer(item, context=self.context).data
        elif isinstance(item, Collection):
            repo = CollectionListSerializer(item, context=self.context).data
        return repo
