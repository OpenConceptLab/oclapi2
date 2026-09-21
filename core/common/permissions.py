from rest_framework.permissions import BasePermission

from core.capabilities.constants import MAPPER_PROJECTS_CAPABILITY_ID
from core.capabilities.exceptions import MapProjectCapacityExceeded
from core.common.constants import ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW
from core.users.constants import (
    MAPPER_AI_ASSISTANT_PERMISSION, MAPPER_CUSTOM_ALGORITHMS_PERMISSION, MAPPER_ORG_PROJECTS_PERMISSION,
    MAPPER_USE_PERMISSION,
)


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
        is_user_parent = isinstance(versioned_object.parent, UserProfile)
        is_requesting_user_parent = is_user_parent and request.user.id == versioned_object.parent_id

        return is_requesting_user_parent or (
                request.user.is_authenticated and request.user.organizations.filter(
                    id=versioned_object.parent_id).exists()
        )


class CanViewConceptDictionaryVersion(HasAccessToVersionedObject):
    """
    The user can view this source
    """

    def has_object_permission(self, request, view, obj):
        if obj.public_access in [ACCESS_TYPE_EDIT, ACCESS_TYPE_VIEW]:
            return True
        return super().has_object_permission(request, view, obj)


class HasMapperCapability(BasePermission):
    """
    Base for the Mapper's four permission gates (mapper.use, mapper.ai_assistant,
    mapper.custom_algorithms, mapper.org_projects) - code checks a permission, never
    a group name. Subclass and set `capability_name`/`error_code`/`denied_message`;
    don't instantiate directly. `message` is set dynamically so DRF's permission-denied
    response carries a structured `error_code`, not just a generic string `detail`.
    """
    capability_name = None
    error_code = 'mapper_access_denied'
    denied_message = 'You do not have access to the Mapper.'

    def has_permission(self, request, view):
        user = request.user
        if user and user.is_authenticated and user.has_perm(self.capability_name):
            return True
        self.message = {'detail': self.denied_message, 'error_code': self.error_code}
        return False


class CanUseMapper(HasMapperCapability):
    capability_name = MAPPER_USE_PERMISSION


class CanUseMapperAIAssistant(HasMapperCapability):
    capability_name = MAPPER_AI_ASSISTANT_PERMISSION
    error_code = 'mapper_ai_assistant_denied'
    denied_message = 'The AI Assistant is not available for your account.'


class CanUseCustomMapperAlgorithms(HasMapperCapability):
    """
    Gate is conditional on the payload, not blanket-required: a request with no
    custom algorithm entry in `algorithms` is always allowed, regardless of
    whether the user holds mapper.custom_algorithms. Only a request that
    actually asks for a custom algorithm is checked against the permission.
    """
    capability_name = MAPPER_CUSTOM_ALGORITHMS_PERMISSION
    error_code = 'mapper_custom_algorithms_denied'
    denied_message = 'Externally hosted algorithms are not available in preview.'

    @staticmethod
    def _has_custom_algorithm(algorithms):
        if isinstance(algorithms, str):
            import json
            try:
                algorithms = json.loads(algorithms)
            except ValueError:
                return False
        return bool(algorithms) and any(isinstance(a, dict) and a.get('type') == 'custom' for a in algorithms)

    def has_permission(self, request, view):
        if not self._has_custom_algorithm(request.data.get('algorithms')):
            return True
        return super().has_permission(request, view)


class CanCreateOrgMapProjects(HasMapperCapability):
    """
    Gate is conditional on the route, not blanket-required: map-project urls are
    included under both `<org>/map-projects/` and `<user>/map-projects/` (see
    core/map_projects/urls.py); the presence of the `org` URL kwarg is what makes
    a request org-scoped; a user-scoped request is always allowed through here.
    """
    capability_name = MAPPER_ORG_PROJECTS_PERMISSION
    error_code = 'mapper_org_projects_denied'
    denied_message = 'Organization-owned map projects are not available in preview.'

    def has_permission(self, request, view):
        if 'org' not in view.kwargs:
            return True
        return super().has_permission(request, view)


class HasMapProjectParentOwnership(BasePermission):
    """
    For map-project creation: the parent (org or user) the project would be
    created under isn't an object DRF's generic view machinery resolves via
    get_object() - it's resolved onto the view by ConceptDictionaryCreateMixin.
    set_parent_resource(), which normally only runs from post()/create(), after
    permissions are checked. Resolve it here too so the ownership check (same
    rule as HasOwnership.has_object_permission) runs as a request-level
    permission instead of view-method code.
    """
    def has_permission(self, request, view):
        view.set_parent_resource()
        parent_resource = view.parent_resource
        if not parent_resource:
            return False
        return HasOwnership().has_object_permission(request, view, parent_resource)


class HasMapProjectCapacity(BasePermission):
    """
    mapper.projects: how many map projects a user may have. Only meaningful at
    creation time - unlike the other three Mapper gates this isn't a plain
    permission check but a numeric usage comparison, so it lives here instead
    of as a view-method-level cap check.
    """
    def has_permission(self, request, view):
        user = request.user
        limit = user.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID)
        if limit == 0:  # explicit grant only - None (unconfigured) is blocked, not uncapped
            return True
        used = user.map_projects_used
        if limit is None or used >= limit:
            raise MapProjectCapacityExceeded(limit, used)
        return True
