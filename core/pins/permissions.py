from rest_framework.permissions import BasePermission, SAFE_METHODS

from core.common.permissions import HasOwnership


class CanEditPins(BasePermission):
    """
    An org's pins can be read by anyone who can view them (Pin.get_visible_to), a user's by that user and staff
    (PinBaseView). A user's pins can be changed by that user, an
    org's pins by its members, and any pin by staff. Checked against the user or org the pins belong to.
    """
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True

        return HasOwnership().has_object_permission(request, view, obj)
