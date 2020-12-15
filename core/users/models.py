from django.contrib import admin
from django.contrib.auth.models import AbstractUser
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import UniqueConstraint
from django.urls import reverse
from pydash import get
from rest_framework.authtoken.models import Token

from core.common.mixins import SourceContainerMixin
from core.common.models import BaseModel, CommonLogoModel
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

    def __str__(self):
        return str(self.mnemonic)

    @property
    def orgs_count(self):
        return self.organizations.count()


class PinnedItem(models.Model):
    class Meta:
        db_table = 'user_pins'
        ordering = ['created_at']
        constraints = [
            UniqueConstraint(
                fields=['resource_type', 'resource_id', 'user'],
                name="user_pin_unique",
                condition=models.Q(organization=None),
            ),
            UniqueConstraint(
                fields=['resource_type', 'resource_id', 'user', 'organization'],
                name="user_org_pin_unique",
                condition=~models.Q(organization=None),
            )
        ]

    resource_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    resource_id = models.PositiveIntegerField()
    resource = GenericForeignKey('resource_type', 'resource_id')
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='pins')
    organization = models.ForeignKey('orgs.Organization', on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def resource_uri(self):
        return get(self, 'resource.uri')

    @classmethod
    def get_resource(cls, resource_type, resource_id):
        if resource_type.lower() == 'source':
            from core.sources.models import Source
            return Source.objects.filter(id=resource_id).first()
        if resource_type.lower() == 'collection':
            from core.collections.models import Collection
            return Collection.objects.filter(id=resource_id).first()
        if resource_type.lower() in ['org', 'organization']:
            from core.orgs.models import Organization
            return Organization.objects.filter(id=resource_id).first()

        return None


admin.site.register(UserProfile)
