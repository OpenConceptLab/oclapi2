from celery.result import AsyncResult
from django.conf import settings
from django.contrib.postgres.fields import JSONField, ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, IntegrityError
from django.db.models import Max, Value
from django.db.models.expressions import CombinedExpression, F
from django.utils import timezone
from django_elasticsearch_dsl.registries import registry
from django_elasticsearch_dsl.signals import RealTimeSignalProcessor
from pydash import get

from core.common.services import S3
from core.common.utils import reverse_resource, reverse_resource_version
from core.settings import DEFAULT_LOCALE
from .constants import (
    ACCESS_TYPE_CHOICES, DEFAULT_ACCESS_TYPE, NAMESPACE_REGEX,
    ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT, SUPER_ADMIN_USER_ID,
    HEAD)
from .tasks import handle_save, handle_m2m_changed


class BaseModel(models.Model):
    """
    Base model from which all resources inherit.
    Contains timestamps and is_active field for logical deletion.
    """
    class Meta:
        abstract = True

    id = models.BigAutoField(primary_key=True)
    internal_reference_id = models.CharField(max_length=255, null=True, blank=True)
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
    extras = JSONField(null=True, blank=True, default=dict)
    uri = models.TextField(null=True, blank=True)
    extras_have_been_encoded = False
    extras_have_been_decoded = False
    is_being_saved = False

    @property
    def model_name(self):
        return self.__class__.__name__

    @property
    def app_name(self):
        return self.__module__.split('.')[1]

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if not self.internal_reference_id and self.id:
            self.internal_reference_id = str(self.id)
        self.is_being_saved = True
        self.encode_extras()
        super().save(force_insert, force_update, using, update_fields)
        self.is_being_saved = False

    def encode_extras(self):
        if self.extras is not None and not self.extras_have_been_encoded:
            self.encode_extras_recursively(self.extras)
            self.extras_have_been_encoded = True

    def encode_extras_recursively(self, extras):
        if isinstance(extras, dict):
            for old_key in extras:
                key = old_key
                key = key.replace('%', '%25')
                key = key.replace('.', '%2E')
                value = extras.get(old_key)
                self.encode_extras_recursively(value)
                if key is not old_key:
                    extras.pop(old_key)
                    extras[key] = value
        elif isinstance(extras, list):
            for item in extras:
                self.encode_extras_recursively(item)

    def decode_extras(self, extras):
        if isinstance(extras, dict):
            for old_key in extras:
                key = old_key
                key = key.replace('%25', '%')
                key = key.replace('%2E', '.')
                value = extras.get(old_key)
                self.decode_extras(value)
                if key is not old_key:
                    extras.pop(old_key)
                    extras[key] = value
        elif isinstance(extras, list):
            for item in extras:
                self.decode_extras(item)

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
        return self.public_access in [ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW]

    @property
    def resource_type(self):
        return get(self, 'OBJECT_TYPE')

    @property
    def num_stars(self):
        return 0

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


class BaseResourceModel(BaseModel):
    """
    A base resource has a mnemonic that is unique across all objects of its type.
    A base resource may contain sub-resources.
    (An Organization is a base resource, but a Concept is not.)
    """
    mnemonic = models.CharField(
        max_length=255, validators=[RegexValidator(regex=NAMESPACE_REGEX)]
    )
    mnemonic_attr = 'mnemonic'

    class Meta:
        abstract = True

    def __str__(self):
        return str(self.mnemonic)


class VersionedModel(BaseResourceModel):
    version = models.CharField(max_length=255)
    released = models.NullBooleanField(default=False, blank=True, null=True)
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
    def is_head(self):
        return self.version == HEAD

    def get_head(self):
        return self.active_versions.filter(version=HEAD).first()

    head = property(get_head)

    @classmethod
    def get_version(cls, mnemonic, version=HEAD, filters=None):
        if not filters:
            filters = dict()
        return cls.objects.filter(**{cls.mnemonic_attr: mnemonic, **filters}, version=version).first()

    def get_latest_version(self):
        return self.active_versions.filter(is_latest_version=True).order_by('-created_at').first()

    def get_latest_released_version(self):
        return self.released_versions.order_by('-created_at').first()

    @classmethod
    def get_version_model(cls):
        return cls

    @property
    def versioned_object(self):
        return self.get_latest_version()

    def get_url_kwarg(self):
        if self.is_head:
            return self.get_resource_url_kwarg()
        return self.get_version_url_kwarg()


