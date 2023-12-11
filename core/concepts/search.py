from elasticsearch_dsl import TermsFacet

from core.common.constants import FACET_SIZE
from core.common.search import CustomESFacetedSearch
from core.concepts.models import Concept


class ConceptFacetedSearch(CustomESFacetedSearch):
    index = 'concepts'
    doc_types = [Concept]
    fields = [
        'datatype', 'concept_class', 'locale', 'retired', 'is_latest_version',
        'source', 'owner', 'owner_type', 'name', 'collection', 'name_types',
        'description_types', 'id', 'synonyms', 'extras', 'updated_by'
    ]

    facets = {
        'datatype': TermsFacet(field='datatype', size=100),
        'conceptClass': TermsFacet(field='concept_class', size=100),
        'locale': TermsFacet(field='locale', size=100),
        'retired': TermsFacet(field='retired'),
        'source': TermsFacet(field='source', size=FACET_SIZE),
        'collection': TermsFacet(field='collection', size=FACET_SIZE),
        'owner': TermsFacet(field='owner', size=FACET_SIZE),
        'ownerType': TermsFacet(field='owner_type'),
        'updatedBy': TermsFacet(field='updated_by', size=FACET_SIZE),
        'is_latest_version': TermsFacet(field='is_latest_version'),
        'is_in_latest_source_version': TermsFacet(field='is_in_latest_source_version'),
        'collection_owner_url': TermsFacet(field='collection_owner_url', size=FACET_SIZE),
        'expansion': TermsFacet(field='expansion', size=FACET_SIZE),
        'nameTypes': TermsFacet(field='name_types', size=FACET_SIZE),
        'descriptionTypes': TermsFacet(field='description_types', size=FACET_SIZE),
        'source_version': TermsFacet(field='source_version', size=FACET_SIZE),
        'collection_version': TermsFacet(field='collection_version', size=FACET_SIZE),
    }
