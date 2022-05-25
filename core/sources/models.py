import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import UniqueConstraint, F
from pydash import compact

from core.common.models import ConceptContainerModel
from core.common.services import PostgresQL
from core.concepts.models import LocalizedText
from core.sources.constants import SOURCE_TYPE, SOURCE_VERSION_TYPE, HIERARCHY_ROOT_MUST_BELONG_TO_SAME_SOURCE, \
    HIERARCHY_MEANINGS, AUTO_ID_CHOICES, AUTO_ID_SEQUENTIAL, AUTO_ID_UUID


class Source(ConceptContainerModel):
    es_fields = {
        'source_type': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'mnemonic': {'sortable': True, 'filterable': True, 'exact': True},
        'name': {'sortable': True, 'filterable': True, 'exact': True},
        'last_update': {'sortable': True, 'filterable': False, 'default': 'desc'},
        'locale': {'sortable': False, 'filterable': True, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'custom_validation_schema': {'sortable': False, 'filterable': True, 'facet': True},
        'canonical_url': {'sortable': True, 'filterable': True},
        'experimental': {'sortable': False, 'filterable': False, 'facet': False},
        'hierarchy_meaning': {'sortable': False, 'filterable': True, 'facet': True},
        'external_id': {'sortable': False, 'filterable': True, 'facet': False, 'exact': False},
    }

    class Meta:
        db_table = 'sources'
        constraints = [
            UniqueConstraint(
                fields=['mnemonic', 'version', 'organization'],
                name="org_source_unique",
                condition=models.Q(user=None),
            ),
            UniqueConstraint(
                fields=['mnemonic', 'version', 'user'],
                name="user_source_unique",
                condition=models.Q(organization=None),
            )
        ]
        indexes = [] + ConceptContainerModel.Meta.indexes
        # + index on UPPER(mnemonic) in custom migration 0022

    source_type = models.TextField(blank=True, null=True)
    content_type = models.TextField(blank=True, null=True)
    collection_reference = models.CharField(null=True, blank=True, max_length=100)
    hierarchy_meaning = models.CharField(null=True, blank=True, max_length=50, choices=HIERARCHY_MEANINGS)
    case_sensitive = models.BooleanField(null=True, blank=True, default=None)
    compositional = models.BooleanField(null=True, blank=True, default=None)
    version_needed = models.BooleanField(null=True, blank=True, default=None)
    hierarchy_root = models.ForeignKey('concepts.Concept', null=True, blank=True, on_delete=models.SET_NULL)
    # auto-id
    autoid_concept_mnemonic = models.CharField(null=True, blank=True, choices=AUTO_ID_CHOICES, max_length=10)
    autoid_concept_external_id = models.CharField(null=True, blank=True, choices=AUTO_ID_CHOICES, max_length=10)
    autoid_mapping_mnemonic = models.CharField(
        null=True, blank=True, choices=AUTO_ID_CHOICES, max_length=10, default=AUTO_ID_SEQUENTIAL)
    autoid_mapping_external_id = models.CharField(null=True, blank=True, choices=AUTO_ID_CHOICES, max_length=10)

    OBJECT_TYPE = SOURCE_TYPE
    OBJECT_VERSION_TYPE = SOURCE_VERSION_TYPE

    @property
    def is_sequential_concept_mnemonic(self):
        return self.autoid_concept_mnemonic == AUTO_ID_SEQUENTIAL

    @property
    def is_sequential_concept_external_id(self):
        return self.autoid_concept_external_id == AUTO_ID_SEQUENTIAL

    @property
    def is_sequential_mapping_mnemonic(self):
        return self.autoid_mapping_mnemonic == AUTO_ID_SEQUENTIAL

    @property
    def is_sequential_mapping_external_id(self):
        return self.autoid_mapping_external_id == AUTO_ID_SEQUENTIAL

    @property
    def concept_mnemonic_next(self):
        return self.get_resource_next_attr_id(
            self.concepts_set, self.autoid_concept_mnemonic, self.concepts_mnemonic_seq_name)

    @property
    def concept_external_id_next(self):
        return self.get_resource_next_attr_id(
            self.concepts_set, self.autoid_concept_external_id, self.concepts_external_id_seq_name)

    @property
    def mapping_mnemonic_next(self):
        return self.get_resource_next_attr_id(
            self.mappings_set, self.autoid_mapping_mnemonic, self.mappings_mnemonic_seq_name)

    @property
    def mapping_external_id_next(self):
        return self.get_resource_next_attr_id(
            self.mappings_set, self.autoid_mapping_external_id, self.mappings_external_id_seq_name)

    @staticmethod
    def get_resource_next_attr_id(queryset, attr_type, seq):
        if attr_type == AUTO_ID_UUID:
            return uuid.uuid4()
        if attr_type == AUTO_ID_SEQUENTIAL:
            next_value = PostgresQL.next_value(seq)
            while queryset.filter(mnemonic=next_value).exists():
                next_value = PostgresQL.next_value(seq)
            return str(next_value)
        return None

    @staticmethod
    def get_search_document():
        from core.sources.documents import SourceDocument
        return SourceDocument

    @classmethod
    def get_base_queryset(cls, params):
        source = params.pop('source', None)
        queryset = super().get_base_queryset(params)
        if source:
            queryset = queryset.filter(cls.get_exact_or_criteria('mnemonic', source))

        return queryset

    @staticmethod
    def get_resource_url_kwarg():
        return 'source'

    @property
    def source(self):
        return self.mnemonic

    def update_version_data(self, head):
        super().update_version_data(head)
        self.source_type = head.source_type
        self.content_type = head.content_type
        self.collection_reference = head.collection_reference
        self.hierarchy_meaning = head.hierarchy_meaning
        self.hierarchy_root_id = head.hierarchy_root_id
        self.case_sensitive = head.case_sensitive
        self.compositional = head.compositional
        self.version_needed = head.version_needed

    def get_concept_name_locales(self):
        return LocalizedText.objects.filter(name_locales__in=self.get_active_concepts())

    def is_validation_necessary(self):
        origin_source = self.get_latest_version()

        if origin_source.custom_validation_schema == self.custom_validation_schema:
            return False

        return self.custom_validation_schema is not None and self.active_concepts

    def update_mappings(self):
        from core.mappings.models import Mapping
        uris = compact([self.uri, self.canonical_url])
        for mapping in Mapping.objects.filter(to_source__isnull=True, to_source_url__in=uris):
            mapping.to_source = self
            mapping.save()

        for mapping in Mapping.objects.filter(from_source__isnull=True, from_source_url__in=uris):
            mapping.from_source = self
            mapping.save()

    def is_hierarchy_root_belonging_to_self(self):
        hierarchy_root = self.hierarchy_root
        if self.is_head:
            return hierarchy_root.parent_id == self.id
        return hierarchy_root.parent_id == self.head.id

    def clean(self):
        super().clean()
        if self.hierarchy_root_id and not self.is_hierarchy_root_belonging_to_self():
            raise ValidationError({'hierarchy_root': [HIERARCHY_ROOT_MUST_BELONG_TO_SAME_SOURCE]})

    def get_parentless_concepts(self):
        return self.concepts.filter(parent_concepts__isnull=True, id=F('versioned_object_id'))

    def hierarchy(self, offset=0, limit=100):
        from core.concepts.serializers import ConceptHierarchySerializer
        hierarchy_root = None
        if offset == 0:
            hierarchy_root = self.hierarchy_root

        parent_less_children = self.get_parentless_concepts()
        if hierarchy_root:
            parent_less_children = parent_less_children.exclude(mnemonic=hierarchy_root.mnemonic)

        total_count = parent_less_children.count()
        adjusted_limit = limit
        if hierarchy_root:
            adjusted_limit -= 1
        parent_less_children = parent_less_children.order_by('mnemonic')[offset:adjusted_limit+offset]

        children = []
        if parent_less_children.exists():
            children = ConceptHierarchySerializer(parent_less_children, many=True).data

        if hierarchy_root:
            children.append({**ConceptHierarchySerializer(hierarchy_root).data, 'root': True})

        return dict(
            id=self.mnemonic,
            children=children,
            count=total_count + (1 if hierarchy_root else 0),
            offset=offset,
            limit=limit
        )

    def set_active_concepts(self):
        queryset = self.concepts.filter(retired=False, is_active=True)
        if self.is_head:
            queryset = queryset.filter(id=F('versioned_object_id'))
        self.active_concepts = queryset.count()

    def set_active_mappings(self):
        queryset = self.mappings.filter(retired=False, is_active=True)
        if self.is_head:
            queryset = queryset.filter(id=F('versioned_object_id'))
        self.active_mappings = queryset.count()

    def seed_concepts(self, index=True):
        head = self.head
        if head:
            self.concepts.set(head.concepts.filter(is_latest_version=True))
            if index:
                from core.concepts.documents import ConceptDocument
                self.batch_index(self.concepts, ConceptDocument)

    def seed_mappings(self, index=True):
        head = self.head
        if head:
            self.mappings.set(head.mappings.filter(is_latest_version=True))
            if index:
                from core.mappings.documents import MappingDocument
                self.batch_index(self.mappings, MappingDocument)

    def index_children(self):
        from core.concepts.documents import ConceptDocument
        from core.mappings.documents import MappingDocument

        self.batch_index(self.concepts, ConceptDocument)
        self.batch_index(self.mappings, MappingDocument)

    def __get_resource_db_sequence_prefix(self):
        return self.uri.replace('/', '_').replace('-', '_').replace('.', '_').replace('@', '_')

    @property
    def concepts_mnemonic_seq_name(self):
        prefix = self.__get_resource_db_sequence_prefix()
        return f"{prefix}_concepts_mnemonic_seq"

    @property
    def concepts_external_id_seq_name(self):
        prefix = self.__get_resource_db_sequence_prefix()
        return f"{prefix}_concepts_external_id_seq"

    @property
    def mappings_mnemonic_seq_name(self):
        prefix = self.__get_resource_db_sequence_prefix()
        return f"{prefix}_mappings_mnemonic_seq"

    @property
    def mappings_external_id_seq_name(self):
        prefix = self.__get_resource_db_sequence_prefix()
        return f"{prefix}_mappings_external_id_seq"

    def save(
        self, force_insert=False, force_update=False, using=None, update_fields=None
    ):
        is_new = not self.id

        super().save(
            force_insert=force_insert, force_update=force_update, using=using, update_fields=update_fields)

        if self.id and is_new and self.is_head:
            if self.is_sequential_concept_mnemonic:
                PostgresQL.create_seq(self.concepts_mnemonic_seq_name, 'sources.uri')
            if self.is_sequential_mapping_mnemonic:
                PostgresQL.create_seq(self.mappings_mnemonic_seq_name, 'sources.uri')
            if self.is_sequential_concept_external_id:
                PostgresQL.create_seq(self.concepts_external_id_seq_name, 'sources.uri')
            if self.is_sequential_mapping_external_id:
                PostgresQL.create_seq(self.mappings_external_id_seq_name, 'sources.uri')

    def post_delete_actions(self):
        if self.is_head:
            PostgresQL.drop_seq(self.concepts_mnemonic_seq_name)
            PostgresQL.drop_seq(self.mappings_mnemonic_seq_name)

    def concept_pre_delete_actions(self, concept):
        if concept.is_versioned_object:
            if concept.mnemonic.isnumeric() and self.is_sequential_concept_mnemonic:
                PostgresQL.update_seq(self.concepts_mnemonic_seq_name, max(int(concept.mnemonic) - 1, 0))
            if concept.external_id and concept.external_id.isnumeric() and self.is_sequential_concept_external_id:
                PostgresQL.update_seq(self.concepts_external_id_seq_name, max(int(concept.external_id) - 1, 0))

    def mapping_pre_delete_actions(self, mapping):
        if mapping.is_versioned_object:
            if mapping.mnemonic.isnumeric() and self.is_sequential_mapping_mnemonic:
                PostgresQL.update_seq(self.mappings_mnemonic_seq_name, max(int(mapping.mnemonic) - 1, 0))
            if mapping.external_id and mapping.external_id.isnumeric() and self.is_sequential_mapping_external_id:
                PostgresQL.update_seq(self.mappings_external_id_seq_name, max(int(mapping.external_id) - 1, 0))
