from django.contrib import admin
from django.core.validators import RegexValidator
from django.db import models

from core.common.constants import NAMESPACE_REGEX, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT
from core.common.mixins import SourceContainerMixin
from core.common.models import BaseResourceModel
from core.orgs.constants import ORG_OBJECT_TYPE


class Organization(BaseResourceModel, SourceContainerMixin):
    class Meta:
        db_table = 'organizations'

    OBJECT_TYPE = ORG_OBJECT_TYPE

    name = models.TextField()
    company = models.TextField(null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    location = models.TextField(null=True, blank=True)
    mnemonic = models.CharField(
        max_length=255, validators=[RegexValidator(regex=NAMESPACE_REGEX)], unique=True
    )

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


admin.site.register(Organization)
