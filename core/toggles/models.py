from django.conf import settings
from django.db import models
from pydash import get

from core.common.constants import SUPER_ADMIN_USER_ID


class Toggle(models.Model):
    class Meta:
        db_table = "toggles"

    name = models.TextField(unique=True)
    dev = models.BooleanField(default=True)
    qa = models.BooleanField(default=False)
    demo = models.BooleanField(default=False)
    staging = models.BooleanField(default=False)
    who_staging = models.BooleanField(default=False)
    production = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        'users.UserProfile',
        related_name='%(app_label)s_%(class)s_related_updated_by',
        related_query_name='%(app_label)s_%(class)ss_updated_by',
        on_delete=models.DO_NOTHING,
        default=SUPER_ADMIN_USER_ID,
    )
    updated_at = models.DateTimeField(auto_now=True, null=True)
    is_active = models.BooleanField(default=True)

    @classmethod
    def all(cls):
        return Toggle.objects.filter(is_active=True)

    @classmethod
    def to_dict(cls):  # pylint: disable=arguments-differ
        env = settings.ENV
        if not env or env in ['development', 'ci']:
            env = 'dev'
        if env in ['who-staging']:
            env = 'who_staging'
        return {toggle.name: get(toggle, env) for toggle in cls.all()}

    @classmethod
    def get(cls, key):
        return get(cls.to_dict(), key, None)
