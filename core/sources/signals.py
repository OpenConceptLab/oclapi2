from django.db.models.signals import post_save
from django.dispatch import receiver
from pydash import get

from core.sources.models import Source


@receiver(post_save, sender=Source)
def propagate_parent_attributes(sender, instance=None, created=False, **kwargs):  # pylint: disable=unused-argument
    if not created and instance and get(instance, 'should_update_public_access'):
        updated_concepts = 0
        updated_mappings = 0
        updated_concepts += instance.concepts_set.exclude(
            is_active=instance.is_active).update(is_active=instance.is_active)
        updated_concepts += instance.concepts_set.exclude(
            public_access=instance.public_access).update(public_access=instance.public_access)
        updated_mappings += instance.mappings_set.exclude(
            is_active=instance.is_active).update(is_active=instance.is_active)
        updated_mappings += instance.mappings_set.exclude(
            public_access=instance.public_access).update(public_access=instance.public_access)

        if updated_concepts:
            from core.concepts.documents import ConceptDocument
            instance.batch_index(instance.concepts_set, ConceptDocument)
        if updated_mappings:
            from core.mappings.documents import MappingDocument
            instance.batch_index(instance.mappings_set, MappingDocument)
