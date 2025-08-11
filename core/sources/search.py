from elasticsearch_dsl import TermsFacet

from core.common.constants import FACET_SIZE
from core.common.search import CustomESFacetedSearch
from core.sources.models import Source


class SourceFacetedSearch(CustomESFacetedSearch):
    index = 'sources'
    doc_types = [Source]
    fields = [
        'source_type', 'locale', 'owner', 'owner_type', 'is_active', 'version', 'custom_validation_schema',
        'hierarchy_meaning', 'name', 'canonical_url', 'mnemonic', 'identifier', 'jurisdiction',
        'publisher', 'content_type', 'extras', 'updated_by', 'property_codes', 'filter_codes',
        'match_algorithm'
    ]

    facets = {
        'sourceType': TermsFacet(field='source_type', size=FACET_SIZE),
        'locale': TermsFacet(field='locale', size=FACET_SIZE),
        'propertyCodes': TermsFacet(field='property_codes', size=FACET_SIZE),
        'filterCodes': TermsFacet(field='filter_codes', size=FACET_SIZE),
        'owner': TermsFacet(field='owner', size=FACET_SIZE),
        'ownerType': TermsFacet(field='owner_type'),
        'is_active': TermsFacet(field='is_active'),
        'version': TermsFacet(field='version', size=FACET_SIZE),
        'customValidationSchema': TermsFacet(field='custom_validation_schema'),
        'hierarchyMeaning': TermsFacet(field='hierarchy_meaning', size=FACET_SIZE),
        'updatedBy': TermsFacet(field='updated_by', size=FACET_SIZE),
        'matchAlgorithm': TermsFacet(field='match_algorithm', size=FACET_SIZE),
    }
