from rest_framework.permissions import BasePermission, IsAuthenticatedOrReadOnly

from core.capabilities.constants import WRITE_ACTION_EDIT
from core.common.permissions import CanViewConceptDictionary, CanEditConceptDictionary, HasPrivateAccess


class CanAccessParentDictionary(BasePermission):
    parent_permission_class = None

    def has_object_permission(self, request, view, obj):
        from core.collections.models import Collection
        from core.sources.models import Source
        if isinstance(obj, (Source, Collection)):
            parent = obj
        else:
            if hasattr(obj, 'versioned_object'):
                obj = obj.versioned_object
            parent = obj.parent
        parent_view_perm = self.parent_permission_class()  # pylint: disable=not-callable
        return parent_view_perm.has_object_permission(request, view, parent)


class CanViewParentDictionary(IsAuthenticatedOrReadOnly, CanAccessParentDictionary, CanViewConceptDictionary):
    parent_permission_class = CanViewConceptDictionary


class CanEditDictionaryContent(CanEditConceptDictionary):
    """
    For a write to what a repository holds (a concept, a mapping, a name, an extra): whatever the method, the capability
    policy is asked about an edit of the repository, never its deletion or publication.
    """
    def get_write_action(self, request, view, obj):  # pylint: disable=unused-argument
        return WRITE_ACTION_EDIT


class CanEditParentDictionary(IsAuthenticatedOrReadOnly, CanAccessParentDictionary, CanEditConceptDictionary):
    parent_permission_class = CanEditDictionaryContent


class CanAdministerParentDictionary(IsAuthenticatedOrReadOnly, CanAccessParentDictionary, HasPrivateAccess):
    """
    Repo admins: platform staff or the dictionary's owner/org-members (its authors/editors), and the capability policy
    doesn't refuse an edit of the repository.
    """
    parent_permission_class = CanEditDictionaryContent


class CanViewParentDictionaryAsGuest(CanAccessParentDictionary, CanViewConceptDictionary):
    parent_permission_class = CanViewConceptDictionary
