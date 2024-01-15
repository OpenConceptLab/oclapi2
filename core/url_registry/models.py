from django.db import models
from pydash import get

from core.common.models import BaseModel


class URLRegistry(BaseModel):
    url = models.URLField()
    name = models.TextField(null=True, blank=True)
    namespace = models.CharField(max_length=300, null=True, blank=True)
    organization = models.ForeignKey(
        'orgs.Organization', on_delete=models.CASCADE, null=True, blank=True,
        related_name='url_registries'
    )
    user = models.ForeignKey(
        'users.UserProfile', on_delete=models.CASCADE, null=True, blank=True,
        related_name='url_registries'
    )
    public_access = None
    uri = None
    OBJECT_TYPE = 'URLRegistry'

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

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @property
    def owner(self):
        return self.organization or self.user

    @property
    def owner_type(self):
        return get(self.owner, 'resource_type') or None

    def is_uniq(self):
        return not self.get_active_entries().filter(url=self.url).exists()

    def get_active_entries(self):
        queryset = URLRegistry.objects.filter(is_active=True)

        if self.organization:
            queryset = queryset.filter(organization=self.organization)
        elif self.user:
            queryset = queryset.filter(user=self.user)
        else:
            queryset = queryset.filter(organization__isnull=True, user__isnull=True)

        return queryset
