"""
The write checkpoint: a capability policy may keep content read-only and refuse writes to it, with a body it sends
(CapabilityPolicy.get_write_refusal). A call site asks only once the user's own access allows the write. The default
policy refuses nothing.
"""
from time import monotonic

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
    private repository row (its HEAD, or a version whose HEAD others can view) viewable by others publishes it, when
    the write applies `public_access` at all. Anything else is an edit.
    """
    if method == 'DELETE':
        return WRITE_ACTION_EDIT if is_repository(obj) and not obj.is_head else WRITE_ACTION_DELETE
    public_access = data.get('public_access') if isinstance(data, dict) else None
    if applies_public_access and is_repository(obj) and makes_viewable(obj, public_access):
        return WRITE_ACTION_PUBLISH
    return WRITE_ACTION_EDIT


def makes_viewable(row, public_access):
    """Whether setting `public_access` lets others view repository row `row` (is_publicly_viewable, once it's set)"""
    if row.public_can_view or not isinstance(public_access, str) or not row.is_public_access(public_access):
        return False
    head = None if row.is_head else row.head
    return head is None or head.public_can_view


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


class BatchWriteRefusals:
    """
    The write checks of one batch (an import's lines): one capability policy, asked once per user, saved repository
    row and action, then again once that answer is `answer_seconds` old. An import line never changes the access of a
    row that already exists; anything else that changes the policy's answer reaches a long batch within that time.
    """
    answer_seconds = 60

    def __init__(self):
        self.policy = get_capability_policy()
        self.answers = {}  # (refusal, when it was asked) by question

    def get(self, user, content, action):
        """get_write_refusal, kept a while for a saved row; a row not saved yet (a new version) is asked every time."""
        if content is None or content.pk is None:
            return get_write_refusal(user, content, action, self.policy)
        key = (user.pk, type(content), content.pk, action)
        now = monotonic()
        if key not in self.answers or now - self.answers[key][1] >= self.answer_seconds:
            # asked about the row as stored now: a batch keeps the rows it loaded, and their access may have changed
            current = type(content).objects.filter(pk=content.pk).first() or content
            self.answers[key] = (get_write_refusal(user, current, action, self.policy), now)
        return self.answers[key][0]


def check_write(user, content, action, policy=None):
    """Raises WriteRefused, a 403 with the policy's body, when the policy refuses `user` this write to `content`."""
    refusal = get_write_refusal(user, content, action, policy)
    if refusal is not None:
        raise WriteRefused(refusal)
