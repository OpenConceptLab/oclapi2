from django.contrib.postgres.fields import JSONField, ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from pydash import get

from core.common.utils import reverse_resource
from core.settings import DEFAULT_LOCALE
from .constants import (
    ACCESS_TYPE_CHOICES, DEFAULT_ACCESS_TYPE, NAMESPACE_REGEX,
    ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT, SUPER_ADMIN_USER_ID,
    HEAD)


class BaseModel(models.Model):
    """
    Base model from which all resources inherit.
    Contains timestamps and is_active field for logical deletion.
    """
    class Meta:
        abstract = True

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
    extras = JSONField(null=True, blank=True)
    uri = models.TextField(null=True, blank=True)
    extras_have_been_encoded = False
    extras_have_been_decoded = False
    is_being_saved = False

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
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
    def public_can_view(self):
        return self.public_access in [ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW]

    @classmethod
    def resource_type(cls):
        return get(cls, 'OBJECT_TYPE')

    @property
    def num_stars(self):
        return 0

    @property
    def url(self):
        return self.uri or reverse_resource(self, self.view_name)

    @property
    def view_name(self):
        return self.get_default_view_name()

    @property
    def _default_view_name(self):
        return '%(model_name)s-detail'

    def get_default_view_name(self):
        model = self.__class__
        model_meta = model._meta  # pylint: disable=protected-access
        format_kwargs = {
            'app_label': model_meta.app_label,
            'model_name': model_meta.object_name.lower()
        }
        return self._default_view_name % format_kwargs


class BaseResourceModel(BaseModel):
    """
    A base resource has a mnemonic that is unique across all objects of its type.
    A base resource may contain sub-resources.
    (An Organization is a base resource, but a Concept is not.)
    """
    mnemonic = models.CharField(
        max_length=255, validators=[RegexValidator(regex=NAMESPACE_REGEX)]
    )

    class Meta:
        abstract = True

    def __str__(self):
        return get(self, 'mnemonic', '')


class VersionedModel(BaseResourceModel):
    version = models.CharField(max_length=255)
    released = models.NullBooleanField(default=False, blank=True, null=True)
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
    def versions(self):
        return self.__class__.objects.filter(mnemonic=self.mnemonic).order_by('-created_at')

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

    def get_latest_version(self):
        return self.active_versions.order_by('-created_at').first()

    def get_latest_released_version(self):
        return self.released_versions.order_by('-created_at').first()

    @classmethod
    def get_version_model(cls):
        return cls


class ConceptContainerModel(VersionedModel):
    """
    A sub-resource is an object that exists within the scope of its parent resource.
    Its mnemonic is unique within the scope of its parent resource.
    (A Source is a sub-resource, but an Organization is not.)
    """
    organization = models.ForeignKey('orgs.Organization', on_delete=models.CASCADE, blank=True, null=True)
    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, blank=True, null=True)

    class Meta:
        abstract = True

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
        return self.parent.resource_type()

    @property
    def versions(self):
        return super().versions.filter(
            organization_id=self.organization_id, user_id=self.user_id
        ).order_by('-created_at')

    def set_parent(self, parent_resource):
        if parent_resource.__class__.__name__ == 'Organization':
            self.organization = parent_resource
        elif parent_resource.__class__.__name__ == 'UserProfile':
            self.user = parent_resource

    @classmethod
    def persist_new(cls, obj, created_by, **kwargs):
        errors = dict()
        parent_resource = kwargs.pop('parent_resource', None)
        if not parent_resource:
            errors['parent'] = 'Parent resource cannot be None.'
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
        finally:
            if not persisted:
                errors['non_field_errors'] = "An error occurred while trying to persist new %s." % cls.__name__
        return errors

    def get_active_concepts(self):
        return self.concepts_set.filter(is_active=True, retired=False, version=HEAD)
