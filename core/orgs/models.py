from django.contrib import admin
from django.contrib.contenttypes.fields import GenericRelation
from django.core.validators import RegexValidator
from django.db import models

from core.client_configs.models import ClientConfig
from core.common.constants import NAMESPACE_REGEX, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT
from core.common.mixins import SourceContainerMixin
from core.common.models import BaseResourceModel
from core.orgs.constants import ORG_OBJECT_TYPE


class Organization(BaseResourceModel, SourceContainerMixin):
    class Meta:
        db_table = 'organizations'

    OBJECT_TYPE = ORG_OBJECT_TYPE
    es_fields = {
        'name': {'sortable': True, 'filterable': True, 'exact': True},
        'mnemonic': {'sortable': True, 'filterable': True, 'exact': True},
        'last_update': {'sortable': True, 'default': 'desc', 'filterable': False},
        'company': {'sortable': False, 'filterable': True, 'exact': True},
        'location': {'sortable': False, 'filterable': True, 'exact': True},
    }

    name = models.TextField()
    company = models.TextField(null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    location = models.TextField(null=True, blank=True)
    mnemonic = models.CharField(
        max_length=255, validators=[RegexValidator(regex=NAMESPACE_REGEX)], unique=True
    )
    description = models.TextField(null=True, blank=True)
    client_configs = GenericRelation(ClientConfig, object_id_field='resource_id', content_type_field='resource_type')
    text = models.TextField(null=True, blank=True)  # for about description (markup)

    @property
    def org(self):
        return self.mnemonic

    @property
    def num_members(self):
        return self.members.count()

    def is_member(self, user_profile):
        return user_profile and self.members.filter(id=user_profile.id).exists()

    @staticmethod
    def get_url_kwarg():
        return 'org'

    @classmethod
    def get_by_username(cls, username):
        return cls.objects.filter(members__username=username)

    @classmethod
    def get_public(cls):
        return cls.objects.filter(public_access__in=[ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT])

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        super().save(force_insert=force_insert, force_update=force_update, using=using, update_fields=update_fields)
        if self.id:
            self.members.add(self.created_by)
            if self.updated_by_id:
                self.members.add(self.updated_by)


admin.site.register(Organization)
