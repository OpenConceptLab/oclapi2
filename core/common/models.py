from celery.result import AsyncResult
from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, IntegrityError
from django.db.models import Value, Q, Max
from django.db.models.expressions import CombinedExpression, F
from django.utils import timezone
from django.utils.functional import cached_property
from django_elasticsearch_dsl.registries import registry
from django_elasticsearch_dsl.signals import RealTimeSignalProcessor
from pydash import get

from core.common.services import S3
from core.common.utils import reverse_resource, reverse_resource_version, parse_updated_since_param, drop_version
from core.settings import DEFAULT_LOCALE
from core.sources.constants import CONTENT_REFERRED_PRIVATELY
from .constants import (
    ACCESS_TYPE_CHOICES, DEFAULT_ACCESS_TYPE, NAMESPACE_REGEX,
    ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT, SUPER_ADMIN_USER_ID,
    HEAD, PERSIST_NEW_ERROR_MESSAGE, SOURCE_PARENT_CANNOT_BE_NONE, PARENT_RESOURCE_CANNOT_BE_NONE,
    CREATOR_CANNOT_BE_NONE, CANNOT_DELETE_ONLY_VERSION, CUSTOM_VALIDATION_SCHEMA_OPENMRS)
from .tasks import handle_save, handle_m2m_changed, seed_children, update_validation_schema


class BaseModel(models.Model):
    """
    Base model from which all resources inherit.
    Contains timestamps and is_active field for logical deletion.
    """
    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['uri']),
            models.Index(fields=['-updated_at']),
            models.Index(fields=['-created_at']),
            models.Index(fields=['is_active']),
            models.Index(fields=['public_access'])
        ]

    id = models.BigAutoField(primary_key=True)
    public_access = models.CharField(
        max_length=16, choices=ACCESS_TYPE_CHOICES, default=DEFAULT_ACCESS_TYPE, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        'users.UserProfile',
        related_name='%(app_label)s_%(class)s_related_created_by',
        related_query_name='%(app_label)s_%(class)ss_created_by',
        on_delete=models.DO_NOTHING,
        default=SUPER_ADMIN_USER_ID,
    )
    updated_by = models.ForeignKey(
        'users.UserProfile',
        related_name='%(app_label)s_%(class)s_related_updated_by',
        related_query_name='%(app_label)s_%(class)ss_updated_by',
        on_delete=models.DO_NOTHING,
        default=SUPER_ADMIN_USER_ID,
    )
    is_active = models.BooleanField(default=True)
    extras = models.JSONField(null=True, blank=True, default=dict)
    uri = models.TextField(null=True, blank=True, db_index=True)
    extras_have_been_encoded = False
    extras_have_been_decoded = False
    is_being_saved = False

    @property
    def model_name(self):
        return self.__class__.__name__

    @property
    def app_name(self):
        return self.__module__.split('.')[1]

    def index(self):
        if not get(settings, 'TEST_MODE', False):
            handle_save.delay(self.app_name, self.model_name, self.id)

    def soft_delete(self):
        if self.is_active:
            self.is_active = False
            self.save()

    def undelete(self):
        if not self.is_active:
            self.is_active = True
            self.save()

    @property
    def is_versioned(self):
        return False

    @property
    def public_can_view(self):
        return self.public_access.lower() in [ACCESS_TYPE_EDIT.lower(), ACCESS_TYPE_VIEW.lower()]

    @property
    def resource_type(self):
        return get(self, 'OBJECT_TYPE')

    @property
    def resource_version_type(self):
        return get(self, 'OBJECT_VERSION_TYPE') or self.resource_type

    @property
    def url(self):
        if self.uri:
            return self.uri

        return self.calculate_uri()

    def calculate_uri(self):
        if self.is_versioned and not self.is_head:
            uri = reverse_resource_version(self, self.view_name)
        else:
            uri = reverse_resource(self, self.view_name)

        return uri

    @property
    def view_name(self):
        return self.get_default_view_name()

    def get_default_view_name(self):
        entity_name = self.__class__.__name__.lower()

        if self.is_versioned and not self.is_head:
            return "{}-version-detail".format(entity_name)

        return "{}-detail".format(entity_name)

    @classmethod
    def pause_indexing(cls):
        cls.toggle_indexing(False)

    @classmethod
    def resume_indexing(cls):
        if not get(settings, 'TEST_MODE', False):
            cls.toggle_indexing(True)   # pragma: no cover

    @staticmethod
    def toggle_indexing(state=True):
        settings.ELASTICSEARCH_DSL_AUTO_REFRESH = state
        settings.ELASTICSEARCH_DSL_AUTOSYNC = state
        settings.ES_SYNC = state

    @staticmethod
    def get_iexact_or_criteria(attr, values):
        criteria = Q()

        if isinstance(values, str):
            values = values.split(',')

        for value in values:
            criteria = criteria | Q(**{'{}__iexact'.format(attr): value})

        return criteria

    @staticmethod
    def batch_index(queryset, document):
        count = queryset.count()
        batch_size = 1000
        offset = 0
        limit = batch_size
        while offset < count:
            document().update(queryset.order_by('-id')[offset:limit], parallel=True)
            offset = limit
            limit += batch_size

    @staticmethod
    def batch_delete(queryset):
        for batch in queryset.iterator(chunk_size=1000):
            batch.delete()


