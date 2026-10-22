"""
Adapter between Mappings and the ``names`` resolver (``core.resolvers.names``).

Picks, for each side (`from` / `to`) of a mapping, the Source (version) its code must be resolved against and projects
the resolver result onto the mapping. The grammar and the lookups live in the resolver; nothing here is persisted.
"""
from pydash import get

from core.common.constants import HEAD
from core.common.utils import decode_string
from core.resolvers.core import user_key
from core.resolvers.names import NamesResolver

SIDES = ('from', 'to')
RESOLVED_NAMES_ATTR = '_resolved_concept_names'


def context_user(context):
    """The user of a serializer context. Without one the caller is anonymous: access is never elevated."""
    return get(context, 'request.user') or get(context, 'user')


def _find_source(mapping, side, cache):
    """
    The Source (version) the code of `side` belongs to: the declared version when there is one, otherwise HEAD.
    The stored FK / URL identify the repo; a version declared in the mapping always wins over the HEAD FK.
    Returns None when the repo cannot be identified locally.
    """
    from core.sources.models import Source
    version = getattr(mapping, f'{side}_source_version') or HEAD
    source_id = getattr(mapping, f'{side}_source_id')
    if source_id:
        head = getattr(mapping, f'{side}_source')
        if version == HEAD:
            return head
        key = ('version', head.id, version)
        if key not in cache:
            cache[key] = head.active_versions.filter(version=version).first()
        return cache[key]

    url = getattr(mapping, f'{side}_source_url')
    if not url:
        return None
    key = ('url', url, version)
    if key not in cache:
        source, _ = Source.resolve_reference_expression(url, None, version)
        cache[key] = source if source.id else None
    return cache[key]


def _target(mapping, side, cache):
    """
    `(source, logical_code)` the side must be resolved against, or None when the stored link already gives the name:
    a concept linked to the mapping without a declared source version is the very concept the resolver would find.
    """
    code = getattr(mapping, f'{side}_concept_code')
    if not code:
        return None
    declared_version = getattr(mapping, f'{side}_source_version')
    if getattr(mapping, f'{side}_concept_id') and (not declared_version or declared_version == HEAD):
        return None
    source = _find_source(mapping, side, cache)
    # stored codes are percent-encoded once; the resolver works on logical codes
    return (source, decode_string(code, plus=False)) if source else None


def _resolve_targets(targets, user):
    """Resolved names keyed by `(source id, code)`: one resolver call per distinct target Source (version)."""
    wanted = {}  # source id -> (source, {code})
    for target in targets:
        if target:
            source, code = target
            wanted.setdefault(source.id, (source, set()))[1].add(code)

    resolved = {}
    resolver_cache = {}
    for source_id, (source, codes) in wanted.items():
        ordered_codes = sorted(codes)
        for code, name in zip(ordered_codes, NamesResolver(source, user, resolver_cache).resolve(ordered_codes)):
            resolved[(source_id, code)] = name
    return resolved


def load_resolved_names(mappings, sides, user):
    """
    Resolves, for a whole page, the names of the given sides and keeps them on each mapping.
    Not saved anywhere; scoped to the user it was computed for.
    """
    cache = {}
    targets = [(mapping, side, _target(mapping, side, cache)) for mapping in mappings for side in sides]
    resolved = _resolve_targets([target for _, _, target in targets], user)

    owner = user_key(user)
    for mapping, side, target in targets:
        names = mapping.__dict__.get(RESOLVED_NAMES_ATTR)
        if not names or names['user'] != owner:
            names = mapping.__dict__[RESOLVED_NAMES_ATTR] = {'user': owner}
        names[side] = resolved.get((target[0].id, target[1])) if target else None


def get_resolved_name(mapping, side, user):
    """
    The name of the `side` concept computed by the resolver (exact match or composition), or None when the serializer
    must keep its stored/legacy value. Loads the single mapping on demand when no page loader prepared it.
    """
    names = mapping.__dict__.get(RESOLVED_NAMES_ATTR)
    if not names or names['user'] != user_key(user) or side not in names:
        load_resolved_names([mapping], (side,), user)
        names = mapping.__dict__[RESOLVED_NAMES_ATTR]
    return names[side]
