from rest_framework.permissions import BasePermission, SAFE_METHODS

from core.common.permissions import HasOwnership


class CanEditPins(BasePermission):
    """
    Anyone can read pins. A user's pins can be changed by that user, an org's pins by its members, and any pin by
    staff. Checked against the user or org the pins belong to.
    """
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True

        return HasOwnership().has_object_permission(request, view, obj)
