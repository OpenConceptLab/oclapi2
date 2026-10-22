import logging

from django.db.models import F
from rest_framework.exceptions import ValidationError, NotAuthenticated, PermissionDenied

from core.code_systems.serializers import CodeSystemDetailSerializer, \
    ValidateCodeParametersSerializer
from core.common.constants import HEAD
from core.common.serializers import IdentifierSerializer
from core.concepts.permissions import CanViewParentDictionaryAsGuest
from core.concepts.views import ConceptRetrieveUpdateDestroyView
from core.fhir.base import FhirViewMixin
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM
from core.fhir.search import FhirSearchMixin, FhirReadMixin
from core.parameters.serializers import ParametersSerializer
from core.sources.models import Source
from core.sources.views import SourceListView, SourceRetrieveUpdateDestroyView

logger = logging.getLogger('oclapi')


def get_viewable_repo(repos, user):
    """The first of these repo versions the user can view, skipping the rest as if they didn't exist. Its HEAD,
    which governs access to its concepts, must be viewable too."""
    for repo in repos.order_by('id'):
        head = repo.head
        if head and repo.has_view_access(user) and head.has_view_access(user):
            return repo
    return None


class CodeSystemListView(FhirViewMixin, FhirSearchMixin, SourceListView):
    resource_type = 'CodeSystem'
    serializer_class = CodeSystemDetailSerializer

    def get_serializer_class(self):
        return self.serializer_class

    def get_detail_serializer(self, obj):
        return self.serializer_class(obj)


class CodeSystemLookupNotFoundError(ValidationError):
    def __init__(self, code: str = None):
        message = f"Code '{code}' not found" if code else "Code not found"
        super().__init__(
            detail={
                "resourceType": "OperationOutcome",
                "id": "exception",
                "text": {
                    "status": "additional",
                    "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{message}</div>',
                },
                "issue": [
                    {
                        "severity": "error",
                        "code": "not-found",
                        "details": {"coding": [{"system": TX_ISSUE_TYPE_SYSTEM, "code": "not-found"}], "text": message},
                    }
                ],
            }
        )


class CodeSystemListLookupView(FhirViewMixin, ConceptRetrieveUpdateDestroyView):
    serializer_class = ParametersSerializer

    def is_container_version_specified(self):
        return True

    def verify_scope(self):
        pass

    def get_queryset(self):
        queryset = super().get_queryset()
        code = self.request.query_params.get('code')
        system = self.request.query_params.get('system')
        version = self.request.query_params.get('version')
        if code and system:
            source = Source.objects.filter(canonical_url=system)
            if not source:
                system = IdentifierSerializer.convert_fhir_url_to_ocl_uri(system, 'sources')
                source = Source.objects.filter(uri=system)
            if source:
                if version:
                    source = source.filter(version=version)
                else:
                    source = source.filter(is_latest_version=True).exclude(version=HEAD)

                source = get_viewable_repo(source, self.request.user)
                if source:
                    queryset = queryset.filter(sources=source, mnemonic=code)
                    if not queryset.exists():
                        raise CodeSystemLookupNotFoundError(code)
                    return queryset

        raise CodeSystemLookupNotFoundError()

    def get_object(self, queryset=None):
        """A code the requester can't view gives the same answer as a code system that doesn't exist."""
        try:
            return super().get_object(queryset)
        except (NotAuthenticated, PermissionDenied) as ex:
            raise CodeSystemLookupNotFoundError() from ex

    def get_serializer(self, instance=None):  # pylint: disable=arguments-differ
        if instance:
            return ParametersSerializer.from_concept(instance)
        return None


class CodeSystemValidateCodeView(FhirViewMixin, ConceptRetrieveUpdateDestroyView):
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
        """The concept with the code in the code system `url` names. A code system that doesn't resolve, or that the
        requester can't view, reads nothing; without a code, or with a code but no url, the request is a 400."""
        queryset = super().get_queryset()

        parameters = self.get_parameters()
        url = parameters.get('url')
        code = parameters.get('code')
        version = parameters.get('version')
        display = parameters.get('display')
        source = None
        if url:
            source = Source.objects.filter(canonical_url=url)
            if not source:
                url = IdentifierSerializer.convert_fhir_url_to_ocl_uri(url, 'sources')
                source = Source.objects.filter(uri=url)

            if version:
                source = source.filter(version=version)
            else:
                source = source.filter(is_latest_version=True).exclude(version=HEAD)
            source = get_viewable_repo(source, self.request.user)
            if not source:
                return queryset.none()

        if not code:
            raise FhirParameterRequiredError('A code to validate is required.', expression='code')
        if not source:
            raise FhirParameterRequiredError(
                'url is required with a code: the code system to validate it in.', expression='url')

        queryset = queryset.filter(sources=source, mnemonic=code)

        if display:
            instance = queryset.first()
            if not instance or display not in (instance.name, instance.display_name):
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
            except (NotAuthenticated, PermissionDenied):
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


class CodeSystemRetrieveUpdateView(FhirViewMixin, FhirReadMixin, SourceRetrieveUpdateDestroyView):
    resource_type = 'CodeSystem'
    serializer_class = CodeSystemDetailSerializer

    def get_filter_params(self, default_version_to_head=True):
        return super().get_filter_params(False)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method == 'DELETE':
            return queryset  # Delete HEAD with all versions
        return queryset.exclude(version=HEAD)

    def get_detail_serializer(self, obj):
        return CodeSystemDetailSerializer(obj)
