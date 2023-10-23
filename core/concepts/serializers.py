from pydash import get
from rest_framework.fields import CharField, DateTimeField, BooleanField, URLField, JSONField, SerializerMethodField, \
    UUIDField, ListField, IntegerField, ReadOnlyField
from rest_framework.serializers import ModelSerializer

from core.common.constants import INCLUDE_INVERSE_MAPPINGS_PARAM, INCLUDE_MAPPINGS_PARAM, INCLUDE_EXTRAS_PARAM, \
    INCLUDE_PARENT_CONCEPTS, INCLUDE_CHILD_CONCEPTS, INCLUDE_SOURCE_VERSIONS, INCLUDE_COLLECTION_VERSIONS, \
    CREATE_PARENT_VERSION_QUERY_PARAM, INCLUDE_HIERARCHY_PATH, INCLUDE_PARENT_CONCEPT_URLS, \
    INCLUDE_CHILD_CONCEPT_URLS, HEAD, INCLUDE_SUMMARY, INCLUDE_VERBOSE_REFERENCES, VERBOSE_PARAM
from core.common.fields import EncodedDecodedCharField
from core.common.serializers import AbstractResourceSerializer
from core.common.utils import to_parent_uri_from_kwargs, get_truthy_values
from core.concepts.models import Concept, ConceptName


TRUTHY = get_truthy_values()


