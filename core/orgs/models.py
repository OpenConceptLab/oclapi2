from django.contrib.contenttypes.fields import GenericRelation
from django.core.validators import RegexValidator
from django.db import models, transaction
from pydash import get

from core.client_configs.models import ClientConfig
from core.common.checksums import ChecksumModel
from core.common.constants import NAMESPACE_REGEX, ACCESS_TYPE_VIEW, ROLE_ADMIN_ID, ROLE_ID_BY_NAME, ROLE_MEMBER_ID
from core.common.mixins import SourceContainerMixin
from core.common.models import BaseResourceModel, Role
from core.orgs.constants import ORG_OBJECT_TYPE
from core.users.models import Follow


class OrganizationMember(models.Model):
    """A user's membership of an organization, with their role in it."""
    class Meta:
        db_table = 'user_profiles_organizations'
        unique_together = ('userprofile', 'organization')

    userprofile = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE)
    organization = models.ForeignKey('orgs.Organization', on_delete=models.CASCADE, related_name='memberships')
    # A new member is a plain member. Only a row inserted without a role gets the column's default: code older than
    # this column, as during a rollout or after a rollback, where any member could do everything an admin can.
    role = models.ForeignKey(Role, on_delete=models.PROTECT, default=ROLE_MEMBER_ID, db_default=ROLE_ADMIN_ID)


class Organization(BaseResourceModel, SourceContainerMixin, ChecksumModel):
    class Meta:
        db_table = 'organizations'
        indexes = [
                      models.Index(fields=['uri']),
                  ] + BaseResourceModel.Meta.indexes

    OBJECT_TYPE = ORG_OBJECT_TYPE
    es_fields = {
        'name': {'sortable': False, 'filterable': True, 'exact': True},
        '_name': {'sortable': True, 'filterable': False, 'exact': False},
        'mnemonic': {'sortable': False, 'filterable': True, 'exact': True},
        '_mnemonic': {'sortable': True, 'filterable': False, 'exact': False},
        'last_update': {'sortable': True, 'default': 'desc', 'filterable': False},
        'updated_by': {'sortable': False, 'filterable': False, 'facet': True},
        'company': {'sortable': True, 'filterable': True, 'exact': True},
        'location': {'sortable': True, 'filterable': True, 'exact': True},
        'created_on': {'sortable': True, 'filterable': False, 'exact': False},
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
    followers = GenericRelation(Follow, object_id_field='following_id', content_type_field='following_type')
    text = models.TextField(null=True, blank=True)  # for about description (markup)
    overview = models.JSONField(default=dict)

    def calculate_uri(self):
        return f"/orgs/{self.mnemonic}/"

    @staticmethod
    def get_search_document():
        from core.orgs.documents import OrganizationDocument
        return OrganizationDocument

    @property
    def org(self):
        return self.mnemonic

    @property
    def num_members(self):
        return self.members.count()

    def is_member(self, user_profile):
        return user_profile and self.members.filter(id=user_profile.id).exists()

    def has_edit_access(self, user):
        """Whether `user` may edit the org's repositories, and so view its private ones: staff and its members."""
        if not (user and user.is_authenticated):
            return False
        return user.is_staff or self.is_member(user)

    def is_admin(self, user_profile):
        return self.memberships.filter(userprofile_id=user_profile.id, role_id=ROLE_ADMIN_ID).exists()

    def has_admin_access(self, user):
        """Whether `user` may edit or delete the org and manage its members: staff and its admins."""
        if not (user and user.is_authenticated):
            return False
        return user.is_staff or self.is_admin(user)

    def is_last_admin(self, user_profile):
        """True if no other active admin would remain without `user_profile`. Every org keeps at least one."""
        return self.is_admin(user_profile) and not self.memberships.filter(
            role_id=ROLE_ADMIN_ID, userprofile__is_active=True).exclude(userprofile_id=user_profile.id).exists()

    def get_member_role(self, user_profile):
        """The name of the role `user_profile` has in the org, or None if they aren't a member."""
        return self.memberships.filter(
            userprofile_id=get(user_profile, 'id')).values_list('role__name', flat=True).first()

    @property
    def member_roles(self):
        return dict(self.memberships.values_list('userprofile_id', 'role__name'))

    def set_member_role(self, user_profile, role=None):
        """
        Adds `user_profile` to the org or changes their role, and returns whether they're new to it. With no role,
        a new member gets the default one and an existing member keeps theirs.
        """
        _, created = self.memberships.update_or_create(
            userprofile=user_profile, defaults={'role_id': ROLE_ID_BY_NAME[role]} if role else {})
        return created

    def is_only_member(self, user_profile):
        """True if `user_profile` is the org's only member. Every org keeps at least one member."""
        return bool(self.is_member(user_profile)) and not self.members.exclude(id=user_profile.id).exists()

    @staticmethod
    def get_url_kwarg():
        return 'org'

    @classmethod
    def get_by_username(cls, username):
        return cls.objects.filter(members__username=username)

    @classmethod
    def get_public(cls):
        return cls.objects.filter(public_access=ACCESS_TYPE_VIEW)

    @classmethod
    def get_visible_to(cls, user, queryset):
        """Limits `queryset` to the orgs `user` can view: public orgs and the user's own. Staff can view all."""
        if get(user, 'is_superuser') or get(user, 'is_staff'):
            return queryset
        visible = cls.get_public()
        if get(user, 'is_authenticated'):
            visible = visible | cls.get_by_username(user.username)
        return queryset.filter(id__in=visible.values('id'))

    def save(self, *args, force_insert=False, force_update=False, using=None, update_fields=None):
        """
        The creator becomes an admin when the org is created. Later saves leave the members alone, so editing
        an org doesn't make the editor a member and removing a member sticks.
        """
        is_new = not self.id
        super().save(*args, force_insert=force_insert, force_update=force_update, using=using,
                     update_fields=update_fields)
        if is_new:
            self.members.add(self.created_by, through_defaults={'role_id': ROLE_ADMIN_ID})

    def delete(self, using=None, keep_parents=False, sync=False):   # pylint: disable=arguments-differ
        with transaction.atomic():
            for source in self.source_set.all():
                self.batch_delete(source.concepts_set)
                self.batch_delete(source.mappings_set)
                source.delete(force=True, sync=sync)
            for collection in self.collection_set.all():
                self.batch_delete(collection.references)
                collection.delete(force=True, sync=sync)
            self.delete_pins()
            self.delete_client_configs()
            from core.events.models import Event
            self.record_event(Event.DELETED)

            return super().delete(using=using, keep_parents=keep_parents)

    def delete_client_configs(self):
        ClientConfig.objects.filter(resource_type__model='organization', resource_id=self.id).delete()

    def delete_following(self):
        Follow.objects.filter(following_type__model='organization', following_id=self.id).delete()

    def delete_pins(self):
        from core.pins.models import Pin
        # deletes pins where org is pinned
        Pin.objects.filter(resource_type__model='organization', resource_id=self.id).delete()
        # deletes pins for this org
        self.pins.all().delete()

    @staticmethod
    def get_brief_serializer():
        from core.orgs.serializers import OrganizationListSerializer
        return OrganizationListSerializer
