from rest_framework import serializers
from rest_framework.fields import CharField, DateField, SerializerMethodField, ChoiceField

from core.code_systems.serializers import CodeSystemConceptSerializer
from core.collections.models import Collection, CollectionReference
from core.collections.serializers import CollectionCreateOrUpdateSerializer
from core.common.serializers import StatusField, IdentifierSerializer, ReadSerializerMixin
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile


class FilterValueSetSerializer(ReadSerializerMixin, serializers.Serializer):
    property = CharField()
    op = ChoiceField(choices=['='])
    value = CharField()


class ValueSetConceptSerializer(CodeSystemConceptSerializer):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('definition')
        self.fields.pop('property')


class ComposeValueSetField(serializers.Field):
    lockedDate = DateField()

    def to_internal_value(self, data):
        if 'include' in data:
            references = []
            for include in data['include']:
                system = include['system']
                system_version = include['version']
                source = Source.objects.filter(canonical_url=system, version=system_version)
                if not source:
                    source_uri = '/' + system.strip('/') + '/' + system_version + '/'
                    source = Source.objects.filter(uri=source_uri)
                else:
                    source_uri = source.first().uri

                if not source:
                    raise Exception(f'Cannot find system "{system}" and version "{system_version}"')

                if 'concept' in include:
                    for concept in include['concept']:
                        mnemonic = concept['code']
                        concept = source.first().concepts.filter(mnemonic=mnemonic)
                        if concept:
                            reference = {
                                'expression': concept.first().uri,
                                'version': system_version
                            }
                            references.append(reference)
                        else:
                            raise Exception(f'Cannot find concept "{mnemonic}" in system "{source_uri}"')

                self.include_filter(include, references, source_uri, system_version)

            res = {'references': references}
            if 'lockedDate' in data:
                res['locked_date'] = data['lockedDate']
            return res

        return {}

    @staticmethod
    def include_filter(include, references, source_uri, system_version):
        if 'filter' in include:
            filters = FilterValueSetSerializer(data=include['filter'], many=True)
            filters.is_valid(raise_exception=True)
            if references:
                # Due to the way include.concept is modeled as individual references
                # we need to apply filter to each reference
                for reference in references:
                    reference['filter'] = filters.validated_data
            else:
                # No include.concept then include the whole system and filter
                reference = {
                    'expression': source_uri,
                    'version': system_version,
                    'filter': filters.validated_data
                }
                references.append(reference)

    def to_representation(self, value):
        includes = []
        inactive = False
        for reference in value.references.all():
            for concept in reference.concepts.all():
                source = concept.sources.exclude(version='HEAD').order_by('created_at').first()
                if concept.retired:
                    inactive = True
                if not source:
                    # Concept is only in HEAD source
                    # TODO: find a better solution than omitting
                    continue
                matching_include = self.find_or_create_include(includes, source, reference)
                matching_include['concept'].append(ValueSetConceptSerializer(concept).data)

        if includes:
            return {'lockedDate': self.lockedDate.to_representation(value.locked_date),
                    'inactive': inactive,
                    'include': includes}

        return None

    @staticmethod
    def find_or_create_include(includes, source, reference):
        matching_include = None

        # TODO: is this use or uri as concept_system correct?
        concept_system = source.canonical_url if source.canonical_url else \
            IdentifierSerializer.convert_ocl_uri_to_fhir_url(source.uri)
        concept_system_version = reference.version if reference.version else source.version

        for include in includes:
            if include['system'] == concept_system \
                    and include['version'] == concept_system_version:
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
                  'jurisdiction', 'name', 'description', 'publisher', 'purpose',
                  'copyright', 'experimental', 'immutable', 'text', 'compose')

    def create(self, validated_data):
        uri = self.context['request'].path + validated_data['mnemonic']
        ident = IdentifierSerializer.include_ocl_identifier(uri, validated_data)
        collection = CollectionCreateOrUpdateSerializer().prepare_object(validated_data)
        collection_version = collection.version if collection.version != 'HEAD' else '0.1'
        collection.version = 'HEAD'

        if ident['owner_type'] == 'orgs':
            collection.set_parent(Organization.objects.filter(mnemonic=ident['owner_id']).first())
        else:
            collection.set_parent(UserProfile.objects.filter(username=ident['owner_id']).first())

        user = self.context['request'].user
        errors = Collection.persist_new(collection, user)
        if errors:
            self._errors.update(errors)
            return collection

        references = []
        if 'references' in validated_data:
            references = [CollectionReference(expression=reference['expression'], version=reference['version'],
                                              collection=collection, filter=reference.get('filter'))
                          for reference in validated_data['references']]

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
        if instance.organization:
            head_collection = Collection.objects.filter(mnemonic=instance.mnemonic, organization=instance.organization,
                                                        version='HEAD').get()
        else:
            head_collection = Collection.objects.filter(mnemonic=instance.mnemonic, user=instance.user,
                                                        version='HEAD').get()

        collection = CollectionCreateOrUpdateSerializer().prepare_object(validated_data, instance)

        # Preserve version specific values
        collection_version = collection.version
        collection_released = collection.released

        # Update HEAD first
        collection.id = head_collection.id
        collection.version = 'HEAD'
        collection.released = False  # HEAD must never be released

        user = self.context['request'].user
        errors = Collection.persist_changes(collection, user, None)

        if errors:
            self._errors.update(errors)
            return collection

        #Update references
        new_references = []
        if 'references' in validated_data:
            new_references = [CollectionReference(expression=reference['expression'], version=reference['version'],
                                                  collection=collection) for reference in validated_data['references']]

        existing_references = []
        for reference in collection.references.all():
            for new_reference in new_references:
                if reference.expression == new_reference.expression:
                    existing_references.append(new_reference)

        new_references = [e for e in new_references if e not in existing_references]

        if new_references:
            _, errors = collection.add_references(new_references, user)
            if errors:
                self._errors.update(errors)
                return collection

        # Create new version
        collection.version = collection_version
        collection.released = collection_released
        collection.id = None
        errors = Collection.persist_new_version(collection, user)
        self._errors.update(errors)

        return collection


    def to_representation(self, instance):
        try:
            rep = super().to_representation(instance)
            IdentifierSerializer.include_ocl_identifier(instance.uri, rep)
        except Exception as error:
            raise Exception(f'Failed to represent "{instance.uri}" as ValueSet') from error
        return rep

    @staticmethod
    def get_resource_type(_):
        return 'ValueSet'

    @staticmethod
    def get_meta(obj):
        return {'lastUpdated': obj.updated_at}
