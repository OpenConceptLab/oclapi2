from rest_framework.permissions import BasePermission


class IsSuperuser(BasePermission):
    """
    The request is authenticated, and the user is a superuser
    """
    def has_object_permission(self, request, view, obj):
        return request.user.is_superuser
