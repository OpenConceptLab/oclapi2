from django.contrib import admin
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse
from rest_framework.authtoken.models import Token

from core.common.mixins import SourceContainerMixin
from core.common.models import BaseModel, CommonLogoModel
from core.common.tasks import send_user_verification_email
from core.common.utils import web_url
from .constants import USER_OBJECT_TYPE


class UserProfile(AbstractUser, BaseModel, CommonLogoModel, SourceContainerMixin):
    class Meta:
        db_table = 'user_profiles'
        swappable = 'AUTH_USER_MODEL'

    OBJECT_TYPE = USER_OBJECT_TYPE
    organizations = models.ManyToManyField('orgs.Organization', related_name='members')
    company = models.TextField(null=True, blank=True)
    location = models.TextField(null=True, blank=True)
    preferred_locale = models.TextField(null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    verified = models.BooleanField(default=True)
    verification_token = models.TextField(null=True, blank=True)

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
        self.__delete_token()
        self.__create_token()

    def get_token(self):
        token = Token.objects.filter(user_id=self.id).first() or self.__create_token()
        return token.key

    def set_token(self, token):
        self.__delete_token()
        Token.objects.create(user=self, key=token)

    def is_admin_for(self, concept_container):  # pragma: no cover
        parent_id = concept_container.parent_id
        return parent_id == self.id or self.organizations.filter(id=parent_id).exists()

    def __create_token(self):
        return Token.objects.create(user=self)

    def __delete_token(self):
        return Token.objects.filter(user=self).delete()

    @property
    def orgs_count(self):
        return self.organizations.count()

    def send_verification_email(self):
        return send_user_verification_email.apply_async((self.id, ))

    @property
    def email_verification_url(self):
        return "{}/#/accounts/{}/verify/{}/".format(web_url(), self.username, self.verification_token)

    def mark_verified(self, token):
        if self.verified:
            return True

        if token == self.verification_token:
            self.verified = True
            self.save()
            return True

        return False


admin.site.register(UserProfile)
