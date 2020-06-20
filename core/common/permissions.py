from rest_framework.permissions import BasePermission

from core.common.constants import ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW


class IsSuperuser(BasePermission):
    """
    The request is authenticated, and the user is a superuser
    """
    def has_object_permission(self, request, view, obj):
        return request.user.is_superuser


class HasPrivateAccess(BasePermission):
    """
    Current user is authenticated as a staff user, or is designated as the referenced object's owner,
    or belongs to an organization that is designated as the referenced object's owner.
    """
    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        if request.user.is_authenticated():
            user = request.user
            if hasattr(obj, 'parent') and user == obj.parent:
                return True
            if user.organizations.filter(id=obj.id):
                return True
            if hasattr(obj, 'parent_id') and user.organizations.filter(id=obj.parent_id):
                return True
        return False


class CanViewConceptDictionary(HasPrivateAccess):
    """
    The user can view this source
    """

    def has_object_permission(self, request, view, obj):
        if obj.public_access in [ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW]:
            return True

        return super().has_object_permission(request, view, obj)


class CanEditConceptDictionary(HasPrivateAccess):
    """
    The request is authenticated as a user, and the user can edit this source
    """

    def has_object_permission(self, request, view, obj):
        if request.user.is_authenticated() and ACCESS_TYPE_EDIT == obj.public_access:
            return True

        return super().has_object_permission(request, view, obj)
