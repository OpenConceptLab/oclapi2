from elasticsearch_dsl import TermsFacet

from core.collections.models import Collection
from core.common.search import CommonSearch


class CollectionSearch(CommonSearch):
    index = 'collections'
    doc_types = [Collection]
    fields = [
        'collection_type', 'locale', 'owner', 'owner_type', 'is_active', 'version', 'custom_validation_schema',
    ]

    facets = {
        'collectionType': TermsFacet(field='collection_type'),
        'locale': TermsFacet(field='locale'),
        'owner': TermsFacet(field='owner'),
        'ownerType': TermsFacet(field='owner_type'),
        'is_active': TermsFacet(field='is_active'),
        'version': TermsFacet(field='version'),
        'customValidationSchema': TermsFacet(field='custom_validation_schema'),
    }
