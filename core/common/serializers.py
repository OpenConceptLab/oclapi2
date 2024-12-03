from pydash import get
from rest_framework.fields import CharField, JSONField, SerializerMethodField, FloatField
from rest_framework.serializers import Serializer, Field, ValidationError, ModelSerializer

from core import settings
from core.code_systems.constants import RESOURCE_TYPE as CODE_SYSTEM_RESOURCE_TYPE
from core.common.constants import INCLUDE_CONCEPTS_PARAM, INCLUDE_MAPPINGS_PARAM, LIMIT_PARAM, OFFSET_PARAM, \
    INCLUDE_VERBOSE_REFERENCES, INCLUDE_SEARCH_META_PARAM
from core.common.feeds import DEFAULT_LIMIT
from core.common.utils import to_int, get_truthy_values
from core.concept_maps.constants import RESOURCE_TYPE as CONCEPT_MAP_RESOURCE_TYPE
from core.orgs.models import Organization
from core.users.models import UserProfile
from core.value_sets.constants import RESOURCE_TYPE as VALUESET_RESOURCE_TYPE


TRUTHY = get_truthy_values()


class RootSerializer(Serializer):  # pylint: disable=abstract-method
    version = CharField()
    routes = JSONField()


class TaskSerializer(Serializer):  # pylint: disable=abstract-method
    pass


class ReadSerializerMixin:
    """ Mixin for serializer which does not update or create resources. """

    def update(self, instance, validated_data):
        pass

    def create(self, validated_data):
        pass


class StatusField(Field):

    def to_internal_value(self, data):
        return {'retired': data == 'retired', 'released': data == 'active'}

    def to_representation(self, value):
        if value.retired:
            return 'retired'
        if value.released:
            return 'active'
        return 'draft'


class IdentifierTypeCodingSerializer(ReadSerializerMixin, Serializer):
    system = CharField(default='http://hl7.org/fhir/v2/0203', required=False)
    code = CharField(default='ACSN', required=False)
    display = CharField(default='Accession ID', required=False)


class IdentifierTypeSerializer(ReadSerializerMixin, Serializer):
    text = CharField(default='Accession ID', required=False)
    coding = IdentifierTypeCodingSerializer(many=True, required=False)


class IdentifierSerializer(ReadSerializerMixin, Serializer):
    system = CharField(default=settings.API_BASE_URL, required=False)
    value = CharField(required=False)
    type = IdentifierTypeSerializer(required=False)

    @staticmethod
    def find_ocl_identifier(identifiers):
        found = None
        for ident in identifiers:
            if isinstance(ident.get('type', {}).get('coding', None), list):
                codings = ident['type']['coding']
                for coding in codings:
                    if coding.get('code', None) == 'ACSN' and \
                            coding.get('system', None) == 'http://hl7.org/fhir/v2/0203':
                        found = ident.get('value', None)
                        if found:
                            break
                if found:
                    break
        return found

    @staticmethod
    def parse_identifier(accession_id):
        id_parts = accession_id.strip().strip('/').split('/')
        if len(id_parts) < 4:
            raise ValidationError(
                'Identifier must be in a format: /{owner_type}/{owner_id}/{resourceType}/{resource_id}/, given: '
                + accession_id)

        identifier = {
            'owner_type': id_parts[0], 'owner_id': id_parts[1], 'resource_type': id_parts[2], 'resource_id': id_parts[3]
        }
        return identifier

    @staticmethod
    def convert_ocl_uri_to_fhir_url(uri, resource_type):
        resource_type_uri = f"/{resource_type}/"
        if uri and (uri.startswith('/orgs/') or uri.startswith('/users/')):
            # Recognize OCL API relative URI
            uri = uri.replace('/sources/', resource_type_uri).replace('/collections/', resource_type_uri)
            uri = uri.strip('/')
            parts = uri.split('/')
            if len(parts) < 4:
                raise ValidationError(
                    'Identifier must be in a format: /{owner_type}/{owner_id}/{resourceType}/{resource_id}/, given: '
                    + uri)
            uri = '/' + '/'.join(parts[:4]) + '/'
        return uri

    @staticmethod
    def convert_fhir_url_to_ocl_uri(uri, resource_type):
        resource_type_uri = f"/{resource_type}/"
        if uri and (uri.startswith('/orgs/') or uri.startswith('/users/')):
            # Recognize OCL FHIR relative URI
            uri = uri.replace('/ConceptMap/', resource_type_uri).replace('/CodeSystem/', resource_type_uri) \
                .replace('/ValueSet/', resource_type_uri)
        return uri

    @staticmethod
    def include_ocl_identifier(uri, resource_type, rep):
        """ Add OCL identifier if not present """
        if 'identifier' not in rep:
            rep['identifier'] = []
        ident = IdentifierSerializer.find_ocl_identifier(rep['identifier'])
        if not ident:
            ident = IdentifierSerializer.convert_ocl_uri_to_fhir_url(uri, resource_type)
            rep['identifier'].append({
                'system': settings.API_BASE_URL,
                'value': ident,
                'type': {
                    'text': 'Accession ID',
                    'coding': [{
                        'system': 'http://hl7.org/fhir/v2/0203',
                        'code': 'ACSN',
                        'display': 'Accession ID'
                    }]
                }
            })
        return IdentifierSerializer.parse_identifier(ident)

    @staticmethod
    def validate_identifier(value):
        accession_id = IdentifierSerializer.find_ocl_identifier(value)
        if accession_id:
            identifier = IdentifierSerializer.parse_identifier(accession_id)
            if identifier['owner_type'] not in ['users', 'orgs']:
                raise ValidationError(
                    f"Owner type='{identifier['owner_type']}' is invalid. It must be 'users' or 'orgs'")
            if identifier['resource_type'] not in [CODE_SYSTEM_RESOURCE_TYPE, VALUESET_RESOURCE_TYPE,
                                                   CONCEPT_MAP_RESOURCE_TYPE]:
                raise ValidationError(
                    f"Resource type='{identifier['resource_type']}' is invalid. "
                    f"It must be '{CODE_SYSTEM_RESOURCE_TYPE}' or '{VALUESET_RESOURCE_TYPE}' or "
                    f"'{CONCEPT_MAP_RESOURCE_TYPE}'"
                )
            if identifier['owner_type'] == 'users':
                owner_exists = UserProfile.objects.filter(username=identifier['owner_id']).exists()
            else:
                owner_exists = Organization.objects.filter(mnemonic=identifier['owner_id']).exists()
            if not owner_exists:
                raise ValidationError(
                    f"Owner of type='{identifier['owner_type']}' and id='{identifier['owner_id']}' not found.")
        else:
            raise ValidationError("OCL accession identifier is required: { "
                                              "'value': '/{owner_type}/{owner_id}/{resourceType}/{resource_id}' }, "
                                              "'type': {'coding': [{'code': 'ACSN', "
                                              "'system': 'http://hl7.org/fhir/v2/0203'}]}}")
        return value


