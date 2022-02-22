from rest_framework import serializers
from rest_framework.fields import CharField, IntegerField, SerializerMethodField, ReadOnlyField

from core import settings
from core.concepts.models import Concept, LocalizedText
from core.sources.models import Source


class CodeSystemSerializerMixin:
    def update(self, instance, validated_data):
        pass

    def create(self, validated_data):
        pass


# pylint: disable=R0201
class CodeSystemConceptDesignationSerializer(serializers.ModelSerializer):
    language = CharField(source='locale')
    value = CharField(source='name')
    use = SerializerMethodField()

    class Meta:
        model = LocalizedText
        fields = ('language', 'value', 'use')

    def get_use(self, obj):
        if obj.type:
            return {'code': obj.type}
        return None


class CodeSystemConceptPropertySerializer(CodeSystemSerializerMixin, serializers.Serializer):
    code = CharField()
    value = CharField()


class CodeSystemConceptSerializer(serializers.ModelSerializer):
    code = CharField(source='mnemonic')
    display = SerializerMethodField()
    definition = SerializerMethodField()
    designation = CodeSystemConceptDesignationSerializer(source='names', many=True)
    property = SerializerMethodField()

    class Meta:
        model = Concept
        fields = ('code', 'display', 'definition', 'designation', 'property')

    def get_display(self, obj):
        if obj.name:
            return obj.name
        return obj.display_name

    def get_definition(self, obj):
        descriptions = obj.descriptions_for_default_locale
        if descriptions:
            return descriptions[0]
        return ''

    def get_property(self, obj):
        """ Populate properties defined for source """
        properties = [{'code': 'conceptclass', 'value': obj.concept_class},
                      {'code': 'datatype', 'value': obj.datatype}]
        if obj.retired:
            properties.append({'code': 'inactive', 'value': obj.retired})

        return CodeSystemConceptPropertySerializer(properties, many=True).data


class CodeSystemPropertySerializer(CodeSystemSerializerMixin, serializers.Serializer):
    code = CharField()
    uri = CharField()
    description = CharField()
    type = CharField()


class CodeSystemIdentifierTypeCodingSerializer(CodeSystemSerializerMixin, serializers.Serializer):
    system = CharField(default='http://hl7.org/fhir/v2/0203')
    code = CharField(default='ACSN')
    display = CharField(default='Accession ID')


class CodeSystemIdentifierTypeSerializer(CodeSystemSerializerMixin, serializers.Serializer):
    text = CharField(default='Accession ID')
    coding = CodeSystemIdentifierTypeCodingSerializer()


class CodeSystemIdentifierSerializer(serializers.Serializer):
    system = CharField()
    value = CharField()
    type = CodeSystemIdentifierTypeSerializer()

    def update(self, instance, validated_data):
        raise NotImplementedError('`update()` must be implemented.')

    def create(self, validated_data):
        raise NotImplementedError('`create()` must be implemented.')


class CodeSystemDetailSerializer(serializers.ModelSerializer):
    resource_type = ReadOnlyField(default='CodeSystem')
    id = CharField(source='mnemonic')
    url = CharField(source='canonical_url')
    title = CharField(source='full_name')
    status = SerializerMethodField()
    language = CharField(source='default_locale')
    count = IntegerField(source='active_concepts', read_only=True)
    content = SerializerMethodField()
    property = SerializerMethodField(read_only=True)
    meta = SerializerMethodField(read_only=True)
    concept = SerializerMethodField()
    identifier = CodeSystemIdentifierSerializer(many=True)

    class Meta:
        model = Source
        fields = ('resource_type', 'url', 'title', 'status', 'id', 'language', 'count', 'content', 'property', 'meta',
                  'version', 'identifier', 'contact', 'jurisdiction', 'name', 'description', 'publisher', 'purpose',
                  'copyright', 'revision_date', 'experimental', 'case_sensitive', 'compositional', 'version_needed',
                  'collection_reference', 'hierarchy_meaning', 'concept')

    def get_status(self, obj):
        if obj.retired:
            return 'retired'
        if obj.released:
            return 'active'
        return 'draft'

    def get_content(self, obj):
        if obj.content_type:
            content_type = obj.content_type.lower()
            if content_type == 'notpresent':
                return 'not-present'
            return content_type
        return None

    def get_concept(self, obj):
        # limit to 1000 concepts by default
        # TODO: support graphQL to go around the limit
        return CodeSystemConceptSerializer(obj.get_concepts_queryset().order_by('id')[:1000], many=True).data

    def get_property(self, _):
        return CodeSystemPropertySerializer([
            {'code': 'conceptclass', 'uri': settings.API_BASE_URL + '/orgs/OCL/sources/Classes/concepts',
             'description': 'Standard list of concept classes.', 'type': 'string'},
            {'code': 'datatype', 'uri': settings.API_BASE_URL + '/orgs/OCL/sources/Datatypes/concepts',
             'description': 'Standard list of concept datatypes.', 'type': 'string'},
            {'code': 'inactive', 'uri': 'http://hl7.org/fhir/concept-properties',
             'description': 'True if the concept is not considered active.',
             'type': 'coding'}
        ], many=True).data

    def get_meta(self, obj):
        return {'lastUpdated': obj.updated_at}

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        self.include_accession_identifier(instance, rep)
        return rep

    def include_accession_identifier(self, instance, rep):
        """ Add accession identifier if not present """
        if not self.has_accession_identifier(rep):
            rep['identifier'].append({
                'system': settings.API_BASE_URL,
                'value': instance.uri.replace('sources', 'CodeSystem').replace('collections', 'ValueSet'),
                'type': {
                    'text': 'Accession ID',
                    'coding': [{
                        'system': 'http://hl7.org/fhir/v2/0203',
                        'code': 'ACSN',
                        'display': 'Accession ID'
                    }]
                }
            })

    def has_accession_identifier(self, rep):
        found = False
        for ident in rep['identifier']:
            if ident['type'] and ident['type']['coding']:
                codings = ident['type']['coding']
                for coding in codings:
                    if coding['code'] == 'ACSN' and coding['system'] == 'http://hl7.org/fhir/v2/0203':
                        found = True
                        break
                if found:
                    break
        return found
