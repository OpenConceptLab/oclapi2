from rest_framework.permissions import BasePermission

from core.common.constants import ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW


class HasPrivateAccess(BasePermission):
    """
    Current user is authenticated as a staff user, or is designated as the referenced object's owner,
    or belongs to an organization that is designated as the referenced object's owner.
    """
    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_staff:
            return True
        if user.is_authenticated:
            if hasattr(obj, 'parent_id') and user == obj.parent:
                return True
            if user.organizations.filter(id=obj.id).exists():
                return True
            if hasattr(obj, 'parent_id') and user.organizations.filter(id=obj.parent_id).exists():
                return True
        return False


class HasOwnership(BasePermission):
    """
    The request is authenticated, and the user is a member of the referenced organization
    """
    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_staff or user.is_superuser:
            return True
        if user.is_authenticated:
            from core.users.models import UserProfile
            from core.orgs.models import Organization
            if isinstance(obj, UserProfile):
                return obj == user
            if isinstance(obj, Organization):
                return obj.is_member(user)
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
        if request.user.is_authenticated and ACCESS_TYPE_EDIT == obj.public_access:
            return True

        return super().has_object_permission(request, view, obj)


class HasAccessToVersionedObject(BasePermission):
    """
    Current user is authenticated as a staff user, or is designated as the owner of the object
    that is versioned by the referenced object, or is a member of an organization
    that is designated as the owner of the object that is versioned by the referenced object.
    """
    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        versioned_object = obj.head

        from core.users.models import UserProfile
        if isinstance(versioned_object.parent, UserProfile) and request.user.id == versioned_object.parent_id:
            return True
        if request.user.is_authenticated:
            return request.user.organizations.filter(id=versioned_object.parent_id).exists()
        return False


class CanViewConceptDictionaryVersion(HasAccessToVersionedObject):
    """
    The user can view this source
    """

    def has_object_permission(self, request, view, obj):
        if obj.public_access in [ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW]:
            return True
        return super().has_object_permission(request, view, obj)


class CanEditConceptDictionaryVersion(HasAccessToVersionedObject):
    """
    The request is authenticated as a user, and the user can edit this source
    """

    def has_object_permission(self, request, view, obj):
        if request.user.is_authenticated() and ACCESS_TYPE_EDIT == obj.public_access:
            return True
        return super().has_object_permission(request, view, obj)
