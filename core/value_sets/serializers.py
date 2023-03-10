import logging

from rest_framework import serializers
from rest_framework.fields import CharField, DateField, SerializerMethodField, ChoiceField, DateTimeField, JSONField, \
    BooleanField, ListField, URLField

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

logger = logging.getLogger('oclapi')


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


class ValueSetComposeIncludeField(ReadSerializerMixin, serializers.Serializer):
    system = CharField(required=False)
    version = CharField(required=False)
    concept = ValueSetConceptSerializer(many=True, required=False)
    filter = FilterValueSetSerializer(many=True, required=False)
    valueSet = ListField(child=URLField(), required=False)

    def to_internal_value(self, data):
        # Handled by ComposeValueSetField
        pass

    def to_representation(self, instance):
        if instance:
            include = {}
            if instance[0].system:
                system = instance[0].system
                system = IdentifierSerializer.convert_ocl_uri_to_fhir_url(system, 'ValueSet')
                include.update({'system': system})
            if instance[0].version:
                include.update({'version': instance[0].version})
            for reference in instance:
                if reference.valueset:
                    include.update({'valueSet': reference.valueset})
                    break
                if reference.filter:
                    filters = include.get('filter', None)
                    if not filters:
                        filters = []
                        include.update({'filter': filters})
                    filters.extend(FilterValueSetSerializer(reference.filter, many=True).data)
                    break
                if reference.code:
                    concepts = include.get('concept', None)
                    if not concepts:
                        concepts = []
                        include.update({'concept': concepts})
                    concepts.append({'code': reference.code, 'display': reference.display})

            return include

        return []


class ComposeValueSetField(serializers.Field):
    lockedDate = DateField(required=False)
    inactive = BooleanField(required=False)
    include = ValueSetComposeIncludeField()
    exclude = ValueSetComposeIncludeField(required=False)

    def to_internal_value(self, data):
        references = []
        for include in data.get('include', []):
            include.update({'transform': 'resourceversions'})
            references += self.transform_to_ref(include)
        for exclude in data.get('exclude', []):
            exclude.update({'transform': 'resourceversions'})
            exclude.udpate({'include': False})
            references += self.transform_to_ref(exclude)
        if references:
            res = dict(references=references)
            if 'lockedDate' in data:
                res['locked_date'] = data['lockedDate']
            return res
        return {}

    @staticmethod
    def transform_to_ref(include):
        parser = CollectionReferenceParser(expression=include)
        parser.parse()
        parser.to_reference_structure()
        refs = parser.to_objects()
        for ref in refs:
            ref.expression = ref.build_expression()
        return refs

    def to_representation(self, value):
        include = []
        exclude = []

        grouped_references = {}
        for reference in value.references.all():
            if reference.reference_type != 'concepts':
                continue
            ref_list = grouped_references.get((reference.include, reference.system, reference.version), None)
            if not ref_list:
                ref_list = []
                grouped_references.update({(reference.include, reference.system, reference.version): ref_list})
            ref_list.append(reference)

        for group, ref_list in grouped_references.items():
            if group[0]:
                include.append(ValueSetComposeIncludeField(ref_list).data)
            else:
                exclude.append(ValueSetComposeIncludeField(ref_list).data)

        result = {}
        if include:
            result.update({'include': include})
        if exclude:
            result.update({'exclude': exclude})

        if result:
            result.update({'lockedDate': self.lockedDate.to_representation(value.locked_date)})
            return result

        return None


class ValueSetDetailSerializer(serializers.ModelSerializer):
    resourceType = SerializerMethodField(method_name='get_resource_type')
    id = CharField(source='mnemonic')
    url = CharField(source='canonical_url', required=False)
    title = CharField(source='full_name', required=False)
    status = StatusField(source='*')
    meta = SerializerMethodField()
    identifier = IdentifierSerializer(many=True, required=False)
    date = DateTimeField(source='revision_date', required=False)
    compose = ComposeValueSetField(source='*', required=False)
    text = JSONField(required=False)

    class Meta:
        model = Collection
        fields = ('resourceType', 'id', 'version', 'url', 'title', 'status', 'meta', 'identifier', 'date', 'contact',
                  'jurisdiction', 'name', 'description', 'publisher', 'purpose', 'copyright', 'experimental',
                  'immutable', 'text', 'compose')

    def create(self, validated_data):
        uri = self.context['request'].path + validated_data['mnemonic']
        ident = IdentifierSerializer.include_ocl_identifier(uri, RESOURCE_TYPE, validated_data)
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
        return rep

    @staticmethod
    def get_resource_type(_):
        return RESOURCE_TYPE

    @staticmethod
    def get_meta(obj):
        return dict(lastUpdated=obj.updated_at)


class ValueSetExpansionParametersSerializer(ParametersSerializer):
    allowed_input_parameters = {
        'url': 'valueUri',
        'filter': 'valueString',
        'date': 'valueDate',
        'offset': 'valueInteger',
        'count': 'valueInteger',
        'activeOnly': 'valueBoolean',
        'exclude-system': 'valueString',
        'system-version': 'valueString'
    }

    def update(self, instance, validated_data):
        pass

    def create(self, validated_data):
        pass


class ValueSetExpansionField(serializers.Field):
    default_count = 1000
    default_offset = 0
    timestamp = DateTimeField()

    def to_internal_value(self, _):
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
