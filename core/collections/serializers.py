import json
from datetime import datetime

from django.core.validators import RegexValidator
from pydash import get
from rest_framework.fields import CharField, ChoiceField, ListField, IntegerField, DateTimeField, JSONField, \
    BooleanField, SerializerMethodField
from rest_framework.relations import PrimaryKeyRelatedField
from rest_framework.serializers import ModelSerializer, Serializer

from core.client_configs.serializers import ClientConfigSerializer
from core.collections.constants import INCLUDE_REFERENCES_PARAM
from core.collections.models import Collection, CollectionReference, Expansion
from core.common.constants import HEAD, DEFAULT_ACCESS_TYPE, NAMESPACE_REGEX, ACCESS_TYPE_CHOICES, INCLUDE_SUMMARY, \
    INCLUDE_CLIENT_CONFIGS, INVALID_EXPANSION_URL
from core.orgs.models import Organization
from core.settings import DEFAULT_LOCALE
from core.sources.serializers import SourceVersionListSerializer
from core.users.models import UserProfile


class CollectionMinimalSerializer(ModelSerializer):
    id = CharField(source='mnemonic')

    class Meta:
        model = Collection
        fields = ('id', 'url')


class CollectionVersionMinimalSerializer(ModelSerializer):
    id = CharField(source='version')
    version_url = CharField(source='uri')
    type = CharField(source='resource_version_type')
    short_code = CharField(source='mnemonic')

    class Meta:
        model = Collection
        fields = ('id', 'version_url', 'type', 'short_code')


