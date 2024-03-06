from elasticsearch_dsl import TermsFacet

from core.collections.models import Collection
from core.common.constants import FACET_SIZE
from core.common.search import CustomESFacetedSearch


class CollectionFacetedSearch(CustomESFacetedSearch):
    index = 'collections'
    doc_types = [Collection]
    fields = [
        'collection_type', 'locale', 'owner', 'owner_type', 'is_active', 'version', 'custom_validation_schema',
        'name', 'mnemonic', 'extras', 'identifier', 'publisher', 'immutable', 'updated_by'
    ]

    facets = {
        'collectionType': TermsFacet(field='collection_type', size=FACET_SIZE),
        'locale': TermsFacet(field='locale', size=FACET_SIZE),
        'owner': TermsFacet(field='owner', size=FACET_SIZE),
        'ownerType': TermsFacet(field='owner_type'),
        'is_active': TermsFacet(field='is_active'),
        'version': TermsFacet(field='version', size=FACET_SIZE),
        'customValidationSchema': TermsFacet(field='custom_validation_schema'),
        'updatedBy': TermsFacet(field='updated_by', size=FACET_SIZE),
    }
