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

    name = models.TextField(default='View Configuration')
    type = models.CharField(choices=CONFIG_TYPES, default=HOME_TYPE, max_length=255)
    is_default = models.BooleanField(default=False)
    config = JSONField()
    resource_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    resource_id = models.PositiveIntegerField()
    resource = GenericForeignKey('resource_type', 'resource_id')

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

    errors = None

    @property
    def is_home(self):
        return self.type == HOME_TYPE

    @property
    def uri(self):
        return "/client-configs/{}/".format(self.id)

    def clean(self):
        self.errors = None
        super().clean()

        if self.is_home:
            self.validate_home_config()
            if not self.errors:
                self.format_home_config_tabs()

        if self.errors:
            raise ValidationError(self.errors)

    @property
    def siblings(self):
        return self.__class__.objects.filter(
            resource_type_id=self.resource_type_id, resource_id=self.resource_id, type=self.type
        ).exclude(id=self.id)

    def format_home_config_tabs(self):
        for tab in self.config.get('tabs', []):
            fields = get(tab, 'fields')
            if isinstance(fields, dict):
                tab['fields'] = [{k: v} for k, v in fields.items()]

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
