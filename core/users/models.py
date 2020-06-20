from django.contrib import admin
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse

from core.common.models import BaseModel
from .constants import USER_OBJECT_TYPE


class UserProfile(AbstractUser, BaseModel):
    class Meta:
        db_table = 'user_profiles'
        swappable = 'AUTH_USER_MODEL'

    OBJECT_TYPE = USER_OBJECT_TYPE
    organizations = models.ManyToManyField('orgs.Organization')
    company = models.TextField(null=True, blank=True)
    location = models.TextField(null=True, blank=True)
    preferred_locale = models.TextField(null=True, blank=True)

    @property
    def user(self):
        return self.username

    @property
    def name(self):
        return "{} {}".format(self.first_name, self.last_name)

    @property
    def full_name(self):
        return self.name

    @property
    def mnemonic(self):
        return self.username

    @staticmethod
    def get_url_kwarg():
        return 'user'

    def sources_url(self):
        return reverse('source-list', kwargs={self.get_url_kwarg(): self.mnemonic})


admin.site.register(UserProfile)
