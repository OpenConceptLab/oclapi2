import logging
from collections import OrderedDict

from rest_framework import serializers
from rest_framework.fields import CharField, SerializerMethodField, \
    DateTimeField

from core.common.fhir_helpers import delete_empty_fields
from core.concept_maps.constants import RESOURCE_TYPE
from core.common.constants import HEAD
from core.common.serializers import StatusField, IdentifierSerializer
from core.mappings.constants import SAME_AS
from core.mappings.models import Mapping
from core.mappings.serializers import MappingDetailSerializer
from core.orgs.models import Organization
from core.parameters.serializers import ParametersSerializer
from core.sources.models import Source
from core.sources.serializers import SourceCreateOrUpdateSerializer
from core.users.models import UserProfile

logger = logging.getLogger('oclapi')


class ConceptMapGroupField(serializers.Field):
    def to_internal_value(self, data):
        mappings = []
        for group in data:
            for element in group.get('element', []):
                for target in element.get('target', []):
                    map_type = target.get('equivalence')
                    if map_type == 'equivalent':
                        map_type = SAME_AS
                    mapping = {
                        'from_source_url': IdentifierSerializer.convert_fhir_url_to_ocl_uri(group.get('source'),
                                                                                            'sources'),
                        'to_source_url': IdentifierSerializer.convert_fhir_url_to_ocl_uri(group.get('target'),
                                                                                          'sources'),
                        'from_concept_code': element.get('code'),
                        'to_concept_code': target.get('code'),
                        'map_type': map_type
                    }
                    mappings.append(mapping)

        return {'mappings': mappings}

    def to_representation(self, value):
        # limit to 1000 mappings by default
        # TODO: support graphQL to go around the limit
        limit = self.get_limit()
        mappings = value.get_mappings_queryset().filter(retired=False).order_by('id')[:limit]
        groups = {}
        for mapping in mappings:
            key = (mapping.from_source_url or '') + (mapping.to_source_url or '')
            group = groups.get(key)
            if not group:
                if mapping.from_source and mapping.from_source.canonical_url:
                    from_url = mapping.from_source.canonical_url
                else:
                    from_url = IdentifierSerializer.convert_ocl_uri_to_fhir_url(mapping.from_source_url, RESOURCE_TYPE)

                if mapping.to_source and mapping.to_source.canonical_url:
                    to_url = mapping.to_source.canonical_url
                else:
                    to_url = IdentifierSerializer.convert_ocl_uri_to_fhir_url(mapping.to_source_url, RESOURCE_TYPE)

                group = {
                    'source': from_url,
                    'target': to_url
                }
                groups.update({key: group})
            elements = group.get('element')
            if not elements:
                elements = []
                group.update({'element': elements})
            element = None
            for candidate in elements:
                if candidate.get('code') is mapping.from_concept_code:
                    element = candidate
                    break
            if not element:
                element = {
                    'code': mapping.from_concept_code,
                    'target': []
                }
                elements.append(element)
            targets = element.get('target')
            relationship = mapping.map_type
            if mapping.map_type == SAME_AS:
                relationship = 'equivalent'
            targets.append({
                'code': mapping.to_concept_code,
                'equivalence': relationship
            })
        return [*groups.values()]

    def get_limit(self):
        if self.context.get('has_many', False):
            limit = 25
        else:
            limit = 1000
        return limit


