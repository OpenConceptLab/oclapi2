from django.core.paginator import Paginator
from rest_framework import serializers
from rest_framework.fields import CharField, IntegerField, SerializerMethodField, ReadOnlyField

from core import settings
from core.concepts.models import Concept, LocalizedText
from core.sources.models import Source

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

class CodeSystemConceptPropertySerializer(serializers.Serializer):
    code = CharField()
    value = CharField()

    def update(self, instance, validated_data):
        raise NotImplementedError('`update()` must be implemented.')

    def create(self, validated_data):
        raise NotImplementedError('`create()` must be implemented.')


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

class CodeSystemPropertySerializer(serializers.Serializer):
    code = CharField()
    uri = CharField()
    description = CharField()
    type = CharField()

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
        paginator = Paginator(obj.get_concepts_queryset().order_by('id'), 100)
        if 'page' in self.context:
            page_number = self.context['page']
        else:
            page_number = 1
        concepts_page = paginator.get_page(page_number)

        return CodeSystemConceptSerializer(concepts_page.object_list, many=True).data

    def get_property(self, obj):
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
        """ Add accession identifier if not present """
        rep = super().to_representation(instance)
        if not rep['identifier']:
            rep['identifier'] = []

        has_accession_identifier = self.has_accession_identifier(rep)

        if not has_accession_identifier:
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
        return rep

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
