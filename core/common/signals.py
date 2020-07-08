from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from core.common.models import BaseModel
from core.orgs.models import Organization
from core.users.models import UserProfile


@receiver(pre_save)
def stamp_uri(sender, instance, **kwargs):  # pylint: disable=unused-argument
    if issubclass(sender, BaseModel):
        instance.uri = instance.calculate_uri()


@receiver(post_save, sender=Organization)
@receiver(post_save, sender=UserProfile)
def propagate_owner_status(sender, instance=None, created=False, **kwargs):  # pylint: disable=unused-argument
    if not created and instance:
        instance.source_set.exclude(is_active=instance.is_active).update(is_active=instance.is_active)
        instance.collection_set.exclude(is_active=instance.is_active).update(is_active=instance.is_active)
