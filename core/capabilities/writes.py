"""
The write checkpoint: a capability policy may keep content read-only and refuse writes to it, with a body it sends
(CapabilityPolicy.get_write_refusal). A call site asks only once the user's own access allows the write. The default
policy refuses nothing.
"""
from core.capabilities.constants import WRITE_ACTION_DELETE, WRITE_ACTION_EDIT, WRITE_ACTION_PUBLISH
from core.capabilities.exceptions import WriteRefused
from core.capabilities.policy import get_capability_policy


def is_repository(obj):
    from core.collections.models import Collection
    from core.sources.models import Source
    return isinstance(obj, (Source, Collection))


def get_write_target(obj):
    """
    What a write to `obj` is asked about: the repository row it changes (its HEAD, or a version, which keeps its own
    public_access), or a map project. None for anything else.
    """
    from core.map_projects.models import MapProject
    if is_repository(obj) or isinstance(obj, MapProject):
        return obj
    return None


def get_own_write_action(obj, method, data, applies_public_access=True):
    """
    The action of a write to `obj` itself, not to something it holds. A DELETE of a repository's HEAD or of a map
    project deletes the whole of it; a DELETE of another version is an edit of the repository. A change that leaves a
    private repository row (its HEAD or a version) viewable by others publishes it, when the write applies
    `public_access` at all. Anything else is an edit.
    """
    if method == 'DELETE':
        return WRITE_ACTION_EDIT if is_repository(obj) and not obj.is_head else WRITE_ACTION_DELETE
    public_access = data.get('public_access') if isinstance(data, dict) else None
    if applies_public_access and is_repository(obj) and not obj.public_can_view and isinstance(public_access, str) \
            and obj.is_public_access(public_access):
        return WRITE_ACTION_PUBLISH
    return WRITE_ACTION_EDIT


def get_write_refusal(user, content, action, policy=None):
    """
    The body the policy refuses `user` this write to `content` with, or None when it allows it (or there's no content
    to ask about). A batch passes one `policy` for all its writes.
    """
    if content is None:
        return None
    if policy is None:
        policy = get_capability_policy()
    return policy.get_write_refusal(user, content, action)


def check_write(user, content, action, policy=None):
    """Raises WriteRefused, a 403 with the policy's body, when the policy refuses `user` this write to `content`."""
    refusal = get_write_refusal(user, content, action, policy)
    if refusal is not None:
        raise WriteRefused(refusal)
