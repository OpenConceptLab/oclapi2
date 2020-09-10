from elasticsearch_dsl import TermsFacet

from core.common.search import CommonSearch
from core.sources.models import Source


class SourceSearch(CommonSearch):
    index = 'sources'
    doc_types = [Source]
    fields = ['source_type', 'locale', 'owner', 'owner_type', 'is_active', 'version']

    facets = {
        'sourceType': TermsFacet(field='source_type'),
        'locale': TermsFacet(field='locale'),
        'owner': TermsFacet(field='owner'),
        'ownerType': TermsFacet(field='owner_type'),
        'is_active': TermsFacet(field='is_active'),
        'version': TermsFacet(field='version'),
    }
