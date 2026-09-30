from django.db import models


class CapacityConfig(models.Model):
    """
    One version of the capacity-limit settings (core.capacity.config). Rows are only ever added: the newest is the
    config in force, and the older ones are its history. With no row at all, the defaults apply.
    """
    class Meta:
        db_table = 'capacity_configs'

    config = models.JSONField()  # the full config this version put in force
    previous_config = models.JSONField(null=True, blank=True)  # the config in force before it
    created_by = models.ForeignKey(
        'users.UserProfile', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=16)  # 'api' or 'command'
    note = models.TextField(blank=True, default='')

    @classmethod
    def get_latest(cls):
        return cls.objects.order_by('-id').first()