class SearchResultSerializer(Serializer):  # pylint: disable=abstract-method
    search_score = FloatField(source='_score', allow_null=True)
    search_confidence = CharField(source='_confidence', allow_null=True, allow_blank=True)
    search_highlight = SerializerMethodField()

    class Meta:
        fields = ('search_score', 'search_confidence', 'search_highlight')

    @staticmethod
    def get_search_highlight(obj):
        highlight = get(obj, '_highlight')
        cleaned_highlight = {}
        if highlight:
            for attr, value in highlight.items():
                cleaned_highlight[attr] = value
        return cleaned_highlight


class AbstractResourceSerializer(ModelSerializer):
    search_meta = SerializerMethodField()

    class Meta:
        abstract = True
        fields = ('search_meta',)

    def __init__(self, *args, **kwargs):  # pylint: disable=too-many-branches
        request = get(kwargs, 'context.request')
        params = get(request, 'query_params')
        self.query_params = (params or {}) if isinstance(params, dict) else (params.dict() if params else {})
        is_csv = self.query_params.get('csv', False)
        self.include_search_meta = (
                                           self.query_params.get(
                                               INCLUDE_SEARCH_META_PARAM) in TRUTHY and self.query_params.get('q')
                                   ) or is_csv or (get(request, 'path') and '/concepts/$match' in request.path)

        try:
            if not self.include_search_meta:
                self.fields.pop('search_meta', None)
            if is_csv:
                self.fields.pop('checksums', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    def get_search_meta(self, obj):
        if self.include_search_meta:
            return SearchResultSerializer(obj).data
        return None


class AbstractRepoResourcesSerializer(AbstractResourceSerializer):
    concepts = SerializerMethodField()
    mappings = SerializerMethodField()
    references = SerializerMethodField()

    class Meta:
        abstract = True
        fields = AbstractResourceSerializer.Meta.fields + ('concepts', 'mappings', 'references')

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')

        self.query_params = {}
        self.include_concepts = False
        self.include_mappings = False
        self.include_references = False
        if params:
            self.query_params = params if isinstance(params, dict) else params.dict()
            self.include_concepts = self.query_params.get(INCLUDE_CONCEPTS_PARAM) in TRUTHY
            self.include_mappings = self.query_params.get(INCLUDE_MAPPINGS_PARAM) in TRUTHY
            self.include_references = self.query_params.get(INCLUDE_VERBOSE_REFERENCES) in TRUTHY
            self.limit = to_int(self.query_params.get(LIMIT_PARAM), DEFAULT_LIMIT)
            self.offset = to_int(self.query_params.get(OFFSET_PARAM), 0)
        try:
            if not self.include_concepts:
                self.fields.pop('concepts', None)
            if not self.include_mappings:
                self.fields.pop('mappings', None)
            if not self.include_references:
                self.fields.pop('references', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    def get_concepts(self, obj):
        results = []
        if self.include_concepts:
            from core.concepts.models import Concept
            from core.concepts.serializers import ConceptListSerializer
            queryset = self._paginate(
                Concept.apply_attribute_based_filters(obj.get_concepts_queryset(), self.query_params))
            results = self._serialize(queryset, ConceptListSerializer)
        return results

    def get_mappings(self, obj):
        results = []
        if self.include_mappings:
            from core.mappings.models import Mapping
            from core.mappings.serializers import MappingListSerializer
            queryset = self._paginate(
                Mapping.apply_attribute_based_filters(obj.get_mappings_queryset(), self.query_params))
            results = self._serialize(queryset, MappingListSerializer)
        return results

    def get_references(self, obj):
        results = []
        if self.include_references and obj.is_collection:
            from core.collections.serializers import CollectionReferenceSerializer
            results = self._serialize(self._paginate(obj.references, '-id'), CollectionReferenceSerializer)
        return results

    def _paginate(self, queryset, order_by='-updated_at'):
        return queryset.order_by(order_by)[self.offset:self.offset + self.limit]

    @staticmethod
    def _serialize(queryset, klass):
        return klass(queryset, many=True).data
