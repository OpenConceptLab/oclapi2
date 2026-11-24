from pydash import get
from rest_framework.permissions import BasePermission, SAFE_METHODS

from core.capabilities.constants import MAPPER_PROJECTS_CAPABILITY_ID, WRITE_ACTION_EDIT
from core.capabilities.exceptions import MapProjectCapacityExceeded
from core.capabilities.policy import get_capability_policy
from core.capabilities.writes import check_write, get_own_write_action, get_write_target
from core.users.constants import (
    MAPPER_CUSTOM_ALGORITHMS_PERMISSION, MAPPER_ORG_PROJECTS_PERMISSION, MAPPER_USE_PERMISSION,
)


class HasPrivateAccess(BasePermission):
    """
    Current user is authenticated as a staff user, or is designated as the referenced object's owner,
    or belongs to an organization that is designated as the referenced object's owner,
    or belongs to the referenced organization itself.
    """
    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_staff:
            return True
        if user.is_authenticated:
            from core.orgs.models import Organization
            if hasattr(obj, 'parent_id') and user == obj.parent:
                return True
            if isinstance(obj, Organization) and user.organizations.filter(id=obj.id).exists():
                return True
            organization_id = getattr(obj, 'organization_id', None)
            if organization_id and user.organizations.filter(id=organization_id).exists():
                return True
        return False


class HasOwnership(BasePermission):
    """
    The user is staff, or the referenced user, or a member of the referenced organization.
    Any other object is refused to everyone but staff.
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
        return False


class CanAdministerOrganization(BasePermission):
    """
    The user is staff or an admin of the referenced organization. Its repositories don't need this: any member
    can edit them.
    """
    def has_object_permission(self, request, view, obj):
        return obj.has_admin_access(request.user)


class CanViewConceptDictionary(HasPrivateAccess):
    """
    The user can view this source. A version is viewable only while its HEAD is too, as a version keeps the
    access it was created with when the HEAD is made private later.
    """

    def has_object_permission(self, request, view, obj):
        if get(obj, 'is_publicly_viewable', obj.public_can_view):
            return True

        return super().has_object_permission(request, view, obj)


class ChecksWrites(BasePermission):
    """
    Listed before the permission it extends: once that permission grants a write to a repository or a map project,
    the capability policy is asked too (core.capabilities.writes), and may refuse it with a WriteRefused 403 that
    carries its body. Reads aren't asked about, nor writes from a view with `writes_content = False`, whose writes
    change no content (a processing flag, say).
    """
    def has_object_permission(self, request, view, obj):
        if not super().has_object_permission(request, view, obj):
            return False
        if request.method not in SAFE_METHODS and getattr(view, 'writes_content', True):
            content = get_write_target(self.get_written_object(request, view, obj))
            if content is not None:
                check_write(request.user, content, self.get_write_action(request, view, obj))
        return True

    @staticmethod
    def get_written_object(request, view, obj):
        """
        `obj`, unless the view sets `updates_head = True` because its PUT and PATCH save onto the HEAD of the
        repository row they resolve, whichever row that is (a FHIR update, which also adds a version).
        """
        if getattr(view, 'updates_head', False) and request.method in ('PUT', 'PATCH') and get(obj, 'head'):
            return obj.head
        return obj

    def get_write_action(self, request, view, obj):
        """
        A view whose PUT, PATCH and DELETE act on the object they're checked against sets `writes_own_object = True`:
        a DELETE of a repository's HEAD or of a map project deletes the whole of it, and a change can publish a
        repository row (core.capabilities.writes.get_own_write_action), unless the view sets
        `applies_public_access = False` because its writes never apply the `public_access` they're sent. Any other
        write is an edit.
        """
        if getattr(view, 'writes_own_object', False):
            # a DELETE's body is never read: it needn't parse
            data = None if request.method == 'DELETE' else request.data
            return get_own_write_action(obj, request.method, data, getattr(view, 'applies_public_access', True))
        return WRITE_ACTION_EDIT


class CanEditConceptDictionary(ChecksWrites, HasPrivateAccess):
    """
    The user is staff, the repo's owner, or a member of the owning organization, and the capability policy doesn't
    refuse the write (ChecksWrites).
    """


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
                request.user.is_authenticated and bool(versioned_object.organization_id) and
                request.user.organizations.filter(id=versioned_object.organization_id).exists()
        )


class CanEditConceptDictionaryVersion(ChecksWrites, HasAccessToVersionedObject):
    """
    HasAccessToVersionedObject, and the capability policy doesn't refuse the write to the version (an edit, unless the
    view writes its own object: ChecksWrites.get_write_action).
    """


class CanViewConceptDictionaryVersion(HasAccessToVersionedObject):
    """
    The user can view this source
    """

    def has_object_permission(self, request, view, obj):
        if get(obj, 'is_publicly_viewable', obj.public_can_view):
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
        # is_staff is exempted the same way get_capability_limit() exempts it from every
        # numeric cap ("the deliberate exception") - otherwise a staff account gets
        # unlimited quota at the meter but a 403 at this gate, unable to reach the
        # endpoint it has budget for.
        if user and user.is_authenticated and (user.is_staff or user.has_perm(self.capability_name)):
            return True
        self.message = {'detail': self.denied_message, 'error_code': self.error_code}
        return False


class CanUseMapper(HasMapperCapability):
    capability_name = MAPPER_USE_PERMISSION


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
    mapper.projects: how many map projects a user may have under the owner they create
    one for. Only meaningful at creation time - unlike the other three Mapper gates
    this isn't a plain permission check but a numeric usage comparison, so it lives
    here instead of as a view-method-level cap check.
    """
    def has_permission(self, request, view):
        # the parent the project is created under is its owner; HasMapProjectParentOwnership, listed first, resolves it
        # and checks the user may create there
        check_map_project_capacity(request.user, get_capability_policy(), getattr(view, 'parent_resource', None))
        return True


def check_map_project_capacity(user, policy, owner):
    """
    Raises MapProjectCapacityExceeded unless `user` may create one more map project under `owner`.
    Explicit grant only - None (unconfigured) is blocked, not uncapped. Kill switch = a limit of -1; 0 is unlimited.
    """
    limit = policy.get_limit(user, MAPPER_PROJECTS_CAPABILITY_ID, owner=owner)
    if limit == 0:
        return
    used = policy.get_usage(user, MAPPER_PROJECTS_CAPABILITY_ID, owner=owner)['used']
    if limit is None or used >= limit:
        raise MapProjectCapacityExceeded(limit, used)
