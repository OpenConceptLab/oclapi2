from django.db import models
from django.db.models import UniqueConstraint
from django.urls import resolve
from pydash import get

from core.common.constants import HEAD
from core.common.models import ConceptContainerModel
from core.common.utils import reverse_resource, get_query_params_from_url_string
from core.concepts.models import LocalizedText
from core.sources.constants import SOURCE_TYPE


class Source(ConceptContainerModel):
    class Meta:
        db_table = 'sources'
        constraints = [
            UniqueConstraint(
                fields=['mnemonic', 'version', 'organization'],
                name="org_source_unique",
                condition=models.Q(user=None),
            ),
            UniqueConstraint(
                fields=['mnemonic', 'version', 'user'],
                name="user_source_unique",
                condition=models.Q(organization=None),
            )
        ]

    source_type = models.TextField(blank=True, null=True)

    OBJECT_TYPE = SOURCE_TYPE

    @classmethod
    def head_from_uri(cls, uri):
        queryset = cls.objects.none()

        try:
            kwargs = get(resolve(uri), 'kwargs', dict())
            query_params = get_query_params_from_url_string(uri)  # parsing query parameters
            kwargs.update(query_params)
            queryset = cls.get_base_queryset(kwargs).filter(version=HEAD)
        except:  # pylint: disable=bare-except
            pass

        return queryset

    @classmethod
    def get_base_queryset(cls, params):
        source = params.pop('source', None)
        queryset = super().get_base_queryset(params)
        if source:
            queryset = queryset.filter(mnemonic=source)

        return queryset

    @staticmethod
    def get_resource_url_kwarg():
        return 'source'

    @property
    def source(self):
        return self.mnemonic  # pragma: no cover

    @property
    def versions_url(self):
        return reverse_resource(self, 'source-version-list')

    def update_version_data(self, obj=None):
        super().update_version_data(obj)
        if not obj:
            obj = self.get_latest_version()

        if obj:
            self.source_type = obj.source_type
            self.custom_validation_schema = obj.custom_validation_schema

    def get_concept_name_locales(self):
        return LocalizedText.objects.filter(name_locales__in=self.get_active_concepts())

    def is_validation_necessary(self):
        origin_source = self.get_latest_version()

        if origin_source.custom_validation_schema == self.custom_validation_schema:
            return False

        return self.custom_validation_schema is not None and self.num_concepts > 0  # pragma: no cover
