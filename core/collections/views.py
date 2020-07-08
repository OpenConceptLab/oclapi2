from core.collections.models import Collection
from core.common.constants import HEAD
from core.common.permissions import CanViewConceptDictionary
from core.common.views import BaseAPIView


class CollectionBaseView(BaseAPIView):
    lookup_field = 'collection'
    pk_field = 'mnemonic'
    model = Collection
    permission_classes = (CanViewConceptDictionary,)
    queryset = Collection.objects.filter(is_active=True)

    # @staticmethod
    # def get_detail_serializer(obj):
    #     return CollectionDetailSerializer(obj)

    def get_queryset(self):
        query_params = self.request.query_params

        username = query_params.get('user', None) or self.kwargs.get('user', None)
        org = query_params.get('org', None) or self.kwargs.get('org', None)
        version = self.kwargs.get('version', HEAD)
        queryset = self.queryset.filter(version=version)

        if username:
            queryset = queryset.filter(user__username=username)
        if org:
            queryset = queryset.filter(organization__mnemonic=org)
        if 'collection' in self.kwargs:
            queryset = queryset.filter(mnemonic=self.kwargs['collection'])
        if 'is_latest' in self.kwargs:
            queryset = queryset.filter(is_latest_version=True)

        return queryset.all()


class CollectionListView(CollectionBaseView):
    pass


class CollectionRetrieveUpdateDestroyView(CollectionBaseView):
    pass


class CollectionVersionRetrieveUpdateDestroyView(CollectionBaseView):
    pass
