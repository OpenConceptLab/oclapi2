from rest_framework import status
from rest_framework.generics import RetrieveAPIView, DestroyAPIView
from rest_framework.response import Response

from core.common.constants import HEAD
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryMixin
from core.common.views import BaseAPIView
from core.concepts.models import Concept
from core.concepts.permissions import CanViewParentDictionary
from core.concepts.serializers import ConceptDetailSerializer, ConceptListSerializer


class ConceptBaseView(BaseAPIView):
    lookup_field = 'concept'
    pk_field = 'mnemonic'
    model = Concept
    permission_classes = (CanViewParentDictionary,)
    queryset = Concept.objects.filter(is_active=True)

    @staticmethod
    def get_detail_serializer(obj, data=None, files=None, partial=False):
        return ConceptDetailSerializer(obj, data, files, partial)

    def get_queryset(self):
        queryset = self.queryset
        source_mnemonic = self.request.query_params.get('source', None) or self.kwargs.get('source', None)

        if source_mnemonic:
            queryset = queryset.filter(parent__mnemonic=source_mnemonic)

        return queryset.filter(version=HEAD)


class ConceptListView(ConceptBaseView, ListWithHeadersMixin):
    serializer_class = ConceptListSerializer

    def get(self, request, *args, **kwargs):
        self.serializer_class = ConceptDetailSerializer if self.is_verbose(request) else ConceptListSerializer
        return self.list(request, *args, **kwargs)


class ConceptRetrieveUpdateDestroyView(ConceptBaseView, RetrieveAPIView, DestroyAPIView):
    serializer_class = ConceptDetailSerializer

    def destroy(self, request, *args, **kwargs):
        concept = self.get_object()
        try:
            concept.delete()
        except Exception as ex:
            return Response({'detail': ex.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Successfully deleted concept.'}, status=status.HTTP_204_NO_CONTENT)


class ConceptVersionsView(ConceptBaseView, ConceptDictionaryMixin, ListWithHeadersMixin):
    serializer_class = ConceptListSerializer
    permission_classes = (CanViewParentDictionary,)

    def get(self, request, *args, **kwargs):
        self.serializer_class = ConceptDetailSerializer if self.is_verbose(request) else ConceptListSerializer
        return self.list(request, *args, **kwargs)
