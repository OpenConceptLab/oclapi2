from elasticsearch_dsl import TermsFacet

from core.common.search import CommonSearch
from core.concepts.models import Concept


class ConceptSearch(CommonSearch):
    index = 'concepts'
    doc_types = [Concept]
    fields = [
        'datatype', 'concept_class', 'locale', 'retired',
        'source', 'owner', 'owner_type', 'is_latest_version', 'is_active', 'name', 'collection', 'name_types', 'description_types',
    ]

    facets = {
        'datatype': TermsFacet(field='datatype'),
        'conceptClass': TermsFacet(field='concept_class'),
        'locale': TermsFacet(field='locale'),
        'retired': TermsFacet(field='retired'),
        'source': TermsFacet(field='source'),
        'collection': TermsFacet(field='collection'),
        'owner': TermsFacet(field='owner'),
        'ownerType': TermsFacet(field='owner_type'),
        'is_active': TermsFacet(field='is_active'),
        'is_latest_version': TermsFacet(field='is_latest_version'),
        'collection_owner_url': TermsFacet(field='collection_owner_url'),
        'name_types': TermsFacet(field='name_types'),
        'description_types': TermsFacet(field='description_types'),
    }
