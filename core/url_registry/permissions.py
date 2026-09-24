from rest_framework.permissions import BasePermission, SAFE_METHODS

from core.common.permissions import HasOwnership


class CanEditURLRegistryEntry(BasePermission):
    """
    Anyone can read registry entries. An org's entries can be changed by its members, a user's entries by that user,
    and global entries by staff only. Staff can change any entry.
    """
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True

        return self.can_edit_entries_of(request, view, obj.owner)

    @staticmethod
    def can_edit_entries_of(request, view, owner):
        """Whether the requester can create or change entries owned by `owner` (an org, a user, or None for global)."""
        if owner is None:
            return request.user.is_staff

        return HasOwnership().has_object_permission(request, view, owner)
