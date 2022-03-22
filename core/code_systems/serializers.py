from rest_framework import serializers
from rest_framework.fields import CharField, IntegerField, SerializerMethodField, ChoiceField

from core import settings
from core.common.serializers import ReadSerializerMixin
from core.concepts.models import Concept, LocalizedText
from core.concepts.serializers import ConceptDetailSerializer
from core.orgs.models import Organization
from core.sources.models import Source
from core.sources.serializers import SourceCreateOrUpdateSerializer
from core.users.models import UserProfile


class CodeSystemConceptDesignationUseSerializer(serializers.Field):

    def to_internal_value(self, data):
        if 'code' in data:
            return {'type': data['code']}
        return {}

    def to_representation(self, value):
        if value.type:
            return {'code': value.type}
        return None


class CodeSystemConceptDesignationSerializer(serializers.ModelSerializer):
    language = CharField(source='locale')
    value = CharField(source='name')
    use = CodeSystemConceptDesignationUseSerializer(source='*', required=False)

    class Meta:
        model = LocalizedText
        fields = ('language', 'value', 'use')


class CodeSystemConceptPropertySerializer(serializers.Field):

    def to_internal_value(self, data):
        ret = {}
        for item in data:
            if item['code'] == 'inactive':
                ret['retired'] = item['value']
            elif item['code'] == 'conceptclass':
                ret['concept_class'] = item['value']
            elif item['code'] == 'datatype':
                ret['datatype'] = item['value']
        return ret

    def to_representation(self, value):
        """ Populate properties defined for source """
        properties = [{'code': 'conceptclass', 'value': value.concept_class},
                      {'code': 'datatype', 'value': value.datatype}]
        if value.retired:
            properties.append({'code': 'inactive', 'value': value.retired})

        return properties


class CodeSystemConceptDisplaySerializer(serializers.Field):
    def to_internal_value(self, data):
        return {'name': data}

    def to_representation(self, value):
        if value.name:
            return value.name
        return value.display_name


class CodeSystemConceptSerializer(ReadSerializerMixin, serializers.Serializer):
    code = CharField(source='mnemonic')
    display = CodeSystemConceptDisplaySerializer(source='*', required=False)
    definition = SerializerMethodField()
    designation = CodeSystemConceptDesignationSerializer(source='names', many=True, required=False)
    property = CodeSystemConceptPropertySerializer(source='*')

    class Meta:
        model = Concept
        fields = ('code', 'display', 'definition', 'designation', 'property')

    def to_internal_value(self, data):
        ret = super().to_internal_value(data)
        ret.update({'id': ret['mnemonic'], 'name': ret['mnemonic']})
        if 'names' not in ret:
            ret.update({'names': []})

        found = False
        for concept_name in ret['names']:
            if concept_name['name'] == ret['name'] and concept_name['locale'] == settings.DEFAULT_LOCALE:
                concept_name['locale_preferred'] = True
                found = True
                break

        if not found:
            ret['names'].append({'name': ret['name'], 'locale': settings.DEFAULT_LOCALE, 'locale_preferred': True})

        return ret

    @staticmethod
    def get_definition(obj):
        descriptions = obj.descriptions_for_default_locale
        if descriptions:
            return descriptions[0]
        return ''


class CodeSystemPropertySerializer(ReadSerializerMixin, serializers.Serializer):
    code = CharField()
    uri = CharField()
    description = CharField()
    type = CharField()


class CodeSystemIdentifierTypeCodingSerializer(ReadSerializerMixin, serializers.Serializer):
    system = CharField(default='http://hl7.org/fhir/v2/0203', required=False)
    code = CharField(default='ACSN', required=False)
    display = CharField(default='Accession ID', required=False)


class CodeSystemIdentifierTypeSerializer(ReadSerializerMixin, serializers.Serializer):
    text = CharField(default='Accession ID', required=False)
    coding = CodeSystemIdentifierTypeCodingSerializer(many=True, required=False)


class CodeSystemIdentifierSerializer(ReadSerializerMixin, serializers.Serializer):
    system = CharField(default=settings.API_BASE_URL, required=False)
    value = CharField(required=False)
    type = CodeSystemIdentifierTypeSerializer(required=False)


class CodeSystemStatusField(serializers.Field):

    def to_internal_value(self, data):
        return {'retired': data == 'retired', 'released': data == 'released'}

    def to_representation(self, value):
        if value.retired:
            return 'retired'
        if value.released:
            return 'active'
        return 'draft'


