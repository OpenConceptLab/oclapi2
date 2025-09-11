from django.contrib.contenttypes.fields import GenericRelation
from django.core.validators import RegexValidator
from django.db import models, transaction

from core.client_configs.models import ClientConfig
from core.common.checksums import ChecksumModel
from core.common.constants import NAMESPACE_REGEX, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT
from core.common.mixins import SourceContainerMixin
from core.common.models import BaseResourceModel
from core.orgs.constants import ORG_OBJECT_TYPE
from core.users.models import Follow


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

    @staticmethod
    def get_url_kwarg():
        return 'org'

    @classmethod
    def get_by_username(cls, username):
        return cls.objects.filter(members__username=username)

    @classmethod
    def get_public(cls):
        return cls.objects.filter(public_access__in=[ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT])

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        super().save(force_insert=force_insert, force_update=force_update, using=using, update_fields=update_fields)
        if self.id:
            self.members.add(self.created_by)
            if self.updated_by_id:
                self.members.add(self.updated_by)

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
