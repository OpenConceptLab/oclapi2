from rest_framework.permissions import BasePermission

from core.common.permissions import CanViewConceptDictionary, CanEditConceptDictionary


class CanAccessParentSource(BasePermission):
    dictionary_permission_class = None

    def has_object_permission(self, request, view, obj):
        source = obj.parent
        dictionary = source.parent
        dictionary_permission = self.dictionary_permission_class()  # pylint: disable=not-callable
        return dictionary_permission.has_object_permission(request, view, dictionary)


class CanViewParentSource(CanAccessParentSource):
    dictionary_permission_class = CanViewConceptDictionary


class CanEditParentSource(CanAccessParentSource):
    dictionary_permission_class = CanEditConceptDictionary
