from elasticsearch_dsl import TermsFacet

from core.common.constants import FACET_SIZE
from core.common.search import CommonSearch
from core.sources.models import Source


class SourceSearch(CommonSearch):
    index = 'sources'
    doc_types = [Source]
    fields = [
        'source_type', 'locale', 'owner', 'owner_type', 'is_active', 'version', 'custom_validation_schema',
        'hierarchy_meaning',
    ]

    facets = {
        'sourceType': TermsFacet(field='source_type', size=FACET_SIZE),
        'locale': TermsFacet(field='locale', size=FACET_SIZE),
        'owner': TermsFacet(field='owner', size=FACET_SIZE),
        'ownerType': TermsFacet(field='owner_type'),
        'is_active': TermsFacet(field='is_active'),
        'version': TermsFacet(field='version', size=FACET_SIZE),
        'customValidationSchema': TermsFacet(field='custom_validation_schema'),
        'hierarchyMeaning': TermsFacet(field='hierarchy_meaning', size=FACET_SIZE),
    }
