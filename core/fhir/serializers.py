"""Shared fields and resource representations for FHIR repository serializers."""
from rest_framework.fields import CharField

SUBSETTED = {'system': 'http://terminology.hl7.org/CodeSystem/v3-ObservationValue', 'code': 'SUBSETTED'}


class CanonicalUrlField(CharField):
    """A resource's canonical URL: the version's own, else its repository's."""

    def get_attribute(self, instance):
        return super().get_attribute(instance) or instance.repository_canonical_url


class TitleField(CharField):
    """
    A repository's FHIR title: its full name, or its name when it has no full name (empty or only whitespace). Writes
    still go to full_name.
    """

    def get_attribute(self, instance):
        full_name = instance.full_name
        return full_name if full_name and full_name.strip() else instance.name


class FhirRepositorySerializerMixin:
    """
    For the CodeSystem, ValueSet and ConceptMap serializers: the opaque id at the global base (context opaque_ids), and
    in a search (many=True, context has_many) the summary, marked SUBSETTED, without `content_field`.
    """
    content_field = None

    @classmethod
    def many_init(cls, *args, **kwargs):
        kwargs['context'] = {**kwargs.get('context', {}), 'has_many': True}
        return super().many_init(*args, **kwargs)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.context.get('has_many'):
            self.fields.pop(self.content_field, None)

    def with_fhir_view(self, instance, rep):
        """
        The id (opaque at the global base), and meta.versionId: the version's opaque id at both bases, so the
        releases of one repository, which share their mnemonic id at an owner base, stay distinct in a Bundle (bdl-7).
        A search entry or a read whose content field is subsetted is tagged SUBSETTED. meta keeps its existing values
        and tags.
        """
        if instance.id:
            if self.context.get('opaque_ids'):
                rep['id'] = str(instance.id)
            rep['meta'] = {'versionId': str(instance.id), **(rep.get('meta') or {})}
        if self.context.get('has_many') or getattr(self.fields.get(self.content_field), 'subsetted', False):
            meta = rep.get('meta') or {}
            tags = list(meta.get('tag') or [])
            if SUBSETTED not in tags:
                tags.append(SUBSETTED)
            rep['meta'] = {**meta, 'tag': tags}
        return rep
