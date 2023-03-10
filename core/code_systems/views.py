import logging

from django.db.models import F
from rest_framework.exceptions import ValidationError, NotAuthenticated

from core.bundles.serializers import FHIRBundleSerializer
from core.code_systems.serializers import CodeSystemDetailSerializer, \
    ValidateCodeParametersSerializer
from core.common.constants import HEAD
from core.common.fhir_helpers import translate_fhir_query
from core.common.serializers import IdentifierSerializer
from core.concepts.permissions import CanViewParentDictionaryAsGuest
from core.concepts.views import ConceptRetrieveUpdateDestroyView
from core.parameters.serializers import ParametersSerializer
from core.sources.models import Source
from core.sources.views import SourceListView, SourceRetrieveUpdateDestroyView

logger = logging.getLogger('oclapi')


class CodeSystemListView(SourceListView):
    serializer_class = CodeSystemDetailSerializer

    @staticmethod
    def bundle_response(data, paginator):
        bundle = FHIRBundleSerializer(
            {'meta': {}, 'type': 'searchset', 'entry': FHIRBundleSerializer.convert_to_entry(data)},
            context=dict(paginator=paginator)
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


class CodeSystemListLookupView(ConceptRetrieveUpdateDestroyView):
    serializer_class = ParametersSerializer

    def is_container_version_specified(self):
        return True

    def get_queryset(self):
        queryset = super().get_queryset()
        code = self.request.query_params.get('code')
        system = self.request.query_params.get('system')
        if code and system:
            source = Source.objects.filter(canonical_url=system, is_latest_version=True).exclude(version=HEAD).first()
            if not source:
                system = IdentifierSerializer.convert_fhir_url_to_ocl_uri(system, 'sources')
                source = Source.objects.filter(uri=system, is_latest_version=True).exclude(version=HEAD).first()
            if source:
                queryset = queryset.filter(sources=source, mnemonic=code)

        return queryset

    def get_serializer(self, instance=None):  # pylint: disable=arguments-differ
        if instance:
            return ParametersSerializer.from_concept(instance)
        return ParametersSerializer()


class CodeSystemValidateCodeView(ConceptRetrieveUpdateDestroyView):
    serializer_class = ValidateCodeParametersSerializer

    def verify_scope(self):
        pass

    def post(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        # Not supported
        pass

    def is_container_version_specified(self):
        return True

    def get_permissions(self):
        return [CanViewParentDictionaryAsGuest(), ]

    def get_queryset(self):
        queryset = super().get_queryset()

        parameters = self.get_parameters()
        url = parameters.get('url')
        code = parameters.get('code')
        version = parameters.get('version')
        display = parameters.get('display')

        if url:
            source = Source.objects.filter(canonical_url=url)
            if not source:
                url = IdentifierSerializer.convert_fhir_url_to_ocl_uri(url, 'sources')
                source = Source.objects.filter(uri=url)

            if not source:
                return queryset.none()

            if version:
                source = source.filter(version=version)
            else:
                source = source.filter(is_latest_version=True).exclude(version=HEAD)

        if code:
            if not source:
                return queryset.none()
            else:
                queryset = queryset.filter(sources=source.first(), mnemonic=code)

        if display and queryset:
            instance = queryset.first()
            if display not in (instance.name, instance.display_name):
                return queryset.none()

        return queryset

    def get_parameters(self):
        if self.request.method in ['POST', 'PUT']:
            parameters = self.get_serializer(data=self.request.data, instance=None)
        else:
            parameters = self.get_serializer_class().parse_query_params(self.request.query_params)
        if not parameters.is_valid():
            raise ValidationError(parameters.errors)
        params = parameters.validated_data
        params = params.get('parameters', {})
        return params

    def get_object(self, queryset=None):
        queryset = self.get_queryset()
        if not self.is_container_version_specified():
            queryset = queryset.filter(id=F('versioned_object_id'))
        instance = queryset.first()
        if instance:
            try:
                self.check_object_permissions(self.request, instance)
            except NotAuthenticated:
                return {
                    'parameter': [
                        {'name': 'result', 'valueBoolean': False}
                    ]
                }

        if instance:
            return {
                'parameter': [
                    {'name': 'result', 'valueBoolean': True}
                ]
            }

        return {
            'parameter': [
                {'name': 'result', 'valueBoolean': False}
            ]
        }


class CodeSystemRetrieveUpdateView(SourceRetrieveUpdateDestroyView):
    serializer_class = CodeSystemDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.exclude(version=HEAD)

    def get_detail_serializer(self, obj):
        return CodeSystemDetailSerializer(obj)