class CommonLogoModel(models.Model):
    logo_path = models.TextField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def logo_url(self):
        url = None
        if self.logo_path:
            url = S3.public_url_for(self.logo_path)

        return url

    def upload_base64_logo(self, data, name):
        name = self.uri[1:] + name
        self.logo_path = S3.upload_base64(data, name, False, True)
        self.save()


class BaseResourceModel(BaseModel, CommonLogoModel):
    """
    A base resource has a mnemonic that is unique across all objects of its type.
    A base resource may contain sub-resources.
    (An Organization is a base resource, but a Concept is not.)
    """
    mnemonic = models.CharField(
        max_length=255, validators=[RegexValidator(regex=NAMESPACE_REGEX)],
        db_index=True
    )
    mnemonic_attr = 'mnemonic'

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['mnemonic']),
        ] + BaseModel.Meta.indexes

    def __str__(self):
        return str(self.mnemonic)


class VersionedModel(BaseResourceModel):
    version = models.CharField(max_length=255)
    released = models.BooleanField(default=False, blank=True, null=True)
    retired = models.BooleanField(default=False)
    is_latest_version = models.BooleanField(default=True)
    name = models.TextField()
    full_name = models.TextField(null=True, blank=True)
    default_locale = models.TextField(default=DEFAULT_LOCALE, blank=True)
    supported_locales = ArrayField(models.CharField(max_length=20), null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    external_id = models.TextField(null=True, blank=True)
    custom_validation_schema = models.TextField(blank=True, null=True)

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['version']),
            models.Index(fields=['retired']),
            models.Index(fields=['is_latest_version']),
        ] + BaseResourceModel.Meta.indexes

    @property
    def is_versioned(self):
        return True

    @property
    def versioned_resource_type(self):
        return self.resource_type

    @property
    def versions(self):
        return self.__class__.objects.filter(**{self.mnemonic_attr: self.mnemonic}).order_by('-created_at')

    @property
    def active_versions(self):
        return self.versions.filter(is_active=True)

    @property
    def released_versions(self):
        return self.active_versions.filter(released=True)

    @property
    def num_versions(self):
        return self.versions.count()

    @property
    def sibling_versions(self):
        return self.versions.exclude(id=self.id)

    @property
    def prev_version(self):
        return self.sibling_versions.filter(
            is_active=True, created_at__lte=self.created_at
        ).order_by('-created_at').first()

    @property
    def prev_version_uri(self):
        return get(self, 'prev_version.uri')

    @property
    def is_head(self):
        return self.version == HEAD

    def get_head(self):
        return self.active_versions.filter(version=HEAD).first()

    head = property(get_head)

    @property
    def versioned_object_url(self):
        return drop_version(self.uri)

    @classmethod
    def get_version(cls, mnemonic, version=HEAD, filters=None):
        if not filters:
            filters = {}
        return cls.objects.filter(**{cls.mnemonic_attr: mnemonic, **filters}, version=version).first()

    def get_latest_version(self):
        return self.active_versions.filter(is_latest_version=True).order_by('-created_at').first()

    def get_latest_released_version(self):
        return self.released_versions.order_by('-created_at').first()

    @classmethod
    def find_latest_released_version_by(cls, filters):
        return cls.objects.filter(**filters, released=True).order_by('-created_at').first()

    def get_url_kwarg(self):
        if self.is_head:
            return self.get_resource_url_kwarg()
        return self.get_version_url_kwarg()

    @property
    def versions_url(self):
        return drop_version(self.uri) + 'versions/'


