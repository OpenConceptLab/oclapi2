from elasticsearch_dsl import TermsFacet

from core.common.constants import FACET_SIZE
from core.common.search import CustomESFacetedSearch
from core.users.models import UserProfile


class UserProfileFacetedSearch(CustomESFacetedSearch):
    index = 'user_profiles'
    doc_types = [UserProfile]
    fields = ['is_superuser', 'is_staff', 'updated_by']

    facets = {
        'isSuperuser': TermsFacet(field='is_superuser'),
        'isAdmin': TermsFacet(field='is_staff'),
        'isStaff': TermsFacet(field='is_staff'),
        'updatedBy': TermsFacet(field='updated_by', size=FACET_SIZE),
    }
