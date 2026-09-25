import uuid
from datetime import datetime
from typing import Any

from dirtyfields import DirtyFieldsMixin
from django.contrib.auth.models import AbstractUser, Group
from django.contrib.auth.password_validation import validate_password
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F
from rest_framework.authtoken.models import Token

from core.common.mixins import SourceContainerMixin
from core.common.models import BaseModel, CommonLogoModel
from core.common.tasks import send_user_verification_email, send_user_reset_password_email
from core.common.utils import web_url
from core.users.constants import STAFF_GROUP, SUPERADMIN_GROUP, GUEST_GROUP, CORE_USER_GROUP, PREVIEW_GROUP
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


class UserProfile(DirtyFieldsMixin, AbstractUser, BaseModel, CommonLogoModel, SourceContainerMixin, ChecksumModel):
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
        user_id = concept_container.user_id
        if user_id and user_id == self.id:
            return True
        organization_id = concept_container.organization_id
        return self.organizations.filter(id=organization_id).exists()

    def __create_token(self):
        token, _ = Token.objects.get_or_create(user=self)
        return token

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

    @property
    def auth_groups(self):
        return self.groups.values_list('name', flat=True)

    def has_auth_group(self, group_name):
        return self.groups.filter(name=group_name).exists()

    @property
    def is_guest_group(self):
        return self.has_auth_group(GUEST_GROUP)

    @property
    def is_core_group(self):
        return self.has_auth_group(CORE_USER_GROUP)

    @property
    def capabilities(self):
        from core.capabilities.models import Capability
        return Capability.objects.all()

    def get_capability_limit(self, capability_id):
        """
        Returns the configured limit as-is. 0 means unlimited.
        Set explicitly via a per-user override or a group's own row.
        There is no implicit-unlimited fallback:
        None means this capability has no override and no group row for this user at
        all, and callers must treat that as BLOCKED, not unlimited (see
        check_and_consume_capability) - a new capability, or a new group that grants
        Mapper access, must have its limit configured explicitly (even to 0) before
        real usage is allowed against it. Silently failing open on missing
        configuration was the previous behavior; don't reintroduce it here.

        Staff and superusers are the deliberate exception.

        Per-user override wins over every group; else the highest of the user's
        groups' limits, where an explicit 0 from any one group wins outright (it
        isn't just "the lowest number" - max() alone would let a capped group beat an
        unlimited one).

        Exception: for the authoring capabilities in AUTHORING_CAPABILITY_IDS (bulk
        import, $clone), which every account has always had, no row means the
        `preview` group's value - the lowest tier - not blocked (ocl_online#230).
        """
        if self.is_superuser or self.is_staff:
            return 0
        override = self._get_capability_limit(capability_id)
        if override is not None:
            return override
        group_limits = self._get_group_capability_limit(capability_id)
        if not group_limits:
            return self._get_default_authoring_limit(capability_id)
        if 0 in group_limits:
            return 0
        return max(group_limits)

    def _get_group_capability_limit(self, capability_id) -> list[Any]:
        from core.capabilities.models import GroupCapability
        return list(GroupCapability.objects.filter(
            group__in=self.groups.all(), capability_id=capability_id
        ).values_list('limit', flat=True))

    def _get_capability_limit(self, capability_id):
        return self.capability_overrides.filter(capability_id=capability_id).values_list('limit', flat=True).first()

    @staticmethod
    def _get_default_authoring_limit(capability_id):
        """None (blocked) unless `capability_id` is an authoring capability, which falls back to `preview`."""
        from core.capabilities.constants import AUTHORING_CAPABILITY_IDS
        from core.capabilities.models import GroupCapability
        if capability_id not in AUTHORING_CAPABILITY_IDS:
            return None
        return GroupCapability.objects.filter(
            group__name=PREVIEW_GROUP, capability_id=capability_id).values_list('limit', flat=True).first()

    def get_capability_usage(self, capability_id):
        from core.capabilities.constants import (
            AUTHORING_CAPABILITY_IDS, MAPPER_PROJECTS_CAPABILITY_ID, MAPPER_ROWS_PER_PROJECT_CAPABILITY_ID
        )
        if capability_id in AUTHORING_CAPABILITY_IDS:
            return None  # per-request limits: nothing accumulates
        if capability_id == MAPPER_ROWS_PER_PROJECT_CAPABILITY_ID:
            # A per-project cap has no per-user usage; clients show the cap itself.
            return None
        if capability_id == MAPPER_PROJECTS_CAPABILITY_ID:
            # mapper.projects is enforced against the live count (HasMapProjectCapacity),
            # not the monotonic UsageCounter - report the same number here, or a user who
            # deletes their one project sees "used" stay at the old total while creation
            # (correctly) succeeds again.
            return self.map_projects_used
        return self.usage_counters.filter(capability_id=capability_id).values_list('used', flat=True).first() or 0

    def check_and_consume_capability(  # pylint: disable=too-many-arguments
            self, capability_id, units=1, action='', algorithm=None, map_project=None, run=None,
            idempotency_key=None
    ):
        """
        Atomically checks this user's remaining `capability_id` quota and, if `units`
        fits, consumes it and logs a `UsageEvent`. Raises `CapabilityExceeded` (nothing
        consumed, nothing logged) if it doesn't fit. An explicit limit of 0 (only) means
        unlimited and always succeeds; usage is still logged. A capability with no
        override and no group row for this user at all (get_capability_limit() returns
        None) is BLOCKED, not unlimited - see get_capability_limit's docstring.
        kill switch = set the limit to -1 in groups.yaml (a deleted GroupCapability row is
        recreated on restart); -1 on a per-user override blocks just that user.
        (0 is NOT the kill switch - it means unlimited).
        `capability_id` must be an id of a capability already seeded (Keycloak/fixtures) -
        a bad id fails with an IntegrityError on insert rather than silently creating a
        Capability row.
        With an `idempotency_key` already consumed, charges nothing and returns that event,
        even past the limit. Returns (usage_event, already_consumed).
        """
        from core.capabilities.constants import CAPABILITY_NAME_BY_ID
        from core.capabilities.exceptions import CapabilityExceeded
        from core.capabilities.models import UsageCounter
        limit = self.get_capability_limit(capability_id)

        with transaction.atomic():
            UsageCounter.objects.get_or_create(user=self, capability_id=capability_id)
            counter = self.usage_counters.select_for_update().get(capability_id=capability_id)

            # under the counter lock and before the limit check, so a retry after the last unit still succeeds
            if idempotency_key:
                event = self.usage_events.filter(capability_id=capability_id, idempotency_key=idempotency_key).first()
                if event:
                    return event, True

            if limit is None or (limit and counter.used + units > limit):
                raise CapabilityExceeded(
                    CAPABILITY_NAME_BY_ID.get(capability_id, capability_id), limit, counter.used, units)

            UsageCounter.objects.filter(pk=counter.pk).update(used=F('used') + units)
            event = self.usage_events.create(
                capability_id=capability_id, units=units, action=action, algorithm=algorithm,
                map_project=map_project, run=run, idempotency_key=idempotency_key or None
            )
            return event, False

    def log_capability_event(  # pylint: disable=too-many-arguments
            self, capability_id, units=1, action='', algorithm=None, map_project=None, run=None
    ):
        """
        Attribution-only logging for a capability whose "used" is entirely derived from
        live domain state (mapper.projects -> map_projects_used) - see
        get_capability_usage(). Writes only a UsageEvent audit row; UsageCounter is never
        touched, so there's nothing to keep in sync (no refund-on-delete needed) and
        nothing to drift.
        """
        self.usage_events.create(
            capability_id=capability_id, units=units, action=action, algorithm=algorithm,
            map_project=map_project, run=run
        )

    @property
    def map_projects_used(self):
        # By created_by, not the owner - counting by owner would let a preview user dodge the mapper.projects cap by
        # creating a new organization per project (orgs are self-serve).
        from core.map_projects.models import MapProject
        return MapProject.objects.filter(created_by=self, is_active=True).count()

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

    def can_view(self, obj):
        """Whether this user can view a user, an org, a repo or content inside a repo."""
        from core.orgs.models import Organization
        if self.is_staff or isinstance(obj, UserProfile):
            return True
        if isinstance(obj, Organization):
            return obj.public_can_view or obj.is_member(self)
        repo = obj if hasattr(obj, 'has_view_access') else (
            getattr(obj, 'collection_version', None) or getattr(obj, 'collection', None) or
            getattr(obj, 'parent', None))
        return bool(repo and hasattr(repo, 'has_view_access') and repo.has_view_access(self))

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

    def set_groups(self, groups, verify=True, _save=True):
        if not verify or sorted(self.groups.values_list('name', flat=True)) != sorted(groups):
            self.groups.set(Group.objects.filter(name__in=groups))
            self.is_superuser = self.has_auth_group(SUPERADMIN_GROUP)
            self.is_staff = self.is_superuser or self.has_auth_group(STAFF_GROUP)  # a superuser is always staff
            if _save:
                self.save()
