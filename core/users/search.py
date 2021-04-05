from elasticsearch_dsl import TermsFacet

from core.common.search import CommonSearch
from core.users.models import UserProfile


class UserProfileSearch(CommonSearch):
    index = 'user_profiles'
    doc_types = [UserProfile]
    fields = ['is_superuser', 'is_staff']

    facets = {
        'isSuperuser': TermsFacet(field='is_superuser'),
        'isAdmin': TermsFacet(field='is_staff'),
        'isStaff': TermsFacet(field='is_staff'),
    }
