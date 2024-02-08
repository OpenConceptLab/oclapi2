import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, IntegrityError
from django.db.models import Q, F, Max
from pydash import get

from core.common.constants import NAMESPACE_REGEX, LATEST, HEAD
from core.common.mixins import SourceChildMixin
from core.common.models import VersionedModel
from core.common.tasks import batch_index_resources
from core.common.utils import separate_version, to_parent_uri, generate_temp_version, \
    encode_string, is_url_encoded_string
from core.mappings.constants import MAPPING_TYPE, MAPPING_IS_ALREADY_RETIRED, MAPPING_WAS_RETIRED, \
    MAPPING_IS_ALREADY_NOT_RETIRED, MAPPING_WAS_UNRETIRED, ALREADY_EXISTS
from core.mappings.mixins import MappingValidationMixin
from core.services.storages.postgres import PostgresQL


class Mapping(MappingValidationMixin, SourceChildMixin, VersionedModel):
    class Meta:
        db_table = 'mappings'
        unique_together = ('mnemonic', 'version', 'parent')
        indexes = [
                      models.Index(name='mappings_up_pub_latest',
                                   fields=['-updated_at', 'public_access', 'is_latest_version'],
                                   condition=(Q(is_active=True) & Q(retired=False))),
                      models.Index(name='mappings_head_up_pub', fields=['-updated_at', 'public_access'],
                                   condition=(Q(is_active=True) & Q(retired=False) & Q(id=F('versioned_object_id')))),
                      models.Index(name='mappings_up_latest', fields=['-updated_at', 'is_latest_version'],
                                   condition=(Q(is_active=True) & Q(retired=False))),
                      models.Index(name='mappings_pub_latest', fields=['public_access', 'is_latest_version'],
                                   condition=(Q(is_active=True) & Q(retired=False))),
                      models.Index(name='mappings_head_pub', fields=['public_access'],
                                   condition=(Q(is_active=True) & Q(retired=False) & Q(id=F('versioned_object_id')))),
                      models.Index(name='mappings_parent_pub_latest',
                                   fields=['parent_id', 'public_access', 'is_latest_version'],
                                   condition=(Q(is_active=True) & Q(retired=False))),
                      models.Index(name='mappings_head_parent_pub', fields=['parent_id', 'public_access'],
                                   condition=(Q(is_active=True) & Q(retired=False) & Q(id=F('versioned_object_id')))),
                      models.Index(name='mappings_latest_parent', fields=['is_latest_version', 'parent_id'],
                                   condition=(Q(is_active=True) & Q(retired=False))),
                      models.Index(name="mappings_map_type_like", fields=["map_type"], opclasses=["text_pattern_ops"]),
                      models.Index(fields=['uri']),
                      models.Index(
                          name='mappings_sort_weight_next',
                          fields=['from_concept_id', 'sort_weight', 'parent_id', 'map_type'],
                          condition=Q(sort_weight__isnull=False, id=F('versioned_object_id'), retired=False)
                      ),
                      models.Index(
                          name='mappings_latest',
                          fields=['versioned_object_id', '-created_at'],
                          condition=(Q(is_active=True, is_latest_version=True) & ~Q(id=F('versioned_object_id')))
                      ),
                      models.Index(
                          name='direct_mappings',
                          fields=['from_concept', 'parent_id'],
                          condition=(Q(id=F('versioned_object_id')))
                      ),
                      models.Index(
                          name='repo_version_mappings',
                          fields=['id', '-updated_at'],
                          condition=Q(is_active=True, retired=False)
                      ),
                  ] + VersionedModel.Meta.indexes
    parent = models.ForeignKey('sources.Source', related_name='mappings_set', on_delete=models.CASCADE)
    map_type = models.TextField()
    sort_weight = models.FloatField(db_index=True, null=True, blank=True)
    sources = models.ManyToManyField('sources.Source', related_name='mappings')
    external_id = models.TextField(null=True, blank=True)
    comment = models.TextField(null=True, blank=True)
    versioned_object = models.ForeignKey(
        'self', related_name='versions_set', null=True, blank=True, on_delete=models.CASCADE
    )
    mnemonic = models.CharField(
        max_length=255, validators=[RegexValidator(regex=NAMESPACE_REGEX)], default=uuid.uuid4,
    )
    from_concept = models.ForeignKey(
        'concepts.Concept', null=True, blank=True, related_name='mappings_from', on_delete=models.SET_NULL
    )
    to_concept = models.ForeignKey(
        'concepts.Concept', null=True, blank=True, related_name='mappings_to', on_delete=models.SET_NULL
    )
    to_source = models.ForeignKey(
        'sources.Source', null=True, blank=True, related_name='mappings_to', on_delete=models.SET_NULL
    )
    from_source = models.ForeignKey(
        'sources.Source', null=True, blank=True, related_name='mappings_from', on_delete=models.SET_NULL
    )

    # new schema -- https://github.com/OpenConceptLab/ocl_issues/issues/408
    from_concept_code = models.TextField(null=True, blank=True, db_index=True)
    from_concept_name = models.TextField(null=True, blank=True)
    from_source_url = models.TextField(null=True, blank=True, db_index=True)
    from_source_version = models.TextField(null=True, blank=True)

    to_concept_code = models.TextField(null=True, blank=True, db_index=True)
    to_concept_name = models.TextField(null=True, blank=True)
    to_source_url = models.TextField(null=True, blank=True, db_index=True)
    to_source_version = models.TextField(null=True, blank=True)
    _counted = models.BooleanField(default=True, null=True, blank=True)
    _index = models.BooleanField(default=True)

    logo_path = None
    name = None
    full_name = None
    default_locale = None
    supported_locales = None
    website = None
    description = None

    OBJECT_TYPE = MAPPING_TYPE
    ALREADY_RETIRED = MAPPING_IS_ALREADY_RETIRED
    ALREADY_NOT_RETIRED = MAPPING_IS_ALREADY_NOT_RETIRED
    WAS_RETIRED = MAPPING_WAS_RETIRED
    WAS_UNRETIRED = MAPPING_WAS_UNRETIRED

    CHECKSUM_INCLUSIONS = [
        'extras', 'map_type',
        'from_concept_code', 'from_concept_name',
        'to_concept_code', 'to_concept_name'
    ]

    es_fields = {
        'id': {'sortable': False, 'filterable': True, 'exact': True},
        'id_lowercase': {'sortable': True, 'filterable': False, 'exact': False},
        'last_update': {'sortable': True, 'filterable': False, 'facet': False, 'default': 'desc'},
        'updated_by': {'sortable': False, 'filterable': False, 'facet': True},
        'from_concept': {'sortable': False, 'filterable': True, 'facet': False, 'exact': True},
        'to_concept': {'sortable': False, 'filterable': True, 'facet': False, 'exact': True},
        'retired': {'sortable': False, 'filterable': True, 'facet': True},
        'map_type': {'sortable': True, 'filterable': True, 'facet': True, 'exact': False},
        'source': {'sortable': True, 'filterable': True, 'facet': True, 'exact': False},
        'collection': {'sortable': False, 'filterable': True, 'facet': True},
        'collection_url': {'sortable': False, 'filterable': True, 'facet': True},
        'collection_owner_url': {'sortable': False, 'filterable': False, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': False},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'concept_source': {'sortable': False, 'filterable': True, 'facet': True, 'exact': False},
        'from_concept_source': {'sortable': False, 'filterable': True, 'facet': True, 'exact': False},
        'to_concept_source': {'sortable': False, 'filterable': True, 'facet': True, 'exact': False},
        'concept_owner': {'sortable': False, 'filterable': True, 'facet': True, 'exact': False},
        'from_concept_owner': {'sortable': False, 'filterable': True, 'facet': True, 'exact': False},
        'to_concept_owner': {'sortable': False, 'filterable': True, 'facet': True, 'exact': False},
        'concept_owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'from_concept_owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'to_concept_owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'external_id': {'sortable': False, 'filterable': True, 'facet': False, 'exact': True},
    }

    def get_standard_checksum_fields(self):
        return self.get_standard_checksum_fields_for_resource(self)

    def get_smart_checksum_fields(self):
        return self.get_smart_checksum_fields_for_resource(self)

    @staticmethod
    def get_standard_checksum_fields_for_resource(data):
        return {
            **Mapping.get_smart_checksum_fields_for_resource(data),
            'extras': get(data, 'extras') or None,
            'external_id': get(data, 'external_id') or None,
        }

    @staticmethod
    def get_smart_checksum_fields_for_resource(data):
        return {
            'map_type': get(data, 'map_type'),
            'from_concept_code': get(data, 'from_concept_code'),
            'to_concept_code': get(data, 'to_concept_code'),
            'from_concept_name': get(data, 'from_concept_name'),
            'to_concept_name': get(data, 'to_concept_name'),
            'retired': get(data, 'retired')
        }

    @staticmethod
    def get_search_document():
        from core.mappings.documents import MappingDocument
        return MappingDocument

    @property
    def mapping(self):  # for url kwargs
        return self.mnemonic

    @property
    def source(self):
        return get(self, 'parent.mnemonic')

    @property
    def parent_source(self):
        return self.parent

    @property
    def from_source_owner(self):
        return str(get(self.get_from_source(), 'parent', ''))

    @property
    def from_source_owner_mnemonic(self):
        return get(self.get_from_source(), 'parent.mnemonic')

    @property
    def from_source_owner_type(self):
        return get(self.get_from_source(), 'parent.resource_type')

    @property
    def from_source_name(self):
        return get(self.get_from_source(), 'mnemonic')

    @property
    def from_source_shorthand(self):
        return f"{self.from_source_owner_mnemonic}:{self.from_source_name}"

    @property
    def from_concept_url(self):
        return get(self, 'from_concept.url', '')

    @property
    def from_concept_shorthand(self):
        return f"{self.from_source_shorthand}:{self.from_concept_code}"

    def get_to_source(self):
        if self.to_source_id:
            return get(self, 'to_source')
        if self.to_concept_id:
            return get(self, 'to_concept.parent')

        return None

    def get_from_source(self):
        if self.from_source_id:
            return get(self, 'from_source')
        if self.from_concept_id:
            return get(self, 'from_concept.parent')

        return None

    @property
    def to_source_name(self):
        return get(self.get_to_source(), 'mnemonic')

    @property
    def to_source_owner(self):
        return str(get(self.get_to_source(), 'parent', ''))

    @property
    def to_source_owner_mnemonic(self):
        return get(self.get_to_source(), 'parent.mnemonic')

    @property
    def to_source_owner_type(self):
        return get(self.get_to_source(), 'parent.resource_type')

    @property
    def to_source_shorthand(self):
        return self.get_to_source() and f"{self.to_source_owner_mnemonic}:{self.to_source_name}"

    def get_to_concept_name(self):
        return self.to_concept_name or get(self, 'to_concept.display_name')

    def get_to_concept_code(self):
        return self.to_concept_code or get(self, 'to_concept.mnemonic')

    @property
    def to_concept_url(self):
        return get(self, 'to_concept.url')

    @property
    def to_concept_shorthand(self):
        return f"{self.to_source_shorthand}:{self.get_to_concept_code()}"

    @staticmethod
    def get_resource_url_kwarg():
        return 'mapping'

    @staticmethod
    def get_version_url_kwarg():
        return 'mapping_version'

    def clone(self, user=None, from_concept=None, to_concept=None):
        mapping = Mapping(
            version=generate_temp_version(),
            mnemonic=self.mnemonic,
            parent_id=self.parent_id,
            map_type=self.map_type,
            retired=self.retired,
            released=self.released,
            is_latest_version=self.is_latest_version,
            extras=self.extras,
            public_access=self.public_access,
            external_id=self.external_id,
            versioned_object_id=self.versioned_object_id,
            to_concept_id=get(to_concept, 'id') or self.to_concept_id,
            to_concept_code=self.to_concept_code,
            to_concept_name=self.to_concept_name,
            to_source_id=self.to_source_id,
            to_source_url=self.to_source_url,
            to_source_version=self.to_source_version,
            from_concept_id=get(from_concept, 'id') or self.from_concept_id,
            from_concept_code=self.from_concept_code,
            from_concept_name=self.from_concept_name,
            from_source_id=self.from_source_id,
            from_source_url=self.from_source_url,
            from_source_version=self.from_source_version,
            _index=self._index,
            sort_weight=self.sort_weight
        )
        if to_concept:
            mapping.to_concept = to_concept
        if from_concept:
            mapping.from_concept = from_concept
        if user:
            mapping.created_by = mapping.updated_by = user

        return mapping

    @classmethod
    def build(cls, map_type, from_concept, to_concept, **kwargs):
        return cls(
            map_type=map_type, from_concept=from_concept, to_concept=to_concept,
            to_concept_code=to_concept.mnemonic, from_concept_code=from_concept.mnemonic, **kwargs
        )

    @classmethod
    def create_initial_version(cls, mapping, **kwargs):
        initial_version = mapping.clone()
        initial_version.created_by = initial_version.updated_by = mapping.created_by
        initial_version.comment = mapping.comment
        initial_version.save(**kwargs)
        initial_version.version = initial_version.id
        initial_version.released = False
        initial_version.is_latest_version = True
        initial_version.save()
        return initial_version

    def populate_fields_from_relations(self, data):  # pylint: disable=too-many-locals
        from core.concepts.models import Concept
        from core.sources.models import Source

        to_concept_code = data.get('to_concept_code')
        from_concept_code = data.get('from_concept_code')
        from_concept_url = data.get('from_concept_url', '')
        to_concept_url = data.get('to_concept_url', '')
        from_source_url = data.get('from_source_url', None) or to_parent_uri(from_concept_url)
        to_source_url = data.get('to_source_url', None) or to_parent_uri(to_concept_url)

        def get_concept(expr):
            concept = Concept.objects.filter(
                uri=expr).first() or Concept.objects.filter(uri=encode_string(expr, safe='/')).first()

            return concept or {'mnemonic': expr.replace(to_parent_uri(expr), '').replace('concepts/', '').split('/')[0]}

        def get_source(url):
            source = Source.resolve_reference_expression(url, None, HEAD)
            if source.id:
                return source, source.versioned_object_url or source.resolution_url or url
            return None, source.resolution_url or url

        self.from_source, self.from_source_url = get_source(from_source_url)
        self.to_source, self.to_source_url = get_source(to_source_url)

        if to_concept_code and not is_url_encoded_string(to_concept_code):
            to_concept_code = encode_string(to_concept_code)
        if from_concept_code and not is_url_encoded_string(from_concept_code):
            from_concept_code = encode_string(from_concept_code)

        if not to_concept_url and not get(self, 'to_concept') and self.to_source_url and (
                to_concept_code or self.to_concept_code):
            to_concept_code = to_concept_code or self.to_concept_code
            to_concept_url = self.to_source_url + 'concepts/' + to_concept_code + '/'

        if not from_concept_url and not get(self, 'from_concept') and self.from_source_url and (
                from_concept_code or self.from_concept_code):
            from_concept_code = from_concept_code or self.from_concept_code
            from_concept_url = self.from_source_url + 'concepts/' + from_concept_code + '/'

        from_concept = get_concept(from_concept_url) if from_concept_url else get(self, 'from_concept')
        to_concept = get_concept(to_concept_url) if to_concept_url else get(self, 'to_concept')

        self.from_concept_id = get(from_concept, 'id')
        self.to_concept_id = get(to_concept, 'id')
        self.from_source = self.from_source or get(from_concept, 'parent')
        self.to_source = self.to_source or get(to_concept, 'parent')
        self.to_source_url = self.to_source_url or get(to_concept, 'parent.uri')
        self.from_source_url = self.from_source_url or get(from_concept, 'parent.uri')
        self.from_concept_code = data.get(
            'from_concept_code', None) or get(from_concept, 'mnemonic') or self.from_concept_code
        self.from_concept_name = data.get('from_concept_name', None) or self.from_concept_name
        self.to_concept_code = data.get(
            'to_concept_code', None) or get(to_concept, 'mnemonic') or self.to_concept_code
        self.to_concept_name = data.get('to_concept_name', None) or self.to_concept_name
        if self.to_source_url:
            to_source_version, to_source_url = separate_version(self.to_source_url)
            self.to_source_version = self.to_source_version or to_source_version
            self.to_source_url = to_source_url
        if self.from_source_url:
            from_source_version, from_source_url = separate_version(self.from_source_url)
            self.from_source_version = self.from_source_version or from_source_version
            self.from_source_url = from_source_url

    def is_existing_in_parent(self):
        return self.parent.mappings_set.filter(mnemonic__exact=self.mnemonic).exists()

    @classmethod
    def create_new_version_for(cls, instance, data, user):
        instance.populate_fields_from_relations(data)
        instance.extras = data.get('extras', instance.extras)
        instance.external_id = data.get('external_id', instance.external_id)
        instance.comment = data.get('update_comment') or data.get('comment')
        instance.retired = data.get('retired', instance.retired)
        instance.mnemonic = data.get('mnemonic', instance.mnemonic)
        instance.map_type = data.get('map_type', instance.map_type)
        instance.sort_weight = data.get('sort_weight', instance.sort_weight)

        return instance.save_as_new_version(user)

    def save_cloned(self):
        try:
            parent = self.parent
            self.is_latest_version = False
            self.public_access = parent.public_access
            self.mnemonic = self.version = generate_temp_version()
            self.errors = {}
            self.full_clean()
            self.save()
            if self.id:
                next_valid_seq = parent.mapping_mnemonic_next  # returns str of int or None
                if parent.is_sequential_mappings_mnemonic:
                    try:
                        available_next = int(parent.get_max_mapping_mnemonic())
                        if available_next and available_next >= int(next_valid_seq):
                            PostgresQL.update_seq(parent.mappings_mnemonic_seq_name, available_next)
                            next_valid_seq = parent.mapping_mnemonic_next
                    except:  # pylint: disable=bare-except
                        pass
                self.mnemonic = next_valid_seq or str(self.id)
                self.versioned_object_id = self.id
                self.version = str(self.id)
                self.external_id = parent.mapping_external_id_next
                self.save()
                initial_version = Mapping.create_initial_version(self)
                initial_version.sources.set([parent])
                self.sources.set([parent])
                self.set_checksums()
                if self.from_concept_id:
                    self.index_from_concept()
        except ValidationError as ex:
            self.errors.update(ex.message_dict)
        except IntegrityError as ex:
            self.errors.update({'__all__': ex.args})

    def index_from_concept(self):
        if self.from_concept_id:
            batch_index_resources(  # pylint: disable=expression-not-assigned
                'concepts', {'versioned_object_id': self.from_concept.versioned_object_id}
            ) if get(settings, 'TEST_MODE', False) else batch_index_resources.delay(
                'concepts', {'versioned_object_id': self.from_concept.versioned_object_id}
            )

    def get_next_sort_weight(self):
        if not self.sort_weight and self.from_concept_id and self.map_type:
            max_sort_weight = self.from_concept.get_unidirectional_mappings().filter(
                map_type=self.map_type, retired=False
            ).aggregate(
                max_sort_weight=Max('sort_weight')
            )['max_sort_weight']
            if max_sort_weight is not None:
                max_sort_weight += 1
            return max_sort_weight
        return None

    @classmethod
    def persist_new(cls, data, user):
        related_fields = ['from_concept_url', 'to_concept_url', 'to_source_url', 'from_source_url']
        field_data = {k: v for k, v in data.items() if k not in related_fields}
        url_params = {k: v for k, v in data.items() if k in related_fields}

        mapping = Mapping(**field_data, created_by=user, updated_by=user)

        temp_version = generate_temp_version()
        mapping.mnemonic = data.get('mnemonic', temp_version)
        mapping.version = temp_version
        mapping.errors = {}
        if mapping.is_existing_in_parent():
            mapping.errors = {'__all__': [ALREADY_EXISTS]}
            return mapping
        mapping.populate_fields_from_relations(url_params)

        try:
            mapping.full_clean()
            mapping.save()
            mapping.versioned_object_id = mapping.id
            mapping.version = str(mapping.id)
            mapping.is_latest_version = False
            parent = mapping.parent
            if mapping.mnemonic == temp_version:
                next_valid_seq = parent.mapping_mnemonic_next  # returns str of int or None
                if parent.is_sequential_mappings_mnemonic:
                    try:
                        available_next = int(parent.get_max_mapping_mnemonic())
                        if available_next and available_next >= int(next_valid_seq):
                            PostgresQL.update_seq(parent.mappings_mnemonic_seq_name, available_next)
                            next_valid_seq = parent.mapping_mnemonic_next
                    except:  # pylint: disable=bare-except
                        pass
                mapping.mnemonic = next_valid_seq or str(mapping.id)
            if not mapping.external_id:
                mapping.external_id = parent.mapping_external_id_next
            mapping.public_access = parent.public_access
            mapping.save()
            initial_version = cls.create_initial_version(mapping)
            if initial_version.id and not mapping._index:
                mapping.latest_version_id = initial_version.id
            initial_version.sources.set([parent])
            mapping.sources.set([parent])
            mapping.set_checksums()
            if mapping._counted is True:
                parent.update_mappings_count()
                mapping.index_from_concept()
        except ValidationError as ex:
            mapping.errors.update(ex.message_dict)
        except IntegrityError as ex:
            mapping.errors.update({'__all__': ex.args})

        return mapping

    def update_versioned_object(self):
        mapping = self.versioned_object
        mapping.extras = self.extras
        mapping.map_type = self.map_type
        mapping.retired = self.retired
        mapping.external_id = self.external_id or mapping.external_id
        mapping.sort_weight = self.sort_weight

        mapping.from_concept_id = self.from_concept_id
        mapping.to_concept_id = self.to_concept_id
        mapping.to_source_id = self.to_source_id
        mapping.from_source_id = self.from_source_id

        mapping.to_concept_code = self.to_concept_code
        mapping.to_concept_name = self.to_concept_name
        mapping.to_source_url = self.to_source_url
        mapping.to_source_version = self.to_source_version
        mapping.from_concept_code = self.from_concept_code
        mapping.from_concept_name = self.from_concept_name
        mapping.from_source_url = self.from_source_url
        mapping.from_source_version = self.from_source_version
        mapping.updated_by_id = self.updated_by_id
        mapping.save()
        mapping.set_checksums()

    def post_version_create(self, parent, _):
        if self.id:
            self.version = str(self.id)
            self.save()
            self.update_versioned_object()
            self.sources.set([parent])

    def _index_on_new_version_creation(self, prev_latest):
        if self._index:
            super()._index_on_new_version_creation(prev_latest)
            self.index_from_concept()

    @classmethod
    def get_base_queryset(cls, params):  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
        queryset = cls.objects.filter(is_active=True)
        user = params.get('user', None)
        org = params.get('org', None)
        collection = params.get('collection', None)
        source = params.get('source', None)
        container_version = params.get('version', None)
        mapping = params.get('mapping', None)
        mapping_version = params.get('mapping_version', None)
        latest_released_version = None
        is_latest_released = container_version == LATEST
        if is_latest_released:
            filters = {'user__username': user, 'organization__mnemonic': org}
            if source:
                from core.sources.models import Source
                latest_released_version = Source.find_latest_released_version_by(
                    {**filters, 'mnemonic': source})
            elif collection:
                from core.collections.models import Collection
                latest_released_version = Collection.find_latest_released_version_by(
                    {**filters, 'mnemonic': collection})

            if not latest_released_version:
                return cls.objects.none()

        if collection:
            queryset = queryset.filter(
                cls.get_filter_by_container_criterion(
                    'expansion_set__collection_version', collection, org, user, container_version,
                    is_latest_released, latest_released_version,
                )
            )
        if source:
            queryset = queryset.filter(
                cls.get_filter_by_container_criterion(
                    'sources', source, org, user, container_version,
                    is_latest_released, latest_released_version, 'parent'
                )
            )

        if mapping:
            queryset = queryset.filter(mnemonic__exact=mapping)
        if mapping_version:
            queryset = queryset.filter(version=mapping_version)

        return cls.apply_attribute_based_filters(queryset, params)

    @staticmethod
    def get_serializer_class(verbose=False, version=False, brief=False, reverse=False):
        if brief:
            from core.mappings.serializers import MappingMinimalSerializer, MappingReverseMinimalSerializer
            return MappingReverseMinimalSerializer if reverse else MappingMinimalSerializer
        if version:
            from core.mappings.serializers import MappingVersionDetailSerializer, MappingVersionListSerializer
            return MappingVersionDetailSerializer if verbose else MappingVersionListSerializer

        from core.mappings.serializers import MappingDetailSerializer, MappingListSerializer
        return MappingDetailSerializer if verbose else MappingListSerializer
