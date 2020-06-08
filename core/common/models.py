from django.contrib.postgres.fields import JSONField
from django.core.validators import RegexValidator
from django.db import models
from pydash import get

from core.common.constants import ACCESS_TYPE_CHOICES, DEFAULT_ACCESS_TYPE, NAMESPACE_REGEX


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
    created_by = models.TextField()
    updated_by = models.TextField()
    is_active = models.BooleanField(default=True)
    is_being_saved = False
    extras = JSONField(null=True, blank=True)
    extras_have_been_encoded = False
    extras_have_been_decoded = False
    uri = models.TextField(null=True, blank=True)

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


class BaseResourceModel(BaseModel):
    """
    A base resource has a mnemonic that is unique across all objects of its type.
    A base resource may contain sub-resources.
    (An Organization is a base resource, but a Concept is not.)
    """
    mnemonic = models.CharField(
        max_length=255, validators=[RegexValidator(regex=NAMESPACE_REGEX)], unique=True
    )

    class Meta:
        abstract = True

    def __str__(self):
        return get(self, 'mnemonic', '')
