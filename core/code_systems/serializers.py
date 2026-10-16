import ast
import logging
from collections import OrderedDict

from rest_framework import serializers
from rest_framework.fields import CharField, BooleanField, SerializerMethodField, ChoiceField, \
    DateTimeField

from core import settings
from core.code_systems.constants import RESOURCE_TYPE
from core.common.constants import HEAD
from core.common.fhir_helpers import delete_empty_fields
from core.common.serializers import ReadSerializerMixin, StatusField, IdentifierSerializer
from core.concepts.models import Concept, ConceptName
from core.concepts.serializers import ConceptDetailSerializer
from core.fhir import search as fhir_search
from core.fhir.search import CanonicalUrlField, FhirRepositorySerializerMixin
from core.parameters.serializers import ParametersSerializer
from core.sources.models import Source
from core.sources.serializers import SourceCreateOrUpdateSerializer

logger = logging.getLogger('oclapi')


class ValidateCodeParametersSerializer(ParametersSerializer):
    allowed_input_parameters = {
        'url': 'valueUri',
        'code': 'valueCode',
        'display': 'valueString',
        'version': 'valueString',
        'systemVersion': 'valueString',
        'system': 'valueUri'
    }

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
        model = ConceptName
        fields = ('language', 'value', 'use')


class CodeSystemConceptPropertySerializer(serializers.Field):
    def to_internal_value(self, data):
        ret = {}
        for item in data:
            if item.get('code') == 'inactive':
                ret['retired'] = item.get('valueBoolean', False)
            elif item.get('code') == 'conceptclass':
                ret['concept_class'] = item.get('valueString', 'Misc')
            elif item.get('code') == 'datatype':
                ret['datatype'] = item.get('valueString', 'N/A')

        return ret

    def to_representation(self, value):
        """ Populate properties defined for source """
        properties = [{'code': 'conceptclass', 'valueString': value.concept_class},
                      {'code': 'datatype', 'valueString': value.datatype}]
        if value.retired:
            properties.append({'code': 'inactive', 'valueBoolean': value.retired})

        return properties


class CodeSystemConceptDisplaySerializer(serializers.Field):
    def to_internal_value(self, data):
        # Handled by parent
        return {}

    def to_representation(self, value):
        return value.display_name


class CodeSystemConceptDefinitionSerializer(serializers.Field):
    def to_internal_value(self, data):
        return {'descriptions': [{'description': data, 'locale': settings.DEFAULT_LOCALE, 'locale_preferred': True}]}

    def to_representation(self, value):
        """The first description in the default locale, from the descriptions a read prefetches."""
        descriptions = [description.name for description in value.descriptions.all()
                        if description.locale == settings.DEFAULT_LOCALE and not description.retired]
        return descriptions[0] if descriptions else None


class CodeSystemConceptSerializer(ReadSerializerMixin, serializers.Serializer):
    code = CharField(source='mnemonic')
    display = CodeSystemConceptDisplaySerializer(source='*', required=False)
    definition = CodeSystemConceptDefinitionSerializer(source='*', required=False)
    designation = CodeSystemConceptDesignationSerializer(source='names', many=True, required=False)
    property = CodeSystemConceptPropertySerializer(source='*', required=False)

    class Meta:
        model = Concept
        fields = ('code', 'display', 'definition', 'designation', 'property')

    def to_internal_value(self, data):
        ret = super().to_internal_value(data)

        if 'retired' not in ret:
            ret['retired'] = False
        if 'concept_class' not in ret:
            ret['concept_class'] = 'Misc'
        if 'datatype' not in ret:
            ret['datatype'] = 'N/A'

        ret.update({'id': ret['mnemonic'], 'name': ret['mnemonic']})
        if 'names' not in ret:
            ret.update({'names': []})

        found = False
        for concept_name in ret['names']:
            if concept_name['name'] == data['display'] and concept_name['locale'] == settings.DEFAULT_LOCALE:
                concept_name['locale_preferred'] = True
                found = True
                break

        if not found:
            ret['names'].append({'name': data.get('display', data.get('code', None)), 'locale': settings.DEFAULT_LOCALE,
                                 'locale_preferred': True})

        return ret


