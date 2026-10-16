import logging
from collections import OrderedDict

from rest_framework import serializers
from rest_framework.fields import CharField, SerializerMethodField, \
    DateTimeField

from core.common.fhir_helpers import delete_empty_fields
from core.concept_maps.constants import RESOURCE_TYPE, RELATIONSHIP_MAP_TYPES
from core.common.constants import HEAD
from core.common.serializers import StatusField, IdentifierSerializer
from core.fhir.adapters import r4
from core.fhir.ops.translate import fhir_code, relationship, side_system
from core.fhir.search import CanonicalUrlField, FhirRepositorySerializerMixin
from core.mappings.models import Mapping
from core.mappings.serializers import MappingDetailSerializer
from core.sources.models import Source
from core.sources.serializers import SourceCreateOrUpdateSerializer

logger = logging.getLogger('oclapi')


class ConceptMapGroupField(serializers.Field):
    """
    A ConceptMap's groups, one per source and target system, each mapping an element target, with core.fhir.ops.
    translate's systems and relationships, in R4 (equivalence). Writes store an equivalence as the map type it is.
    """

    def to_internal_value(self, data):
        mappings = []
        for group in data:
            for element in group.get('element', []):
                for target in element.get('target', []):
                    equivalence = target.get('equivalence')
                    mapping = {
                        'from_source_url': IdentifierSerializer.convert_fhir_url_to_ocl_uri(group.get('source'),
                                                                                            'sources'),
                        'to_source_url': IdentifierSerializer.convert_fhir_url_to_ocl_uri(group.get('target'),
                                                                                          'sources'),
                        'from_concept_code': element.get('code'),
                        'to_concept_code': target.get('code'),
                        'map_type': RELATIONSHIP_MAP_TYPES.get(relationship(equivalence), equivalence)
                    }
                    mappings.append(mapping)

        return {'mappings': mappings}

    def to_representation(self, value):
        # limit to 1000 mappings by default
        # TODO: support graphQL to go around the limit
        mappings = value.get_active_mappings().select_related(  # neither retired nor deleted
            'from_source', 'to_source').order_by('id')[:1000]
        groups, elements = {}, {}
        for mapping in mappings:
            key = (mapping.from_source_url or '') + (mapping.to_source_url or '')
            if key not in groups:
                groups[key] = {
                    'source': self.system(mapping.from_source, mapping.from_source_url),
                    'target': self.system(mapping.to_source, mapping.to_source_url),
                    'element': []
                }
            code = fhir_code(mapping.from_concept_code)
            element = elements.get((key, code))
            if element is None:
                element = elements[(key, code)] = {'code': code, 'target': []}
                groups[key]['element'].append(element)
            element['target'].append({
                'code': fhir_code(mapping.to_concept_code),
                'equivalence': r4.equivalence(relationship(mapping.map_type))
            })
        return [*groups.values()]

    @staticmethod
    def system(source, url):
        """A group's source or target: its source's canonical URL, else the URL the mapping stores (side_system)."""
        return side_system(source.canonical_url if source else None, url)


class ConceptMapDetailSerializer(FhirRepositorySerializerMixin, serializers.ModelSerializer):
    content_field = 'group'
    resourceType = SerializerMethodField(method_name='get_resource_type')
    id = CharField(source='mnemonic')
    url = CanonicalUrlField(source='canonical_url', required=False)
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
        mappings = validated_data.pop('mappings', [])
        uri = '/'.join([self.context['request'].path.rstrip('/'), validated_data['mnemonic']])
        ident = IdentifierSerializer.include_ocl_identifier(uri, RESOURCE_TYPE, validated_data)
        source = SourceCreateOrUpdateSerializer().prepare_object(validated_data)

        source.set_parent(IdentifierSerializer.get_checked_owner(self.context, ident))
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
        source.is_latest_version = True

        source.id = None  # pylint: disable=invalid-name
        errors = Source.persist_new_version(source, user)
        self._errors.update(errors)

        return source

    @staticmethod
    def is_mapping_same(first, second):
        if not isinstance(first, dict):
            first = vars(first)
        if not isinstance(second, dict):
            second = vars(second)
        return first.get('from_source_url', None) == second.get('from_source_url', None) and \
            first.get('to_source_url', None) == second.get('to_source_url', None) and \
            first.get('from_concept_code', None) == second.get('from_concept_code', None) and \
            first.get('to_concept_code', None) == second.get('to_concept_code', None) and \
            first.get('map_type', None) == second.get('map_type', None)

    def update(self, instance, validated_data):
        mappings = validated_data.pop('mappings', [])
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

        # Retire mapping if it does not exist in HEAD
        for mapping in source.mappings.filter(retired=False):
            found = False
            for new_mapping in mappings:
                if ConceptMapDetailSerializer.is_mapping_same(mapping, new_mapping):
                    found = True
            if not found:
                mapping.retire(user, None, 'Deleted from ConceptMap resource')

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

        source.id = None
        source.version = source_version
        source.released = source_released
        source.is_latest_version = True

        errors = Source.persist_new_version(source, user)
        self._errors.update(errors)

        return source
