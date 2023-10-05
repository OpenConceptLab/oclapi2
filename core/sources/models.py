import uuid

from dirtyfields import DirtyFieldsMixin
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import UniqueConstraint, F, Max, Count
from django.db.models.functions import Cast
from pydash import get

from core.common.constants import HEAD
from core.common.models import ConceptContainerModel
from core.common.services import PostgresQL
from core.common.tasks import update_mappings_source
from core.common.validators import validate_non_negative
from core.concepts.models import ConceptName, Concept
from core.sources.constants import SOURCE_TYPE, SOURCE_VERSION_TYPE, HIERARCHY_ROOT_MUST_BELONG_TO_SAME_SOURCE, \
    HIERARCHY_MEANINGS, AUTO_ID_CHOICES, AUTO_ID_SEQUENTIAL, AUTO_ID_UUID


class Source(DirtyFieldsMixin, ConceptContainerModel):
    DEFAULT_AUTO_ID_START_FROM = 1
    CHECKSUM_INCLUSIONS = ConceptContainerModel.CHECKSUM_INCLUSIONS + [
        'hierarchy_meaning',
        'source_type'
    ]

    es_fields = {
        'source_type': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'mnemonic': {'sortable': False, 'filterable': True, 'exact': True},
        '_mnemonic': {'sortable': True, 'filterable': False, 'exact': False},
        'name': {'sortable': False, 'filterable': True, 'exact': True},
        '_name': {'sortable': True, 'filterable': False, 'exact': False},
        'last_update': {'sortable': True, 'filterable': False, 'default': 'desc'},
        'updated_by': {'sortable': False, 'filterable': False, 'facet': True},
        'locale': {'sortable': False, 'filterable': True, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'custom_validation_schema': {'sortable': False, 'filterable': True, 'facet': True},
        'canonical_url': {'sortable': False, 'filterable': True, 'exact': True},
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
        indexes = [
                      models.Index(fields=['uri']),
                      models.Index(fields=['public_access'])
                  ] + ConceptContainerModel.Meta.indexes
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

    @classmethod
    def get_first_or_head(cls, url):
        queryset = cls.objects.filter(models.Q(uri=url) | models.Q(canonical_url=url))
        if queryset.count() > 1:
            return queryset.filter(version=HEAD).first() or queryset.first()
        return queryset.first()

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
        # Updates mappings where mapping.to_source_url or mapping.from_source_url matches source url or canonical url
        from core.mappings.models import Mapping
        from core.mappings.documents import MappingDocument

        uris = self.identity_uris

        to_queryset = Mapping.objects.filter(to_source_url__in=uris)
        from_queryset = Mapping.objects.filter(from_source_url__in=uris)

        to_queryset.filter(to_source_id__isnull=True).update(to_source=self)
        from_queryset.filter(from_source_id__isnull=True).update(from_source=self)

        Mapping.batch_index(to_queryset.filter(to_source=self), MappingDocument)
        Mapping.batch_index(from_queryset.filter(to_source=self), MappingDocument)

    def is_hierarchy_root_belonging_to_self(self):
        hierarchy_root = self.hierarchy_root
        if self.is_head:
            return hierarchy_root.parent_id == self.id
        return hierarchy_root.parent_id == self.head.id

    def clean(self):
        self.hierarchy_meaning = self.hierarchy_meaning or None

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

        return {
            'id': self.mnemonic,
            'children': children,
            'count': total_count + (1 if hierarchy_root else 0),
            'offset': offset,
            'limit': limit
        }

    def set_active_concepts(self):
        queryset = self.concepts
        if self.is_head:
            queryset = self.concepts_set.filter(id=F('versioned_object_id'))
        self.active_concepts = queryset.filter(retired=False, is_active=True).count()

    def set_active_mappings(self):
        queryset = self.mappings
        if self.is_head:
            queryset = self.mappings_set.filter(id=F('versioned_object_id'))
        self.active_mappings = queryset.filter(retired=False, is_active=True).count()

    def seed_concepts(self, index=True):
        head = self.head
        if head:
            concepts = head.concepts.filter(is_latest_version=True)
            self.concepts.set(concepts)
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
    def is_sequential_concepts_mnemonic(self):
        return self.autoid_concept_mnemonic == AUTO_ID_SEQUENTIAL

    @property
    def is_sequential_mappings_mnemonic(self):
        return self.autoid_mapping_mnemonic == AUTO_ID_SEQUENTIAL

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
        def should_update(is_seq, field):
            start_from = get(self, field)
            return field in dirty_fields and is_seq and start_from and start_from > 0

        def should_create(is_seq, field):
            return field in dirty_fields and dirty_fields.get(field) != AUTO_ID_SEQUENTIAL and is_seq

        def to_seq(start_from):
            return int(start_from) - 1

        if should_create(self.is_sequential_concept_mnemonic, 'autoid_concept_mnemonic'):
            PostgresQL.create_seq(
                self.concepts_mnemonic_seq_name, 'sources.uri', 0, self.autoid_concept_mnemonic_start_from)
        elif should_update(self.is_sequential_concept_mnemonic, 'autoid_concept_mnemonic_start_from'):
            PostgresQL.update_seq(self.concepts_mnemonic_seq_name, to_seq(self.autoid_concept_mnemonic_start_from))

        if should_create(self.is_sequential_mapping_mnemonic, 'autoid_mapping_mnemonic'):
            PostgresQL.create_seq(
                self.mappings_mnemonic_seq_name, 'sources.uri', 0, self.autoid_mapping_mnemonic_start_from
            )
        elif should_update(self.is_sequential_mapping_mnemonic, 'autoid_mapping_mnemonic_start_from'):
            PostgresQL.update_seq(self.mappings_mnemonic_seq_name, to_seq(self.autoid_mapping_mnemonic_start_from))

        if should_create(self.is_sequential_mapping_external_id, 'autoid_mapping_external_id'):
            PostgresQL.create_seq(
                self.mappings_external_id_seq_name, 'sources.uri', 0, self.autoid_mapping_external_id_start_from
            )
        elif should_update(self.is_sequential_mapping_external_id, 'autoid_mapping_external_id_start_from'):
            PostgresQL.update_seq(
                self.mappings_external_id_seq_name, to_seq(self.autoid_mapping_external_id_start_from))

        if should_create(self.is_sequential_concept_external_id, 'autoid_concept_external_id'):
            PostgresQL.create_seq(
                self.concepts_external_id_seq_name, 'sources.uri', 0, self.autoid_concept_external_id_start_from
            )
        elif should_update(self.is_sequential_concept_external_id, 'autoid_concept_external_id_start_from'):
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

    def post_create_actions(self):
        if get(settings, 'TEST_MODE', False):
            update_mappings_source(self.id)
        else:
            update_mappings_source.delay(self.id)

    def get_max_concept_attribute(self, attribute):
        return get(self.get_concepts_queryset().aggregate(max_val=Max(attribute)), 'max_val', None)

    def get_max_mapping_attribute(self, attribute):
        return get(self.get_mappings_queryset().aggregate(max_val=Max(attribute)), 'max_val', None)

    def get_max_concept_mnemonic(self):
        return self.get_max_mnemonic_for_resource(self.get_concepts_queryset())

    def get_max_mapping_mnemonic(self):
        return self.get_max_mnemonic_for_resource(self.get_mappings_queryset())

    @staticmethod
    def get_max_mnemonic_for_resource(queryset):
        return get(
            queryset.filter(
                mnemonic__regex=r'^\d+$'
            ).annotate(
                mnemonic_int=Cast('mnemonic', models.IntegerField())
            ).aggregate(
                max_val=Max('mnemonic_int')), 'max_val', None
        )

    @property
    def last_concept_update(self):
        return self.get_max_concept_attribute('updated_at')

    @property
    def last_mapping_update(self):
        return self.get_max_mapping_attribute('updated_at')

    def get_mapped_sources(self):
        """Returns only direct mapped sources"""
        mappings = self.mappings.exclude(to_source_id=self.id)
        mappings = mappings.order_by('to_source_id').distinct('to_source_id')
        return Source.objects.filter(id__in=mappings.values_list('to_source_id', flat=True))

    def clone_resources(self, user, concepts, mappings, **kwargs):
        from core.mappings.models import Mapping
        added_concepts, added_mappings = [], []
        equivalency_map_types = (kwargs.get('equivalency_map_types') or '').split(',')
        _concepts_to_add_mappings_for = []
        for concept in concepts:
            if not self.get_equivalent_concept(concept, equivalency_map_types):
                cloned_concept = concept.versioned_object.clone()
                added_concepts += self.clone_concepts([cloned_concept], user, False)
                if equivalency_map_types:
                    added_mappings += self.clone_mappings(
                        [Mapping.build(
                            map_type=equivalency_map_types[0], from_concept=cloned_concept, to_concept=concept,
                            parent=self
                        )],
                        user,
                        False
                    )
                _concepts_to_add_mappings_for.append([concept, cloned_concept])
        for concept_pair in _concepts_to_add_mappings_for:
            concept, cloned_concept = concept_pair
            for mapping in mappings.filter(from_concept__versioned_object_id=concept.versioned_object_id):
                existing_to_concept = self.get_equivalent_concept(mapping.to_concept, equivalency_map_types)
                added_mappings += self.clone_mappings(
                    [mapping.clone(user, cloned_concept, existing_to_concept)], user, False)

        if added_concepts or added_mappings:
            self.update_children_counts()

        return added_concepts, added_mappings

    def get_equivalent_concept(self, concept, equivalency_map_type):
        equivalent_mapping = self.get_equivalent_mapping(concept, equivalency_map_type)
        return get(equivalent_mapping, 'from_concept')

    def get_equivalent_mapping(self, concept, equivalency_map_type):
        return self.mappings_set.filter(
            map_type__in=equivalency_map_type, to_concept__versioned_object_id=concept.versioned_object_id,
            from_concept__parent_id=self.id, retired=False, id=F('versioned_object_id')
        ).first() if equivalency_map_type and concept else None

    def clone_with_cascade(self, concept_to_clone, user, **kwargs):
        from core.mappings.models import Mapping
        mappings = Mapping.objects.none()
        concepts = Concept.objects.filter(id=concept_to_clone.id)
        if kwargs:
            kwargs.pop('view', None)
            kwargs['repo_version'] = kwargs.get('repo_version') or concept_to_clone.parent
            result = concept_to_clone.cascade(**kwargs, omit_if_exists_in=self.uri, include_self=False)
            concepts = result['concepts']
            mappings = result['mappings']
        return self.clone_resources(user, concepts, mappings, **kwargs)

    def clone_mappings(self, cloned_mappings, user, update_count=True):
        _update_count = False
        added = []
        for mapping in cloned_mappings:
            to_concept = get(mapping, 'to_concept')
            from_concept = get(mapping, 'from_concept')
            if not get(from_concept, 'id'):
                from_concept = self.find_concept_by_mnemonic(from_concept.mnemonic)
            if not get(to_concept, 'id') and get(to_concept, 'mnemonic'):
                to_concept = self.find_concept_by_mnemonic(to_concept.mnemonic)
            mapping.from_concept_code = get(from_concept, 'mnemonic') or mapping.from_concept_code
            mapping.from_concept_id = get(from_concept, 'id')
            mapping.to_concept_id = get(to_concept, 'id')
            mapping.to_concept_code = get(to_concept, 'mnemonic') or mapping.to_concept_code
            mapping.to_source_id = get(to_concept, 'parent_id') or mapping.to_source_id
            mapping.from_source_id = get(from_concept, 'parent_id') or mapping.from_source_id
            self._clone_resource(mapping, user)
            added.append(mapping)
            if mapping.id and update_count:
                _update_count = True
        if _update_count:
            self.update_mappings_count()
        return added

    def clone_concepts(self, cloned_concepts, user, update_count=True):
        _update_count = False
        added = []
        for concept in cloned_concepts:
            concept._parent_concepts = None  # pylint: disable=protected-access
            self._clone_resource(concept, user)
            added.append(concept)
            if concept.id and update_count:
                _update_count = True
        if _update_count:
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

    def _get_map_type_distribution(self, filters, concept_field):
        _result = {'total': 0, 'retired': 0, 'active': 0, 'concepts': 0}
        result = {**_result, 'map_types': []}

        queryset = self.get_mappings_queryset().filter(**filters)
        queryset = queryset.values(
            'map_type').annotate(
            total=Count('id')).annotate(
            retired=Count('id', filter=models.Q(retired=True))).annotate(
            concepts=Count(concept_field, distinct=True))
        for info in queryset.values('map_type', 'total', 'retired', 'concepts').order_by('-total'):
            active = info['total'] - info['retired']
            result['total'] += info['total']
            result['retired'] += info['retired']
            result['active'] += active
            result['concepts'] += info['concepts']
            result['map_types'].append({
                'map_type': info['map_type'],
                'concepts': info['concepts'],
                'total': info['total'],
                'retired': info['retired'],
                'active': active
            })
        return result

    def get_from_source_map_type_distribution(self, from_source):
        return self._get_map_type_distribution({'from_source_id': from_source.id}, 'to_concept__versioned_object_id')

    def get_to_source_map_type_distribution(self, to_source):
        return self._get_map_type_distribution({'to_source_id': to_source.id}, 'from_concept__versioned_object_id')

    @property
    def from_sources(self):
        return Source.objects.filter(id__in=self.referenced_from_sources().values_list('id', flat=True))

    @property
    def to_sources(self):
        return Source.objects.filter(id__in=self.referenced_to_sources().values_list('id', flat=True))

    def get_to_sources_map_type_distribution(self, source_names=None):
        sources = self.to_sources
        if source_names:
            sources = sources.filter(mnemonic__in=source_names)
        return self._get_sources_map_type_distribution(sources, 'toConceptSource')

    def get_from_sources_map_type_distribution(self, source_names=None):
        sources = self.from_sources
        if source_names:
            sources = sources.filter(mnemonic__in=source_names)
        return self._get_sources_map_type_distribution(sources, 'fromConceptSource')

    @staticmethod
    def __to_distribution(map_types, is_retired, result, total, active, retired):  # pylint: disable=too-many-arguments
        for map_type in map_types:
            if map_type[0] not in result:
                result[map_type[0]] = {
                    'total': 0,
                    'active': 0,
                    'retired': 0,
                    'map_type': map_type[0]
                }
            result[map_type[0]]['total'] += map_type[1]
            if is_retired:
                result[map_type[0]]['retired'] += map_type[1]
                retired += map_type[1]
            else:
                result[map_type[0]]['active'] += map_type[1]
                active += map_type[1]
            total += map_type[1]

        return result, total, active, retired

    def _get_sources_map_type_distribution(self, sources, facet_source_key):
        from core.sources.serializers import SourceVersionMinimalSerializer
        distribution = []
        for source in sources:
            active_mapping_facets = self.get_mapping_facets({facet_source_key: source.mnemonic})
            retired_mapping_facets = self.get_mapping_facets({facet_source_key: source.mnemonic, 'retired': True})

            result, total, active, retired = {}, 0, 0, 0

            result, total, active, retired = self.__to_distribution(
                active_mapping_facets.mapType, False, result, total, active, retired)
            result, total, active, retired = self.__to_distribution(
                retired_mapping_facets.mapType, True, result, total, active, retired)

            distribution.append(
                {
                    'distribution': {
                        'active': active,
                        'retired': retired,
                        'total': total,
                        'map_types': list(result.values())
                    },
                    **SourceVersionMinimalSerializer(source).data
                }
            )
        return sorted(distribution, key=lambda dist: dist['distribution']['total'], reverse=True)

    def referenced_from_sources(self):
        return Source.objects.exclude(
            organization_id=self.organization_id, user_id=self.user_id,
            mnemonic=self.mnemonic
        ).filter(id__in=self.get_mappings_queryset().values_list('from_source_id', flat=True))

    def referenced_to_sources(self):
        return Source.objects.exclude(
            organization_id=self.organization_id, user_id=self.user_id,
            mnemonic=self.mnemonic
        ).filter(id__in=self.get_mappings_queryset().values_list('to_source_id', flat=True))

    def get_concepts_queryset(self):
        if self.is_head:
            return self.concepts_set.filter(id=F('versioned_object_id'))

        return self.concepts.filter()

    def get_mappings_queryset(self):
        if self.is_head:
            return self.mappings_set.filter(id=F('versioned_object_id'))

        return self.mappings.filter()

    @property
    def mappings_distribution(self):
        facets = self.get_mapping_facets()

        return {
            'active': self.active_mappings,
            'retired': self.retired_mappings_count,
            'map_type': self._to_clean_facets(facets.mapType or []),
            'to_concept_source': self._to_clean_facets(facets.toConceptSource or [], True),
            'from_concept_source': self._to_clean_facets(facets.fromConceptSource or [], True),
            'contributors': self._to_clean_facets(facets.updatedBy or [])
        }

    def _get_resource_facet_filters(self, filters=None):
        _filters = {
            'source': self.mnemonic,
            'ownerType': self.parent.resource_type,
            'owner': self.parent.mnemonic,
            'retired': False
        }
        if self.is_head:
            _filters['is_latest_version'] = True
        else:
            _filters['source_version'] = self.version

        return {**_filters, **(filters or {})}
