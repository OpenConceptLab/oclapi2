from elasticsearch_dsl import TermsFacet

from core.collections.models import Collection
from core.common.constants import FACET_SIZE
from core.common.search import CustomESFacetedSearch
from core.sources.models import Source


class RepoFacetedSearch(CustomESFacetedSearch):
    doc_types = [Source, Collection]
    fields = [
        'source_type',
        'collection_type',
        'locale',
        'owner',
        'owner_type',
        'custom_validation_schema',
        'hierarchy_meaning',
        'canonical_url'
    ]

    facets = {
        'sourceType': TermsFacet(field='source_type', size=FACET_SIZE),
        'collectionType': TermsFacet(field='collection_type', size=FACET_SIZE),
        'customValidationSchema': TermsFacet(field='custom_validation_schema'),
        'locale': TermsFacet(field='locale', size=FACET_SIZE),
        'owner': TermsFacet(field='owner', size=FACET_SIZE),
        'ownerType': TermsFacet(field='owner_type')
    }
