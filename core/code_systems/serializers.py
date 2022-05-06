from rest_framework import serializers
from rest_framework.fields import CharField, DateField, BooleanField, IntegerField, SerializerMethodField, ChoiceField

from core import settings
from core.common.serializers import ReadSerializerMixin, StatusField, IdentifierSerializer
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

        if 'retired' not in ret:
            ret['retired'] = False
        if 'concept_class' not in ret:
            ret['concept_class'] = 'Misc'
        if 'datatype' not in ret:
            ret['datatype'] = 'N/A'

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
    property = CodeSystemConceptPropertySerializer(source='*', required=False)

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
    status = StatusField(source='*')
    language = CharField(source='default_locale', required=False)
    count = IntegerField(source='active_concepts', read_only=True)
    content = ChoiceField(source='content_type', choices=['not-present', 'example', 'fragment', 'complete',
                                                          'supplement'], allow_blank=True)
    property = SerializerMethodField()
    meta = SerializerMethodField()
    concept = CodeSystemConceptField(source='*', required=False)
    identifier = IdentifierSerializer(many=True, required=False)

    caseSensitive = BooleanField(source='case_sensitive', required=False)
    versionNeeded = BooleanField(source='version_needed', required=False)
    collectionReference = CharField(source='collection_reference', required=False)
    hierarchyMeaning = CharField(source='hierarchy_meaning', required=False)
    revisionDate = DateField(source='revision_date', required=False)

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
            IdentifierSerializer.include_ocl_identifier(instance.uri, rep)
        except Exception as error:
            raise Exception(f'Failed to represent "{instance.uri}" as CodeSystem') from error
        return rep

    def get_ocl_identifier(self):
        ident = IdentifierSerializer.find_ocl_identifier(self.validated_data['identifier'])
        ident = IdentifierSerializer.parse_identifier(ident)
        return ident

    def create(self, validated_data):
        if 'concepts' in validated_data:
            concepts = validated_data.pop('concepts')
        else:
            concepts = []
        uri = self.context['request'].path + validated_data['mnemonic']
        ident = IdentifierSerializer.include_ocl_identifier(uri, validated_data)
        source = SourceCreateOrUpdateSerializer().prepare_object(validated_data)

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
        source.id = None  # pylint: disable=invalid-name
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
