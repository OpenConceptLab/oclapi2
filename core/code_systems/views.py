import logging

from django.db.models import F

from core.bundles.serializers import FhirBundleSerializer
from core.code_systems.serializers import CodeSystemDetailSerializer
from core.concepts.views import ConceptRetrieveUpdateDestroyView
from core.parameters.serializers import ParametersSerializer
from core.sources.models import Source
from core.sources.views import SourceListView, SourceRetrieveUpdateDestroyView

logger = logging.getLogger('oclapi')


class CodeSystemListView(SourceListView):
    serializer_class = CodeSystemDetailSerializer

    @staticmethod
    def bundle_response(data):
        bundle = FhirBundleSerializer(
            {'meta': {}, 'type': 'searchset', 'entry': FhirBundleSerializer.convert_to_entry(data)})
        return bundle.data

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def apply_query_filters(self, queryset):
        url = self.request.query_params.get('url')
        if url:
            queryset = queryset.filter(canonical_url=url)
        return queryset

    def apply_filters(self, queryset):
        queryset = queryset.exclude(version='HEAD').filter(is_latest_version=True)
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
            source = Source.objects.filter(canonical_url=system, is_latest_version=True).exclude(version='HEAD').first()
            if source:
                queryset = queryset.filter(sources=source, mnemonic=code)

        return queryset

    def get_serializer(self, instance=None):  # pylint: disable=arguments-differ
        if instance:
            return ParametersSerializer.from_concept(instance)
        return ParametersSerializer()


class CodeSystemListValidateCodeView(ConceptRetrieveUpdateDestroyView):
    serializer_class = ParametersSerializer

    def is_container_version_specified(self):
        return True

    def get_queryset(self):
        queryset = super().get_queryset()
        code = self.request.query_params.get('code')
        system = self.request.query_params.get('url')
        display = self.request.query_params.get('display')
        version = self.request.query_params.get('version')
        if code and system:
            source = Source.objects.filter(canonical_url=system)
            if version:
                source = source.filter(version=version)
            else:
                source = source.filter(is_latest_version=True).exclude(version='HEAD')
            if source:
                queryset = queryset.filter(sources=source.first(), mnemonic=code)

        if display:
            instance = queryset.first()
            if display not in (instance.name, instance.display_name):
                return queryset.none()

        return queryset

    def get_object(self, queryset=None):
        queryset = self.get_queryset()
        if not self.is_container_version_specified():
            queryset = queryset.filter(id=F('versioned_object_id'))
        instance = queryset.first()
        if instance:
            self.check_object_permissions(self.request, instance)

        return instance

    def get_serializer(self, instance=None):  # pylint: disable=arguments-differ
        if instance:
            return ParametersSerializer({'parameter': [
                {
                    'name': 'result',
                    'valueBoolean': True
                }
            ]})
        return ParametersSerializer({'parameter': [
            {
                'name': 'result',
                'valueBoolean': False
            },
            {
                'name': 'message',
                'valueString': 'The code is incorrect.'
            }
        ]})


class CodeSystemRetrieveUpdateView(SourceRetrieveUpdateDestroyView):
    serializer_class = CodeSystemDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.exclude(version='HEAD')

    def get_detail_serializer(self, obj):
        return CodeSystemDetailSerializer(obj)
