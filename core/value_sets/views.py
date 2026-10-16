import logging

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.response import Response

from core.bundles.serializers import FHIRBundleSerializer
from core.code_systems.views import CodeSystemValidateCodeView, get_viewable_repo, FhirParameterRequiredError
from core.collections.models import Collection
from core.collections.views import CollectionListView, CollectionRetrieveUpdateDestroyView, \
    CollectionVersionExpansionsView
from core.common.constants import HEAD, LATEST
from core.common.fhir_helpers import translate_fhir_query
from core.concepts.models import Concept
from core.sources.models import Source
from core.value_sets.serializers import ValueSetDetailSerializer, \
    ValueSetExpansionParametersSerializer, ValueSetExpansionSerializer

logger = logging.getLogger('oclapi')


class ValueSetListView(CollectionListView):
    serializer_class = ValueSetDetailSerializer

    @staticmethod
    def bundle_response(data, paginator):
        bundle = FHIRBundleSerializer(
            {'meta': {}, 'total': len(data), 'type': 'searchset', 'entry': FHIRBundleSerializer.convert_to_entry(data)},
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


class ValueSetValidateCodeView(CodeSystemValidateCodeView):
    CONTAINER_PARAMS = ('user', 'org', 'source', 'collection', 'version')

    def get_route_value_set_versions(self):
        """Ids of the route's collection versions the requester can view, narrowed by a `version` query param. The
        owner-less route reads every collection with that id the requester can view."""
        user = self.request.user
        heads = Collection.objects.filter(
            mnemonic=self.kwargs['collection'], version=HEAD).select_related('organization')
        if 'org' in self.kwargs:
            heads = heads.filter(organization__mnemonic=self.kwargs['org'])
        elif 'user' in self.kwargs:
            heads = heads.filter(user__username=self.kwargs['user'])
        version = self.request.query_params.get('version')
        version_ids = []
        for head in heads:
            has_private_access = bool(user.is_authenticated and head.has_parent_edit_access(user))
            if not has_private_access and not head.public_can_view:
                continue
            versions = head.versions.only('id', 'public_access')
            if version == LATEST:
                versions = versions.filter(released=True)[:1]
            elif version:
                versions = versions.filter(version=version)
            version_ids += [repo.id for repo in versions if has_private_access or repo.public_can_view]
        return version_ids

    def get_queryset(self):
        """Reads only the value sets the request names (`url` and the route's collection), never ones other query
        params pick; a value set that doesn't resolve or the requester can't view reads nothing. Without a code, or
        with a code but no system, the request is a 400."""
        params = {key: value for key, value in self.params.items() if key not in self.CONTAINER_PARAMS}
        queryset = Concept.get_base_queryset(params)

        parameters = self.get_parameters()

        url = parameters.get('url')
        code = parameters.get('code')
        system = parameters.get('system')
        display = parameters.get('display')
        system_version = parameters.get('systemVersion')

        if url:
            collection = get_viewable_repo(
                Collection.objects.filter(canonical_url=url, is_latest_version=True).exclude(version=HEAD),
                self.request.user)
            if not collection:
                return queryset.none()
            queryset = queryset.filter(references__collection=collection)

        if 'collection' in self.kwargs:
            version_ids = self.get_route_value_set_versions()
            if not version_ids:
                return queryset.none()
            queryset = queryset.filter(expansion_set__collection_version__in=version_ids)

        if not code:
            raise FhirParameterRequiredError('A code to validate is required.', expression='code')
        if not system:
            raise FhirParameterRequiredError(
                'system is required with a code: the code system the code is from.', expression='system')

        concept_source = Source.objects.filter(canonical_url=system)
        if system_version:
            concept_source = concept_source.filter(version=system_version)
        else:
            concept_source = concept_source.filter(is_latest_version=True).exclude(version=HEAD)
        concept_source = get_viewable_repo(concept_source, self.request.user)
        if not concept_source:
            return queryset.none()
        queryset = queryset.filter(sources=concept_source, mnemonic=code)

        if display:
            instance = queryset.first()
            if not instance or display not in (instance.name, instance.display_name):
                return queryset.none()

        return queryset


class ValueSetRetrieveUpdateView(CollectionRetrieveUpdateDestroyView):
    serializer_class = ValueSetDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method == 'DELETE':
            return queryset  # Delete HEAD with all versions

        return queryset.exclude(version=HEAD)

    def get_detail_serializer(self, obj):
        return ValueSetDetailSerializer(obj)


class ValueSetExpandView(CollectionVersionExpansionsView):
    sync = True

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ValueSetExpansionParametersSerializer

        return ValueSetExpansionSerializer

    def get_response_serializer_class(self):
        return ValueSetExpansionSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_base_queryset(self):
        queryset = super().get_base_queryset()
        queryset = queryset.exclude(version=HEAD).filter(is_latest_version=True)[:1]
        return queryset

    def get_queryset(self):
        qs = super().get_queryset()

        if self.request.method == 'GET':
            parameters = ValueSetExpansionParametersSerializer.parse_query_params(self.request.query_params)
            if not parameters.is_valid():
                raise ValidationError(message=parameters.errors)

            params = parameters.validated_data
            params = params.get('parameters', {})
            qs = qs.filter(parameters=params if params else {}).order_by('-id')

        return qs

    def get(self, request, *args, **kwargs):
        instance = self.get_queryset().first()
        if instance:
            serializer = self.get_serializer(instance)
            return Response(serializer.data)

        return Response(status=status.HTTP_404_NOT_FOUND)
