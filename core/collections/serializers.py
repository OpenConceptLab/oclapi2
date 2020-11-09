from django.core.validators import RegexValidator
from rest_framework.fields import CharField, ChoiceField, ListField, IntegerField, DateTimeField, JSONField, \
    BooleanField, SerializerMethodField
from rest_framework.relations import PrimaryKeyRelatedField
from rest_framework.serializers import ModelSerializer

from core.collections.constants import INCLUDE_REFERENCES_PARAM
from core.collections.models import Collection, CollectionReference
from core.common.constants import HEAD, DEFAULT_ACCESS_TYPE, NAMESPACE_REGEX, ACCESS_TYPE_CHOICES
from core.orgs.models import Organization
from core.settings import DEFAULT_LOCALE
from core.users.models import UserProfile


class CollectionListSerializer(ModelSerializer):
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    id = CharField(source='mnemonic')

    class Meta:
        model = Collection
        fields = (
            'short_code', 'name', 'url', 'owner', 'owner_type', 'owner_url', 'version', 'created_at', 'id',
            'collection_type', 'updated_at', 'canonical_url',
        )


class CollectionVersionListSerializer(ModelSerializer):
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    id = CharField(source='version')
    version_url = CharField(source='uri')
    url = CharField(source='versioned_object_url')
    previous_version_url = CharField(source='prev_version_uri')

    class Meta:
        model = Collection
        fields = (
            'short_code', 'name', 'url', 'owner', 'owner_type', 'owner_url', 'version', 'created_at', 'id',
            'collection_type', 'updated_at', 'canonical_url', 'version_url', 'previous_version_url',
        )


class CollectionCreateOrUpdateSerializer(ModelSerializer):
    class Meta:
        model = Collection
        lookup_field = 'mnemonic'
        fields = (
            '__all__'
        )

    def prepare_object(self, validated_data, instance=None):
        collection = instance if instance else Collection()
        collection.canonical_url = validated_data.get('canonical_url', collection.canonical_url)
        collection.version = validated_data.get('version', collection.version) or HEAD
        collection.mnemonic = validated_data.get(self.Meta.lookup_field, collection.mnemonic)
        collection.name = validated_data.get('name', collection.name)
        collection.full_name = validated_data.get('full_name', collection.full_name)
        collection.description = validated_data.get('description', collection.description)
        collection.collection_type = validated_data.get('collection_type', collection.collection_type)
        collection.custom_validation_schema = validated_data.get(
            'custom_validation_schema', collection.custom_validation_schema
        )
        collection.public_access = validated_data.get('public_access', collection.public_access or DEFAULT_ACCESS_TYPE)
        collection.default_locale = validated_data.get('default_locale', collection.default_locale or DEFAULT_LOCALE)
        collection.website = validated_data.get('website', collection.website)
        collection.custom_resources_linked_source = validated_data.get(
            'custom_resources_linked_source', collection.custom_resources_linked_source
        )
        collection.repository_type = validated_data.get('repository_type', collection.repository_type)
        collection.preferred_source = validated_data.get('preferred_source', collection.preferred_source)
        supported_locales = validated_data.get('supported_locales')
        if not supported_locales:
            supported_locales = collection.supported_locales
        if supported_locales and isinstance(supported_locales, str):
            supported_locales = supported_locales.split(',')

        collection.supported_locales = supported_locales
        collection.extras = validated_data.get('extras', collection.extras)
        collection.external_id = validated_data.get('external_id', collection.external_id)
        collection.user_id = validated_data.get('user_id', collection.user_id)
        collection.organization_id = validated_data.get('organization_id', collection.organization_id)
        collection.user = validated_data.get('user', collection.user)
        collection.organization = validated_data.get('organization', collection.organization)
        return collection

    def update(self, instance, validated_data):
        collection = self.prepare_object(validated_data, instance)
        user = self.context['request'].user
        errors = Collection.persist_changes(collection, user)
        self._errors.update(errors)
        return collection


