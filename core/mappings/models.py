from django.core.exceptions import ValidationError
from django.db import models, IntegrityError, transaction
from pydash import get, compact

from core.common.constants import TEMP, INCLUDE_RETIRED_PARAM
from core.common.mixins import SourceChildMixin
from core.common.models import VersionedModel
from core.common.utils import parse_updated_since_param
from core.mappings.constants import MAPPING_TYPE, MAPPING_IS_ALREADY_RETIRED, MAPPING_WAS_RETIRED, \
    MAPPING_IS_ALREADY_NOT_RETIRED, MAPPING_WAS_UNRETIRED
from core.mappings.mixins import MappingValidationMixin


class Mapping(MappingValidationMixin, SourceChildMixin, VersionedModel):
    class Meta:
        db_table = 'mappings'

    parent = models.ForeignKey('sources.Source', related_name='mappings_set', on_delete=models.DO_NOTHING)
    map_type = models.TextField()
    from_concept = models.ForeignKey(
        'concepts.Concept', related_name='mappings_from', on_delete=models.CASCADE
    )
    to_concept = models.ForeignKey(
        'concepts.Concept', null=True, blank=True, related_name='mappings_to', on_delete=models.CASCADE
    )
    to_source = models.ForeignKey(
        'sources.Source', null=True, blank=True, related_name='mappings_to', on_delete=models.CASCADE
    )
    to_concept_code = models.TextField(null=True, blank=True)
    to_concept_name = models.TextField(null=True, blank=True)
    sources = models.ManyToManyField('sources.Source', related_name='mappings')
    external_id = models.TextField(null=True, blank=True)
    comment = models.TextField(null=True, blank=True)
    versioned_object = models.ForeignKey(
        'self', related_name='versions_set', null=True, blank=True, on_delete=models.CASCADE
    )
    name = None
    full_name = None
    default_locale = None
    supported_locales = None
    website = None
    description = None
    mnemonic = None
    mnemonic_attr = 'versioned_object_id'

    OBJECT_TYPE = MAPPING_TYPE
    ALREADY_RETIRED = MAPPING_IS_ALREADY_RETIRED
    ALREADY_NOT_RETIRED = MAPPING_IS_ALREADY_NOT_RETIRED
    WAS_RETIRED = MAPPING_WAS_RETIRED
    WAS_UNRETIRED = MAPPING_WAS_UNRETIRED

    @property
    def mnemonic(self):  # pylint: disable=function-redefined
        return self.versioned_object_id

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
    def from_source(self):
        return self.from_concept.parent

    @property
    def from_source_owner(self):
        return str(self.from_source.parent)

    @property
    def from_source_owner_mnemonic(self):
        return self.from_source.parent.mnemonic

    @property
    def from_source_owner_type(self):
        return self.from_source.parent.resource_type

    @property
    def from_source_name(self):
        return self.from_source.mnemonic

    @property
    def from_source_url(self):
        return self.from_source.url

    @property
    def from_source_shorthand(self):
        return "%s:%s" % (self.from_source_owner_mnemonic, self.from_source_name)

    @property
    def from_concept_code(self):
        return self.from_concept.mnemonic

    @property
    def from_concept_name(self):
        return self.from_concept.display_name

    @property
    def from_concept_url(self):
        return self.from_concept.url

    @property
    def from_concept_shorthand(self):
        return "%s:%s" % (self.from_source_shorthand, self.from_concept_code)

    def get_to_source(self):
        if self.to_source_id:
            return self.to_source
        if self.to_concept_id:
            return self.to_concept.parent

        return None

    @property
    def to_source_name(self):
        return get(self.get_to_source(), 'mnemonic')

    @property
    def to_source_url(self):
        return get(self.get_to_source(), 'url')

    @property
    def to_source_owner(self):
        return str(get(self.get_to_source(), 'parent', ''))

    @property
    def to_source_owner_mnemonic(self):
        return get(self.get_to_source(), 'owner.mnemonic')

    @property
    def to_source_owner_type(self):
        return get(self.get_to_source(), 'owner_type')

    @property
    def to_source_shorthand(self):
        return self.get_to_source() and "%s:%s" % (self.to_source_owner_mnemonic, self.to_source_name)

    @property
    def versioned_object_url(self):
        if self.is_versioned_object:
            return self.uri

        return self.versioned_object.uri

    def get_to_concept_name(self):
        if self.to_concept_name:
            return self.to_concept_name

        if self.to_concept_id:
            return self.to_concept.display_name

        return None

    def get_to_concept_code(self):
        return self.to_concept_code or (self.to_concept and self.to_concept.mnemonic)

    @property
    def to_concept_url(self):
        return self.to_concept.url if self.to_concept else None

    @property
    def to_concept_shorthand(self):
        return "%s:%s" % (self.to_source_shorthand, self.get_to_concept_code)

    @staticmethod
    def get_resource_url_kwarg():
        return 'mapping'

    @staticmethod
    def get_version_url_kwarg():
        return 'mapping_version'

    def clone(self, user=None):
        mapping = Mapping(
            version=TEMP,
            parent_id=self.parent_id,
            map_type=self.map_type,
            from_concept_id=self.from_concept_id,
            to_concept_id=self.to_concept_id,
            to_source_id=self.to_source_id,
            to_concept_code=self.to_concept_code,
            to_concept_name=self.to_concept_name,
            retired=self.retired,
            released=self.released,
            is_latest_version=self.is_latest_version,
            extras=self.extras,
            public_access=self.public_access,
            external_id=self.external_id,
            versioned_object_id=self.versioned_object_id
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

    @classmethod
    def persist_new(cls, data, user):
        from core.concepts.models import Concept

        from_concept_url = data.pop('from_concept_url', None)
        to_concept_url = data.pop('to_concept_url', None)

        mapping = Mapping(**data, created_by=user, updated_by=user)

        if from_concept_url:
            mapping.from_concept = Concept.from_uri_queryset(from_concept_url).first()
        if to_concept_url:
            mapping.to_concept = Concept.from_uri_queryset(to_concept_url).first()

        mapping.version = TEMP
        mapping.errors = dict()

        try:
            mapping.full_clean()
            mapping.save()
            if mapping.id:
                mapping.is_latest_version = False
                mapping.version = str(mapping.id)
                mapping.versioned_object_id = mapping.id
                mapping.save()
                initial_version = cls.create_initial_version(mapping)
                parent = mapping.parent
                parent_head = parent.head
                initial_version.sources.set([parent, parent_head])
                mapping.sources.set([parent, parent_head])
                parent.save()
                parent_head.save()
        except ValidationError as ex:
            mapping.errors.update(ex.message_dict)
        except IntegrityError as ex:
            mapping.errors.update(dict(__all__=ex.args))

        return mapping

    def update_versioned_object(self):
        mapping = self.versioned_object
        mapping.extras = self.extras
        mapping.map_type = self.map_type
        mapping.from_concept_id = self.from_concept_id
        mapping.to_concept_id = self.to_concept_id
        mapping.to_concept_code = self.to_concept_code
        mapping.to_concept_name = self.to_concept_name
        mapping.to_source_id = self.to_source_id
        mapping.retired = self.retired
        mapping.save()

    @classmethod
    def persist_clone(cls, obj, user=None, **kwargs):
        errors = dict()
        if not user:
            errors['version_created_by'] = "Must specify which user is attempting to create a new {} version.".format(
                cls.get_resource_url_kwarg()
            )
            return errors
        obj.version = TEMP
        obj.created_by = user
        obj.updated_by = user
        parent = obj.parent
        parent_head = parent.head
        persisted = False
        errored_action = 'saving new mapping version'
        latest_version = None
        try:
            with transaction.atomic():
                cls.pause_indexing()
                obj.is_latest_version = True
                obj.full_clean()
                obj.save(**kwargs)
                if obj.id:
                    obj.version = str(obj.id)
                    obj.save()
                    obj.update_versioned_object()
                    versioned_object = obj.versioned_object
                    latest_version = versioned_object.versions.exclude(id=obj.id).filter(is_latest_version=True).first()
                    latest_version.is_latest_version = False
                    latest_version.save()
                    obj.sources.set(compact([parent, parent_head]))

                    persisted = True
                    cls.resume_indexing()

                    def index_all():
                        parent.save()
                        parent_head.save()
                        latest_version.save()
                        obj.save()

                    transaction.on_commit(index_all)
        except ValidationError as err:
            errors.update(err.message_dict)
        finally:
            cls.resume_indexing()
            if not persisted:
                if obj.id:
                    obj.sources.remove(parent_head)
                    if latest_version:
                        latest_version.is_latest_version = True
                        latest_version.save()
                    obj.delete()
                errors['non_field_errors'] = ['An error occurred while %s.' % errored_action]

        return errors

    @classmethod
    def get_base_queryset(cls, params):  # pylint: disable=too-many-branches
        queryset = cls.objects.filter(is_active=True)
        user = params.get('user', None)
        org = params.get('org', None)
        collection = params.get('collection', None)
        source = params.get('source', None)
        container_version = params.get('version', None)
        mapping = params.get('mapping', None)
        mapping_version = params.get('mapping_version', None)
        is_latest = params.get('is_latest', None)
        include_retired = params.get(INCLUDE_RETIRED_PARAM, False)
        updated_since = parse_updated_since_param(params)

        if collection:
            queryset = queryset.filter(collection_set__mnemonic=collection)
            if user:
                queryset = queryset.filter(collection_set__user__mnemonic=user)
            if org:
                queryset = queryset.filter(collection_set__organization__mnemonic=org)
            if container_version:
                queryset = queryset.filter(collection_set__version=container_version)
        if source:
            queryset = queryset.filter(sources__mnemonic=source)
            if user:
                queryset = queryset.filter(parent__user__username=user)
            if org:
                queryset = queryset.filter(parent__organization__mnemonic=org)
            if container_version:
                queryset = queryset.filter(sources__version=container_version)

        if mapping:
            queryset = queryset.filter(versioned_object_id=mapping)
        if mapping_version:
            queryset = queryset.filter(version=mapping_version)
        if is_latest:
            queryset = queryset.filter(is_latest_version=True)
        if not include_retired:
            queryset = queryset.filter(retired=False)
        if updated_since:
            queryset = queryset.filter(updated_at__gte=updated_since)

        return queryset.distinct()
