from rest_framework.fields import CharField, JSONField
from rest_framework.serializers import Serializer, Field, ValidationError

from core import settings
from core.code_systems.constants import RESOURCE_TYPE as CODE_SYSTEM_RESOURCE_TYPE
from core.orgs.models import Organization
from core.users.models import UserProfile
from core.value_sets.constants import RESOURCE_TYPE as VALUESET_RESOURCE_TYPE
from core.concept_maps.constants import RESOURCE_TYPE as CONCEPT_MAP_RESOURCE_TYPE


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
        fhir_uri = uri.replace('/sources/', resource_type_uri).replace('/collections/', resource_type_uri)
        fhir_uri = fhir_uri.strip('/')
        parts = fhir_uri.split('/')
        if len(parts) < 4:
            raise ValidationError(
                'Identifier must be in a format: /{owner_type}/{owner_id}/{resourceType}/{resource_id}/, given: '
                + fhir_uri)
        fhir_uri = '/' + '/'.join(parts[:4]) + '/'
        return fhir_uri

    @staticmethod
    def convert_fhir_url_to_ocl_uri(uri, resource_type):
        resource_type_uri = f"/{resource_type}/"
        fhir_uri = uri.replace('/ConceptMap/', resource_type_uri).replace('/CodeSystem/', resource_type_uri)\
            .replace('/ValueSet/', resource_type_uri)
        return fhir_uri

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
