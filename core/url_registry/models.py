from django.db import models
from pydash import get

from core.common.fields import URIField
from core.common.models import BaseModel


class URLRegistry(BaseModel):
    url = URIField()
    name = models.TextField(null=True, blank=True)
    namespace = models.CharField(max_length=300, null=True, blank=True)
    organization = models.ForeignKey(
        'orgs.Organization', on_delete=models.CASCADE, null=True, blank=True,
        related_name='url_registry_entries'
    )
    user = models.ForeignKey(
        'users.UserProfile', on_delete=models.CASCADE, null=True, blank=True,
        related_name='url_registry_entries'
    )
    public_access = None
    uri = None
    OBJECT_TYPE = 'URLRegistryEntry'

    es_fields = {
        'name': {'sortable': False, 'filterable': True, 'exact': True},
        '_name': {'sortable': True, 'filterable': False, 'exact': False},
        'namespace': {'sortable': False, 'filterable': True, 'exact': True},
        '_namespace': {'sortable': True, 'filterable': False, 'exact': False},
        'url': {'sortable': False, 'filterable': True, 'exact': True},
        '_url': {'sortable': True, 'filterable': False, 'exact': False},
        'last_update': {'sortable': True, 'default': 'desc', 'filterable': False},
        'updated_by': {'sortable': False, 'filterable': False, 'facet': True}
    }

    class Meta:
        db_table = 'url_registries'
        constraints = [
            models.UniqueConstraint(
                condition=models.Q(is_active=True, user__isnull=False),
                fields=('user', 'url'), name='user_url_unique'
            ),
            models.UniqueConstraint(
                condition=models.Q(is_active=True, organization__isnull=False),
                fields=('organization', 'url'), name='org_url_unique'
            ),
            models.UniqueConstraint(
                condition=models.Q(is_active=True, organization__isnull=True, user__isnull=True),
                fields=('url',), name='global_url_unique'
            ),
        ]

    @property
    def relative_uri(self):
        owner = self.owner
        return f'{owner.uri}url-registry/{self.id}/' if owner else f'/url-registry/{self.id}/'

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @property
    def owner(self):
        return self.organization or self.user

    @property
    def owner_url(self):
        owner = self.owner
        return get(owner, 'uri') or '/'

    @property
    def owner_type(self):
        return get(self.owner, 'resource_type') or None

    @property
    def active_entries(self):
        return URLRegistry.get_active_entries(self.owner)

    def is_uniq(self):
        return not self.active_entries.filter(url=self.url).exists()

    @classmethod
    def get_active_entries(cls, owner):
        queryset = owner.url_registry_entries if owner else cls.get_global_entries()
        return queryset.filter(is_active=True)

    @classmethod
    def get_global_entries(cls):
        return cls.objects.filter(organization__isnull=True, user__isnull=True)

    @classmethod
    def get_active_global_entries(cls):
        return cls.get_global_entries().filter(is_active=True)

    @property
    def namespace_owner(self):
        from core.common.mixins import SourceContainerMixin
        return SourceContainerMixin.get_object_from_namespace(self.namespace)

    @classmethod
    def lookup(cls, url, registry_owner=None):
        entry = cls.get_entry(url, registry_owner)
        if entry:
            return entry.lookup_repo()

        return None

    def lookup_repo(self):
        namespace_owner = self.namespace_owner
        if namespace_owner:
            return namespace_owner.find_repo_by_canonical_url(self.url)

        return None

    @classmethod
    def get_entry(cls, url, owner=None):
        entries = owner.url_registry_entries.filter(is_active=True) if owner else cls.get_active_global_entries()
        return entries.filter(url=url).first()