class ConceptMapDetailSerializer(serializers.ModelSerializer):
    resourceType = SerializerMethodField(method_name='get_resource_type')
    id = CharField(source='mnemonic')
    url = CharField(source='canonical_url', required=False)
    title = CharField(source='full_name', required=False)
    status = StatusField(source='*')
    language = CharField(source='default_locale', required=False)
    meta = SerializerMethodField()
    identifier = IdentifierSerializer(many=True, required=False)
    date = DateTimeField(source='revision_date', required=False)
    group = ConceptMapGroupField(source='*', required=False)

    class Meta:
        model = Source
        fields = ('resourceType', 'url', 'title', 'status', 'id', 'language', 'meta',
                  'version', 'identifier', 'contact', 'jurisdiction', 'name', 'description', 'publisher', 'purpose',
                  'copyright', 'date', 'experimental', 'group')

    def __new__(cls, *args, **kwargs):
        if kwargs.get('many', False):
            context = kwargs.get('context', {})
            context.update({'has_many': True})
            kwargs.update({'context': context})

        return super().__new__(cls, *args, **kwargs)

    @staticmethod
    def get_resource_type(_):
        return RESOURCE_TYPE

    @staticmethod
    def get_meta(obj):
        return {'lastUpdated': DateTimeField().to_representation(obj.updated_at)}

    def to_representation(self, instance):
        try:
            rep = super().to_representation(instance)
            delete_empty_fields(rep)
            IdentifierSerializer.include_ocl_identifier(instance.uri, RESOURCE_TYPE, rep)
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
        mappings = validated_data.pop('mappings', [])
        uri = self.context['request'].path + validated_data['mnemonic']
        ident = IdentifierSerializer.include_ocl_identifier(uri, RESOURCE_TYPE, validated_data)
        source = SourceCreateOrUpdateSerializer().prepare_object(validated_data)

        if ident['owner_type'] == 'orgs':
            owner = Organization.objects.filter(mnemonic=ident['owner_id']).first()
        else:
            owner = UserProfile.objects.filter(username=ident['owner_id']).first()

        source.set_parent(owner)
        source.source_type = 'ConceptMap'

        user = self.context['request'].user
        version = source.version  # remember version if set
        source.version = HEAD
        errors = Source.persist_new(source, user)
        if errors:
            self._errors.update(errors)
            return source

        for mapping in mappings:
            mapping.update({'parent_id': source.id})
            mapping_serializer = MappingDetailSerializer(data=mapping)
            mapping_serializer.is_valid(raise_exception=True)
            Mapping.persist_new(mapping_serializer.validated_data, user)

        # Create new version
        source.version = '0.1' if version == HEAD else version

        source.id = None  # pylint: disable=invalid-name
        errors = Source.persist_new_version(source, user)
        self._errors.update(errors)

        return source

    @staticmethod
    def is_mapping_same(first, second):
        if not isinstance(first, dict):
            first = vars(first)
        if not isinstance(second, dict):
            print(second)
            second = vars(second)
        return first.get('from_source_url', None) == second.get('from_source_url', None) and \
            first.get('to_source_url', None) == second.get('to_source_url', None) and \
            first.get('from_concept_code', None) == second.get('from_concept_code', None) and \
            first.get('to_concept_code', None) == second.get('to_concept_code', None) and \
            first.get('map_type', None) == second.get('map_type', None)

    def update(self, instance, validated_data):
        mappings = validated_data.pop('mappings', [])
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

        # Retire mapping if it does not exist in HEAD
        for mapping in source.mappings.filter(retired=False):
            found = False
            for new_mapping in mappings:
                if ConceptMapDetailSerializer.is_mapping_same(mapping, new_mapping):
                    found = True
            if not found:
                mapping.retire(user, 'Deleted from ConceptMap resource')

        source.refresh_from_db()

        # Add a new mapping if it does not exist in HEAD
        for new_mapping in mappings:
            found = False
            for mapping in source.mappings.filter(retired=False):
                if ConceptMapDetailSerializer.is_mapping_same(mapping, new_mapping):
                    found = True
            if not found:
                new_mapping.update({'parent_id': source.id})
                new_mapping_serializer = MappingDetailSerializer(data=new_mapping)
                new_mapping_serializer.is_valid(raise_exception=True)
                Mapping.persist_new(new_mapping_serializer.validated_data, user)

        existing_source_version = source.versions.filter(version=source_version)
        if existing_source_version:
            existing_source_version.delete()

        source.id = None
        source.version = source_version
        source.released = source_released

        errors = Source.persist_new_version(source, user)
        self._errors.update(errors)

        return source


class ConceptMapParametersSerializer(ParametersSerializer):

    def update(self, instance, validated_data):
        pass

    def create(self, validated_data):
        pass

    allowed_input_parameters = {
            'url': 'valueUri',
            'conceptMapVersion': 'valueString',
            'code': 'valueCode',
            'system': 'valueUri',
            'version': 'valueString',
            'source': 'valueUri',
            'coding': 'valueCoding',
            'codeableConcept': 'valueCodeableConcept',
            'target': 'valueUri',
            'targetsystem': 'valueUri',
            # TODO: dependency?
            'reverse': 'valueBoolean'
        }
