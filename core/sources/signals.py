from django.db.models.signals import post_save
from django.dispatch import receiver
from pydash import get

from core.sources.models import Source


@receiver(post_save, sender=Source)
def propagate_parent_attributes(sender, instance=None, created=False, **kwargs):  # pylint: disable=unused-argument
    if created:
        instance.record_create_event()
    if not created and instance:
        if get(instance, '_should_update_is_active'):
            instance.concepts_set.exclude(is_active=instance.is_active).update(is_active=instance.is_active)
            instance.mappings_set.exclude(is_active=instance.is_active).update(is_active=instance.is_active)

        if get(instance, '_should_update_public_access'):
            updated_concepts = instance.concepts_set.exclude(
                public_access=instance.public_access).update(public_access=instance.public_access)
            updated_mappings = instance.mappings_set.exclude(
                public_access=instance.public_access).update(public_access=instance.public_access)

            if updated_concepts or updated_mappings:
                instance._public_access_task = instance.index_public_access_async()  # pylint: disable=protected-access