class ConceptContainerModel(VersionedModel):
    """
    A sub-resource is an object that exists within the scope of its parent resource.
    Its mnemonic is unique within the scope of its parent resource.
    (A Source is a sub-resource, but an Organization is not.)
    """
    organization = models.ForeignKey('orgs.Organization', on_delete=models.CASCADE, blank=True, null=True)
    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, blank=True, null=True)
    _background_process_ids = ArrayField(models.CharField(max_length=255), default=list, null=True, blank=True)

    canonical_url = models.URLField(null=True, blank=True)
    identifier = models.JSONField(null=True, blank=True, default=dict)
    contact = models.JSONField(null=True, blank=True, default=dict)
    jurisdiction = models.JSONField(null=True, blank=True, default=dict)
    publisher = models.TextField(null=True, blank=True)
    purpose = models.TextField(null=True, blank=True)
    copyright = models.TextField(null=True, blank=True)
    revision_date = models.DateField(null=True, blank=True)
    text = models.TextField(null=True, blank=True)  # for about description (markup)
    client_configs = GenericRelation(
        'client_configs.ClientConfig', object_id_field='resource_id', content_type_field='resource_type'
    )
    snapshot = models.JSONField(null=True, blank=True, default=dict)
    experimental = models.BooleanField(null=True, blank=True, default=None)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        abstract = True
        indexes = [] + VersionedModel.Meta.indexes

    @property
    def is_openmrs_schema(self):
        return self.custom_validation_schema == CUSTOM_VALIDATION_SCHEMA_OPENMRS

    @property
    def active_concepts(self):
        return self.concepts.filter(retired=False, is_active=True).count()

    @property
    def active_mappings(self):
        return self.mappings.filter(retired=False, is_active=True).count()

    @property
    def last_concept_update(self):
        return get(self.concepts.aggregate(max_updated_at=Max('updated_at')), 'max_updated_at', None)

    @property
    def last_mapping_update(self):
        return get(self.mappings.aggregate(max_updated_at=Max('updated_at')), 'max_updated_at', None)

    @property
    def last_child_update(self):
        last_concept_update = self.last_concept_update
        last_mapping_update = self.last_mapping_update
        if last_concept_update and last_mapping_update:
            return max(last_concept_update, last_mapping_update)
        return last_concept_update or last_mapping_update or self.updated_at or timezone.now()

    @classmethod
    def get_base_queryset(cls, params):
        username = params.get('user', None)
        org = params.get('org', None)
        version = params.get('version', None)
        is_latest = params.get('is_latest', None) in [True, 'true']
        updated_since = parse_updated_since_param(params)

        queryset = cls.objects.filter(is_active=True)
        if username:
            queryset = queryset.filter(cls.get_iexact_or_criteria('user__username', username))
        if org:
            queryset = queryset.filter(cls.get_iexact_or_criteria('organization__mnemonic', org))
        if version:
            queryset = queryset.filter(cls.get_iexact_or_criteria('version', version))
        if is_latest:
            queryset = queryset.filter(is_latest_version=True)
        if updated_since:
            queryset = queryset.filter(updated_at__gte=updated_since)

        return queryset

    @property
    def concepts_url(self):
        return reverse_resource(self, 'concept-list')

    @property
    def mappings_url(self):
        return reverse_resource(self, 'mapping-list')

    @property
    def parent(self):
        parent = None
        if self.organization_id:
            parent = self.organization
        if self.user_id:
            parent = self.user

        return parent

    @property
    def parent_id(self):
        return self.organization_id or self.user_id

    @property
    def parent_url(self):
        return get(self, 'parent.url')

    @property
    def parent_resource(self):
        return get(self, 'parent.mnemonic')

    @property
    def parent_resource_type(self):
        return get(self, 'parent.resource_type')

    @property
    def versions(self):
        return super().versions.filter(
            organization_id=self.organization_id, user_id=self.user_id
        ).order_by('-created_at')

    @staticmethod
    def is_content_privately_referred():
        return False

    def delete(self, using=None, keep_parents=False, force=False):  # pylint: disable=arguments-differ
        if self.is_content_privately_referred():
            raise ValidationError(dict(detail=CONTENT_REFERRED_PRIVATELY.format(self.mnemonic)))

        generic_export_path = self.generic_export_path(suffix=None)

        if self.is_head:
            self.versions.exclude(id=self.id).delete()
        else:
            if self.is_latest_version:
                prev_version = self.prev_version
                if not force and not prev_version:
                    raise ValidationError(dict(detail=CANNOT_DELETE_ONLY_VERSION))
                if prev_version:
                    prev_version.is_latest_version = True
                    prev_version.save()

        from core.pins.models import Pin
        Pin.objects.filter(resource_type__model=self.resource_type.lower(), resource_id=self.id).delete()

        super().delete(using=using, keep_parents=keep_parents)
        S3.delete_objects(generic_export_path)

    def get_active_concepts(self):
        return self.get_concepts_queryset().filter(is_active=True, retired=False)

    @property
    def num_concepts(self):
        return self.get_concepts_queryset().count()

    def get_concepts_queryset(self):
        return self.concepts_set.filter(id=F('versioned_object_id'))

    @staticmethod
    def get_version_url_kwarg():
        return 'version'

    def set_parent(self, parent_resource):
        parent_resource_type = parent_resource.resource_type

        if parent_resource_type == 'Organization':
            self.organization = parent_resource
        elif parent_resource_type in ['UserProfile', 'User']:
            self.user = parent_resource

    @staticmethod
    def update_mappings():
        pass

    @staticmethod
    def seed_references():
        pass

    @classmethod
    def persist_new(cls, obj, created_by, **kwargs):
        errors = {}
        parent_resource = kwargs.pop('parent_resource', None) or obj.parent
        if not parent_resource:
            errors['parent'] = PARENT_RESOURCE_CANNOT_BE_NONE
            return errors
        obj.set_parent(parent_resource)
        user = created_by
        if not user:
            errors['created_by'] = CREATOR_CANNOT_BE_NONE
        if errors:
            return errors

        obj.created_by = user
        obj.updated_by = user
        try:
            obj.full_clean()
        except ValidationError as ex:
            errors.update(ex.message_dict)
        if errors:
            return errors

        persisted = False
        obj.version = HEAD
        try:
            obj.save(**kwargs)
            obj.update_mappings()
            persisted = True
        except IntegrityError as ex:
            errors.update({'__all__': ex.args})
        finally:
            if not persisted:
                errors['non_field_errors'] = PERSIST_NEW_ERROR_MESSAGE.format(cls.__name__)
        return errors

    @classmethod
    def persist_new_version(cls, obj, user=None, **kwargs):
        from core.collections.serializers import CollectionDetailSerializer
        from core.sources.serializers import SourceDetailSerializer

        errors = {}

        obj.is_active = True
        if user:
            obj.created_by = user
            obj.updated_by = user
        serializer = SourceDetailSerializer if obj.__class__.__name__ == 'Source' else CollectionDetailSerializer
        obj.snapshot = serializer(obj.head).data
        obj.update_version_data()
        obj.save(**kwargs)

        if get(settings, 'TEST_MODE', False):
            obj.seed_concepts()
            obj.seed_mappings()
            obj.seed_references()
        else:
            seed_children.delay(obj.resource_type.lower(), obj.id)

        if obj.id:
            obj.sibling_versions.update(is_latest_version=False)

        return errors

    @classmethod
    def persist_changes(cls, obj, updated_by, original_schema, **kwargs):
        errors = {}
        parent_resource = kwargs.pop('parent_resource', obj.parent)
        if not parent_resource:
            errors['parent'] = SOURCE_PARENT_CANNOT_BE_NONE

        queue_schema_update_task = obj.is_validation_necessary()

        try:
            obj.full_clean()
        except ValidationError as ex:
            errors.update(ex.message_dict)

        if errors:
            return errors

        if updated_by:
            obj.updated_by = updated_by
        try:
            if queue_schema_update_task:
                target_schema = obj.custom_validation_schema
                obj.custom_validation_schema = original_schema

            obj.save(**kwargs)

            if queue_schema_update_task:
                update_validation_schema.delay(obj.app_name, obj.id, target_schema)
        except IntegrityError as ex:
            errors.update({'__all__': ex.args})

        return errors

    def validate_child_concepts(self):
        # If source is being configured to have a validation schema
        # we need to validate all concepts
        # according to the new schema
        from core.concepts.validators import ValidatorSpecifier

        concepts = self.get_active_concepts()
        failed_concept_validations = []

        validator = ValidatorSpecifier().with_validation_schema(
            self.custom_validation_schema
        ).with_repo(self).with_reference_values().get()

        for concept in concepts:
            try:
                validator.validate(concept)
            except ValidationError as validation_error:
                concept_validation_error = dict(
                    mnemonic=concept.mnemonic, url=concept.url, errors=validation_error.message_dict
                )
                failed_concept_validations.append(concept_validation_error)

        return failed_concept_validations

    def update_version_data(self, obj=None):
        if obj:
            self.description = obj.description
        else:
            obj = self.get_latest_version()

        if obj:
            self.name = obj.name
            self.full_name = obj.full_name
            self.website = obj.website
            self.public_access = obj.public_access
            self.supported_locales = obj.supported_locales
            self.default_locale = obj.default_locale
            self.external_id = obj.external_id
            self.organization = obj.organization
            self.user = obj.user
            self.canonical_url = obj.canonical_url

    def seed_concepts(self, index=True):
        head = self.head
        if head:
            from core.sources.models import Source
            if self.__class__ == Source:
                concepts = head.concepts.filter(is_latest_version=True)
            else:
                concepts = head.concepts.all()

            self.concepts.set(concepts)
            if index:
                from core.concepts.documents import ConceptDocument
                self.batch_index(self.concepts, ConceptDocument)

    def seed_mappings(self, index=True):
        head = self.head
        if head:
            from core.sources.models import Source
            if self.__class__ == Source:
                mappings = head.mappings.filter(is_latest_version=True)
            else:
                mappings = head.mappings.all()

            self.mappings.set(mappings)
            if index:
                from core.mappings.documents import MappingDocument
                self.batch_index(self.mappings, MappingDocument)

    def index_children(self):
        from core.concepts.documents import ConceptDocument
        from core.mappings.documents import MappingDocument

        self.batch_index(self.concepts, ConceptDocument)
        self.batch_index(self.mappings, MappingDocument)

    def add_processing(self, process_id):
        if self.id:
            self.__class__.objects.filter(id=self.id).update(
                _background_process_ids=CombinedExpression(
                    F('_background_process_ids'),
                    '||',
                    Value([process_id], ArrayField(models.CharField(max_length=255)))
                )
            )
        if process_id:
            self._background_process_ids.append(process_id)

    def remove_processing(self, process_id):
        if self.id and self._background_process_ids and process_id in self._background_process_ids:
            self._background_process_ids.remove(process_id)
            self.save(update_fields=['_background_process_ids'])

    @property
    def is_processing(self):
        background_ids = self._background_process_ids
        if background_ids:
            for process_id in background_ids.copy():
                if process_id:
                    res = AsyncResult(process_id)
                    if res.successful() or res.failed():
                        self.remove_processing(process_id)
                    else:
                        return True
                else:
                    self.remove_processing(process_id)

        return False

    def clear_processing(self):
        self._background_process_ids = []
        self.save(update_fields=['_background_process_ids'])

    @property
    def is_exporting(self):
        is_processing = self.is_processing

        if is_processing:
            for process_id in self._background_process_ids:
                res = AsyncResult(process_id)
                task_name = res.name
                if task_name and task_name.startswith('core.common.tasks.export_'):
                    return True

        return False

    @cached_property
    def export_path(self):
        last_update = self.last_child_update.strftime('%Y%m%d%H%M%S')
        return self.generic_export_path(suffix="{}.zip".format(last_update))

    def generic_export_path(self, suffix='*'):
        path = "{}/{}_{}.".format(self.parent_resource, self.mnemonic, self.version)
        if suffix:
            path += suffix

        return path

    def get_export_url(self):
        return S3.url_for(self.export_path)

    def has_export(self):
        return S3.exists(self.export_path)


class CelerySignalProcessor(RealTimeSignalProcessor):
    def handle_save(self, sender, instance, **kwargs):
        if settings.ES_SYNC and instance.__class__ in registry.get_models():
            handle_save.delay(instance.app_name, instance.model_name, instance.id)

    def handle_m2m_changed(self, sender, instance, action, **kwargs):
        if settings.ES_SYNC and instance.__class__ in registry.get_models():
            handle_m2m_changed.delay(instance.app_name, instance.model_name, instance.id, action)
