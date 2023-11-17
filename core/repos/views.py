from drf_yasg.utils import swagger_auto_schema

from core.common.constants import HEAD
from core.common.mixins import ListWithHeadersMixin
from core.common.permissions import CanViewConceptDictionary
from core.common.swagger_parameters import q_param, limit_param, sort_desc_param, sort_asc_param, page_param, \
    include_retired_param, updated_since_param, compress_header
from core.common.views import BaseAPIView
from core.repos.documents import RepoDocument
from core.repos.serializers import RepoListSerializer

es_fields = {
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
}


class ReposListView(BaseAPIView, ListWithHeadersMixin):
    serializer_class = RepoListSerializer
    document_model = RepoDocument
    default_filters = {'version': HEAD}
    es_fields = es_fields
    is_searchable = True
    is_only_searchable = True
    permission_classes = (CanViewConceptDictionary,)

    @swagger_auto_schema(
        manual_parameters=[
            q_param, limit_param, sort_desc_param, sort_asc_param, page_param,
            include_retired_param, updated_since_param, compress_header
        ]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class OrganizationRepoListView(ReposListView):
    pass
