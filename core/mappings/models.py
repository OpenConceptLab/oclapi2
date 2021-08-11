import uuid

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, IntegrityError, transaction
from pydash import get, compact

from core.common.constants import INCLUDE_RETIRED_PARAM, NAMESPACE_REGEX, HEAD, LATEST
from core.common.mixins import SourceChildMixin
from core.common.models import VersionedModel
from core.common.utils import parse_updated_since_param, separate_version, to_parent_uri, generate_temp_version, \
    encode_string, is_url_encoded_string
from core.mappings.constants import MAPPING_TYPE, MAPPING_IS_ALREADY_RETIRED, MAPPING_WAS_RETIRED, \
    MAPPING_IS_ALREADY_NOT_RETIRED, MAPPING_WAS_UNRETIRED, PERSIST_CLONE_ERROR, PERSIST_CLONE_SPECIFY_USER_ERROR, \
    ALREADY_EXISTS
from core.mappings.mixins import MappingValidationMixin


class Mapping(MappingValidationMixin, SourceChildMixin, VersionedModel):
    class Meta:
        db_table = 'mappings'
        unique_together = ('mnemonic', 'version', 'parent')
        indexes = [
            models.Index(fields=['is_active', 'retired', 'is_latest_version', 'public_access']),
        ] + VersionedModel.Meta.indexes

    parent = models.ForeignKey('sources.Source', related_name='mappings_set', on_delete=models.CASCADE)
    map_type = models.TextField(db_index=True)

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

    es_fields = {
        'id': {'sortable': True, 'filterable': True, 'exact': True},
        'last_update': {'sortable': True, 'filterable': False, 'facet': False, 'default': 'desc'},
        'concept': {'sortable': False, 'filterable': True, 'facet': False, 'exact': True},
        'from_concept': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'to_concept': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'retired': {'sortable': False, 'filterable': True, 'facet': True},
        'map_type': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'source': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'collection': {'sortable': False, 'filterable': True, 'facet': True},
        'collection_owner_url': {'sortable': False, 'filterable': False, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'concept_source': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'from_concept_source': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'to_concept_source': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'concept_owner': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'from_concept_owner': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'to_concept_owner': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'concept_owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'from_concept_owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'to_concept_owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'external_id': {'sortable': False, 'filterable': True, 'facet': False, 'exact': False},
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
        return "%s:%s" % (self.from_source_owner_mnemonic, self.from_source_name)

    @property
    def from_concept_url(self):
        return get(self, 'from_concept.url', '')

    @property
    def from_concept_shorthand(self):
        return "%s:%s" % (self.from_source_shorthand, self.from_concept_code)

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
        return self.get_to_source() and "%s:%s" % (self.to_source_owner_mnemonic, self.to_source_name)

    def get_to_concept_name(self):
        return self.to_concept_name or get(self, 'to_concept.display_name')

    def get_to_concept_code(self):
        return self.to_concept_code or get(self, 'to_concept.mnemonic')

    @property
    def to_concept_url(self):
        return get(self, 'to_concept.url')

    @property
    def to_concept_shorthand(self):
        return "%s:%s" % (self.to_source_shorthand, self.get_to_concept_code())

    @staticmethod
    def get_resource_url_kwarg():
        return 'mapping'

    @staticmethod
    def get_version_url_kwarg():
        return 'mapping_version'

    def clone(self, user=None):
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
            to_concept_id=self.to_concept_id,
            to_concept_code=self.to_concept_code,
            to_concept_name=self.to_concept_name,
            to_source_id=self.to_source_id,
            to_source_url=self.to_source_url,
            to_source_version=self.to_source_version,
            from_concept_id=self.from_concept_id,
            from_concept_code=self.from_concept_code,
            from_concept_name=self.from_concept_name,
            from_source_id=self.from_source_id,
            from_source_url=self.from_source_url,
            from_source_version=self.from_source_version,
        )
        if user:
            mapping.created_by = mapping.updated_by = user

        return mapping

    @classmethod
    def create_initial_version(cls, mapping, **kwargs):
        initial_version = mapping.clone()
        initial_version.save(**kwargs)
        initial_version.version = initial_version.id
        initial_version.released = False
        initial_version.is_latest_version = True
        initial_version.save()
        return initial_version

    def populate_fields_from_relations(self, data):
        from core.concepts.models import Concept
        from core.sources.models import Source

        to_concept_url = data.get('to_concept_url', None)
        from_concept_url = data.get('from_concept_url', None)
        to_source_url = data.get('to_source_url', None)
        from_source_url = data.get('from_source_url', None)

        def get_concept(expression):
            concept = Concept.objects.filter(uri=expression).first()
            if concept:
                return concept
            concept = Concept.objects.filter(uri=encode_string(expression, safe='/')).first()
            if concept:
                return concept

            parent_uri = to_parent_uri(expression)
            code = expression.replace(parent_uri, '').replace('concepts/', '').split('/')[0]
            return dict(mnemonic=code)

        def get_source_info(parent_uri, child_uri, existing_version, concept):
            if not parent_uri and not child_uri:
                return existing_version, get(concept, 'parent.uri')

            if parent_uri:
                version, uri = separate_version(parent_uri)
            else:
                version, uri = separate_version(to_parent_uri(child_uri))

            return version or existing_version, uri or get(concept, 'parent.uri')

        to_concept_code = data.get('to_concept_code')
        from_concept_code = data.get('from_concept_code')
        if to_concept_code and not is_url_encoded_string(to_concept_code):
            to_concept_code = encode_string(to_concept_code)
        if from_concept_code and not is_url_encoded_string(from_concept_code):
            from_concept_code = encode_string(from_concept_code)

        if not to_concept_url and not get(self, 'to_concept') and to_source_url and (
                to_concept_code or self.to_concept_code):
            to_concept_code = to_concept_code or self.to_concept_code
            to_concept_url = to_source_url + 'concepts/' + to_concept_code + '/'

        if not from_concept_url and not get(self, 'from_concept') and from_source_url and (
                from_concept_code or self.from_concept_code):
            from_concept_code = from_concept_code or self.from_concept_code
            from_concept_url = from_source_url + 'concepts/' + from_concept_code + '/'

        from_concept = get_concept(from_concept_url) if from_concept_url else get(self, 'from_concept')
        to_concept = get_concept(to_concept_url) if to_concept_url else get(self, 'to_concept')

        self.from_concept_id = get(from_concept, 'id')
        self.to_concept_id = get(to_concept, 'id')

        self.from_concept_code = data.get(
            'from_concept_code', None) or get(from_concept, 'mnemonic') or self.from_concept_code
        self.from_concept_name = data.get('from_concept_name', None) or self.from_concept_name
        self.to_concept_code = data.get('to_concept_code', None) or get(to_concept, 'mnemonic') or self.to_concept_code
        self.to_concept_name = data.get('to_concept_name', None) or self.to_concept_name

        self.from_source_version, self.from_source_url = get_source_info(
            from_source_url, from_concept_url, self.from_source_version, from_concept
        )
        self.to_source_version, self.to_source_url = get_source_info(
            to_source_url, to_concept_url, self.to_source_version, to_concept
        )

        self.to_source = Source.objects.filter(
            models.Q(uri=self.to_source_url) | models.Q(canonical_url=self.to_source_url)
        ).filter(version=HEAD).first()
        self.from_source = Source.objects.filter(
            models.Q(uri=self.from_source_url) | models.Q(canonical_url=self.from_source_url)
        ).filter(version=HEAD).first()

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

        return cls.persist_clone(instance, user)

    @classmethod
    def persist_new(cls, data, user):
        related_fields = ['from_concept_url', 'to_concept_url', 'to_source_url', 'from_source_url']
        field_data = {k: v for k, v in data.items() if k not in related_fields}
        url_params = {k: v for k, v in data.items() if k in related_fields}

        mapping = Mapping(**field_data, created_by=user, updated_by=user)

        temp_version = generate_temp_version()
        mapping.mnemonic = data.get('mnemonic', temp_version)
        mapping.version = temp_version
        mapping.errors = dict()
        if mapping.is_existing_in_parent():
            mapping.errors = dict(__all__=[ALREADY_EXISTS])
            return mapping
        mapping.populate_fields_from_relations(url_params)

        try:
            mapping.full_clean()
            mapping.save()
            mapping.versioned_object_id = mapping.id
            mapping.version = str(mapping.id)
            mapping.is_latest_version = False
            if mapping.mnemonic == temp_version:
                mapping.mnemonic = str(mapping.id)
            parent = mapping.parent
            parent_head = parent.head
            mapping.public_access = parent.public_access
            mapping.save()
            initial_version = cls.create_initial_version(mapping)
            initial_version.sources.set([parent, parent_head])
            mapping.sources.set([parent, parent_head])
        except ValidationError as ex:
            mapping.errors.update(ex.message_dict)
        except IntegrityError as ex:
            mapping.errors.update(dict(__all__=ex.args))

        return mapping

    def update_versioned_object(self):
        mapping = self.versioned_object
        mapping.extras = self.extras
        mapping.map_type = self.map_type
        mapping.retired = self.retired
        mapping.external_id = self.external_id or mapping.external_id

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

        mapping.save()

    @classmethod
    def persist_clone(cls, obj, user=None, **kwargs):
        errors = dict()
        if not user:
            errors['version_created_by'] = PERSIST_CLONE_SPECIFY_USER_ERROR
            return errors
        obj.version = obj.version or generate_temp_version()
        obj.created_by = user
        obj.updated_by = user
        parent = obj.parent
        parent_head = parent.head
        persisted = False
        prev_latest_version = None
        try:
            with transaction.atomic():
                cls.pause_indexing()

                obj.is_latest_version = True
                obj.save(**kwargs)
                if obj.id:
                    obj.version = str(obj.id)
                    obj.save()
                    obj.update_versioned_object()
                    versioned_object = obj.versioned_object
                    prev_latest_version = versioned_object.versions.exclude(id=obj.id).filter(
                        is_latest_version=True).first()
                    if prev_latest_version:
                        prev_latest_version.is_latest_version = False
                        prev_latest_version.save()

                    obj.sources.set(compact([parent, parent_head]))
                    persisted = True
                    cls.resume_indexing()

                    def index_all():
                        if prev_latest_version:
                            prev_latest_version.index()
                        obj.index()

                    transaction.on_commit(index_all)
        except ValidationError as err:
            errors.update(err.message_dict)
        finally:
            cls.resume_indexing()
            if not persisted:
                if obj.id:
                    obj.sources.remove(parent_head)
                    if prev_latest_version:
                        prev_latest_version.is_latest_version = True
                        prev_latest_version.save()
                    obj.delete()
                errors['non_field_errors'] = [PERSIST_CLONE_ERROR]

        return errors

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
        is_latest = params.get('is_latest', None) in [True, 'true']
        include_retired = params.get(INCLUDE_RETIRED_PARAM, None) in [True, 'true']
        updated_since = parse_updated_since_param(params)
        uri = params.get('uri', None)
        latest_released_version = None
        is_latest_released = container_version == LATEST
        if is_latest_released:
            filters = dict(user__username=user, organization__mnemonic=org)
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
                    'collection_set', collection, org, user, container_version,
                    is_latest_released, latest_released_version,
                )
            )
        if source:
            queryset = queryset.filter(
                cls.get_filter_by_container_criterion(
                    'sources', source, org, user, container_version,
                    is_latest_released, latest_released_version
                )
            )

        if mapping:
            queryset = queryset.filter(mnemonic__exact=mapping)
        if mapping_version:
            queryset = queryset.filter(version=mapping_version)
        if is_latest:
            queryset = queryset.filter(is_latest_version=True)
        if not include_retired and not mapping:
            queryset = queryset.filter(retired=False)
        if updated_since:
            queryset = queryset.filter(updated_at__gte=updated_since)
        if uri:
            queryset = queryset.filter(uri__icontains=uri)

        return queryset

    def is_from_same_as_to(self):
        return self.from_concept_code == self.to_concept_code and self.from_source_url == self.to_source_url
