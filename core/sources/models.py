from django.db import models

from core.common.models import ConceptContainerModel
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
