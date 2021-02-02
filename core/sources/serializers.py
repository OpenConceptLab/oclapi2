from django.core.validators import RegexValidator
from pydash import get
from rest_framework.fields import CharField, IntegerField, DateTimeField, ChoiceField, JSONField, ListField, \
    BooleanField, SerializerMethodField
from rest_framework.relations import PrimaryKeyRelatedField
from rest_framework.serializers import ModelSerializer

from core.common.constants import DEFAULT_ACCESS_TYPE, NAMESPACE_REGEX, ACCESS_TYPE_CHOICES, HEAD, \
    INCLUDE_SUMMARY
from core.orgs.models import Organization
from core.settings import DEFAULT_LOCALE
from core.sources.models import Source
from core.users.models import UserProfile


class SourceListSerializer(ModelSerializer):
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    id = CharField(source='mnemonic')

    class Meta:
        model = Source
        fields = (
            'short_code', 'name', 'url', 'owner', 'owner_type', 'owner_url', 'version', 'created_at', 'id',
            'source_type', 'updated_at', 'canonical_url'
        )


class SourceVersionListSerializer(ModelSerializer):
    short_code = CharField(source='mnemonic')
    owner = CharField(source='parent_resource')
    owner_type = CharField(source='parent_resource_type')
    owner_url = CharField(source='parent_url')
    id = CharField(source='version')
    version_url = CharField(source='uri')
    url = CharField(source='versioned_object_url')
    previous_version_url = CharField(source='prev_version_uri')

    class Meta:
        model = Source
        fields = (
            'short_code', 'name', 'url', 'owner', 'owner_type', 'owner_url', 'version', 'created_at', 'id',
            'source_type', 'updated_at', 'canonical_url', 'released', 'retired', 'version_url', 'previous_version_url'
        )


class SourceCreateOrUpdateSerializer(ModelSerializer):
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
        source.name = validated_data.get('name', source.name)
        source.full_name = validated_data.get('full_name', source.full_name) or source.name
        source.description = validated_data.get('description', source.description)
        source.source_type = validated_data.get('source_type', source.source_type)
        source.custom_validation_schema = validated_data.get(
            'custom_validation_schema', source.custom_validation_schema
        )
        source.public_access = validated_data.get('public_access', source.public_access or DEFAULT_ACCESS_TYPE)
        source.default_locale = validated_data.get('default_locale', source.default_locale or DEFAULT_LOCALE)
        source.website = validated_data.get('website', source.website)

        supported_locales = validated_data.get('supported_locales')
        if not supported_locales:
            supported_locales = source.supported_locales
        if supported_locales and isinstance(supported_locales, str):
            supported_locales = supported_locales.split(',')

        source.supported_locales = supported_locales or [source.default_locale]
        source.extras = validated_data.get('extras', source.extras)
        source.external_id = validated_data.get('external_id', source.external_id)
        source.user_id = validated_data.get('user_id', source.user_id)
        source.organization_id = validated_data.get('organization_id', source.organization_id)
        source.user = validated_data.get('user', source.user)
        source.organization = validated_data.get('organization', source.organization)
        source.released = validated_data.get('released', source.released)
        source.retired = validated_data.get('retired', source.retired)

        source.canonical_url = validated_data.get('canonical_url', source.canonical_url)
        source.identifier = validated_data.get('identifier', source.identifier)
        source.publisher = validated_data.get('publisher', source.publisher)
        source.contact = validated_data.get('contact', source.contact)
        source.jurisdiction = validated_data.get('jurisdiction', source.jurisdiction)
        source.purpose = validated_data.get('purpose', source.purpose)
        source.copyright = validated_data.get('copyright', source.copyright)
        source.content_type = validated_data.get('content_type', source.content_type)
        source.revision_date = validated_data.get('revision_date', source.revision_date)
        source.text = validated_data.get('text', source.text)

        return source

    def update(self, instance, validated_data):
        source = self.prepare_object(validated_data, instance)
        user = self.context['request'].user
        errors = Source.persist_changes(source, user)
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
    publisher = CharField(required=False, allow_null=True, allow_blank=True)
    purpose = CharField(required=False, allow_null=True, allow_blank=True)
    copyright = CharField(required=False, allow_null=True, allow_blank=True)
    content_type = CharField(required=False, allow_null=True, allow_blank=True)

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
            *SourceSummarySerializer.Meta.fields, 'id', 'uuid',
        )


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


class SourceDetailSerializer(SourceCreateOrUpdateSerializer):
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
    updated_by = DateTimeField(source='updated_by.username', read_only=True)
    summary = SerializerMethodField()

    class Meta:
        model = Source
        lookup_field = 'mnemonic'
        fields = (
            'type', 'uuid', 'id', 'short_code', 'name', 'full_name', 'description', 'source_type',
            'custom_validation_schema', 'public_access', 'default_locale', 'supported_locales', 'website',
            'url', 'owner', 'owner_type', 'owner_url',
            'created_on', 'updated_on', 'created_by', 'updated_by', 'extras', 'external_id', 'versions_url',
            'version', 'concepts_url', 'mappings_url',
            'canonical_url', 'identifier', 'publisher', 'contact', 'jurisdiction', 'purpose', 'copyright',
            'content_type', 'revision_date', 'logo_url', 'summary', 'text',
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
            summary = SourceSummarySerializer(obj).data

        return summary


class SourceVersionDetailSerializer(SourceCreateOrUpdateSerializer):
    type = CharField(source='resource_type')
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
            'content_type', 'revision_date', 'summary', 'text'
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
            summary = SourceVersionSummarySerializer(obj).data

        return summary


class SourceVersionExportSerializer(SourceVersionDetailSerializer):
    source = SourceDetailSerializer(source='head')

    class Meta:
        model = Source
        fields = (
            *SourceVersionDetailSerializer.Meta.fields, 'source'
        )