class CodeSystemPropertySerializer(ReadSerializerMixin, serializers.Serializer):
    """Represent CodeSystem property definitions from source metadata."""
    code = CharField()
    uri = CharField(required=False)
    description = CharField(required=False)
    type = CharField()


class CodeSystemConceptField(serializers.Field):
    def to_internal_value(self, data):
        concepts = CodeSystemConceptSerializer(data=data, many=True)
        concepts.is_valid(raise_exception=True)
        return {'concepts': concepts.validated_data}

    def to_representation(self, value):
        """Every concept, retired ones included, up to the limit; none above it (content not-present)."""
        concepts = fhir_search.code_system_concepts(value)[1]
        return CodeSystemConceptSerializer(concepts, many=True).data if concepts else []


class TextField(ReadSerializerMixin, serializers.Serializer):
    status = ChoiceField(choices=['generated', 'extensions', 'additional', 'empty'], required=True)
    div = CharField(required=True)

    def to_internal_value(self, data):
        validated_data = super().to_internal_value(data)
        return dict(validated_data)

    def to_representation(self, instance):
        try:
            obj = ast.literal_eval(instance)
        except:  # pylint: disable=bare-except
            obj = instance

        if isinstance(obj, str) or not isinstance(obj, dict):
            obj = {'status': 'generated', 'div': obj}

        return super().to_representation(obj)


