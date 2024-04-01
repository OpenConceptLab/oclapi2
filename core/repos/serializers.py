from rest_framework import serializers

from core.collections.models import Collection
from core.collections.serializers import CollectionListSerializer
from core.sources.models import Source
from core.sources.serializers import SourceListSerializer


class RepoListSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    def to_representation(self, instance):
        data = self.get_data(instance)
        if self.context.get('url_registry_entry', None):
            data['url_registry_entry'] = self.context['url_registry_entry'].relative_uri
        return data

    def get_data(self, item):
        repo = None
        if isinstance(item, Source):
            repo = SourceListSerializer(item, context=self.context).data
        elif isinstance(item, Collection):
            repo = CollectionListSerializer(item, context=self.context).data
        return repo
