from django.db import models, transaction


class Capability(models.Model):
    """A named, arbitrary-value usage limit (e.g. `mapper.match_operations`)."""
    class Meta:
        db_table = 'capabilities'

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default='')

    def __str__(self):
        return str(self.name or self.__class__.__name__)


class GroupCapability(models.Model):
    """This group's limit for a capability. Unlimited = no row here."""
    class Meta:
        db_table = 'group_capabilities'
        unique_together = ('group', 'capability')

    group = models.ForeignKey('auth.Group', on_delete=models.CASCADE, related_name='capabilities')
    capability = models.ForeignKey(Capability, on_delete=models.CASCADE, related_name='group_capabilities')
    limit = models.IntegerField()


class UserCapabilityOverride(models.Model):
    """Per-user override, wins over every GroupCapability the user's groups carry."""
    class Meta:
        db_table = 'user_capability_overrides'
        unique_together = ('user', 'capability')

    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='capability_overrides')
    capability = models.ForeignKey(Capability, on_delete=models.CASCADE, related_name='user_overrides')
    limit = models.IntegerField()


class UsageCounter(models.Model):
    """Fast, concurrency-safe running total for one (user, capability) pair."""
    class Meta:
        db_table = 'usage_counters'
        unique_together = ('user', 'capability')

    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='usage_counters')
    capability = models.ForeignKey(Capability, on_delete=models.CASCADE, related_name='usage_counters')
    used = models.IntegerField(default=0)

    @classmethod
    def refund(cls, user, capability_id, units=1):
        """
        Manual ledger correction (e.g. staff fixing an over-count after a bug).
        Not part of the normal consumption flow: callers should consume a
        capability only after the action it gates has actually succeeded (see
        UserProfile.check_and_consume_capability), not reserve-then-refund -
        that would need a failure path reachable by the same caller that
        consumed, and CapabilityRefundView is staff-only precisely so a
        cross-service caller (e.g. ocl-ai-assistant) never needs to reach it.
        Floors at 0; never goes negative. `capability_id` must be an id of a
        capability already seeded (Keycloak/fixtures) - a bad id fails with an
        IntegrityError rather than silently creating a Capability row.
        """
        with transaction.atomic():
            cls.objects.get_or_create(user=user, capability_id=capability_id)
            counter = cls.objects.select_for_update().get(user=user, capability_id=capability_id)
            new_used = max(counter.used - units, 0)
            cls.objects.filter(pk=counter.pk).update(used=new_used)


class UsageEvent(models.Model):
    """Append-only per-action audit trail. Not read on the hot enforcement path."""
    class Meta:
        db_table = 'usage_events'
        indexes = [models.Index(fields=['user', 'capability'])]

    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='usage_events')
    capability = models.ForeignKey(Capability, on_delete=models.CASCADE, related_name='usage_events')
    units = models.IntegerField(default=1)
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
    created_at = models.DateTimeField(auto_now_add=True)