class ConceptContainerModel(VersionedModel):
    """
    A sub-resource is an object that exists within the scope of its parent resource.
    Its mnemonic is unique within the scope of its parent resource.
    (A Source is a sub-resource, but an Organization is not.)
    """
    organization = models.ForeignKey('orgs.Organization', on_delete=models.CASCADE, blank=True, null=True)
    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, blank=True, null=True)
    active_concepts = models.IntegerField(default=0)
    active_mappings = models.IntegerField(default=0)
    last_concept_update = models.DateTimeField(default=timezone.now, null=True, blank=True)
    last_mapping_update = models.DateTimeField(default=timezone.now, null=True, blank=True)
    last_child_update = models.DateTimeField(default=timezone.now)
    _background_process_ids = ArrayField(models.CharField(max_length=255), default=list, null=True, blank=True)

    class Meta:
        abstract = True

    @classmethod
    def get_base_queryset(cls, params):
        username = params.get('user', None)
        org = params.get('org', None)
        version = params.get('version', None)
        is_latest = params.get('is_latest', None)

        queryset = cls.objects.filter(is_active=True)
        if username:
            queryset = queryset.filter(user__username=username)
        if org:
            queryset = queryset.filter(organization__mnemonic=org)
        if version:
            queryset = queryset.filter(version=version)
        if is_latest:
            queryset = queryset.filter(is_latest_version=True)

        return queryset

    @property
    def concepts_url(self):
        return reverse_resource(self, 'concept-list')

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
        return self.parent.url

    @property
    def parent_resource(self):
        return self.parent.mnemonic

    @property
    def parent_resource_type(self):
        return self.parent.resource_type

    @property
    def versions(self):
        return super().versions.filter(
            organization_id=self.organization_id, user_id=self.user_id
        ).order_by('-created_at')

    @property
    def sibling_versions(self):
        return self.versions.exclude(id=self.id)

    @property
    def prev_version(self):
        return self.sibling_versions.filter(is_active=True).order_by('-created_at').first()

    def delete(self, using=None, keep_parents=False):
        if self.is_latest_version:
            prev_version = self.prev_version
            if not prev_version:
                raise ValidationError(dict(detail='Cannot delete only version.'))
            prev_version.is_latest_version = True
            prev_version.save()
        super().delete(using=using, keep_parents=keep_parents)

    def get_active_concepts(self):
        return self.concepts_set.filter(is_active=True, retired=False, version=HEAD)

    @property
    def num_concepts(self):
        return self.concepts_set.count()

    @staticmethod
    def get_version_url_kwarg():
        return 'version'

    def set_parent(self, parent_resource):
        parent_resource_type = parent_resource.resource_type

        if parent_resource_type == 'Organization':
            self.organization = parent_resource
        elif parent_resource_type in ['UserProfile', 'User']:
            self.user = parent_resource

    @classmethod
    def persist_new(cls, obj, created_by, **kwargs):
        errors = dict()
        parent_resource = kwargs.pop('parent_resource', None) or obj.parent
        if not parent_resource:
            errors['parent'] = 'Parent resource cannot be None.'
            return errors
        obj.set_parent(parent_resource)
        user = created_by
        if not user:
            errors['created_by'] = 'Creator cannot be None.'
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
            persisted = True
        except IntegrityError as ex:
            errors.update({'__all__': ex.args})
        finally:
            if not persisted:
                errors['non_field_errors'] = "An error occurred while trying to persist new %s." % cls.__name__
        return errors

    @classmethod
    def persist_new_version(cls, obj, user=None, **kwargs):
        errors = dict()

        obj.is_active = True
        if user:
            obj.created_by = user
            obj.updated_by = user
        obj.update_version_data()
        obj.save(**kwargs)
        obj.seed_concepts()
        obj.seed_mappings()
        from core.collections.models import Collection
        if isinstance(obj, Collection):
            obj.seed_references()

        if obj.id:
            obj.sibling_versions.update(is_latest_version=False)

        return errors

    @classmethod
    def persist_changes(cls, obj, updated_by, **kwargs):
        errors = dict()
        parent_resource = kwargs.pop('parent_resource', obj.parent)
        if not parent_resource:
            errors['parent'] = 'Source parent cannot be None.'

        if obj.is_validation_necessary():
            failed_concept_validations = obj.validate_child_concepts() or []
            if len(failed_concept_validations) > 0:
                errors.update({'failed_concept_validations': failed_concept_validations})

        try:
            obj.full_clean()
        except ValidationError as ex:
            errors.update(ex.message_dict)

        if errors:
            return errors

        if updated_by:
            obj.updated_by = updated_by
        try:
            obj.save(**kwargs)
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

    def update_active_counts(self):
        self.active_concepts = self.concepts.filter(retired=False).exclude(id=F('versioned_object_id')).count()
        self.active_mappings = self.mappings.filter(retired=False).count()

    def update_last_updates(self):
        self.last_concept_update = self.__get_last_concept_updated_at()
        self.last_mapping_update = self.__get_last_mapping_updated_at()
        self.last_child_update = self.__get_last_child_updated_at()

    def __get_last_concept_updated_at(self):
        concepts = self.concepts
        if not concepts.exists():
            return None
        agg = concepts.aggregate(Max('updated_at'))
        return agg.get('updated_at__max')

    def __get_last_mapping_updated_at(self):
        mappings = self.mappings
        if not mappings.exists():
            return None
        agg = mappings.aggregate(Max('updated_at'))
        return agg.get('updated_at__max')

    def __get_last_child_updated_at(self):
        last_concept_update = self.last_concept_update
        last_mapping_update = self.last_mapping_update
        if last_concept_update and last_mapping_update:
            return max(last_concept_update, last_mapping_update)
        return last_concept_update or last_mapping_update or self.updated_at or timezone.now()

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if self.id:
            self.update_active_counts()
            self.update_last_updates()
        super().save(force_insert, force_update, using, update_fields)

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

    def seed_concepts(self):
        head = self.head
        if head:
            self.concepts.set(head.concepts.all())

    def seed_mappings(self):
        head = self.head
        if head:
            self.mappings.set(head.mappings.all())

    def add_processing(self, process_id):
        if self.id:
            self.__class__.objects.filter(id=self.id).update(
                _background_process_ids=CombinedExpression(
                    F('_background_process_ids'),
                    '||',
                    Value([process_id], ArrayField(models.CharField(max_length=255)))
                )
            )
        self._background_process_ids.append(process_id)

    def remove_processing(self, process_id):
        if self.id:
            self._background_process_ids.remove(process_id)
            self.save(update_fields=['_background_process_ids'])

    @property
    def is_processing(self):
        if self._background_process_ids:
            for process_id in self._background_process_ids:
                res = AsyncResult(process_id)
                if res.successful() or res.failed():
                    self.remove_processing(process_id)
                else:
                    return True
        return bool(self._background_process_ids)

    def clear_processing(self):
        self._background_process_ids = list()
        self.save(update_fields=['_background_process_ids'])

    @staticmethod
    def clear_all_processing(klass):
        klass.objects.all().update(_background_process_ids=set())

    @property
    def export_path(self):
        last_update = self.last_child_update.strftime('%Y%m%d%H%M%S')
        source = self.head
        return "%s/%s_%s.%s.zip" % (source.owner_name, source.mnemonic, self.version, last_update)

    def get_export_url(self):
        return S3.url_for(self.export_path)

    def has_export(self):
        return bool(self.get_export_url())


class CelerySignalProcessor(RealTimeSignalProcessor):
    def handle_save(self, sender, instance, **kwargs):
        if settings.ES_SYNC and instance.__class__ in registry.get_models():
            handle_save.delay(instance.app_name, instance.model_name, instance.id)

    def handle_m2m_changed(self, sender, instance, action, **kwargs):
        if settings.ES_SYNC and instance.__class__ in registry.get_models():
            handle_m2m_changed.delay(instance.app_name, instance.model_name, instance.id, action)
