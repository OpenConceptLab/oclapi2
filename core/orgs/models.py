from django.contrib import admin
from django.db import models
from core.common.models import BaseResourceModel
from core.orgs.constants import ORG_OBJECT_TYPE


class Organization(BaseResourceModel):
    class Meta:
        db_table = 'organizations'

    OBJECT_TYPE = ORG_OBJECT_TYPE

    name = models.TextField()
    company = models.TextField(null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    location = models.TextField(null=True, blank=True)

    @property
    def org_id(self):
        return self.id

    @property
    def members(self):
        return self.userprofile_set

    @property
    def num_members(self):
        return self.members.count()

    def is_member(self, userprofile):
        return userprofile and self.members.filter(id=userprofile.id).exists()


admin.site.register(Organization)
