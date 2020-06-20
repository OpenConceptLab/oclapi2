from django.db import models
from django.urls import reverse

from core.common.models import ConceptContainerModel
from core.common.utils import reverse_resource
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
        return reverse_resource(self, 'sourceversion-list')

    @property
    def concepts_url(self):
        parent_kwarg = self.parent.get_url_kwarg()
        return reverse('concept-list', kwargs={'source': self.mnemonic, parent_kwarg: self.parent_resource})