class LocalizedNameSerializer(ModelSerializer):
    uuid = CharField(read_only=True, source='id')
    name_type = CharField(source='type', required=False, allow_null=True, allow_blank=True)
    type = CharField(source='name_type', required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = ConceptName
        fields = (
            'uuid', 'name', 'external_id', 'type', 'locale', 'locale_preferred', 'name_type', 'checksum'
        )

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret.update({"type": "ConceptName"})
        return ret


class LocalizedDescriptionSerializer(ModelSerializer):
    uuid = CharField(read_only=True, source='id')
    description = CharField(source='name')
    description_type = CharField(source='type', required=False, allow_null=True, allow_blank=True)
    type = CharField(source='description_type', required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = ConceptName
        fields = (
            'uuid', 'description', 'external_id', 'type', 'locale', 'locale_preferred', 'description_type', 'checksum'
        )

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret.update({"type": "ConceptDescription"})
        return ret


class ConceptLabelSerializer(ModelSerializer):
    uuid = CharField(read_only=True, source='id')
    external_id = CharField(required=False)
    locale = CharField(required=True)
    locale_preferred = BooleanField(required=False, default=False)
    concept_id = IntegerField(write_only=True, required=False)

    class Meta:
        model = ConceptName
        fields = (
            'uuid', 'external_id', 'type', 'locale', 'locale_preferred', 'concept_id'
        )

    def create(self, validated_data, instance=None):  # pylint: disable=arguments-differ
        locale = instance if instance else ConceptName()
        locale.name = validated_data.get('name', locale.name)
        locale.locale = validated_data.get('locale', locale.locale)
        locale.locale_preferred = validated_data.get('locale_preferred', locale.locale_preferred)
        _type = validated_data.get('type', None)
        if _type in ['ConceptName', 'ConceptDescription']:
            _type = validated_data.get('name_type', validated_data.get('description_type', locale.type))
        locale.type = _type
        locale.external_id = validated_data.get('external_id', locale.external_id)
        locale.concept_id = validated_data.get('concept_id', locale.concept_id)
        locale.save()
        return locale


class ConceptNameSerializer(ConceptLabelSerializer):
    name = CharField(required=True)
    name_type = CharField(required=False)

    class Meta:
        model = ConceptName
        fields = (*ConceptLabelSerializer.Meta.fields, 'name', 'name_type')

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret.update({"type": "ConceptName"})
        return ret


class ConceptDescriptionSerializer(ConceptLabelSerializer):
    description = CharField(required=True, source='name')
    description_type = CharField(required=False)

    class Meta:
        model = ConceptName
        fields = (
            *ConceptLabelSerializer.Meta.fields, 'description', 'description_type'
        )

    def to_representation(self, instance):  # used to be to_native
        ret = super().to_representation(instance)
        ret.update({"type": "ConceptDescription"})
        return ret


class ConceptAbstractSerializer(AbstractResourceSerializer):
    uuid = CharField(source='id', read_only=True)
    mappings = SerializerMethodField()
    parent_concepts = SerializerMethodField()
    child_concepts = SerializerMethodField()
    hierarchy_path = SerializerMethodField()
    parent_concept_urls = ListField(allow_null=True, required=False, allow_empty=True)
    child_concept_urls = ListField(read_only=True)
    summary = SerializerMethodField()
    references = SerializerMethodField()
    checksums = SerializerMethodField()

    class Meta:
        model = Concept
        abstract = True
        fields = AbstractResourceSerializer.Meta.fields + (
            'uuid', 'parent_concept_urls', 'child_concept_urls', 'parent_concepts', 'child_concepts', 'hierarchy_path',
            'mappings', 'extras', 'summary', 'references', 'has_children', 'checksums'
        )

    def __init__(self, *args, **kwargs):  # pylint: disable=too-many-branches
        request = get(kwargs, 'context.request')
        params = get(request, 'query_params')
        self.view_kwargs = get(kwargs, 'context.view.kwargs', {})

        self.query_params = params.dict() if params else {}
        self.include_indirect_mappings = self.query_params.get(INCLUDE_INVERSE_MAPPINGS_PARAM) in TRUTHY
        self.include_direct_mappings = self.query_params.get(INCLUDE_MAPPINGS_PARAM) in TRUTHY
        self.include_parent_concept_urls = self.query_params.get(INCLUDE_PARENT_CONCEPT_URLS) in TRUTHY
        self.include_child_concept_urls = self.query_params.get(INCLUDE_CHILD_CONCEPT_URLS) in TRUTHY
        self.include_parent_concepts = self.query_params.get(INCLUDE_PARENT_CONCEPTS) in TRUTHY
        self.include_child_concepts = self.query_params.get(INCLUDE_CHILD_CONCEPTS) in TRUTHY
        self.include_hierarchy_path = self.query_params.get(INCLUDE_HIERARCHY_PATH) in TRUTHY
        self.include_extras = self.query_params.get(INCLUDE_EXTRAS_PARAM) in TRUTHY
        self.include_summary = self.query_params.get(INCLUDE_SUMMARY) in TRUTHY
        self.include_verbose_references = self.query_params.get(INCLUDE_VERBOSE_REFERENCES) in TRUTHY
        if CREATE_PARENT_VERSION_QUERY_PARAM in self.query_params:
            self.create_parent_version = self.query_params.get(CREATE_PARENT_VERSION_QUERY_PARAM) in TRUTHY
        else:
            self.create_parent_version = True

        is_verbose = self.__class__ == ConceptDetailSerializer

        try:
            if not self.include_parent_concepts:
                self.fields.pop('parent_concepts', None)
            if not self.include_child_concepts:
                self.fields.pop('child_concepts', None)
            if not self.include_child_concept_urls:
                self.fields.pop('child_concept_urls')
            if not self.include_parent_concept_urls and (not get(request, 'method') or get(request, 'method') == 'GET'):
                self.fields.pop('parent_concept_urls')
            if not self.include_hierarchy_path:
                self.fields.pop('hierarchy_path', None)
            if not self.include_extras and not is_verbose:
                self.fields.pop('extras', None)
            if not self.include_direct_mappings and not self.include_indirect_mappings:
                self.fields.pop('mappings', None)
            if not self.include_summary:
                self.fields.pop('summary', None)
            if not get(request, 'instance'):
                self.fields.pop('references', None)
            if get(params, 'onlyParentLess') not in TRUTHY:
                self.fields.pop('has_children', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    @staticmethod
    def get_checksums(obj):
        return obj.get_checksums()

    def get_references(self, obj):
        collection = get(self, 'context.request.instance')
        if collection:
            if self.include_verbose_references:
                from core.collections.serializers import CollectionReferenceSerializer
                return CollectionReferenceSerializer(obj.collection_references(collection), many=True).data
            return obj.collection_references_uris(collection)
        return None

    def get_mappings(self, obj):
        from core.mappings.serializers import MappingDetailSerializer
        context = get(self, 'context')
        is_collection = 'collection' in self.view_kwargs
        collection_version = self.view_kwargs.get('version', HEAD) if is_collection else None
        parent_uri = to_parent_uri_from_kwargs(self.view_kwargs) if is_collection else None
        if self.include_indirect_mappings:
            mappings = obj.get_bidirectional_mappings_for_collection(
                parent_uri, collection_version
            ) if is_collection else obj.get_bidirectional_mappings()
            return MappingDetailSerializer(mappings, many=True, context=context).data
        if self.include_direct_mappings:
            mappings = obj.get_unidirectional_mappings_for_collection(
                parent_uri, collection_version) if is_collection else obj.get_unidirectional_mappings()
            return MappingDetailSerializer(mappings, many=True, context=context).data

        return []

    def get_child_concepts(self, obj):
        if self.include_child_concepts:
            return ConceptDetailSerializer(obj.child_concepts.all(), many=True).data
        return None

    def get_parent_concepts(self, obj):
        if self.include_parent_concepts:
            return ConceptDetailSerializer(obj.parent_concepts.all(), many=True).data
        return None

    def get_hierarchy_path(self, obj):
        if self.include_hierarchy_path:
            return obj.get_hierarchy_path()
        return None

    def get_summary(self, obj):
        if self.include_summary:
            return ConceptSummarySerializer(obj).data
        return None


class ConceptLookupListSerializer(ModelSerializer):
    uuid = CharField(source='id')
    id = EncodedDecodedCharField(source='mnemonic')
    url = CharField(read_only=True, source='uri')
    locale = CharField(source='iso_639_1_locale', read_only=True)

    class Meta:
        model = Concept
        fields = ('uuid', 'id', 'display_name', 'url', 'locale')

    def __init__(self, *args, **kwargs):  # pylint: disable=too-many-branches
        request = get(kwargs, 'context.request')
        params = get(request, 'query_params')
        self.query_params = params.dict() if params else {}
        self.is_verbose = self.query_params.get(VERBOSE_PARAM) in TRUTHY
        try:
            if not self.is_verbose:
                self.fields.pop('display_name', None)
                self.fields.pop('locale', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)


class ConceptListSerializer(ConceptAbstractSerializer):
    type = CharField(source='resource_type', read_only=True)
    id = EncodedDecodedCharField(source='mnemonic')
    source = CharField(source='parent_resource')
    owner = CharField(source='owner_name')
    update_comment = CharField(source='comment', required=False, allow_null=True, allow_blank=True)
    locale = CharField(source='iso_639_1_locale', read_only=True)
    url = CharField(required=False, source='versioned_object_url')
    version_created_on = DateTimeField(source='created_at', read_only=True)
    version_created_by = DateTimeField(source='created_by.username', read_only=True)
    version_updated_on = DateTimeField(source='updated_at', read_only=True)
    version_updated_by = DateTimeField(source='updated_by.username', read_only=True)

    class Meta:
        model = Concept
        fields = ConceptAbstractSerializer.Meta.fields + (
            'uuid', 'id', 'external_id', 'concept_class', 'datatype', 'url', 'retired', 'source',
            'owner', 'owner_type', 'owner_url', 'display_name', 'display_locale', 'version', 'update_comment',
            'locale', 'version_created_by', 'version_created_on', 'mappings', 'is_latest_version', 'versions_url',
            'version_url', 'extras', 'type', 'versioned_object_id', 'version_updated_on', 'version_updated_by',
        )


class ConceptVersionListSerializer(ConceptListSerializer):
    previous_version_url = CharField(read_only=True, source='prev_version_uri')
    source_versions = ListField(read_only=True)
    collection_versions = ListField(read_only=True)

    class Meta:
        model = Concept
        fields = ConceptListSerializer.Meta.fields + (
            'previous_version_url', 'source_versions', 'collection_versions'
        )

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')
        self.query_params = params.dict() if params else {}
        self.include_source_versions = self.query_params.get(INCLUDE_SOURCE_VERSIONS) in TRUTHY
        self.include_collection_versions = self.query_params.get(INCLUDE_COLLECTION_VERSIONS) in TRUTHY

        try:
            if not self.include_source_versions:
                self.fields.pop('source_versions', None)
            if not self.include_collection_versions:
                self.fields.pop('collection_versions', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)


class ConceptVersionCascadeSerializer(ConceptVersionListSerializer):
    class Meta:
        model = Concept
        fields = tuple(field for field in ConceptVersionListSerializer.Meta.fields if field not in ('uuid', ))


class ConceptSummarySerializer(ModelSerializer):
    uuid = CharField(source='id', read_only=True)
    id = EncodedDecodedCharField(source='mnemonic', read_only=True)
    names = SerializerMethodField()
    descriptions = SerializerMethodField()
    versions = SerializerMethodField()
    parents = IntegerField(source='parent_concepts_count', read_only=True)
    children = IntegerField(source='children_concepts_count', read_only=True)

    class Meta:
        model = Concept
        fields = ('children', 'parents', 'names', 'descriptions', 'versions', 'id', 'uuid', 'versioned_object_id')

    @staticmethod
    def get_names(obj):
        return obj.names.count()

    @staticmethod
    def get_descriptions(obj):
        return obj.descriptions.count()

    @staticmethod
    def get_versions(obj):
        return obj.versions.count()


class ConceptMinimalSerializer(ConceptAbstractSerializer):
    id = EncodedDecodedCharField(source='mnemonic', read_only=True)
    type = CharField(source='resource_type', read_only=True)
    url = CharField(source='uri', read_only=True)

    class Meta:
        model = Concept
        fields = ConceptAbstractSerializer.Meta.fields + ('id', 'type', 'url', 'version_url', 'retired')


class ConceptCascadeMinimalSerializer(ConceptMinimalSerializer):
    class Meta:
        model = Concept
        fields = tuple(field for field in ConceptMinimalSerializer.Meta.fields if field not in ('uuid', ))


class ConceptMinimalSerializerRecursive(ConceptAbstractSerializer):
    id = EncodedDecodedCharField(source='mnemonic', read_only=True)
    type = CharField(source='resource_type', read_only=True)
    url = CharField(source='uri', read_only=True)
    entries = SerializerMethodField()

    class Meta:
        model = Concept
        fields = ConceptAbstractSerializer.Meta.fields + (
            'id', 'type', 'url', 'version_url', 'terminal', 'entries', 'display_name', 'retired')

    def __init__(self, *args, **kwargs):
        if 'mappings' in self.fields:
            self.fields.pop('mappings', None)
        if 'uuid' in self.fields:
            self.fields.pop('uuid', None)
        super().__init__(*args, **kwargs)

    def get_entries(self, obj):
        result = []
        if obj.cascaded_entries:
            result += ConceptMinimalSerializerRecursive(
                obj.cascaded_entries['concepts'], many=True, context=self.context).data

            from core.mappings.serializers import MappingMinimalSerializer, MappingReverseMinimalSerializer
            if get(self, 'context.request.query_params.reverse') in TRUTHY:
                result += MappingReverseMinimalSerializer(obj.cascaded_entries['mappings'], many=True).data
            else:
                result += MappingMinimalSerializer(obj.cascaded_entries['mappings'], many=True).data

        return result


class ConceptDetailSerializer(ConceptAbstractSerializer):
    version = CharField(read_only=True)
    type = CharField(source='versioned_resource_type', read_only=True)
    id = EncodedDecodedCharField(source='mnemonic', required=False)
    source = CharField(source='parent_resource', read_only=True)
    parent_id = UUIDField(write_only=True)
    owner = CharField(source='owner_name', read_only=True)
    created_on = DateTimeField(source='created_at', read_only=True)
    updated_on = DateTimeField(source='updated_at', read_only=True)
    names = LocalizedNameSerializer(many=True)
    descriptions = LocalizedDescriptionSerializer(many=True, allow_null=True, required=False)
    external_id = CharField(required=False, allow_blank=True)
    concept_class = CharField(required=True)
    datatype = CharField(required=True)
    display_name = CharField(read_only=True)
    display_locale = CharField(read_only=True)
    retired = BooleanField(required=False)
    owner_type = CharField(read_only=True)
    owner_url = URLField(read_only=True)
    extras = JSONField(required=False, allow_null=True)
    update_comment = CharField(required=False, source='comment', allow_null=True, allow_blank=True)
    url = CharField(required=False, source='versioned_object_url')
    updated_by = DateTimeField(source='updated_by.username', read_only=True)
    created_by = DateTimeField(source='created_by.username', read_only=True)

    class Meta:
        model = Concept
        fields = ConceptAbstractSerializer.Meta.fields + (
            'id', 'external_id', 'concept_class', 'datatype', 'url', 'retired', 'source',
            'owner', 'owner_type', 'owner_url', 'display_name', 'display_locale', 'names', 'descriptions',
            'created_on', 'updated_on', 'versions_url', 'version', 'extras', 'parent_id', 'type',
            'update_comment', 'version_url', 'updated_by', 'created_by',
            'public_can_view', 'versioned_object_id'
        )

    def create(self, validated_data):
        concept = Concept.persist_new(
            data=validated_data, user=self.context.get('request').user,
            create_parent_version=self.create_parent_version
        )
        if concept.errors:
            self._errors.update(concept.errors)
        return concept

    def update(self, instance, validated_data):
        errors = Concept.create_new_version_for(
            instance=instance, data=validated_data, user=self.context.get('request').user,
            create_parent_version=self.create_parent_version
        )
        if errors:
            self._errors.update(errors)
        return instance


class ConceptVersionExportSerializer(ModelSerializer):
    type = CharField(source='resource_type')
    uuid = CharField(source='id')
    id = EncodedDecodedCharField(source='mnemonic')
    names = LocalizedNameSerializer(many=True)
    descriptions = LocalizedDescriptionSerializer(many=True, required=False, allow_null=True)
    source = CharField(source='parent_resource')
    source_url = URLField(source='parent_url')
    owner = CharField(source='owner_name')
    created_on = DateTimeField(source='created_at', read_only=True)
    updated_on = DateTimeField(source='updated_at', read_only=True)
    version_created_on = DateTimeField(source='created_at')
    version_created_by = CharField(source='created_by')
    version_updated_on = DateTimeField(source='updated_at')
    version_updated_by = CharField(source='updated_by')
    locale = CharField(source='iso_639_1_locale')
    url = CharField(source='versioned_object_url', read_only=True)
    previous_version_url = CharField(source='prev_version_uri', read_only=True)
    update_comment = CharField(source='comment', required=False, allow_null=True, allow_blank=True)
    parent_concept_urls = ListField(read_only=True)
    child_concept_urls = ListField(read_only=True)
    checksums = SerializerMethodField()

    class Meta:
        model = Concept
        fields = (
            'type', 'uuid', 'id', 'external_id', 'concept_class', 'datatype', 'display_name', 'display_locale',
            'names', 'descriptions', 'extras', 'retired', 'source', 'source_url', 'owner', 'owner_name', 'owner_url',
            'version', 'created_on', 'updated_on', 'version_created_on', 'version_created_by', 'update_comment',
            'is_latest_version', 'locale', 'url', 'owner_type', 'version_url', 'previous_version_url',
            'parent_concept_urls', 'child_concept_urls', 'version_updated_on', 'version_updated_by', 'checksums'
        )

    @staticmethod
    def get_checksums(obj):
        return obj.get_checksums()


class ConceptVersionDetailSerializer(ModelSerializer):
    type = CharField(source='resource_type')
    uuid = CharField(source='id')
    id = EncodedDecodedCharField(source='mnemonic')
    names = LocalizedNameSerializer(many=True)
    descriptions = LocalizedDescriptionSerializer(many=True, required=False, allow_null=True)
    source = CharField(source='parent_resource')
    source_url = URLField(source='parent_url')
    owner = CharField(source='owner_name')
    created_on = DateTimeField(source='created_at', read_only=True)
    updated_on = DateTimeField(source='updated_at', read_only=True)
    version_created_on = DateTimeField(source='created_at')
    version_created_by = CharField(source='created_by')
    version_updated_on = DateTimeField(source='updated_at')
    version_updated_by = CharField(source='updated_by')
    locale = CharField(source='iso_639_1_locale')
    mappings = SerializerMethodField()
    url = CharField(source='versioned_object_url', read_only=True)
    previous_version_url = CharField(source='prev_version_uri', read_only=True)
    update_comment = CharField(source='comment', required=False, allow_null=True, allow_blank=True)
    parent_concepts = SerializerMethodField()
    child_concepts = SerializerMethodField()
    parent_concept_urls = ListField(read_only=True)
    child_concept_urls = ListField(read_only=True)
    source_versions = ListField(read_only=True)
    collection_versions = ListField(read_only=True)
    references = SerializerMethodField()

    def __init__(self, *args, **kwargs):
        request = get(kwargs, 'context.request')
        params = get(request, 'query_params')
        self.view_kwargs = get(kwargs, 'context.view.kwargs', {})

        self.include_indirect_mappings = False
        self.include_direct_mappings = False
        self.query_params = params.dict() if params else {}
        self.include_indirect_mappings = self.query_params.get(INCLUDE_INVERSE_MAPPINGS_PARAM) in TRUTHY
        self.include_direct_mappings = self.query_params.get(INCLUDE_MAPPINGS_PARAM) in TRUTHY
        self.include_parent_concepts = self.query_params.get(INCLUDE_PARENT_CONCEPTS) in TRUTHY
        self.include_child_concepts = self.query_params.get(INCLUDE_CHILD_CONCEPTS) in TRUTHY
        self.include_parent_concept_urls = self.query_params.get(INCLUDE_PARENT_CONCEPT_URLS) in TRUTHY
        self.include_child_concept_urls = self.query_params.get(INCLUDE_CHILD_CONCEPT_URLS) in TRUTHY
        self.include_verbose_references = self.query_params.get(INCLUDE_VERBOSE_REFERENCES) in TRUTHY

        try:
            if not self.include_parent_concepts:
                self.fields.pop('parent_concepts', None)
            if not self.include_child_concepts:
                self.fields.pop('child_concepts', None)
            if not self.include_child_concept_urls:
                self.fields.pop('child_concept_urls')
            if not self.include_parent_concept_urls:
                self.fields.pop('parent_concept_urls')
            if not get(request, 'instance'):
                self.fields.pop('references', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    class Meta:
        model = Concept
        fields = (
            'type', 'uuid', 'id', 'external_id', 'concept_class', 'datatype', 'display_name', 'display_locale',
            'names', 'descriptions', 'extras', 'retired', 'source', 'source_url', 'owner', 'owner_name', 'owner_url',
            'version', 'created_on', 'updated_on', 'version_created_on', 'version_created_by', 'update_comment',
            'is_latest_version', 'locale', 'url', 'owner_type', 'version_url', 'mappings', 'previous_version_url',
            'parent_concepts', 'child_concepts', 'parent_concept_urls', 'child_concept_urls',
            'source_versions', 'collection_versions', 'versioned_object_id', 'references', 'checksums',
            'version_updated_on', 'version_updated_by'
        )

    def get_references(self, obj):
        collection = get(self, 'context.request.instance')
        if collection:
            if self.include_verbose_references:
                from core.collections.serializers import CollectionReferenceSerializer
                return CollectionReferenceSerializer(obj.collection_references(collection), many=True).data
            return obj.collection_references_uris(collection)
        return None

    def get_mappings(self, obj):
        from core.mappings.serializers import MappingDetailSerializer
        context = get(self, 'context')
        is_collection = 'collection' in self.view_kwargs
        collection_version = self.view_kwargs.get('version', HEAD) if is_collection else None
        parent_uri = to_parent_uri_from_kwargs(self.view_kwargs) if is_collection else None
        if self.include_indirect_mappings:
            mappings = obj.get_bidirectional_mappings_for_collection(
                parent_uri, collection_version
            ) if is_collection else obj.get_bidirectional_mappings()
            return MappingDetailSerializer(mappings, many=True, context=context).data
        if self.include_direct_mappings:
            mappings = obj.get_unidirectional_mappings_for_collection(
                parent_uri, collection_version) if is_collection else obj.get_unidirectional_mappings()
            return MappingDetailSerializer(mappings, many=True, context=context).data

        return []

    def get_child_concepts(self, obj):
        if self.include_child_concepts:
            return ConceptDetailSerializer(obj.child_concepts.all(), many=True).data
        return None

    def get_parent_concepts(self, obj):
        if self.include_parent_concepts:
            return ConceptDetailSerializer(obj.parent_concepts.all(), many=True).data
        return None


class ConceptHierarchySerializer(ModelSerializer):
    uuid = CharField(source='id')
    id = EncodedDecodedCharField(source='mnemonic')
    url = CharField(source='uri')
    children = ListField(source='child_concept_urls')
    name = CharField(source='display_name')

    class Meta:
        model = Concept
        fields = ('uuid', 'id', 'url', 'children', 'name')


class ConceptChildrenSerializer(ConceptListSerializer):
    children = SerializerMethodField()

    class Meta:
        model = Concept
        fields = ConceptListSerializer.Meta.fields + ('children', 'has_children')

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')

        self.query_params = params.dict() if params else {}
        self.include_child_concepts = self.query_params.get(INCLUDE_CHILD_CONCEPTS) in TRUTHY

        try:
            if not self.include_child_concepts:
                self.fields.pop('children', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

        if 'has_children' not in self.fields:
            self.fields['has_children'] = ReadOnlyField()

    def get_children(self, obj):
        if self.include_child_concepts:
            return obj.child_concept_urls
        return None


class ConceptParentsSerializer(ModelSerializer):
    uuid = CharField(source='id')
    id = EncodedDecodedCharField(source='mnemonic')
    url = CharField(source='uri')
    name = CharField(source='display_name')
    parents = SerializerMethodField()

    class Meta:
        model = Concept
        fields = ('uuid', 'id', 'url', 'parents', 'name')

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')

        self.query_params = params.dict() if params else {}
        self.include_parent_concepts = self.query_params.get(INCLUDE_PARENT_CONCEPTS) in TRUTHY

        try:
            if not self.include_parent_concepts:
                self.fields.pop('parents', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)

    def get_parents(self, obj):
        if self.include_parent_concepts:
            return obj.parent_concept_urls
        return None
