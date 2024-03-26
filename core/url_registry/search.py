from elasticsearch_dsl import TermsFacet

from core.common.constants import FACET_SIZE
from core.common.search import CustomESFacetedSearch
from core.url_registry.models import URLRegistry


class URLRegistryFacetedSearch(CustomESFacetedSearch):
    index = 'url_registries'
    doc_types = [URLRegistry]
    fields = [
        'owner_type', 'owner', 'updated_by',
        'repo_owner_type', 'repo_owner', 'is_active'
    ]

    facets = {
        'owner': TermsFacet(field='owner', size=FACET_SIZE),
        'ownerType': TermsFacet(field='owner_type'),
        'repoOwner': TermsFacet(field='repo_owner', size=FACET_SIZE),
        'repoOwnerType': TermsFacet(field='repo_owner_type'),
        'updatedBy': TermsFacet(field='updated_by', size=FACET_SIZE),
        'is_active': TermsFacet(field='is_active'),
    }
