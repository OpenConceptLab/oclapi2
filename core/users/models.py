from django.contrib import admin
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse
from rest_framework.authtoken.models import Token

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

    @property
    def sources_url(self):
        return reverse('source-list', kwargs={self.get_url_kwarg(): self.mnemonic})

    @property
    def organizations_url(self):
        return reverse('userprofile-orgs', kwargs={'user': self.mnemonic})

    def update_password(self, password=None, hashed_password=None):
        if not password and not hashed_password:
            return

        if password:
            self.set_password(password)
        elif hashed_password:
            self.password = hashed_password
        self.save()
        self.refresh_token()

    def refresh_token(self):
        Token.objects.filter(user=self).delete()
        Token.objects.create(user=self)


admin.site.register(UserProfile)
