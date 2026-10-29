from django.db import models, transaction
from django.db.models import F


class Capability(models.Model):
    """A named, arbitrary-value usage limit (e.g. `mapper.match_operations`)."""
    class Meta:
        db_table = 'capabilities'

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default='')

    def __str__(self):
        return str(self.name or self.__class__.__name__)


class GroupCapability(models.Model):
    """
    This group's limit for a capability: 0 = unlimited, -1 = blocked (the kill switch), N > 0 = the limit.
    No row = this group grants nothing; a user with no row anywhere is not entitled (blocked).
    """
    class Meta:
        db_table = 'group_capabilities'
        unique_together = ('group', 'capability')

    group = models.ForeignKey('auth.Group', on_delete=models.CASCADE, related_name='capabilities')
    capability = models.ForeignKey(Capability, on_delete=models.CASCADE, related_name='group_capabilities')
    limit = models.IntegerField()


class UserCapabilityOverride(models.Model):
    """
    Per-user override; wins over every GroupCapability the user's groups carry.
    0 = unlimited, -1 = blocked (this user only), N > 0 = the limit.
    """
    class Meta:
        db_table = 'user_capability_overrides'
        unique_together = ('user', 'capability')

    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='capability_overrides')
    capability = models.ForeignKey(Capability, on_delete=models.CASCADE, related_name='user_overrides')
    limit = models.IntegerField()


class UsageCounter(models.Model):
    """
    Fast, concurrency-safe running total for one (user, capability) pair in one usage window.
    `window_start` None is the window that never ends.
    """
    class Meta:
        db_table = 'usage_counters'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'capability', 'window_start'], name='usage_counters_unique_window'
            ),
            # PostgreSQL 14 treats nulls as distinct in the constraint above
            models.UniqueConstraint(
                fields=['user', 'capability'], condition=models.Q(window_start__isnull=True),
                name='usage_counters_unique_unwindowed'
            ),
        ]

    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='usage_counters')
    capability = models.ForeignKey(Capability, on_delete=models.CASCADE, related_name='usage_counters')
    used = models.BigIntegerField(default=0)
    window_start = models.DateTimeField(null=True, blank=True)

    @classmethod
    def get_used(cls, user, capability_id, window_start=None):
        return cls.objects.filter(
            user=user, capability_id=capability_id, window_start=window_start
        ).values_list('used', flat=True).first() or 0

    @classmethod
    def get_locked(cls, user, capability_id, window_start=None):
        """
        The window's counter, created at 0 on first use, locked until the caller's transaction ends.
        `capability_id` must be an id of a capability already seeded (Keycloak/fixtures) - a bad id fails with an
        IntegrityError rather than silently creating a Capability row.
        """
        cls.objects.get_or_create(user=user, capability_id=capability_id, window_start=window_start)
        return cls.objects.select_for_update().get(user=user, capability_id=capability_id, window_start=window_start)

    def consume(self, units, idempotency_key=None, **attribution):
        """Adds `units` and logs the event. The caller holds this counter's lock."""
        UsageCounter.objects.filter(pk=self.pk).update(used=F('used') + units)
        return UsageEvent.objects.create(
            user_id=self.user_id, capability_id=self.capability_id, window_start=self.window_start, units=units,
            idempotency_key=idempotency_key or None, **attribution
        )

    @classmethod
    def refund(cls, user, capability_id, units=1, window_start=None):
        """
        Manual correction (e.g. staff fixing an over-count after a bug) where there's no event to reverse.
        CapabilityRefundView is staff-only, so a cross-service caller (e.g. ocl-ai-assistant) never reaches it.
        Floors at 0; never goes negative.
        """
        with transaction.atomic():
            counter = cls.get_locked(user, capability_id, window_start)
            cls.objects.filter(pk=counter.pk).update(used=max(counter.used - units, 0))


class UsageEvent(models.Model):
    """Append-only per-action audit trail. Not read on the hot enforcement path."""
    class Meta:
        db_table = 'usage_events'
        indexes = [models.Index(fields=['user', 'capability'])]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'capability', 'idempotency_key'], condition=models.Q(idempotency_key__isnull=False),
                name='usage_events_unique_idempotency_key'
            )
        ]

    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='usage_events')
    capability = models.ForeignKey(Capability, on_delete=models.CASCADE, related_name='usage_events')
    units = models.BigIntegerField(default=1)
    # taken back after consumption (e.g. the search they were consumed for failed); `units` stays as consumed
    # db_default: code that predates this column, still serving during a deploy, inserts events without it
    reversed_units = models.BigIntegerField(default=0, db_default=0)
    # the counter window the units counted in
    window_start = models.DateTimeField(null=True, blank=True)
    action = models.CharField(max_length=100, blank=True, default='')
    algorithm = models.CharField(max_length=100, null=True, blank=True)
    map_project = models.ForeignKey(
        'map_projects.MapProject', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='usage_events'
    )
    run = models.ForeignKey(
        'map_projects.AutomatchRun', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='usage_events'
    )
    # client-sent dedup key (e.g. per run/row): a repeat consume with the same key charges nothing
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def find(cls, user, capability_id, idempotency_key):
        if not idempotency_key:
            return None
        return cls.objects.filter(user=user, capability_id=capability_id, idempotency_key=idempotency_key).first()

    def reverse(self, units):
        """
        Takes back up to `units` of this event's units, from the counter of the window they counted in, even after
        that window has ended. Returns the units taken back.
        """
        with transaction.atomic():
            counter = UsageCounter.get_locked(self.user, self.capability_id, self.window_start)
            reversed_units = UsageEvent.objects.filter(pk=self.pk).values_list('reversed_units', flat=True).get()
            units = max(min(units, self.units - reversed_units), 0)
            if units:
                UsageCounter.objects.filter(pk=counter.pk).update(used=max(counter.used - units, 0))
                UsageEvent.objects.filter(pk=self.pk).update(reversed_units=F('reversed_units') + units)
            self.reversed_units = reversed_units + units
            return units
