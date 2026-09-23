from rest_framework.permissions import BasePermission, SAFE_METHODS


class CanChangeClientConfig(BasePermission):
    """
    Any logged-in user can read a config. Only its creator or staff can change or delete it.
    """
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True

        return request.user.is_staff or obj.created_by_id == request.user.id