class CollectionListSerializer(ModelSerializer):
    type = CharField(source='resource_type')
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    id = CharField(source='mnemonic')
    summary = SerializerMethodField()

    class Meta:
        model = Collection
        fields = (
            'short_code', 'name', 'url', 'owner', 'owner_type', 'owner_url', 'version', 'created_at', 'id',
            'collection_type', 'updated_at', 'canonical_url', 'autoexpand_head',
            'summary', 'type',
        )

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')

        self.query_params = {}
        if params:
            self.query_params = params if isinstance(params, dict) else params.dict()
        self.include_summary = self.query_params.get(INCLUDE_SUMMARY) in ['true', True]
        try:
            if not self.include_summary:
                self.fields.pop('summary', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    def get_summary(self, obj):
        summary = None

        if self.include_summary:
            summary = CollectionSummarySerializer(obj).data

        return summary


class CollectionVersionListSerializer(ModelSerializer):
    type = CharField(source='resource_version_type')
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    id = CharField(source='version')
    version_url = CharField(source='uri')
    url = CharField(source='versioned_object_url')
    previous_version_url = CharField(source='prev_version_uri')
    autoexpand = BooleanField(source='should_auto_expand')
    expansion_url = CharField(source='expansion_uri', read_only=True)

    class Meta:
        model = Collection
        fields = (
            'type', 'short_code', 'name', 'url', 'canonical_url', 'owner', 'owner_type', 'owner_url', 'version',
            'created_at', 'id', 'collection_type', 'updated_at', 'released', 'retired', 'version_url',
            'previous_version_url', 'autoexpand', 'expansion_url',
        )


class CollectionCreateOrUpdateSerializer(ModelSerializer):
    canonical_url = CharField(allow_blank=True, allow_null=True, required=False)

    class Meta:
        model = Collection
        lookup_field = 'mnemonic'
        fields = (
            '__all__'
        )

    def prepare_object(self, validated_data, instance=None):
        collection = instance if instance else Collection()
        collection.version = validated_data.get('version', collection.version) or HEAD
        collection.mnemonic = validated_data.get(self.Meta.lookup_field, collection.mnemonic)
        collection.public_access = validated_data.get('public_access', collection.public_access or DEFAULT_ACCESS_TYPE)
        collection.default_locale = validated_data.get('default_locale', collection.default_locale or DEFAULT_LOCALE)
        supported_locales = validated_data.get('supported_locales')
        if not supported_locales:
            supported_locales = collection.supported_locales
        if supported_locales and isinstance(supported_locales, str):
            supported_locales = supported_locales.split(',')

        collection.supported_locales = supported_locales or [collection.default_locale]

        for attr in [
                'experimental', 'locked_date', 'text', 'revision_date', 'immutable', 'copyright', 'purpose',
                'jurisdiction', 'contact', 'publisher', 'identifier', 'canonical_url', 'retired', 'released',
                'organization', 'user', 'organization_id', 'user_id', 'external_id', 'extras', 'preferred_source',
                'custom_resources_linked_source', 'website', 'custom_validation_schema',
                'collection_type', 'description', 'name', 'meta',
        ]:
            setattr(collection, attr, validated_data.get(attr, get(collection, attr)))

        for attr in ['jurisdiction', 'identifier', 'contact', 'meta']:
            value = validated_data.get(attr, get(collection, attr))
            try:
                value = json.loads(value) if isinstance(value, str) else value
            except:  # pylint: disable=bare-except
                pass
            setattr(collection, attr, value)

        collection.full_name = validated_data.get('full_name', collection.full_name) or collection.name
        collection.autoexpand_head = validated_data.get('autoexpand_head', collection.autoexpand_head)
        collection.autoexpand = validated_data.get('autoexpand', collection.autoexpand)
        collection.expansion_uri = validated_data.get('expansion_uri', collection.expansion_uri)
        if collection.id and collection.expansion_uri and not collection.expansions.filter(
                uri=collection.expansion_uri).exists():
            self._errors.update({'expansion_url': INVALID_EXPANSION_URL})

        return collection

    def update(self, instance, validated_data):
        original_schema = instance.custom_validation_schema
        collection = self.prepare_object(validated_data, instance)
        if self._errors:
            return collection
        user = self.context['request'].user
        errors = Collection.persist_changes(collection, user, original_schema)
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
    text = CharField(required=False, allow_blank=True)
    collection_type = CharField(required=False)
    custom_validation_schema = CharField(required=False, allow_blank=True, allow_null=True)
    public_access = ChoiceField(required=False, choices=ACCESS_TYPE_CHOICES)
    default_locale = CharField(required=False, allow_blank=True)
    supported_locales = ListField(required=False, allow_empty=True)
    website = CharField(required=False, allow_blank=True)
    url = CharField(read_only=True)
    canonical_url = CharField(required=False, allow_null=True, allow_blank=True)
    custom_resources_linked_source = CharField(required=False, allow_null=True, allow_blank=True)
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
    identifier = JSONField(required=False, allow_null=True)
    contact = JSONField(required=False, allow_null=True)
    jurisdiction = JSONField(required=False, allow_null=True)
    meta = JSONField(required=False, allow_null=True)
    publisher = CharField(required=False, allow_null=True, allow_blank=True)
    purpose = CharField(required=False, allow_null=True, allow_blank=True)
    copyright = CharField(required=False, allow_null=True, allow_blank=True)
    experimental = BooleanField(required=False, allow_null=True, default=None)
    locked_date = DateTimeField(required=False, allow_null=True)
    autoexpand_head = BooleanField(required=False, default=True)
    autoexpand = BooleanField(required=False, default=True)

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


class ExpansionSummarySerializer(ModelSerializer):
    class Meta:
        model = Expansion
        fields = ('active_concepts', 'active_mappings')


class CollectionSummarySerializer(ModelSerializer):
    versions = IntegerField(source='num_versions')
    expansions = IntegerField(source='expansions_count')

    class Meta:
        model = Collection
        fields = ('active_mappings', 'active_concepts', 'versions', 'active_references', 'expansions')


class CollectionSummaryDetailSerializer(CollectionSummarySerializer):
    uuid = CharField(source='id')
    id = CharField(source='mnemonic')

    class Meta:
        model = Collection
        fields = (
            *CollectionSummarySerializer.Meta.fields, 'id', 'uuid',
        )


class AbstractCollectionSummaryVerboseSerializer(ModelSerializer):
    # sources = JSONField(source='referenced_sources_distribution')
    # collections = JSONField(source='referenced_collections_distribution')
    concepts = JSONField(source='concepts_distribution')
    mappings = JSONField(source='mappings_distribution')
    versions = JSONField(source='versions_distribution')
    references = JSONField(source='references_distribution')
    expansions = IntegerField(source='expansions_count')
    uuid = CharField(source='id')

    class Meta:
        model = Collection
        fields = (
            'id', 'uuid', 'concepts', 'mappings', 'versions', 'references', 'expansions',
            # 'sources', 'collections'
        )


class AbstractCollectionSummaryFieldDistributionSerializer(ModelSerializer):
    uuid = CharField(source='id')
    distribution = SerializerMethodField()

    class Meta:
        model = Collection
        fields = (
            'id', 'uuid', 'distribution'
        )

    def get_distribution(self, obj):
        result = {}
        fields = (get(self.context, 'request.query_params.distribution') or '').split(',')
        for field in fields:
            func = get(obj, f"get_{field}_distribution")
            if func:
                result[field] = func()
        return result


class CollectionSummaryVerboseSerializer(AbstractCollectionSummaryVerboseSerializer):
    id = CharField(source='mnemonic')


class CollectionVersionSummaryVerboseSerializer(AbstractCollectionSummaryVerboseSerializer):
    id = CharField(source='version')

    def __init__(self, *args, **kwargs):
        try:
            self.fields.pop('versions', None)
        except:  # pylint: disable=bare-except
            pass
        super().__init__(*args, **kwargs)


class CollectionSummaryFieldDistributionSerializer(AbstractCollectionSummaryFieldDistributionSerializer):
    id = CharField(source='mnemonic')


class CollectionVersionSummaryFieldDistributionSerializer(AbstractCollectionSummaryFieldDistributionSerializer):
    id = CharField(source='version')


class CollectionVersionSummarySerializer(ModelSerializer):
    expansions = IntegerField(source='expansions_count')

    class Meta:
        model = Collection
        fields = ('active_mappings', 'active_concepts', 'active_references', 'expansions')


class CollectionVersionSummaryDetailSerializer(CollectionVersionSummarySerializer):
    uuid = CharField(source='id')
    id = CharField(source='version')

    class Meta:
        model = Collection
        fields = (
            *CollectionVersionSummarySerializer.Meta.fields, 'id', 'uuid',
        )


class CollectionDetailSerializer(CollectionCreateOrUpdateSerializer):
    type = CharField(source='resource_type')
    uuid = CharField(source='id')
    id = CharField(source='mnemonic')
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    created_on = DateTimeField(source='created_at')
    updated_on = DateTimeField(source='updated_at')
    supported_locales = ListField(required=False, allow_empty=True)
    created_by = CharField(read_only=True, source='created_by.username')
    updated_by = CharField(read_only=True, source='updated_by.username')
    references = SerializerMethodField()
    summary = SerializerMethodField()
    client_configs = SerializerMethodField()
    expansion_url = CharField(source='expansion_uri', read_only=True, allow_null=True, allow_blank=True)

    class Meta:
        model = Collection
        lookup_field = 'mnemonic'
        fields = (
            'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'collection_type',
            'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
            'url', 'owner', 'owner_type', 'owner_url',
            'created_on', 'updated_on', 'created_by', 'updated_by', 'extras', 'external_id', 'versions_url',
            'version', 'concepts_url', 'mappings_url', 'expansions_url',
            'custom_resources_linked_source', 'preferred_source', 'references',
            'canonical_url', 'identifier', 'publisher', 'contact', 'jurisdiction', 'purpose', 'copyright', 'meta',
            'immutable', 'revision_date', 'logo_url', 'summary', 'text', 'client_configs',
            'experimental', 'locked_date', 'autoexpand_head', 'expansion_url'
        )

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')

        self.query_params = {}
        if params:
            self.query_params = params if isinstance(params, dict) else params.dict()
        self.include_summary = self.query_params.get(INCLUDE_SUMMARY) in ['true', True]
        self.include_client_configs = self.query_params.get(INCLUDE_CLIENT_CONFIGS) in ['true', True]

        try:
            if not self.include_summary:
                self.fields.pop('summary', None)
            if not self.include_client_configs:
                self.fields.pop('client_configs', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    def get_summary(self, obj):
        summary = None

        if self.include_summary:
            summary = CollectionSummarySerializer(obj).data

        return summary

    def get_client_configs(self, obj):
        if self.include_client_configs:
            return ClientConfigSerializer(obj.client_configs.filter(is_active=True), many=True).data

        return None

    def get_references(self, obj):
        if self.context.get(INCLUDE_REFERENCES_PARAM, False):
            return CollectionReferenceSerializer(obj.references.all(), many=True).data

        return []

    def to_representation(self, instance):  # used to be to_native
        ret = super().to_representation(instance)
        ret.update({"supported_locales": instance.get_supported_locales()})
        return ret


class CollectionVersionDetailSerializer(CollectionCreateOrUpdateSerializer):
    type = CharField(source='resource_version_type')
    uuid = CharField(source='id')
    id = CharField(source='version')
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    created_on = DateTimeField(source='created_at')
    updated_on = DateTimeField(source='updated_at')
    supported_locales = ListField(required=False, allow_empty=True)
    is_processing = BooleanField(read_only=True)
    released = BooleanField(default=False)
    version_url = CharField(source='uri')
    url = CharField(source='versioned_object_url')
    previous_version_url = CharField(source='prev_version_uri')
    created_by = CharField(read_only=True, source='created_by.username')
    updated_by = CharField(read_only=True, source='updated_by.username')
    summary = SerializerMethodField()
    autoexpand = SerializerMethodField()
    expansion_url = CharField(source='expansion_uri', allow_null=True, allow_blank=True)

    class Meta:
        model = Collection
        lookup_field = 'mnemonic'
        fields = (
            'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'collection_type',
            'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
            'url', 'owner', 'owner_type', 'owner_url', 'version_url', 'previous_version_url',
            'created_on', 'updated_on', 'created_by', 'updated_by', 'extras', 'external_id', 'version',
            'version', 'concepts_url', 'mappings_url', 'expansions_url', 'is_processing', 'released', 'retired',
            'canonical_url', 'identifier', 'publisher', 'contact', 'jurisdiction', 'purpose', 'copyright', 'meta',
            'immutable', 'revision_date', 'summary', 'text', 'experimental', 'locked_date',
            'autoexpand', 'expansion_url'
        )

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')
        self.include_summary = False
        if params:
            self.query_params = params.dict()
            self.include_summary = self.query_params.get(INCLUDE_SUMMARY) in ['true', True]

        try:
            if not self.include_summary:
                self.fields.pop('summary', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    def get_summary(self, obj):
        summary = None

        if self.include_summary:
            summary = CollectionVersionSummarySerializer(obj).data

        return summary

    @staticmethod
    def get_autoexpand(obj):
        return obj.should_auto_expand


class CollectionReferenceSerializer(ModelSerializer):
    reference_type = CharField(read_only=True)
    uuid = CharField(source='id', read_only=True)
    type = CharField(source='resource_type', read_only=True)

    class Meta:
        model = CollectionReference
        fields = ('expression', 'reference_type', 'id', 'last_resolved_at', 'uri', 'uuid', 'include', 'type')


class CollectionReferenceDetailSerializer(CollectionReferenceSerializer):
    concepts = IntegerField(source='concepts_count', read_only=True)
    mappings = IntegerField(source='mappings_count', read_only=True)

    class Meta:
        model = CollectionReference
        fields = (
            *CollectionReferenceSerializer.Meta.fields,
            'code', 'resource_version', 'namespace', 'system', 'version', 'valueset', 'cascade', 'filter', 'display',
            'created_at', 'updated_at', 'concepts', 'mappings', 'translation', 'transform'
        )


class CollectionVersionExportSerializer(CollectionVersionDetailSerializer):
    collection = JSONField(source='snapshot')

    class Meta:
        model = Collection
        fields = (
            *CollectionVersionDetailSerializer.Meta.fields, 'collection'
        )


class ExpansionSerializer(ModelSerializer):
    summary = SerializerMethodField()
    url = CharField(source='uri', read_only=True)
    parameters = JSONField()

    class Meta:
        model = Expansion
        fields = (
            'mnemonic', 'id', 'parameters', 'canonical_url', 'url', 'summary', 'is_processing',
        )

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')
        self.include_summary = False
        if params:
            self.query_params = params.dict()
            self.include_summary = self.query_params.get(INCLUDE_SUMMARY) in ['true', True]

        try:
            if not self.include_summary:
                self.fields.pop('summary', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    def get_summary(self, obj):
        summary = None

        if self.include_summary:
            summary = ExpansionSummarySerializer(obj).data

        return summary


class ExpansionDetailSerializer(ModelSerializer):
    summary = SerializerMethodField()
    url = CharField(source='uri', read_only=True)
    parameters = JSONField()
    created_on = DateTimeField(source='created_at', read_only=True)
    created_by = DateTimeField(source='created_by.username', read_only=True)
    resolved_collection_versions = CollectionVersionListSerializer(many=True, read_only=True)
    resolved_source_versions = SourceVersionListSerializer(many=True, read_only=True)

    class Meta:
        model = Expansion
        fields = (
            'mnemonic', 'id', 'parameters', 'canonical_url', 'url', 'summary', 'created_on', 'created_by',
            'is_processing', 'resolved_collection_versions', 'resolved_source_versions'
        )

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')
        self.include_summary = False
        if params:
            self.query_params = params.dict()
            self.include_summary = self.query_params.get(INCLUDE_SUMMARY) in ['true', True]

        try:
            if not self.include_summary:
                self.fields.pop('summary', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    def get_summary(self, obj):
        summary = None

        if self.include_summary:
            summary = ExpansionSummarySerializer(obj).data

        return summary


class ReferenceExpressionResolveSerializer(Serializer):  # pylint: disable=abstract-method
    reference_type = SerializerMethodField()
    resolved = SerializerMethodField()
    timestamp = SerializerMethodField()

    class Meta:
        fields = (
            'reference_type', 'resolved', 'timestamp'
        )

    @staticmethod
    def get_reference_type(obj):
        return 'canonical' if get(obj, 'is_fqdn') else 'relative'

    @staticmethod
    def get_resolved(obj):
        return bool(obj.id)

    @staticmethod
    def get_timestamp(_):
        return datetime.now()
