from drf_yasg.utils import swagger_auto_schema

from core.common.constants import HEAD
from core.common.mixins import ListWithHeadersMixin
from core.common.permissions import CanViewConceptDictionary
from core.common.swagger_parameters import q_param, limit_param, sort_desc_param, sort_asc_param, page_param, \
    include_retired_param, updated_since_param, compress_header, canonical_url_param, all_versions_param
from core.common.views import BaseAPIView
from core.repos.documents import RepoDocument
from core.repos.models import CountableRepoList, Repository
from core.repos.search import RepoFacetedSearch
from core.repos.serializers import RepoListSerializer

es_fields = {
    'repo_type': {
        'sortable': True,
        'filterable': True,
        'facet': True,
        'exact': True
    },
    'source_type': {
        'sortable': True,
        'filterable': True,
        'facet': True,
        'exact': True
    },
    'collection_type': {
        'sortable': True,
        'filterable': True,
        'facet': True,
        'exact': True
    },
    'mnemonic': {
        'sortable': False,
        'filterable': True,
        'exact': True
    },
    '_mnemonic': {
        'sortable': True,
        'filterable': False,
        'exact': False
    },
    'name': {
        'sortable': False,
        'filterable': True,
        'exact': True
    },
    '_name': {
        'sortable': True,
        'filterable': False,
        'exact': False
    },
    'last_update': {
        'sortable': True,
        'filterable': False,
        'default': 'desc'
    },
    'updated_by': {
        'sortable': False,
        'filterable': False,
        'facet': True
    },
    'locale': {
        'sortable': False,
        'filterable': True,
        'facet': True
    },
    'owner': {
        'sortable': True,
        'filterable': True,
        'facet': True,
        'exact': True
    },
    'owner_type': {
        'sortable': False,
        'filterable': True,
        'facet': True
    },
    'custom_validation_schema': {
        'sortable': False,
        'filterable': True,
        'facet': True
    },
    'canonical_url': {
        'sortable': False,
        'filterable': True,
        'exact': True
    },
    'experimental': {
        'sortable': False,
        'filterable': False,
        'facet': False
    },
    'hierarchy_meaning': {
        'sortable': False,
        'filterable': True,
        'facet': True
    },
    'external_id': {
        'sortable': False,
        'filterable': True,
        'facet': False,
        'exact': True
    },
    'retired': {
        'sortable': False,
        'filterable': True,
        'facet': True
    },
}


class ReposListView(BaseAPIView, ListWithHeadersMixin):
    serializer_class = RepoListSerializer
    document_model = RepoDocument
    facet_class = RepoFacetedSearch
    default_filters = {'version': HEAD}
    es_fields = es_fields
    is_searchable = True
    default_qs_sort_attr = None
    permission_classes = (CanViewConceptDictionary,)

    def get_owner_filters(self):
        filters = {}
        org = self.kwargs.get('org')
        user = self.kwargs.get('user')
        if user:
            filters['user'] = user
        elif self.user_is_self and self.request.user.is_authenticated:
            filters['user'] = self.request.user.username
        if org:
            filters['org'] = org
        return filters

    def get_queryset(self):
        params = {'version': HEAD, **self.get_owner_filters()}
        sources, collections = Repository.get_base_querysets(
            params, exclude_retired=self._should_exclude_retired_from_search_results())
        sources = self.filter_queryset_by_public_access(sources.select_related('user', 'organization'))
        collections = self.filter_queryset_by_public_access(collections.select_related('user', 'organization'))
        merged, self.total_count = Repository.merge_querysets(
            sources, collections, self.limit, self.request.query_params.get('page'))
        return merged

    @swagger_auto_schema(
        manual_parameters=[
            q_param, limit_param, sort_desc_param, sort_asc_param, page_param,
            include_retired_param, updated_since_param, canonical_url_param, all_versions_param, compress_header
        ]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class OrganizationRepoListView(ReposListView):
    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return CountableRepoList([])

        org_mnemonics = list(user.organizations.values_list('mnemonic', flat=True))
        if not org_mnemonics:
            return CountableRepoList([])

        sources, collections = Repository.get_base_querysets(
            {'version': HEAD}, exclude_retired=self._should_exclude_retired_from_search_results())
        sources = sources.filter(organization__mnemonic__in=org_mnemonics)
        collections = collections.filter(organization__mnemonic__in=org_mnemonics)
        sources = self.filter_queryset_by_public_access(sources.select_related('user', 'organization'))
        collections = self.filter_queryset_by_public_access(collections.select_related('user', 'organization'))
        merged, self.total_count = Repository.merge_querysets(
            sources, collections, self.limit, self.request.query_params.get('page'))
        return merged
