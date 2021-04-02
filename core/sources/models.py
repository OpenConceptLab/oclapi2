from django.db import models
from django.db.models import UniqueConstraint
from django.urls import resolve
from pydash import get, compact

from core.common.constants import HEAD, ACCESS_TYPE_NONE
from core.common.models import ConceptContainerModel
from core.common.utils import reverse_resource, get_query_params_from_url_string
from core.concepts.models import LocalizedText
from core.sources.constants import SOURCE_TYPE, SOURCE_VERSION_TYPE


class Source(ConceptContainerModel):
    es_fields = {
        'source_type': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'mnemonic': {'sortable': True, 'filterable': True, 'exact': True},
        'name': {'sortable': True, 'filterable': True, 'exact': True},
        'last_update': {'sortable': True, 'filterable': False, 'default': 'desc'},
        'locale': {'sortable': False, 'filterable': True, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True},
        'custom_validation_schema': {'sortable': False, 'filterable': True, 'facet': True},
        'canonical_url': {'sortable': True, 'filterable': True},
    }

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
    content_type = models.TextField(blank=True, null=True)
    collection_reference = models.CharField(null=True, blank=True, max_length=100)
    hierarchy_meaning = models.CharField(null=True, blank=True, max_length=50)
    case_sensitive = models.BooleanField(null=True, blank=True, default=None)
    compositional = models.BooleanField(null=True, blank=True, default=None)
    version_needed = models.BooleanField(null=True, blank=True, default=None)

    OBJECT_TYPE = SOURCE_TYPE
    OBJECT_VERSION_TYPE = SOURCE_VERSION_TYPE

    @classmethod
    def head_from_uri(cls, uri):
        queryset = cls.objects.none()
        if not uri:
            return queryset

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
            queryset = queryset.filter(cls.get_iexact_or_criteria('mnemonic', source))

        return queryset

    @staticmethod
    def get_resource_url_kwarg():
        return 'source'

    @property
    def source(self):
        return self.mnemonic

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

        return self.custom_validation_schema is not None and self.num_concepts > 0

    def any_concept_referred_privately(self):
        from core.collections.models import Collection
        return Collection.objects.filter(
            public_access=ACCESS_TYPE_NONE
        ).filter(concepts__in=self.concepts_set.all()).exists()

    def any_mapping_referred_privately(self):
        from core.collections.models import Collection
        return Collection.objects.filter(
            public_access=ACCESS_TYPE_NONE
        ).filter(mappings__in=self.mappings_set.all()).exists()

    def is_content_privately_referred(self):
        return self.any_concept_referred_privately() or self.any_mapping_referred_privately()

    def update_mappings(self):
        from core.mappings.models import Mapping
        uris = compact([self.uri, self.canonical_url])
        for mapping in Mapping.objects.filter(to_source__isnull=True, to_source_url__in=uris):
            mapping.to_source = self
            mapping.save()

        for mapping in Mapping.objects.filter(from_source__isnull=True, from_source_url__in=uris):
            mapping.from_source = self
            mapping.save()
