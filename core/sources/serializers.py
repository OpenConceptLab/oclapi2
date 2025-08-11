import json

from django.core.validators import RegexValidator
from pydash import get, compact
from rest_framework.fields import CharField, IntegerField, DateTimeField, ChoiceField, JSONField, ListField, \
    BooleanField, SerializerMethodField
from rest_framework.relations import PrimaryKeyRelatedField
from rest_framework.serializers import ModelSerializer

from core.client_configs.serializers import ClientConfigSerializer
from core.common.constants import DEFAULT_ACCESS_TYPE, NAMESPACE_REGEX, ACCESS_TYPE_CHOICES, HEAD, \
    INCLUDE_SUMMARY, INCLUDE_CLIENT_CONFIGS, INCLUDE_HIERARCHY_ROOT, INCLUDE_STATES, INCLUDE_TASKS
from core.common.serializers import AbstractRepoResourcesSerializer, AbstractResourceSerializer
from core.common.utils import get_truthy_values
from core.orgs.models import Organization
from core.settings import DEFAULT_LOCALE
from core.sources.models import Source
from core.users.models import UserProfile


TRUTHY = get_truthy_values()


class SourceMinimalSerializer(AbstractResourceSerializer):
    id = CharField(source='mnemonic')
    type = CharField(source='resource_type')

    class Meta:
        model = Source
        fields = AbstractResourceSerializer.Meta.fields + ('id', 'url', 'type', 'name', 'description')


class SourceVersionMinimalSerializer(ModelSerializer):
    id = CharField(source='version')
    version_url = CharField(source='uri')
    type = CharField(source='resource_version_type')
    short_code = CharField(source='mnemonic')

    class Meta:
        model = Source
        fields = ('id', 'version_url', 'type', 'short_code', 'released', 'description', 'name')


