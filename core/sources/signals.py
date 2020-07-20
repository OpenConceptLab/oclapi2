from django.db.models.signals import post_save
from django.dispatch import receiver

from core.sources.models import Source


@receiver(post_save, sender=Source)
def propagate_parent_attributes(sender, instance=None, created=False, **kwargs):  # pylint: disable=unused-argument
    if not created and instance:
        instance.concepts_set.exclude(is_active=instance.is_active).update(is_active=instance.is_active)
        instance.concepts_set.exclude(public_access=instance.public_access).update(public_access=instance.public_access)
        instance.mappings_set.exclude(is_active=instance.is_active).update(is_active=instance.is_active)
        instance.mappings_set.exclude(public_access=instance.public_access).update(public_access=instance.public_access)
