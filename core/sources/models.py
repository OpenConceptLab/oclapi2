from django.db import models
from django.urls import reverse

from core.common.models import ConceptContainerModel
from core.common.utils import reverse_resource
from core.concepts.models import LocalizedText
from core.sources.constants import SOURCE_TYPE


class Source(ConceptContainerModel):
    class Meta:
        db_table = 'sources'
        unique_together = (('mnemonic', 'version', 'organization'), ('mnemonic', 'version', 'user'))

    source_type = models.TextField(blank=True)

    OBJECT_TYPE = SOURCE_TYPE

    @property
    def source(self):
        return self.mnemonic

    @staticmethod
    def get_url_kwarg():
        return 'source'

    @property
    def versions_url(self):
        return reverse_resource(self, 'source-version-list')

    @property
    def concepts_url(self):
        parent_kwarg = self.parent.get_url_kwarg()
        return reverse('concept-list', kwargs={'source': self.mnemonic, parent_kwarg: self.parent_resource})

    def update_version_data(self, obj=None):
        if obj:
            self.description = obj.description
        else:
            obj = self.get_head()

        if obj:
            self.name = obj.name
            self.full_name = obj.full_name
            self.website = obj.website
            self.public_access = obj.public_access
            self.source_type = obj.source_type
            self.supported_locales = obj.supported_locales
            self.custom_validation_schema = obj.custom_validation_schema
            self.default_locale = obj.default_locale
            self.external_id = obj.external_id

    def get_concept_name_locales(self):
        return LocalizedText.objects.filter(name_locales__in=self.get_active_concepts())

    def is_validation_necessary(self):
        origin_source = self.get_latest_version()

        if origin_source.custom_validation_schema == self.custom_validation_schema:
            return False

        return self.custom_validation_schema is not None and self.num_concepts > 0

    def seed_concepts(self):
        head = self.head
        if head:
            self.concepts.set(head.concepts.all())
