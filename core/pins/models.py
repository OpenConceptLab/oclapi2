from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import UniqueConstraint
from ordered_model.models import OrderedModel
from pydash import get


class Pin(OrderedModel):
    class Meta(OrderedModel.Meta):
        db_table = 'pins'
        ordering = ['order']
        constraints = [
            UniqueConstraint(
                fields=['resource_type', 'resource_id', 'user'],
                name="user_pin_unique",
                condition=models.Q(organization=None),
            ),
            UniqueConstraint(
                fields=['resource_type', 'resource_id', 'organization'],
                name="org_pin_unique",
                condition=~models.Q(organization=None),
            )
        ]

    resource_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    resource_id = models.PositiveIntegerField()
    resource = GenericForeignKey('resource_type', 'resource_id')
    user = models.ForeignKey(
        'users.UserProfile', on_delete=models.CASCADE, null=True, blank=True, related_name='pins'
    )
    organization = models.ForeignKey(
        'orgs.Organization', on_delete=models.CASCADE, null=True, blank=True, related_name='pins'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    order_with_respect_to = ('user', 'organization')

    def clean(self):
        if not self.user_id and not self.organization_id:
            raise ValidationError(dict(parent=['Pin needs to be owned by a user or an organization.']))

    @property
    def uri(self):
        if self.parent:
            return self.parent.uri + f"pins/{self.id}/"

        return None

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

    @property
    def parent(self):
        if self.organization_id:
            return self.organization
        if self.user_id:
            return self.user

        return None

    def soft_delete(self):
        return self.delete()
