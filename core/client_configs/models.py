from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import JSONField
from django.core.exceptions import ValidationError
from django.db import models
from pydash import get

from core.client_configs.constants import HOME_TYPE, CONFIG_TYPES, EMPTY_TABS_CONFIG, NOT_LIST_TABS_CONFIG, \
    INVALID_TABS_CONFIG, ONE_DEFAULT_TAB
from core.common.constants import SUPER_ADMIN_USER_ID


class ClientConfig(models.Model):
    class Meta:
        db_table = 'client_configurations'

    name = models.TextField(null=True, blank=True)
    type = models.CharField(choices=CONFIG_TYPES, default=HOME_TYPE, max_length=255)
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
    errors = None

    @property
    def is_home(self):
        return self.type == HOME_TYPE

    def clean(self):
        self.errors = None
        super().clean()

        if self.is_home:
            self.validate_home_config()

        if self.errors:
            raise ValidationError(self.errors)

    def validate_home_config(self):
        self.validate_home_tabs_config()

    def validate_home_tabs_config(self):
        tabs = get(self.config, 'tabs')
        if not tabs:
            self.errors = dict(tabs=[EMPTY_TABS_CONFIG])
        elif not isinstance(tabs, list):
            self.errors = dict(tabs=[NOT_LIST_TABS_CONFIG])
        elif any([not isinstance(tab, dict) for tab in tabs]):
            self.errors = dict(tabs=[INVALID_TABS_CONFIG])
        else:
            default_tabs = [tab for tab in tabs if tab.get('default', False)]
            if len(default_tabs) != 1:
                self.errors = dict(tabs=[ONE_DEFAULT_TAB])
