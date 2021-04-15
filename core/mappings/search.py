from elasticsearch_dsl import TermsFacet

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
        'toConceptSource': TermsFacet(field='to_concept_source'),
        'fromConceptSource': TermsFacet(field='from_concept_source'),
        'toConceptOwnerType': TermsFacet(field='to_concept_owner_type'),
        'fromConceptOwnerType': TermsFacet(field='from_concept_owner_type'),
        'conceptOwnerType': TermsFacet(field='concept_owner_type'),
        'toConceptOwner': TermsFacet(field='to_concept_owner'),
        'fromConceptOwner': TermsFacet(field='from_concept_owner'),
        'conceptOwner': TermsFacet(field='concept_owner'),
        'conceptSource': TermsFacet(field='concept_source'),
        'concept': TermsFacet(field='concept'),
        'toConcept': TermsFacet(field='to_concept'),
        'fromConcept': TermsFacet(field='from_concept'),
        'mapType': TermsFacet(field='map_type'),
        'retired': TermsFacet(field='retired'),
        'source': TermsFacet(field='source'),
        'collection': TermsFacet(field='collection'),
        'owner': TermsFacet(field='owner'),
        'ownerType': TermsFacet(field='owner_type'),
        'is_active': TermsFacet(field='is_active'),
        'is_latest_version': TermsFacet(field='is_latest_version'),
        'collection_owner_url': TermsFacet(field='collection_owner_url'),
    }
