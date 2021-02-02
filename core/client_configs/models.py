from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import JSONField
from django.db import models

from core.client_configs.constants import DEFAULT_TYPE, CONFIG_TYPES, LAYOUT_TYPES, DEFAULT_LAYOUT_TYPE, \
    DEFAULT_PAGE_SIZE
from core.common.constants import SUPER_ADMIN_USER_ID


class ClientConfig(models.Model):
    class Meta:
        db_table = 'client_configurations'

    name = models.TextField(null=True, blank=True)
    type = models.CharField(choices=CONFIG_TYPES, default=DEFAULT_TYPE, max_length=255)
    layout = models.CharField(choices=LAYOUT_TYPES, default=DEFAULT_LAYOUT_TYPE, max_length=255)
    page_size = models.IntegerField(default=DEFAULT_PAGE_SIZE)
    resource_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    resource_id = models.PositiveIntegerField()
    resource = GenericForeignKey('resource_type', 'resource_id')
    config = JSONField()
    created_by = models.ForeignKey(
        'users.UserProfile', default=SUPER_ADMIN_USER_ID, on_delete=models.SET_DEFAULT,
        related_name='%(app_label)s_%(class)s_related_created_by',
        related_query_name='%(app_label)s_%(class)ss_created_by',
    )
    updated_by = models.ForeignKey(
        'users.UserProfile', default=SUPER_ADMIN_USER_ID, on_delete=models.SET_DEFAULT,
        related_name='%(app_label)s_%(class)s_related_updated_by',
        related_query_name='%(app_label)s_%(class)ss_updated_by',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