class SourceListSerializer(AbstractResourceSerializer):
    type = CharField(source='resource_type')
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    id = CharField(source='mnemonic')
    summary = SerializerMethodField()
    checksums = SerializerMethodField()

    class Meta:
        model = Source
        fields = AbstractResourceSerializer.Meta.fields + (
            'short_code', 'name', 'url', 'owner', 'owner_type', 'owner_url', 'version', 'created_at', 'id',
            'source_type', 'updated_at', 'canonical_url', 'summary', 'type', 'checksums'
        )

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')

        self.query_params = {}
        if params:
            self.query_params = params if isinstance(params, dict) else params.dict()
        self.include_summary = self.query_params.get(INCLUDE_SUMMARY) in TRUTHY
        try:
            if not self.include_summary:
                self.fields.pop('summary', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    def get_summary(self, obj):
        summary = None

        if self.include_summary:
            summary = SourceSummarySerializer(obj).data

        return summary

    @staticmethod
    def get_checksums(obj):
        return obj.get_all_checksums()


class SourceVersionListSerializer(ModelSerializer):
    type = CharField(source='resource_version_type')
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    id = CharField(source='version')
    version_url = CharField(source='uri')
    url = CharField(source='versioned_object_url')
    previous_version_url = CharField(source='prev_version_uri')
    checksums = SerializerMethodField()

    class Meta:
        model = Source
        fields = (
            'type', 'short_code', 'name', 'url', 'canonical_url', 'owner', 'owner_type', 'owner_url', 'version',
            'created_at', 'id', 'source_type', 'updated_at', 'released', 'retired', 'version_url',
            'previous_version_url', 'checksums', 'match_algorithms'
        )

    @staticmethod
    def get_checksums(obj):
        return obj.get_all_checksums()


class SourceCreateOrUpdateSerializer(ModelSerializer):
    canonical_url = CharField(allow_blank=True, allow_null=True, required=False)

    class Meta:
        model = Source
        lookup_field = 'mnemonic'
        fields = (
            '__all__'
        )

    def prepare_object(self, validated_data, instance=None):
        source = instance if instance else Source()
        source.version = validated_data.get('version', source.version) or HEAD
        source.mnemonic = validated_data.get(self.Meta.lookup_field, source.mnemonic)
        source.public_access = validated_data.get('public_access', source.public_access or DEFAULT_ACCESS_TYPE)
        source.default_locale = validated_data.get('default_locale', source.default_locale or DEFAULT_LOCALE)

        supported_locales = validated_data.get('supported_locales')
        if not supported_locales:
            supported_locales = source.supported_locales
        if supported_locales and isinstance(supported_locales, str):
            supported_locales = supported_locales.split(',')

        source.supported_locales = supported_locales or [source.default_locale]

        for attr in [
                'website', 'description', 'source_type', 'name', 'custom_validation_schema',
                'retired', 'released', 'organization', 'user', 'organization_id', 'user_id', 'external_id', 'extras',
                'experimental', 'case_sensitive', 'collection_reference', 'hierarchy_meaning', 'compositional',
                'version_needed', 'canonical_url', 'identifier', 'publisher', 'contact', 'jurisdiction', 'purpose',
                'copyright', 'content_type', 'revision_date', 'text', 'meta',
                'autoid_concept_mnemonic', 'autoid_concept_external_id',
                'autoid_mapping_mnemonic', 'autoid_mapping_external_id',
                'autoid_concept_mnemonic_start_from', 'autoid_concept_external_id_start_from',
                'autoid_mapping_mnemonic_start_from', 'autoid_mapping_external_id_start_from',
                'autoid_concept_name_external_id', 'autoid_concept_description_external_id', 'properties', 'filters',
                'match_algorithms'
        ]:
            setattr(source, attr, validated_data.get(attr, get(source, attr)))
        for attr in ['jurisdiction', 'identifier', 'contact', 'meta']:
            value = validated_data.get(attr, get(source, attr))
            try:
                value = json.loads(value) if isinstance(value, str) else value
            except:  # pylint: disable=bare-except
                pass
            setattr(source, attr, value)

        source.full_name = validated_data.get('full_name', source.full_name) or source.name

        if 'hierarchy_root' in validated_data or 'hierarchy_root_url' in validated_data:
            hierarchy_root_url = get(validated_data, 'hierarchy_root.url') or get(
                validated_data, 'hierarchy_root_url')
            from core.concepts.models import Concept
            source.hierarchy_root = Concept.objects.filter(
                uri=hierarchy_root_url).first() if hierarchy_root_url else None
        return source

    def update(self, instance, validated_data):
        original_schema = instance.custom_validation_schema
        source = self.prepare_object(validated_data, instance)
        user = self.context['request'].user
        errors = Source.persist_changes(source, user, original_schema)
        self._errors.update(errors)
        return source


class SourceCreateSerializer(SourceCreateOrUpdateSerializer):
    type = CharField(source='resource_type', read_only=True)
    uuid = CharField(source='id', read_only=True)
    id = CharField(required=True, validators=[RegexValidator(regex=NAMESPACE_REGEX)], source='mnemonic')
    short_code = CharField(source='mnemonic', read_only=True)
    name = CharField(required=True)
    full_name = CharField(required=False)
    description = CharField(required=False, allow_blank=True)
    text = CharField(required=False, allow_blank=True)
    source_type = CharField(required=False, allow_blank=True)
    custom_validation_schema = CharField(required=False, allow_blank=True, allow_null=True)
    public_access = ChoiceField(required=False, choices=ACCESS_TYPE_CHOICES)
    default_locale = CharField(required=False, allow_blank=True)
    supported_locales = ListField(required=False, allow_empty=True)
    website = CharField(required=False, allow_blank=True)
    url = CharField(read_only=True)
    canonical_url = CharField(required=False, allow_null=True, allow_blank=True)
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
    content_type = CharField(required=False, allow_null=True, allow_blank=True)
    experimental = BooleanField(required=False, allow_null=True, default=None)
    case_sensitive = BooleanField(required=False, allow_null=True, default=None)
    compositional = BooleanField(required=False, allow_null=True, default=None)
    version_needed = BooleanField(required=False, allow_null=True, default=None)
    hierarchy_meaning = CharField(required=False, allow_null=True, allow_blank=True)
    collection_reference = CharField(required=False, allow_null=True, allow_blank=True)
    hierarchy_root_url = CharField(allow_null=True, allow_blank=True, required=False)

    def create(self, validated_data):
        source = self.prepare_object(validated_data)
        user = self.context['request'].user
        errors = Source.persist_new(source, user)
        self._errors.update(errors)
        return source

    def create_version(self, validated_data):
        source = self.prepare_object(validated_data)
        user = self.context['request'].user
        errors = Source.persist_new_version(source, user)
        self._errors.update(errors)
        return source


class SourceSummarySerializer(ModelSerializer):
    versions = IntegerField(source='num_versions')

    class Meta:
        model = Source
        fields = ('active_mappings', 'active_concepts', 'versions')


class SourceSummaryDetailSerializer(SourceSummarySerializer):
    uuid = CharField(source='id')
    id = CharField(source='mnemonic')

    class Meta:
        model = Source
        fields = (
            'id', 'uuid', *SourceSummarySerializer.Meta.fields
        )


class AbstractSourceSummaryVerboseSerializer(ModelSerializer):
    concepts = JSONField(source='concepts_distribution')
    mappings = JSONField(source='mappings_distribution')
    versions = JSONField(source='versions_distribution')
    uuid = CharField(source='id')

    class Meta:
        model = Source
        fields = (
            'id', 'uuid', 'concepts', 'mappings', 'versions', 'default_locale', 'supported_locales'
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        user = self.context['request'].user
        if not get(user, 'is_authenticated'):
            data['concepts'].pop('contributors', None)
            data['mappings'].pop('contributors', None)
        return data


class AbstractSourceSummaryFieldDistributionSerializer(ModelSerializer):
    uuid = CharField(source='id')
    distribution = SerializerMethodField()

    class Meta:
        model = Source
        fields = (
            'id', 'uuid', 'distribution'
        )

    def get_distribution(self, obj):
        result = {}
        fields = compact((get(self.context, 'request.query_params.distribution') or '').split(','))
        source_names = compact((get(self.context, 'request.query_params.sources') or '').split(','))
        for field in fields:
            func = get(obj, f"get_{field}_distribution")
            if func:
                kwargs = {
                    'source_names': source_names
                } if field in ['to_sources_map_type', 'from_sources_map_type'] else {}
                result[field] = func(**kwargs)
        return result


class SourceSummaryVerboseSerializer(AbstractSourceSummaryVerboseSerializer):
    id = CharField(source='mnemonic')


class SourceSummaryFieldDistributionSerializer(AbstractSourceSummaryFieldDistributionSerializer):
    id = CharField(source='mnemonic')


class SourceVersionSummarySerializer(ModelSerializer):
    class Meta:
        model = Source
        fields = ('active_mappings', 'active_concepts')


class SourceVersionSummaryDetailSerializer(SourceVersionSummarySerializer):
    uuid = CharField(source='id')
    id = CharField(source='version')

    class Meta:
        model = Source
        fields = (
            *SourceVersionSummarySerializer.Meta.fields, 'id', 'uuid',
        )


class SourceVersionSummaryVerboseSerializer(AbstractSourceSummaryVerboseSerializer):
    id = CharField(source='version')

    def __init__(self, *args, **kwargs):
        try:
            self.fields.pop('versions', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)


class SourceVersionSummaryFieldDistributionSerializer(AbstractSourceSummaryFieldDistributionSerializer):
    id = CharField(source='version')


class SourceDetailSerializer(SourceCreateOrUpdateSerializer, AbstractRepoResourcesSerializer):
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
    created_by = CharField(source='created_by.username', read_only=True)
    updated_by = CharField(source='updated_by.username', read_only=True)
    summary = SerializerMethodField()
    client_configs = SerializerMethodField()
    hierarchy_root = SerializerMethodField()
    hierarchy_root_url = CharField(source='hierarchy_root.url', required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = Source
        lookup_field = 'mnemonic'
        fields = (
            'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'source_type',
            'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
            'url', 'owner', 'owner_type', 'owner_url', 'created_on', 'updated_on', 'created_by', 'updated_by',
            'extras', 'external_id', 'versions_url', 'version', 'concepts_url', 'mappings_url',
            'canonical_url', 'identifier', 'publisher', 'contact', 'jurisdiction', 'purpose', 'copyright',
            'content_type', 'revision_date', 'logo_url', 'summary', 'text', 'client_configs',
            'experimental', 'case_sensitive', 'collection_reference', 'hierarchy_meaning', 'compositional',
            'version_needed', 'hierarchy_root_url', 'hierarchy_root', 'meta',
            'autoid_concept_mnemonic', 'autoid_concept_external_id',
            'autoid_concept_name_external_id', 'autoid_concept_description_external_id',
            'autoid_mapping_mnemonic', 'autoid_mapping_external_id',
            'autoid_concept_mnemonic_start_from', 'autoid_concept_external_id_start_from',
            'autoid_mapping_mnemonic_start_from', 'autoid_mapping_external_id_start_from', 'checksums',
            'properties', 'filters', 'match_algorithms'
        ) + AbstractRepoResourcesSerializer.Meta.fields

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')

        self.query_params = {}
        if params:
            self.query_params = params if isinstance(params, dict) else params.dict()
        self.include_summary = self.query_params.get(INCLUDE_SUMMARY) in TRUTHY
        self.include_client_configs = self.query_params.get(INCLUDE_CLIENT_CONFIGS) in TRUTHY
        self.include_hierarchy_root = self.query_params.get(INCLUDE_HIERARCHY_ROOT) in TRUTHY

        try:
            if not self.include_summary:
                self.fields.pop('summary', None)
            if not self.include_client_configs:
                self.fields.pop('client_configs')
            if not self.include_hierarchy_root:
                self.fields.pop('hierarchy_root')
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    def get_summary(self, obj):
        summary = None

        if self.include_summary:
            summary = SourceSummarySerializer(obj).data

        return summary

    def get_client_configs(self, obj):
        if self.include_client_configs:
            return ClientConfigSerializer(obj.client_configs.filter(is_active=True), many=True).data

        return None

    def get_hierarchy_root(self, obj):
        if self.include_hierarchy_root:
            from core.concepts.serializers import ConceptDetailSerializer
            return ConceptDetailSerializer(obj.hierarchy_root).data
        return None

    def to_representation(self, instance):  # used to be to_native
        ret = super().to_representation(instance)
        ret.update({"supported_locales": instance.get_supported_locales()})
        return ret


class SourceVersionDetailSerializer(SourceCreateOrUpdateSerializer, AbstractRepoResourcesSerializer):
    type = CharField(source='resource_version_type')
    uuid = CharField(source='id')
    id = CharField(source='version')
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    created_on = DateTimeField(source='created_at')
    updated_on = DateTimeField(source='updated_at')
    created_by = CharField(source='created_by.username', read_only=True)
    updated_by = DateTimeField(source='updated_by.username', read_only=True)
    supported_locales = ListField(required=False, allow_empty=True)
    is_processing = BooleanField(read_only=True)
    released = BooleanField(default=False)
    version_url = CharField(source='uri')
    url = CharField(source='versioned_object_url')
    previous_version_url = CharField(source='prev_version_uri')
    summary = SerializerMethodField()
    states = SerializerMethodField()
    tasks = SerializerMethodField()
    hierarchy_root_url = CharField(source='hierarchy_root.url', required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = Source
        lookup_field = 'mnemonic'
        fields = (
            'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'source_type',
            'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
            'url', 'owner', 'owner_type', 'owner_url', 'retired', 'version_url', 'previous_version_url',
            'created_on', 'updated_on', 'created_by', 'updated_by', 'extras', 'external_id',
            'version', 'concepts_url', 'mappings_url', 'is_processing', 'released',
            'canonical_url', 'identifier', 'publisher', 'contact', 'jurisdiction', 'purpose', 'copyright',
            'content_type', 'revision_date', 'summary', 'text', 'meta',
            'experimental', 'case_sensitive', 'collection_reference', 'hierarchy_meaning', 'compositional',
            'version_needed', 'hierarchy_root_url', 'checksums', 'states', 'tasks', 'properties', 'filters',
            'match_algorithms'
        ) + AbstractRepoResourcesSerializer.Meta.fields

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')
        self.include_summary = False
        self.include_states = False
        self.include_tasks = False
        if params:
            self.query_params = params.dict()
            self.include_summary = self.query_params.get(INCLUDE_SUMMARY) in TRUTHY
            self.include_states = self.query_params.get(INCLUDE_STATES) in TRUTHY
            self.include_tasks = self.query_params.get(INCLUDE_TASKS) in TRUTHY

        try:
            if not self.include_summary:
                self.fields.pop('summary', None)
            if not self.include_states:
                self.fields.pop('states', None)
            if not self.include_tasks:
                self.fields.pop('tasks', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    def get_summary(self, obj):
        summary = None

        if self.include_summary:
            summary = SourceVersionSummarySerializer(obj).data

        return summary

    def get_states(self, obj):
        states = None

        if self.include_states:
            states = obj.states

        return states

    def get_tasks(self, obj):
        tasks = None

        if self.include_tasks:
            tasks = obj.get_tasks_info()

        return tasks


class SourceVersionExportSerializer(SourceVersionDetailSerializer):
    source = JSONField(source='snapshot')

    class Meta:
        model = Source
        fields = (
            *SourceVersionDetailSerializer.Meta.fields, 'source'
        )
