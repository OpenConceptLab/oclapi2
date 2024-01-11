from django.db import models

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
                condition=models.Q(is_active=True, namespace__isnull=True),
                fields=('url',), name='global_url_unique'
            ),
        ]

    def _set_owner_from_uri(self):
        if '/orgs/' in self.namespace:
            from core.orgs.models import Organization
            self.organization = Organization.objects.filter(uri=self.namespace).first()
        elif '/users/' in self.namespace:
            from core.users.models import UserProfile
            self.user = UserProfile.objects.filter(uri=self.namespace).first()

    def clean(self):
        owner = self.owner
        if owner:
            self.namespace = owner.uri
        if not owner and self.namespace:
            self._set_owner_from_uri()
            if not self.owner:
                self.namespace = None

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @property
    def owner(self):
        return self.organization or self.user

    def is_uniq(self):
        return not self.get_active_entries().filter(url=self.url).exists()

    def get_active_entries(self):
        queryset = URLRegistry.objects.filter(is_active=True)

        if self.organization:
            queryset = queryset.filter(organization_id=self.organization)
        elif self.user:
            queryset = queryset.filter(user=self.user)
        else:
            queryset = queryset.filter(namespace__isnull=True)

        return queryset
