from rest_framework import serializers
from rest_framework.fields import CharField, DateField, SerializerMethodField, ChoiceField, DateTimeField

from core.code_systems.serializers import CodeSystemConceptSerializer
from core.collections.models import Collection, Expansion
from core.collections.parsers import CollectionReferenceParser
from core.collections.serializers import CollectionCreateOrUpdateSerializer
from core.common.constants import HEAD
from core.common.serializers import StatusField, IdentifierSerializer, ReadSerializerMixin
from core.orgs.models import Organization
from core.parameters.serializers import ParametersSerializer
from core.users.models import UserProfile
from core.value_sets.constants import RESOURCE_TYPE


class FilterValueSetSerializer(ReadSerializerMixin, serializers.Serializer):
    property = CharField()
    op = ChoiceField(choices=['='])
    value = CharField()


class ValueSetConceptSerializer(CodeSystemConceptSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('definition')
        self.fields.pop('property')


class ValueSetExpansionConceptSerializer(CodeSystemConceptSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('definition')


class ComposeValueSetField(serializers.Field):
    lockedDate = DateField()

    def to_internal_value(self, data):
        if 'include' in data:
            references = []
            for include in data['include']:
                parser = CollectionReferenceParser(expression=include)
                parser.parse()
                parser.to_reference_structure()
                refs = parser.to_objects()
                for ref in refs:
                    ref.expression = ref.build_expression()
                references += refs
            res = dict(references=references)
            if 'lockedDate' in data:
                res['locked_date'] = data['lockedDate']
            return res

        return {}

    def to_representation(self, value):
        includes = []
        inactive = False
        for reference in value.references.all():
            for concept in reference.concepts.all():
                source = concept.sources.exclude(version=HEAD).order_by('created_at').first()
                if concept.retired:
                    inactive = True
                if not source:
                    # Concept is only in HEAD source
                    # TODO: find a better solution than omitting
                    continue
                matching_include = self.find_or_create_include(includes, source, reference)
                matching_include['concept'].append(ValueSetConceptSerializer(concept).data)

        if includes:
            return dict(
                lockedDate=self.lockedDate.to_representation(value.locked_date), inactive=inactive, include=includes)

        return None

    @staticmethod
    def find_or_create_include(includes, source, reference):
        matching_include = None

        # TODO: is this use or uri as concept_system correct?
        concept_system = source.canonical_url if source.canonical_url else \
            IdentifierSerializer.convert_ocl_uri_to_fhir_url(source.uri)
        concept_system_version = reference.version or source.version

        for include in includes:
            if include['system'] == concept_system and include['version'] == concept_system_version:
                matching_include = include
                break
        if not matching_include:
            matching_include = {
                'system': concept_system,
                'version': concept_system_version,
                'concept': [],
            }
            if reference.filter:
                # Include filter in newly added include
                matching_include['filter'] = FilterValueSetSerializer(reference.filter, many=True).data
            includes.append(matching_include)
        return matching_include


class ValueSetDetailSerializer(serializers.ModelSerializer):
    resourceType = SerializerMethodField(method_name='get_resource_type')
    id = CharField(source='mnemonic')
    url = CharField(source='canonical_url', required=False)
    title = CharField(source='full_name', required=False)
    status = StatusField(source='*')
    meta = SerializerMethodField()
    identifier = IdentifierSerializer(many=True, required=False)
    date = DateField(source='revision_date', required=False)
    compose = ComposeValueSetField(source='*', required=False)

    class Meta:
        model = Collection
        fields = ('resourceType', 'id', 'version', 'url', 'title', 'status', 'meta', 'identifier', 'date', 'contact',
                  'jurisdiction', 'name', 'description', 'publisher', 'purpose', 'copyright', 'experimental',
                  'immutable', 'text', 'compose')

    def create(self, validated_data):
        uri = self.context['request'].path + validated_data['mnemonic']
        ident = IdentifierSerializer.include_ocl_identifier(uri, validated_data)
        collection = CollectionCreateOrUpdateSerializer().prepare_object(validated_data)
        collection_version = collection.version if collection.version != HEAD else '0.1'
        collection.version = HEAD

        parent_klass = Organization if ident['owner_type'] == 'orgs' else UserProfile
        collection.set_parent(parent_klass.objects.filter(**{parent_klass.mnemonic_attr: ident['owner_id']}).first())

        user = self.context['request'].user
        errors = Collection.persist_new(collection, user)
        if errors:
            self._errors.update(errors)
            return collection

        references = validated_data.get('references', [])
        if references:
            _, errors = collection.add_references(references, user)
            if errors:
                self._errors.update(errors)
                return collection

        collection.id = None  # pylint: disable=invalid-name
        collection.version = collection_version
        errors = Collection.persist_new_version(collection, user)
        self._errors.update(errors)

        return collection

    def update(self, instance, validated_data):
        # Find HEAD first
        head_collection = instance.head

        collection = CollectionCreateOrUpdateSerializer().prepare_object(validated_data, instance)

        # Preserve version specific values
        collection_version = collection.version
        collection_released = collection.released

        # Update HEAD first
        collection.id = head_collection.id
        collection.version = HEAD
        collection.released = False  # HEAD must never be released
        collection.expansion_uri = head_collection.expansion_uri

        user = self.context['request'].user
        errors = Collection.persist_changes(collection, user, None)

        if errors:
            self._errors.update(errors)
            return collection

        # Update references
        new_references = validated_data.get('references', [])
        existing_references = []
        for reference in collection.references.all():
            for new_reference in new_references:
                if reference.expression == new_reference.expression:
                    existing_references.append(new_reference)

        new_references = [reference for reference in new_references if reference not in existing_references]

        if new_references:
            _, errors = collection.add_references(new_references, user)
            if errors:
                self._errors.update(errors)
                return collection

        # Create new version
        collection.version = collection_version
        collection.released = collection_released
        collection.id = None
        collection.expansion_uri = None
        errors = Collection.persist_new_version(collection, user)
        self._errors.update(errors)

        return collection

    def to_representation(self, instance):
        try:
            rep = super().to_representation(instance)
            IdentifierSerializer.include_ocl_identifier(instance.uri, rep)
        except Exception as error:
            raise Exception(f'Failed to represent "{instance.uri}" as {RESOURCE_TYPE}') from error
        return rep

    @staticmethod
    def get_resource_type(_):
        return RESOURCE_TYPE

    @staticmethod
    def get_meta(obj):
        return dict(lastUpdated=obj.updated_at)


class ValueSetExpansionParametersSerializer(ParametersSerializer):
    def to_internal_value(self, data):
        parameters = {}

        for parameter in data.get('parameter'):
            name = parameter.get('name')
            value = None
            match name:
                case 'filter':
                    value = parameter.get('valueString')
            if value:
                parameters[name] = value

        return {'parameters': parameters}


class ValueSetExpansionField(serializers.Field):
    default_count = 1000
    default_offset = 0
    timestamp = DateTimeField()

    @staticmethod
    def to_internal_value( _):
        return None

    def to_representation(self, value):
        return {
            'identifier': value.uri,
            'timestamp': self.timestamp.to_representation(value.created_at),
            'total': value.concepts.count(),
            'offset': self.default_offset,
            'contains': ValueSetExpansionConceptSerializer(value.concepts.order_by('id')
                                                           [self.default_offset:self.default_count], many=True).data
        }


class ValueSetExpansionSerializer(serializers.ModelSerializer):
    resourceType = SerializerMethodField(method_name='get_resource_type')
    expansion = ValueSetExpansionField(source='*')

    class Meta:
        model = Expansion
        fields = ('resourceType', 'id', 'expansion')

    @staticmethod
    def get_resource_type(_):
        return RESOURCE_TYPE
