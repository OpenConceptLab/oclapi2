import logging

from django.http import Http404

from core.common.constants import HEAD
from core.common.permissions import HasOwnership
from core.concept_maps.serializers import ConceptMapDetailSerializer
from core.fhir.adapters import r4
from core.fhir.base import FhirViewMixin, FhirOperationView
from core.fhir.ops.translate import translate
from core.fhir.search import FhirSearchMixin, FhirReadMixin
from core.sources.views import SourceListView, SourceRetrieveUpdateDestroyView

logger = logging.getLogger('oclapi')


class ConceptMapListView(FhirViewMixin, FhirSearchMixin, SourceListView):
    resource_type = 'ConceptMap'
    serializer_class = ConceptMapDetailSerializer

    def get_serializer_class(self):
        return self.serializer_class

    def get_detail_serializer(self, obj):
        return self.serializer_class(obj, context=self.get_serializer_context())


class ConceptMapRetrieveUpdateView(FhirViewMixin, FhirReadMixin, SourceRetrieveUpdateDestroyView):
    resource_type = 'ConceptMap'
    serializer_class = ConceptMapDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method == 'DELETE':
            return queryset  # Delete xHEAD with all versions
        return queryset.exclude(version=HEAD)

    def get_object(self, queryset=None):
        """
        A PUT or PATCH to a ConceptMap that doesn't exist gets None, which creates it, so it needs what a POST needs:
        staff, the owner, or a member of the owning org (HasOwnership on the owner). Anyone else is refused as they are
        for an existing one. Any other request for one is a 404 (a read's from FhirReadMixin).
        """
        try:
            return super().get_object(queryset)
        except Http404:
            if self.request.method not in ('PUT', 'PATCH'):
                raise
            if not HasOwnership().has_object_permission(self.request, self, self.parent_resource):
                self.permission_denied(self.request)
            return None

    def get_detail_serializer(self, obj):
        return ConceptMapDetailSerializer(obj, context=self.get_serializer_context())


class ConceptMapTranslateView(FhirOperationView):
    """ConceptMap/$translate, GET and POST, at the global base and at owner bases: core.fhir.ops.translate, in R4."""
    operation = staticmethod(translate)
    adapter = staticmethod(r4.translate_parameters)
