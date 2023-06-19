from opensearch_dsl import TermsFacet

from core.common.constants import FACET_SIZE
from core.common.search import CommonSearch
from core.concepts.models import Concept


class ConceptSearch(CommonSearch):
    index = 'concepts'
    doc_types = [Concept]
    fields = [
        'datatype', 'concept_class', 'locale', 'retired',
        'source', 'owner', 'owner_type', 'is_latest_version', 'is_active', 'name', 'collection', 'name_types',
        'description_types', 'id', 'synonyms', 'extras'
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
        'is_active': TermsFacet(field='is_active'),
        'is_latest_version': TermsFacet(field='is_latest_version'),
        'collection_owner_url': TermsFacet(field='collection_owner_url', size=FACET_SIZE),
        'expansion': TermsFacet(field='expansion', size=FACET_SIZE),
        'nameTypes': TermsFacet(field='name_types', size=FACET_SIZE),
        'descriptionTypes': TermsFacet(field='description_types', size=FACET_SIZE),
        'source_version': TermsFacet(field='source_version', size=FACET_SIZE),
        'collection_version': TermsFacet(field='collection_version', size=FACET_SIZE),
    }
