from django.db.models import QuerySet
from django.db.models.signals import post_save, pre_delete, m2m_changed
from django.dispatch import receiver
from pydash import get

from core.collections.models import Collection, Expansion
from core.concepts.models import Concept
from core.mappings.models import Mapping
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
            if not instance.is_head:
                instance.index_release_rows_mapped_codes_async()


@receiver(m2m_changed, sender=Expansion.concepts.through)
@receiver(m2m_changed, sender=Expansion.mappings.through)
def mark_expansion_member_removal(sender, instance, action, reverse, pk_set, **kwargs):  # pylint: disable=unused-argument
    """A member taken out of an expansion moves its collection version's last_child_update"""
    if reverse:  # instance is the concept or mapping, pk_set its expansions
        if action == 'pre_clear':
            expansions = instance.expansion_set.all()
        elif action == 'post_remove' and pk_set:
            expansions = Expansion.objects.filter(id__in=pk_set)
        else:
            return
        Collection.mark_child_removal(expansions.values_list('collection_version_id', flat=True))
    elif action == 'post_clear' or (action == 'post_remove' and pk_set):
        Collection.mark_child_removal([instance.collection_version_id])


@receiver(pre_delete, sender=Concept)
@receiver(pre_delete, sender=Mapping)
def mark_hard_delete(sender, instance, origin=None, **kwargs):  # pylint: disable=unused-argument
    """
    A hard-deleted concept or mapping moves the last_child_update of the collection versions that list it, and of its
    source when the delete started at a concept or mapping (not a deleted repo or owner, which takes its members with
    it). Each repo is marked once per delete.
    """
    origin_model = origin.model if isinstance(origin, QuerySet) else type(origin)
    # a collector built without an origin has none to keep the marks on
    marked = set() if origin is None else origin.__dict__.setdefault('_child_removal_marked', set())
    if origin_model in (Concept, Mapping) and instance.parent_id and ('source', instance.parent_id) not in marked:
        marked.add(('source', instance.parent_id))
        Source.mark_child_removal([instance.parent_id])
    collection_ids = set(
        instance.expansion_set.values_list('collection_version_id', flat=True)
    ) - {_id for kind, _id in marked if kind == 'collection'}
    if collection_ids:
        marked.update(('collection', _id) for _id in collection_ids)
        Collection.mark_child_removal(collection_ids)
