import uuid
from datetime import datetime

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from rest_framework.authtoken.models import Token

from core.common.mixins import SourceContainerMixin
from core.common.models import BaseModel, CommonLogoModel
from core.common.tasks import send_user_verification_email, send_user_reset_password_email
from core.common.utils import web_url
from core.users.constants import AUTH_GROUPS
from .constants import USER_OBJECT_TYPE


class UserProfile(AbstractUser, BaseModel, CommonLogoModel, SourceContainerMixin):
    class Meta:
        db_table = 'user_profiles'
        swappable = 'AUTH_USER_MODEL'
        indexes = [] + BaseModel.Meta.indexes

    OBJECT_TYPE = USER_OBJECT_TYPE
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    organizations = models.ManyToManyField('orgs.Organization', related_name='members')
    company = models.TextField(null=True, blank=True)
    location = models.TextField(null=True, blank=True)
    preferred_locale = models.TextField(null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    verified = models.BooleanField(default=True)
    verification_token = models.TextField(null=True, blank=True)
    mnemonic_attr = 'username'
    deactivated_at = models.DateTimeField(null=True, blank=True)

    es_fields = {
        'username': {'sortable': True, 'filterable': True, 'exact': True},
        'date_joined': {'sortable': True, 'default': 'asc', 'filterable': False},
        'company': {'sortable': True, 'filterable': True, 'exact': True},
        'location': {'sortable': True, 'filterable': True, 'exact': True},
        'is_superuser': {'sortable': False, 'filterable': True, 'exact': False, 'facet': True},
        'is_staff': {'sortable': False, 'filterable': False, 'exact': False, 'facet': True},
        'is_admin': {'sortable': False, 'filterable': False, 'exact': False, 'facet': True}
    }

    @staticmethod
    def get_search_document():
        from core.users.documents import UserProfileDocument
        return UserProfileDocument

    @property
    def status(self):
        if not self.is_active:
            return 'deactivated'
        if not self.verified:
            return 'verification_pending' if self.verification_token else 'unverified'

        return 'verified'

    @property
    def user(self):
        return self.username

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}"

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
            return None

        if password:
            try:
                validate_password(password)
                self.set_password(password)
            except ValidationError as ex:
                return dict(errors=ex.messages)
        elif hashed_password:
            self.password = hashed_password

        if self.verification_token:
            self.verification_token = None
        self.save()
        self.refresh_token()
        return None

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
        return send_user_verification_email.delay(self.id)

    def send_reset_password_email(self):
        return send_user_reset_password_email.delay(self.id)

    @property
    def email_verification_url(self):
        return f"{web_url()}/#/accounts/{self.username}/verify/{self.verification_token}/"

    @property
    def reset_password_url(self):
        return f"{web_url()}/#/accounts/{self.username}/password/reset/{self.verification_token}/"

    def mark_verified(self, token):
        if self.verified:
            return True

        if token == self.verification_token:
            self.verified = True
            self.verification_token = None
            self.deactivated_at = None
            self.save()
            return True

        return False

    @staticmethod
    def is_valid_auth_group(*names):
        return all(name in AUTH_GROUPS for name in names)

    @property
    def auth_groups(self):
        return self.groups.values_list('name', flat=True)

    @property
    def auth_headers(self):
        return dict(Authorization=f'Token {self.get_token()}')

    def organizations_count(self):
        return self.organizations.count()

    def deactivate(self):
        self.is_active = False
        self.verified = False
        self.verification_token = None
        self.deactivated_at = datetime.now()
        self.__delete_token()
        self.save()

    def verify(self):
        self.is_active = True
        self.verified = False
        self.verification_token = uuid.uuid4()

        self.save()
        self.token = self.get_token()
        self.send_verification_email()

    def soft_delete(self):
        self.deactivate()

    def undelete(self):
        self.verified = True
        self.verification_token = None
        self.deactivated_at = None
        self.is_active = True
        self.save()
