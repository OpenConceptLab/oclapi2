from django.db.models.signals import post_save
from django.dispatch import receiver
from pydash import get

from core.sources.models import Source


@receiver(post_save, sender=Source)
def propagate_parent_attributes(sender, instance=None, created=False, **kwargs):  # pylint: disable=unused-argument
    if created:
        instance.record_create_event()
    if not created and instance:
        from core.concepts.documents import ConceptDocument
        if get(instance, '_should_update_is_active'):
            instance.concepts_set.exclude(is_active=instance.is_active).update(is_active=instance.is_active)
            # Direct GraphQL hits use this flag without reloading the concept from the database.
            instance.batch_index(instance.concepts_set, ConceptDocument, partial_doc={'is_active': instance.is_active})
            instance.mappings_set.exclude(is_active=instance.is_active).update(is_active=instance.is_active)

        if get(instance, '_should_update_public_access'):
            updated_concepts = instance.concepts_set.exclude(
                public_access=instance.public_access).update(public_access=instance.public_access)
            updated_mappings = instance.mappings_set.exclude(
                public_access=instance.public_access).update(public_access=instance.public_access)

            partial_doc = {'public_can_view': instance.public_can_view}
            if updated_concepts:
                instance.batch_index(instance.concepts_set, ConceptDocument, partial_doc=partial_doc)
            if updated_mappings:
                from core.mappings.documents import MappingDocument
                instance.batch_index(instance.mappings_set, MappingDocument, partial_doc=partial_doc)
