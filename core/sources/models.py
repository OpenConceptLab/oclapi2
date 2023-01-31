import uuid

from dirtyfields import DirtyFieldsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import UniqueConstraint, F, Max
from pydash import compact, get

from core.common.models import ConceptContainerModel
from core.common.services import PostgresQL
from core.common.validators import validate_non_negative
from core.concepts.models import ConceptName, Concept
from core.mappings.constants import SAME_AS
from core.sources.constants import SOURCE_TYPE, SOURCE_VERSION_TYPE, HIERARCHY_ROOT_MUST_BELONG_TO_SAME_SOURCE, \
    HIERARCHY_MEANINGS, AUTO_ID_CHOICES, AUTO_ID_SEQUENTIAL, AUTO_ID_UUID


class Source(DirtyFieldsMixin, ConceptContainerModel):
    DEFAULT_AUTO_ID_START_FROM = 1

    es_fields = {
        'source_type': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'mnemonic': {'sortable': True, 'filterable': True, 'exact': True},
        'name': {'sortable': True, 'filterable': True, 'exact': True},
        'last_update': {'sortable': True, 'filterable': False, 'default': 'desc'},
        'locale': {'sortable': False, 'filterable': True, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'custom_validation_schema': {'sortable': False, 'filterable': True, 'facet': True},
        'canonical_url': {'sortable': True, 'filterable': True, 'exact': True},
        'experimental': {'sortable': False, 'filterable': False, 'facet': False},
        'hierarchy_meaning': {'sortable': False, 'filterable': True, 'facet': True},
        'external_id': {'sortable': False, 'filterable': True, 'facet': False, 'exact': True},
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
    autoid_concept_mnemonic_start_from = models.IntegerField(
        default=DEFAULT_AUTO_ID_START_FROM, validators=[validate_non_negative])
    autoid_concept_external_id_start_from = models.IntegerField(
        default=DEFAULT_AUTO_ID_START_FROM, validators=[validate_non_negative])
    autoid_mapping_mnemonic_start_from = models.IntegerField(
        default=DEFAULT_AUTO_ID_START_FROM, validators=[validate_non_negative])
    autoid_mapping_external_id_start_from = models.IntegerField(
        default=DEFAULT_AUTO_ID_START_FROM, validators=[validate_non_negative])

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
        return self.get_resource_next_attr_id(self.autoid_concept_mnemonic, self.concepts_mnemonic_seq_name)

    @property
    def concept_external_id_next(self):
        return self.get_resource_next_attr_id(self.autoid_concept_external_id, self.concepts_external_id_seq_name)

    @property
    def mapping_mnemonic_next(self):
        try:
            return self.get_resource_next_attr_id(self.autoid_mapping_mnemonic, self.mappings_mnemonic_seq_name)
        except:  # pylint: disable=bare-except
            return None

    @property
    def mapping_external_id_next(self):
        return self.get_resource_next_attr_id(self.autoid_mapping_external_id, self.mappings_external_id_seq_name)

    @staticmethod
    def get_resource_next_attr_id(attr_type, seq):
        if attr_type == AUTO_ID_UUID:
            return uuid.uuid4()
        if attr_type == AUTO_ID_SEQUENTIAL:
            return str(PostgresQL.next_value(seq))
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
        return ConceptName.objects.filter(concept__in=self.get_active_concepts())

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
        if self.is_head:
            queryset = self.concepts_set.filter(id=F('versioned_object_id'))
        else:
            queryset = self.concepts
        self.active_concepts = queryset.filter(retired=False, is_active=True).count()

    def set_active_mappings(self):
        if self.is_head:
            queryset = self.mappings_set.filter(id=F('versioned_object_id'))
        else:
            queryset = self.mappings
        self.active_mappings = queryset.filter(retired=False, is_active=True).count()

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
        dirty_fields = self.get_dirty_fields()

        super().save(force_insert=force_insert, force_update=force_update, using=using, update_fields=update_fields)

        if self.id and self.is_head:
            if is_new:
                self.__create_sequences()
            else:
                self.__update_sequences(dirty_fields)

    def __update_sequences(self, dirty_fields=[]):  # pylint: disable=dangerous-default-value
        def should_update(is_seq, field, start_from):
            return field in dirty_fields and is_seq and start_from and start_from > 0

        def to_seq(start_from):
            return int(start_from) - 1

        if should_update(
                self.is_sequential_mapping_mnemonic, 'autoid_mapping_mnemonic_start_from',
                self.autoid_mapping_mnemonic_start_from
        ):
            PostgresQL.update_seq(self.mappings_mnemonic_seq_name, to_seq(self.autoid_mapping_mnemonic_start_from))
        if should_update(
                self.is_sequential_mapping_external_id, 'autoid_mapping_external_id_start_from',
                self.autoid_mapping_external_id_start_from
        ):
            PostgresQL.update_seq(
                self.mappings_external_id_seq_name, to_seq(self.autoid_mapping_external_id_start_from))
        if should_update(
                self.is_sequential_concept_mnemonic, 'autoid_concept_mnemonic_start_from',
                self.autoid_concept_mnemonic_start_from
        ):
            PostgresQL.update_seq(self.concepts_mnemonic_seq_name, to_seq(self.autoid_concept_mnemonic_start_from))
        if should_update(
                self.is_sequential_concept_external_id, 'autoid_concept_external_id_start_from',
                self.autoid_concept_external_id_start_from
        ):
            PostgresQL.update_seq(
                self.concepts_external_id_seq_name, to_seq(self.autoid_concept_external_id_start_from))

    def __create_sequences(self):
        if self.is_sequential_concept_mnemonic:
            PostgresQL.create_seq(
                self.concepts_mnemonic_seq_name, 'sources.uri', 0, self.autoid_concept_mnemonic_start_from
            )
        if self.is_sequential_mapping_mnemonic:
            PostgresQL.create_seq(
                self.mappings_mnemonic_seq_name, 'sources.uri', 0, self.autoid_mapping_mnemonic_start_from
            )
        if self.is_sequential_concept_external_id:
            PostgresQL.create_seq(
                self.concepts_external_id_seq_name, 'sources.uri', 0, self.autoid_concept_external_id_start_from
            )
        if self.is_sequential_mapping_external_id:
            PostgresQL.create_seq(
                self.mappings_external_id_seq_name, 'sources.uri', 0, self.autoid_mapping_external_id_start_from
            )

    def post_delete_actions(self):
        if self.is_head:
            PostgresQL.drop_seq(self.concepts_mnemonic_seq_name)
            PostgresQL.drop_seq(self.concepts_external_id_seq_name)
            PostgresQL.drop_seq(self.mappings_mnemonic_seq_name)
            PostgresQL.drop_seq(self.mappings_external_id_seq_name)

    @property
    def last_concept_update(self):
        queryset = self.concepts_set.filter(id=F('versioned_object_id')) if self.is_head else self.concepts
        return get(queryset.aggregate(max_updated_at=Max('updated_at')), 'max_updated_at', None)

    @property
    def last_mapping_update(self):
        queryset = self.mappings_set.filter(id=F('versioned_object_id')) if self.is_head else self.mappings
        return get(queryset.aggregate(max_updated_at=Max('updated_at')), 'max_updated_at', None)

    def get_mapped_sources(self):
        """Returns only direct mapped sources"""
        mappings = self.mappings.exclude(to_source_id=self.id)
        mappings = mappings.order_by('to_source_id').distinct('to_source_id')
        return Source.objects.filter(id__in=mappings.values_list('to_source_id', flat=True))

    def clone_resources(self, user, concepts, mappings, **kwargs):  # pylint: disable=too-many-locals
        from core.mappings.models import Mapping
        cloned_concepts, cloned_mappings = [], []
        map_types = kwargs.get('map_types', '') or ''
        for concept in concepts:
            if not self.concepts_set.filter(mnemonic=concept.mnemonic).exists():
                cloned_concept = concept.versioned_object.clone()
                cloned_concepts.append(cloned_concept)
                cloned_mappings.append(
                    Mapping(
                        map_type=SAME_AS, from_concept=cloned_concept, to_concept=concept, parent=self,
                        to_concept_code=concept.mnemonic, from_concept_code=cloned_concept.mnemonic
                    )
                )
                for mapping in mappings.filter(from_concept__versioned_object_id=concept.versioned_object_id):
                    original_to_concept = concepts.filter(id=mapping.to_concept_id).first()
                    existing_to_concept = self.find_concept_by_mnemonic(
                        original_to_concept.mnemonic) if original_to_concept else None
                    cloned_mappings.append(mapping.clone(user, cloned_concept, existing_to_concept))
                    if mapping.map_type in map_types and mapping.to_concept_id and not existing_to_concept:
                        cloned_concepts.append(original_to_concept.clone())
        added_concepts = self.clone_concepts(cloned_concepts, user)
        added_mappings = self.clone_mappings(cloned_mappings, user)
        return added_concepts, added_mappings

    def clone_with_cascade(self, concept_to_clone, user, **kwargs):
        if kwargs:
            kwargs.pop('view', None)
            kwargs['repo_version'] = kwargs.get('repo_version') or concept_to_clone.parent
            result = concept_to_clone.cascade(**kwargs, omit_if_exists_in=self.uri)
            concepts = result['concepts']
            mappings = result['mappings']
        else:
            from core.mappings.models import Mapping
            concepts = Concept.objects.filter(id=concept_to_clone.id)
            mappings = Mapping.objects.none()
        return self.clone_resources(user, concepts, mappings, **kwargs)

    def clone_mappings(self, cloned_mappings, user):
        update_count = False
        added = []
        for mapping in cloned_mappings:
            to_concept = get(mapping, 'to_concept')
            from_concept = get(mapping, 'from_concept')
            if not from_concept.id:
                from_concept = self.find_concept_by_mnemonic(from_concept.mnemonic)
            if not to_concept.id:
                to_concept = self.find_concept_by_mnemonic(to_concept.mnemonic)
            mapping.from_concept_id = from_concept.id
            mapping.to_concept_id = to_concept.id
            mapping.to_source_id = get(to_concept, 'parent_id')
            mapping.from_source_id = get(from_concept, 'parent_id')
            self._clone_resource(mapping, user)
            added.append(mapping)
            if mapping.id:
                update_count = True
        if update_count:
            self.update_mappings_count()
        return added

    def clone_concepts(self, cloned_concepts, user):
        update_count = False
        added = []
        for concept in cloned_concepts:
            concept._parent_concepts = None  # pylint: disable=protected-access
            self._clone_resource(concept, user)
            added.append(concept)
            if concept.id:
                update_count = True
        if update_count:
            self.update_concepts_count()
        return added

    def _clone_resource(self, resource, user):
        resource.parent = self
        resource.parent_id = self.id
        resource.uri = None
        resource.created_by = user
        resource.updated_by = user
        resource.save_cloned()

    def find_concept_by_mnemonic(self, mnemonic):
        queryset = self.concepts_set if self.is_head else self.concepts
        queryset = queryset.filter(mnemonic=mnemonic).order_by('-created_at')
        return (
                queryset.filter(
                    id=F('versioned_object_id')
                ) or queryset.filter(is_latest_version=True) or queryset.filter(retired=False) or queryset
        ).first()
