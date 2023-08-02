from rest_framework.permissions import BasePermission, IsAuthenticatedOrReadOnly

from core.common.permissions import CanViewConceptDictionary, CanEditConceptDictionary


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


class CanEditParentDictionary(IsAuthenticatedOrReadOnly, CanAccessParentDictionary, CanEditConceptDictionary):
    parent_permission_class = CanEditConceptDictionary


class CanViewParentDictionaryAsGuest(CanAccessParentDictionary, CanViewConceptDictionary):
    parent_permission_class = CanViewConceptDictionary
