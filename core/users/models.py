import uuid
from datetime import datetime

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.password_validation import validate_password
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from pydash import get
from rest_framework.authtoken.models import Token

from core.common.mixins import SourceContainerMixin
from core.common.models import BaseModel, CommonLogoModel
from core.common.tasks import send_user_verification_email, send_user_reset_password_email
from core.common.utils import web_url
from core.users.constants import AUTH_GROUPS
from .constants import USER_OBJECT_TYPE
from ..common.checksums import ChecksumModel


class Follow(models.Model):
    class Meta:
        db_table = 'follows'
        unique_together = ('follower', 'following_id', 'following_type')

    follower = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='following')
    follow_date = models.DateTimeField(auto_now_add=True)
    following_id = models.PositiveIntegerField()
    following_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    following = GenericForeignKey('following_type', 'following_id')

    @property
    def type(self):
        return 'Follow'

    def clean(self):
        if self.follower == self.following:
            raise ValidationError("User cannot follow themselves.")

    @property
    def uri(self):
        return f"/users/{self.follower.username}/following/{self.id}/"


class UserRateLimit(BaseModel):
    STANDARD_PLAN = 'standard'
    GUEST_PLAN = 'guest'

    class Meta:
        db_table = 'user_api_rate_limits'

    RATE_PLANS = (
        (STANDARD_PLAN, STANDARD_PLAN),
        (GUEST_PLAN, GUEST_PLAN),
    )

    user = models.OneToOneField('users.UserProfile', on_delete=models.CASCADE, related_name='api_rate_limit')
    rate_plan = models.CharField(choices=RATE_PLANS, max_length=100, default=STANDARD_PLAN)
    public_access = None
    uri = None
    extras = None

    @property
    def is_standard(self):
        return self.rate_plan == self.STANDARD_PLAN

    @property
    def is_guest(self):
        return self.rate_plan == self.GUEST_PLAN

    def clean(self):
        if not self.rate_plan:
            self.rate_plan = self.STANDARD_PLAN
        super().clean()

    @classmethod
    def upsert(cls, user, rate_plan, updated_by):
        self = get(user, 'api_rate_limit')
        update = False
        if not self:
            self = cls(user=user, rate_plan=rate_plan, updated_by=updated_by, created_by=updated_by)
            update = True
        if rate_plan and self.rate_plan != rate_plan:
            self.rate_plan = rate_plan
            self.updated_by = updated_by
            update = True
        if update:
            self.full_clean()
            self.save()

    def __repr__(self):
        return f"{self.__class__}:{self.rate_plan}"


class UserProfile(AbstractUser, BaseModel, CommonLogoModel, SourceContainerMixin, ChecksumModel):
    class Meta:
        db_table = 'user_profiles'
        swappable = 'AUTH_USER_MODEL'
        indexes = [
                      models.Index(fields=['uri']),
                      models.Index(fields=['public_access']),
                  ] + BaseModel.Meta.indexes

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
    deactivated_at = models.DateTimeField(null=True, blank=True)
    followers = GenericRelation(Follow, object_id_field='following_id', content_type_field='following_type')
    bio = models.TextField(null=True, blank=True)

    mnemonic_attr = 'username'

    es_fields = {
        'username': {'sortable': False, 'filterable': True, 'exact': True},
        '_username': {'sortable': True, 'filterable': False, 'exact': False},
        'name': {'sortable': False, 'filterable': True, 'exact': True},
        '_name': {'sortable': True, 'filterable': False, 'exact': False},
        'date_joined': {'sortable': True, 'default': 'asc', 'filterable': False},
        'updated_by': {'sortable': False, 'filterable': False, 'facet': True},
        'company': {'sortable': True, 'filterable': True, 'exact': True},
        'location': {'sortable': True, 'filterable': True, 'exact': True},
        'is_superuser': {'sortable': False, 'filterable': True, 'exact': False, 'facet': True},
        'is_staff': {'sortable': False, 'filterable': False, 'exact': False, 'facet': True},
        'is_admin': {'sortable': False, 'filterable': False, 'exact': False, 'facet': True}
    }

    @property
    def events(self):
        from core.events.models import Event
        return Event.objects.filter(object_url=self.uri)

    def calculate_uri(self):
        return f"/users/{self.username}/"

    @staticmethod
    def get_search_document():
        from core.users.documents import UserProfileDocument
        return UserProfileDocument

    @staticmethod
    def get_brief_serializer():
        from core.users.serializers import UserListSerializer
        return UserListSerializer

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
        name = self.first_name.strip()
        if self.last_name:
            name += f" {self.last_name.strip()}"
        return name

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
        return f"/users/{self.mnemonic}/orgs/"

    def update_password(self, password=None, hashed_password=None):
        if not password and not hashed_password:
            return None

        if password:
            try:
                validate_password(password)
                self.set_password(password)
            except ValidationError as ex:
                return {'errors': ex.messages}
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

    @property
    def owned_orgs_count(self):
        return self.organizations.filter(created_by=self).count()

    def send_verification_email(self):
        return send_user_verification_email.apply_async((self.id,), queue='default', permanent=False)

    def send_reset_password_email(self):
        return send_user_reset_password_email.apply_async((self.id,), queue='default', permanent=False)

    @property
    def email_verification_url(self):
        return f"{web_url()}/#/accounts/{self.username}/verify/{self.verification_token}/"

    @property
    def reset_password_url(self):
        return f"{web_url()}/#/accounts/{self.username}/password/reset/{self.verification_token}/"

    def mark_verified(self, token, force=False):
        if self.verified:
            return True

        if token == self.verification_token or force:
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

    def has_auth_group(self, group_name):
        return self.groups.filter(name=group_name).exists()

    @property
    def auth_headers(self):
        return {'Authorization': f'Token {self.get_token()}'}

    def deactivate(self):
        self.is_active = False
        self.verified = False
        self.verification_token = None
        self.deactivated_at = datetime.now()
        self.__delete_token()
        self.save()
        self.set_checksums()

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
        self.set_checksums()

    def is_member_of_org(self, org_mnemonic):
        return self.organizations.filter(mnemonic=org_mnemonic).exists()

    def follow(self, following):
        self.following.create(following_id=following.id, following_type=ContentType.objects.get_for_model(following))

        from core.events.models import Event
        self.events.create(
            actor=self,
            event_type=Event.FOLLOWED,
            object_url=self.url,
            referenced_object_url=following.url,
        )

    def unfollow(self, following):
        self.following.filter(
            following_id=following.id, following_type=ContentType.objects.get_for_model(following)).delete()

        from core.events.models import Event
        self.events.create(
            actor=self,
            event_type=Event.UNFOLLOWED,
            object_url=self.url,
            referenced_object_url=following.url,
        )