class CodeSystemDetailSerializer(FhirRepositorySerializerMixin, serializers.ModelSerializer):
    content_field = 'concept'
    resourceType = SerializerMethodField(method_name='get_resource_type')
    id = CharField(source='mnemonic')
    url = CanonicalUrlField(source='canonical_url', required=False)
    title = CharField(source='full_name', required=False)
    status = StatusField(source='*')
    language = CharField(source='default_locale', required=False)
    count = SerializerMethodField(method_name='get_total_count')
    content = ChoiceField(
        source='content_type',
        choices=['not-present', 'example', 'fragment', 'complete', 'supplement'],
        required=True
    )
    property = SerializerMethodField()
    meta = SerializerMethodField()
    concept = CodeSystemConceptField(source='*', required=False)
    identifier = IdentifierSerializer(many=True, required=False)

    caseSensitive = BooleanField(source='case_sensitive', required=False)
    versionNeeded = BooleanField(source='version_needed', required=False)
    collectionReference = CharField(source='collection_reference', required=False)
    hierarchyMeaning = CharField(source='hierarchy_meaning', required=False)
    revisionDate = DateTimeField(source='revision_date', required=False)
    text = TextField(required=False)

    class Meta:
        model = Source
        fields = ('resourceType', 'url', 'title', 'status', 'id', 'language', 'count', 'content', 'property', 'meta',
                  'version', 'identifier', 'contact', 'jurisdiction', 'name', 'description', 'publisher', 'purpose',
                  'copyright', 'revisionDate', 'experimental', 'caseSensitive', 'compositional', 'versionNeeded',
                  'collectionReference', 'hierarchyMeaning', 'concept', 'text', 'filters')

    @staticmethod
    def get_resource_type(_):
        return RESOURCE_TYPE

    def get_total_count(self, obj):
        """A read's concept count; a search's summary has none (it only checks the limit)."""
        return None if self.context.get('has_many') else fhir_search.code_system_concepts(obj)[0]

    @staticmethod
    def get_content(obj):
        """complete (or the content mode the CodeSystem was saved with) up to the concept limit, else not-present"""
        if fhir_search.concepts_over_limit(obj):
            return 'not-present'
        return obj.content_type if obj.content_type in ('example', 'fragment', 'supplement') else 'complete'

    @staticmethod
    def get_property(obj):
        properties = [
            {
                'code': 'conceptclass',
                'uri': settings.API_BASE_URL + '/orgs/OCL/sources/Classes/concepts',
                'description': 'Standard list of concept classes.',
                'type': 'string'
            },
            {
                'code': 'datatype',
                'uri': settings.API_BASE_URL + '/orgs/OCL/sources/Datatypes/concepts',
                'description': 'Standard list of concept datatypes.',
                'type': 'string'
            },
            {
                'code': 'inactive',
                'uri': 'http://hl7.org/fhir/concept-properties',
                'description': 'True if the concept is not considered active.',
                'type': 'Coding'
            }
        ]
        new_properties = [*(obj.properties or [])]
        defined_property_codes = {prop['code'] for prop in new_properties}
        for prop in properties:
            if prop['code'] not in defined_property_codes:
                new_properties.append(prop)
        return CodeSystemPropertySerializer(
            new_properties,
            many=True
        ).data

    @staticmethod
    def get_meta(obj):
        return {'lastUpdated': DateTimeField().to_representation(obj.updated_at)}

    def to_representation(self, instance):
        try:
            rep = super().to_representation(instance)
            rep['content'] = self.get_content(instance)
            delete_empty_fields(rep)
            IdentifierSerializer.include_ocl_identifier(instance.uri, RESOURCE_TYPE, rep)
            self.with_fhir_view(instance, rep)
        except (Exception, ):
            msg = f'Failed to represent "{instance.uri}" as {RESOURCE_TYPE}'
            logger.exception(msg)
            return {
                'resourceType': 'OperationOutcome',
                'issue': [{
                    'severity': 'error',
                    'details': msg
                }]
            }
        # Remove fields with 'None' value
        return OrderedDict([(key, rep[key]) for key in rep if rep[key] is not None])

    def create(self, validated_data):
        concepts = validated_data.pop('concepts', [])
        uri = '/'.join([self.context['request'].path.rstrip('/'), validated_data['mnemonic']])
        ident = IdentifierSerializer.include_ocl_identifier(uri, RESOURCE_TYPE, validated_data)
        source = SourceCreateOrUpdateSerializer().prepare_object(validated_data)

        source.set_parent(IdentifierSerializer.get_checked_owner(self.context, ident))
        source.source_type = 'CodeSystem'

        user = self.context['request'].user
        version = source.version  # remember version if set
        source.version = HEAD
        errors = Source.persist_new(source, user)
        if errors:
            self._errors.update(errors)
            return source

        for concept_item in concepts:
            concept_item.update({'parent_id': source.id})
            concept_serializer = ConceptDetailSerializer(data=concept_item)
            concept_serializer.is_valid(raise_exception=True)
            Concept.persist_new(data=concept_serializer.validated_data, sync_checksum=False)

        # Create new version
        source.version = '0.1' if version == HEAD else version

        source.id = None  # pylint: disable=invalid-name
        # Make it synchronous for now so that the list of concepts is included in the response
        errors = Source.persist_new_version(source, user, sync=True)
        self._errors.update(errors)
        return source

    def update(self, instance, validated_data):
        concepts = validated_data.pop('concepts', [])
        new_version = validated_data.get('version')

        if instance.active_versions.filter(version=new_version):
            self._errors.update({'version': f'Version {new_version} already exists for CodeSystem'
                                            f' {instance.mnemonic}.'})
            return instance

        source = SourceCreateOrUpdateSerializer().prepare_object(validated_data, instance)

        # Preserve version specific values
        source_version = source.version
        source_released = source.released

        user = self.context['request'].user

        # Update HEAD first
        # Determine existing source ID
        source_head = source.head
        source.id = source_head.id
        source.version = HEAD
        source.released = False  # HEAD must never be released
        source.custom_validation_schema = source_head.custom_validation_schema

        errors = Source.persist_changes(source, user, None)

        if errors:
            self._errors.update(errors)
            return source

        for concept_item in concepts:
            concept_item.update({'parent_id': source.id})
            existing_concept = source.get_concepts_queryset().filter(mnemonic=concept_item['mnemonic'])
            concept_serializer = ConceptDetailSerializer(
                context=self.context, instance=existing_concept.first(), data=concept_item)
            concept_serializer.is_valid(raise_exception=True)

            if existing_concept:
                concept_serializer.save()
            else:
                Concept.persist_new(concept_serializer.validated_data)

        # Create new version
        source.version = source_version
        source.released = source_released
        source.id = None
        # Make it synchronous for now so that the list of concepts is included in the response
        errors = Source.persist_new_version(source, user, sync=True)
        self._errors.update(errors)
        return source