class CollectionCreateSerializer(CollectionCreateOrUpdateSerializer):
    type = CharField(source='resource_type', read_only=True)
    uuid = CharField(source='id', read_only=True)
    id = CharField(required=True, validators=[RegexValidator(regex=NAMESPACE_REGEX)], source='mnemonic')
    short_code = CharField(source='mnemonic', read_only=True)
    name = CharField(required=True)
    full_name = CharField(required=False)
    description = CharField(required=False, allow_blank=True)
    collection_type = CharField(required=False)
    custom_validation_schema = CharField(required=False, allow_blank=True, allow_null=True)
    public_access = ChoiceField(required=False, choices=ACCESS_TYPE_CHOICES)
    default_locale = CharField(required=False, allow_blank=True)
    supported_locales = ListField(required=False, allow_empty=True)
    website = CharField(required=False, allow_blank=True)
    url = CharField(read_only=True)
    canonical_url = CharField(required=False, allow_null=True, allow_blank=True)
    custom_resources_linked_source = CharField(required=False, allow_null=True, allow_blank=True)
    repository_type = CharField(required=False, allow_null=True, allow_blank=True)
    preferred_source = CharField(required=False, allow_null=True, allow_blank=True)
    versions_url = CharField(read_only=True)
    concepts_url = CharField(read_only=True)
    mappings_url = CharField(read_only=True)
    owner = CharField(source='parent_resource', read_only=True)
    owner_type = CharField(source='parent_resource_type', read_only=True)
    owner_url = CharField(source='parent_url', read_only=True)
    versions = IntegerField(source='num_versions', read_only=True)
    created_on = DateTimeField(source='created_at', read_only=True)
    updated_on = DateTimeField(source='updated_at', read_only=True)
    created_by = CharField(source='owner', read_only=True)
    updated_by = CharField(read_only=True)
    extras = JSONField(required=False, allow_null=True)
    external_id = CharField(required=False, allow_blank=True)
    user_id = PrimaryKeyRelatedField(required=False, queryset=UserProfile.objects.all(), allow_null=True)
    organization_id = PrimaryKeyRelatedField(required=False, queryset=Organization.objects.all(), allow_null=True)
    version = CharField(default=HEAD)

    def create(self, validated_data):
        collection = self.prepare_object(validated_data)
        user = self.context['request'].user
        errors = Collection.persist_new(collection, user)
        self._errors.update(errors)
        return collection

    def create_version(self, validated_data):
        collection = self.prepare_object(validated_data)
        user = self.context['request'].user
        errors = Collection.persist_new_version(collection, user)
        self._errors.update(errors)
        return collection


class CollectionDetailSerializer(CollectionCreateOrUpdateSerializer):
    type = CharField(source='resource_type')
    uuid = CharField(source='id')
    id = CharField(source='mnemonic')
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    versions = IntegerField(source='num_versions')
    created_on = DateTimeField(source='created_at')
    updated_on = DateTimeField(source='updated_at')
    supported_locales = ListField(required=False, allow_empty=True)
    created_by = CharField(read_only=True, source='created_by.username')
    updated_by = CharField(read_only=True, source='updated_by.username')
    references = SerializerMethodField()

    class Meta:
        model = Collection
        lookup_field = 'mnemonic'
        fields = (
            'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'collection_type',
            'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
            'url', 'owner', 'owner_type', 'owner_url', 'versions',
            'created_on', 'updated_on', 'created_by', 'updated_by', 'extras', 'external_id', 'versions_url',
            'version', 'concepts_url', 'mappings_url', 'active_concepts', 'active_mappings', 'canonical_url',
            'custom_resources_linked_source', 'repository_type', 'preferred_source', 'references',
        )

    def get_references(self, obj):
        if self.context.get(INCLUDE_REFERENCES_PARAM, False):
            return CollectionReferenceSerializer(obj.references.all(), many=True).data

        return []


class CollectionVersionDetailSerializer(CollectionCreateOrUpdateSerializer):
    type = CharField(source='resource_type')
    uuid = CharField(source='id')
    id = CharField(source='version')
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    versions = IntegerField(source='num_versions')
    created_on = DateTimeField(source='created_at')
    updated_on = DateTimeField(source='updated_at')
    supported_locales = ListField(required=False, allow_empty=True)
    is_processing = BooleanField(read_only=True)
    version_url = CharField(source='uri')
    url = CharField(source='versioned_object_url')
    previous_version_url = CharField(source='prev_version_uri')

    class Meta:
        model = Collection
        lookup_field = 'mnemonic'
        fields = (
            'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'collection_type',
            'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
            'url', 'owner', 'owner_type', 'owner_url', 'versions', 'version_url', 'previous_version_url',
            'created_on', 'updated_on', 'created_by', 'updated_by', 'extras', 'external_id', 'version',
            'version', 'concepts_url', 'mappings_url', 'is_processing', 'canonical_url', 'released', 'retired',
        )


class CollectionReferenceSerializer(ModelSerializer):
    reference_type = CharField(read_only=True)

    class Meta:
        model = CollectionReference
        fields = ('expression', 'reference_type',)
