import logging

from core.code_systems.serializers import CodeSystemDetailSerializer
from core.sources.views import SourceLatestVersionRetrieveUpdateView, SourceListView

logger = logging.getLogger('oclapi')

class CodeSystemListView(SourceListView):
    serializer_class = CodeSystemDetailSerializer

    # pylint: disable=R0201
    def should_bundle_result(self):
        return True

    def apply_filters(self, queryset):
        url = self.request.query_params.get('url')
        if url:
            queryset = queryset.filter(canonical_url=url)
        return queryset

    def get_serializer_class(self):
        return self.serializer_class

class CodeSystemRetrieveUpdateView(SourceLatestVersionRetrieveUpdateView):
    serializer_class = CodeSystemDetailSerializer

    def update(self, request, *args, **kwargs):
        raise NotImplementedError()
