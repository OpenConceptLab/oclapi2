from elasticsearch_dsl import TermsFacet

from core.common.constants import FACET_SIZE
from core.common.search import CommonSearch
from core.mappings.models import Mapping


class MappingSearch(CommonSearch):
    index = 'mappings'
    doc_types = [Mapping]
    fields = [
        'map_type', 'retired', 'from_concept', 'to_concept', 'concept',
        'source', 'owner', 'owner_type', 'is_latest_version', 'is_active',
        'concept_source', 'concept_owner', 'from_concept_owner',
        'to_concept_owner', 'concept_owner_type', 'from_concept_owner_type', 'to_concept_owner_type',
        'from_concept_source', 'to_concept_source', 'collection',
    ]

    facets = {
        'toConceptSource': TermsFacet(field='to_concept_source', size=FACET_SIZE),
        'fromConceptSource': TermsFacet(field='from_concept_source', size=FACET_SIZE),
        'toConceptOwnerType': TermsFacet(field='to_concept_owner_type'),
        'fromConceptOwnerType': TermsFacet(field='from_concept_owner_type'),
        'conceptOwnerType': TermsFacet(field='concept_owner_type'),
        'toConceptOwner': TermsFacet(field='to_concept_owner', size=FACET_SIZE),
        'fromConceptOwner': TermsFacet(field='from_concept_owner', size=FACET_SIZE),
        'conceptOwner': TermsFacet(field='concept_owner', size=FACET_SIZE),
        'conceptSource': TermsFacet(field='concept_source', size=FACET_SIZE),
        'concept': TermsFacet(field='concept', size=FACET_SIZE),
        'toConcept': TermsFacet(field='to_concept', size=FACET_SIZE),
        'fromConcept': TermsFacet(field='from_concept', size=FACET_SIZE),
        'mapType': TermsFacet(field='map_type', size=FACET_SIZE),
        'retired': TermsFacet(field='retired'),
        'source': TermsFacet(field='source', size=FACET_SIZE),
        'collection': TermsFacet(field='collection', size=FACET_SIZE),
        'owner': TermsFacet(field='owner', size=FACET_SIZE),
        'ownerType': TermsFacet(field='owner_type'),
        'is_active': TermsFacet(field='is_active'),
        'is_latest_version': TermsFacet(field='is_latest_version'),
        'collection_owner_url': TermsFacet(field='collection_owner_url', size=FACET_SIZE),
        'expansion': TermsFacet(field='expansion', size=FACET_SIZE),
    }
