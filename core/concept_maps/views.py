import logging

from django.core.exceptions import ValidationError
from django.db.models import Q
from pydash import get

from core.bundles.serializers import FHIRBundleSerializer
from core.common.constants import HEAD
from core.common.fhir_helpers import translate_fhir_query
from core.common.permissions import CanViewConceptDictionary
from core.common.serializers import IdentifierSerializer
from core.concept_maps.constants import RESOURCE_TYPE
from core.concept_maps.serializers import ConceptMapDetailSerializer, ConceptMapParametersSerializer
from core.concepts.permissions import CanAccessParentDictionary
from core.mappings.constants import SAME_AS
from core.mappings.views import MappingListView
from core.sources.views import SourceListView, SourceRetrieveUpdateDestroyView

logger = logging.getLogger('oclapi')


class ConceptMapListView(SourceListView):
    serializer_class = ConceptMapDetailSerializer

    @staticmethod
    def bundle_response(data, paginator):
        bundle = FHIRBundleSerializer(
            {'meta': {}, 'type': 'searchset', 'entry': FHIRBundleSerializer.convert_to_entry(data)},
            context={'paginator': paginator}
        )
        return bundle.data

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def apply_query_filters(self, queryset):
        query_fields = list(self.serializer_class.Meta.fields)
        queryset = translate_fhir_query(query_fields, self.request.query_params, queryset)
        return queryset

    def apply_filters(self, queryset):
        queryset = queryset.exclude(version=HEAD).filter(is_latest_version=True)
        return self.apply_query_filters(queryset)

    def get_serializer_class(self):
        return self.serializer_class

    def get_detail_serializer(self, obj):
        return self.serializer_class(obj)


class ConceptMapRetrieveUpdateView(SourceRetrieveUpdateDestroyView):
    serializer_class = ConceptMapDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method == 'DELETE':
            return queryset  # Delete HEAD with all versions
        return queryset.exclude(version=HEAD)

    def get_detail_serializer(self, obj):
        return ConceptMapDetailSerializer(obj)


class ConceptMapTranslateView(MappingListView):
    serializer_class = ConceptMapParametersSerializer

    def get_permissions(self):
        return [CanAccessParentDictionary(), CanViewConceptDictionary()]

    def get_serializer_class(self):
        return ConceptMapParametersSerializer

    def verify_scope(self):
        """
        Override to not verify scope and allow POSTing for everyone
        :return: None
        """

    def apply_filters(self, queryset):
        if self.request.method in ['POST', 'PUT']:
            parameters = self.get_serializer(data=self.request.data, instance=None)
        else:
            parameters = self.get_serializer_class().parse_query_params(self.request.query_params)

        if not parameters.is_valid():
            raise ValidationError(message=parameters.errors)

        params = parameters.validated_data
        params = params.get('parameters', {})

        url = params.get('url')
        code = params.get('code')
        system = params.get('system')
        targetsystem = params.get('targetsystem')
        # TODO: implement 'source' and 'target'
        if url:
            queryset = queryset.filter(canonical_url=url)
            if not queryset:
                return queryset

        if code:
            queryset = queryset.filter(from_concept_code=code)
            if not queryset:
                return queryset
        if system:
            system_url = IdentifierSerializer.convert_fhir_url_to_ocl_uri(system, 'sources')
            queryset = queryset.filter(Q(from_source__canonical_url=system) |
                                       Q(from_source_url=system_url) |
                                       Q(from_source__uri=system_url))
            if not queryset:
                return queryset
        if targetsystem:
            target_url = IdentifierSerializer.convert_fhir_url_to_ocl_uri(targetsystem, 'sources')
            queryset = queryset.filter(Q(to_source__canonical_url=targetsystem) |
                                       Q(to_source_url=target_url) |
                                       Q(to_source__uri=target_url))
        return queryset

    def get_serializer(self, *args, **kwargs):
        instance = get(args, '0')
        many = kwargs.get('many', False)
        if many:
            if isinstance(instance, list) and len(instance) != 0:
                matches = []
                for mapping in instance:
                    equivalence = mapping.map_type
                    if mapping.map_type == SAME_AS:
                        equivalence = "equivalent"

                    if mapping.to_source and mapping.to_source.canonical_url:
                        to_url = mapping.to_source.canonical_url
                    elif mapping.to_source_url:
                        to_url = IdentifierSerializer.convert_ocl_uri_to_fhir_url(mapping.to_source_url, RESOURCE_TYPE)
                    elif mapping.to_source:
                        to_url = IdentifierSerializer.convert_ocl_uri_to_fhir_url(mapping.to_source.uri, RESOURCE_TYPE)

                    matches.append({
                        'name': 'match',
                        'part': [
                            {
                                'name': 'equivalence',
                                'valueCode': equivalence
                            },
                            {
                                'name': 'concept',
                                'valueCoding': {
                                    'system': to_url,
                                    'code': mapping.to_concept_code,
                                    'userSelected': False
                                }
                            }
                        ]
                    })
                return ConceptMapParametersSerializer({'parameter': [
                        {
                            'name': 'result',
                            'valueBoolean': True
                        },
                        *matches
                   ]})

            return ConceptMapParametersSerializer({'parameter': [
                {
                    'name': 'result',
                    'valueBoolean': False
                }
            ]})
        return super().get_serializer(instance, many, *args, **kwargs)

    # Change POST behavior to get
    def post(self, request, *args, **kwargs):
        """
        Change POST behavior to simply list
        :param request:
        :param args:
        :param kwargs:
        :return: parameters
        """
        return self.get(request, *args, **kwargs)