class CodeSystemConceptField(serializers.Field):

    def to_internal_value(self, data):
        concepts = CodeSystemConceptSerializer(data=data, many=True)
        concepts.is_valid(raise_exception=True)
        return {'concepts': concepts.validated_data}

    def to_representation(self, value):
        # limit to 1000 concepts by default
        # TODO: support graphQL to go around the limit
        return CodeSystemConceptSerializer(value.concepts.order_by('id')[:1000], many=True).data


class CodeSystemDetailSerializer(serializers.ModelSerializer):
    resourceType = SerializerMethodField(method_name='get_resource_type')
    id = CharField(source='mnemonic')
    url = CharField(source='canonical_url', required=False)
    title = CharField(source='full_name', required=False)
    status = CodeSystemStatusField(source='*')
    language = CharField(source='default_locale', required=False)
    count = IntegerField(source='active_concepts', read_only=True)
    content = ChoiceField(source='content_type', choices=['not-present', 'example', 'fragment', 'complete',
                                                          'supplement'], allow_blank=True)
    property = SerializerMethodField()
    meta = SerializerMethodField()
    concept = CodeSystemConceptField(source='*', required=False)
    identifier = CodeSystemIdentifierSerializer(many=True, required=False)

    caseSensitive = CharField(source='case_sensitive', required=False)
    versionNeeded = CharField(source='version_needed', required=False)
    collectionReference = CharField(source='collection_reference', required=False)
    hierarchyMeaning = CharField(source='hierarchy_meaning', required=False)
    revisionDate = CharField(source='revision_date', required=False)

    class Meta:
        model = Source
        fields = ('resourceType', 'url', 'title', 'status', 'id', 'language', 'count', 'content', 'property', 'meta',
                  'version', 'identifier', 'contact', 'jurisdiction', 'name', 'description', 'publisher', 'purpose',
                  'copyright', 'revisionDate', 'experimental', 'caseSensitive', 'compositional', 'versionNeeded',
                  'collectionReference', 'hierarchyMeaning', 'concept')

    @staticmethod
    def get_resource_type(_):
        return 'CodeSystem'

    @staticmethod
    def get_property(_):
        return CodeSystemPropertySerializer([
            {'code': 'conceptclass', 'uri': settings.API_BASE_URL + '/orgs/OCL/sources/Classes/concepts',
             'description': 'Standard list of concept classes.', 'type': 'string'},
            {'code': 'datatype', 'uri': settings.API_BASE_URL + '/orgs/OCL/sources/Datatypes/concepts',
             'description': 'Standard list of concept datatypes.', 'type': 'string'},
            {'code': 'inactive', 'uri': 'http://hl7.org/fhir/concept-properties',
             'description': 'True if the concept is not considered active.',
             'type': 'coding'}
        ], many=True).data

    @staticmethod
    def get_meta(obj):
        return {'lastUpdated': obj.updated_at}

    def to_representation(self, instance):
        try:
            rep = super().to_representation(instance)
            self.include_ocl_identifier(instance, rep)
        except Exception as error:
            raise Exception(f'Failed to represent "{instance.uri}" as CodeSystem') from error
        return rep

    @staticmethod
    def parse_identifier(accession_id):
        id_parts = accession_id.strip().strip('/').split('/')
        if len(id_parts) != 4:
            raise serializers.ValidationError('Identifier must be in a format: '
                                              '/{owner_type}/{owner_id}/CodeSystem/{code_system_id}')

        identifier = {'owner_type': id_parts[0], 'owner_id': id_parts[1], 'resource_type': id_parts[2],
                      'resource_id': id_parts[3]}
        return identifier

    def validate_identifier(self, value):
        accession_id = self.find_ocl_identifier(value)
        if accession_id:
            identifier = self.parse_identifier(accession_id)
            if identifier['owner_type'] not in ['users', 'orgs']:
                raise serializers.ValidationError(
                    f"Owner type='{identifier['owner_type']}' is invalid. It must be 'users' or 'orgs'")
            if identifier['resource_type'] != 'CodeSystem':
                raise serializers.ValidationError(
                    f"Resource type='{identifier['resource_type']}' is invalid. It must be 'CodeSystem'")
            if identifier['owner_type'] == 'users':
                owner_exists = UserProfile.objects.filter(username=identifier['owner_id']).exists()
            else:
                owner_exists = Organization.objects.filter(mnemonic=identifier['owner_id']).exists()
            if not owner_exists:
                raise serializers.ValidationError(
                    f"Owner of type='{identifier['owner_type']}' and id='{identifier['owner_id']}' not found.")
        else:
            raise serializers.ValidationError("OCL accession identifier is required: { "
                                              "'value': '/{owner_type}/{owner_id}/CodeSystem/{code_system_id}' }, "
                                              "'type': {'coding': [{'code': 'ACSN', "
                                              "'system': 'http://hl7.org/fhir/v2/0203'}]}}")
        return value

    def include_ocl_identifier(self, instance, rep):
        """ Add OCL identifier if not present """
        if not self.find_ocl_identifier(rep['identifier']):
            rep['identifier'].append({
                'system': settings.API_BASE_URL,
                'value': self.convert_ocl_to_fhir_url(instance),
                'type': {
                    'text': 'Accession ID',
                    'coding': [{
                        'system': 'http://hl7.org/fhir/v2/0203',
                        'code': 'ACSN',
                        'display': 'Accession ID'
                    }]
                }
            })

    @staticmethod
    def convert_ocl_to_fhir_url(instance):
        uri = instance.uri.replace('sources', 'CodeSystem').replace('collections', 'ValueSet')
        if len(uri.split('/')) > 4:
            uri = uri.rsplit('/', 2)[0] + '/'
        return uri

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

    def get_ocl_identifier(self):
        ident = self.find_ocl_identifier(self.validated_data['identifier'])
        ident = self.parse_identifier(ident)
        return ident

    def create(self, validated_data):
        if 'concepts' in validated_data:
            concepts = validated_data.pop('concepts')
        else:
            concepts = []
        source = SourceCreateOrUpdateSerializer().prepare_object(validated_data)

        ident = self.get_ocl_identifier()
        if ident['owner_type'] == 'orgs':
            source.set_parent(Organization.objects.filter(mnemonic=ident['owner_id']).first())
        else:
            source.set_parent(UserProfile.objects.filter(username=ident['owner_id']).first())

        user = self.context['request'].user
        version = source.version  # remember version if set
        source.version = 'HEAD'
        errors = Source.persist_new(source, user)
        if errors:
            self._errors.update(errors)
            return source

        for concept_item in concepts:
            concept_item.update({'parent_id': source.id})
            concept_serializer = ConceptDetailSerializer(data=concept_item)
            concept_serializer.is_valid(raise_exception=True)
            Concept.persist_new(concept_serializer.validated_data)

        # Create new version
        if version != 'HEAD':
            source.version = version
        else:
            source.version = '0.1'
        source.id = None  # pylint: disable=C0103
        errors = Source.persist_new_version(source, user)
        self._errors.update(errors)

        return source

    def update(self, instance, validated_data):
        if 'concepts' in validated_data:
            concepts = validated_data.pop('concepts')
        else:
            concepts = []
        source = SourceCreateOrUpdateSerializer().prepare_object(validated_data, instance)

        # Preserve version specific values
        source_version = source.version
        source_released = source.released

        user = self.context['request'].user

        # Update HEAD first
        # Determine existing source ID
        if source.organization:
            existing_source = Source.objects.filter(mnemonic=source.mnemonic, organization=source.organization,
                                                    version='HEAD').get()
        else:
            existing_source = Source.objects.filter(mnemonic=source.mnemonic, user=source.user, version='HEAD').get()
        source.id = existing_source.id
        source.version = 'HEAD'
        source.released = False  # HEAD must never be released
        source.custom_validation_schema = existing_source.custom_validation_schema

        errors = Source.persist_changes(source, user, None)

        if errors:
            self._errors.update(errors)
            return source

        for concept_item in concepts:
            concept_item.update({'parent_id': source.id})
            existing_concept = source.get_concepts_queryset().filter(mnemonic=concept_item['mnemonic'])
            if existing_concept:
                concept_serializer = ConceptDetailSerializer(context=self.context, instance=existing_concept.first(),
                                                             data=concept_item)
                concept_serializer.is_valid(raise_exception=True)
                concept_serializer.save()
            else:
                concept_serializer = ConceptDetailSerializer(context=self.context, data=concept_item)
                concept_serializer.is_valid(raise_exception=True)
                Concept.persist_new(concept_serializer.validated_data)

        # Create new version
        source.version = source_version
        source.released = source_released
        source.id = None
        errors = Source.persist_new_version(source, user)
        self._errors.update(errors)

        return source
