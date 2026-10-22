"""
Base of the read-time resolvers a Source declares in ``Source.resolvers`` (one key per resolver family).

A resolver answers on read, from the content of ONE concrete Source / Source version, and never writes anything.
``names`` is the first family; ``concepts`` and ``mappings`` (see DOCS_RESOLVERS.md) will subclass ``Resolver`` and be
added to ``get_resolver_classes`` when they are built.
"""
import unicodedata

from django.core.exceptions import ValidationError
from pydash import get

from core.common.utils import encode_string

# Server-side safety limit of one batched concept lookup. The Source configuration can never raise it.
MAX_LOOKUP_CHUNK = 500


def get_resolver_classes():
    """The resolver families a Source can declare, by their key in ``Source.resolvers``."""
    from core.resolvers.names import NamesResolver
    return {NamesResolver.key: NamesResolver}


def validate_resolvers(resolvers):
    """
    Validates a ``Source.resolvers`` value: ``None`` (no resolver) or an object whose keys are resolver families, each
    holding a valid configuration. Raises ``ValidationError`` keyed by ``resolvers`` naming the first problem found.
    """
    if resolvers is None:
        return
    resolver_classes = get_resolver_classes()
    try:
        check_keys(resolvers, 'resolvers', resolver_classes)
        for key, config in resolvers.items():
            resolver_classes[key].validate(config, f'resolvers.{key}')
    except ValueError as ex:
        raise ValidationError({'resolvers': [str(ex)]}) from ex


def check_keys(value, path, allowed, required=()):
    """``value`` must be an object with only ``allowed`` keys and every ``required`` one."""
    if not isinstance(value, dict):
        raise ValueError(f'{path} must be an object')
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f'{path} has unsupported keys: {sorted(unknown)}')
    missing = set(required) - set(value)
    if missing:
        raise ValueError(f'{path} is missing keys: {sorted(missing)}')


def check_list(value, path, keys, min_items, max_items):
    """``value`` must be a list of ``min_items`` to ``max_items`` objects holding exactly ``keys``."""
    if not isinstance(value, list) or not min_items <= len(value) <= max_items:
        raise ValueError(f'{path} must be a list of {min_items} to {max_items} items')
    for index, item in enumerate(value):
        check_keys(item, f'{path}[{index}]', keys, keys)


def check_string(value, path, min_length, max_length, allow_whitespace=True):
    """Strict string: no coercion, bounded length, no control characters and, if asked, no whitespace."""
    if not isinstance(value, str):
        raise ValueError(f'{path} must be a string')
    if not min_length <= len(value) <= max_length:
        raise ValueError(f'{path} must have between {min_length} and {max_length} characters')
    if any(unicodedata.category(char).startswith('C') for char in value):
        raise ValueError(f'{path} must not contain control characters')
    if not allow_whitespace and any(char.isspace() for char in value):
        raise ValueError(f'{path} must not contain whitespace')


def user_key(user):
    """Cache key of a user: its id when authenticated, None for an anonymous caller."""
    return get(user, 'id') if get(user, 'is_authenticated') else None


class Resolver:
    """
    A resolver family bound to one concrete Source (version) and one user.

    ``cache`` is an optional dict owned by the caller (one request, one user). Resolvers sharing it check access and
    look up each concept once.
    """
    key = None  # key of the family in ``Source.resolvers``

    def __init__(self, source, user, cache=None):
        self.source = source
        self.user = user
        self.cache = {} if cache is None else cache
        self.config = self.get_config(source)

    @classmethod
    def validate(cls, config, path):
        """Raises ``ValueError`` naming the first problem of ``config``; ``path`` prefixes the message."""
        raise NotImplementedError

    @classmethod
    def get_config(cls, source):
        """The stored config of the family, or None when absent or not valid: a stored invalid value never raises."""
        config = get(source.resolvers, cls.key) if isinstance(source.resolvers, dict) else None
        if config is None:
            return None
        try:
            cls.validate(config, cls.key)
        except ValueError:
            return None
        return config

    def has_view_access(self):
        """Whether the user can see the Source (version); checked once per cache."""
        key = (self.source.id, user_key(self.user))
        access = self.cache.setdefault('access', {})
        if key not in access:
            access[key] = bool(self.source.has_view_access(self.user))
        return access[key]

    @staticmethod
    def stored_mnemonics(code):
        """
        Concept mnemonics hold reserved characters percent-encoded (``1B1Y%26XK5A``), so a logical code is looked up
        as typed and in its stored form. The code is encoded once, never decoded repeatedly.
        """
        encoded = encode_string(code, safe=' ')
        return (code, ) if encoded == code else (code, encoded)

    def load_concepts(self, codes):
        """
        Fills ``cache['concepts'][(source id, mnemonic)]`` for every stored form of the codes, with one batched query
        per chunk: None when the Source (version) holds no such active concept, otherwise its name and retired flag.
        """
        mnemonics = {mnemonic for code in codes for mnemonic in self.stored_mnemonics(code)}
        concepts = self.cache.setdefault('concepts', {})
        pending = sorted({mnemonic for mnemonic in mnemonics if (self.source.id, mnemonic) not in concepts})
        for start in range(0, len(pending), MAX_LOOKUP_CHUNK):
            chunk = pending[start:start + MAX_LOOKUP_CHUNK]
            queryset = self.source.get_concepts_queryset().filter(
                mnemonic__in=chunk, is_active=True
            ).select_related('parent').prefetch_related('names')
            for concept in queryset:
                concepts[(self.source.id, concept.mnemonic)] = {
                    'name': concept.display_name, 'retired': concept.retired}
            for mnemonic in chunk:
                concepts.setdefault((self.source.id, mnemonic), None)

    def find_concept(self, code):
        """The loaded concept of a logical code, trying each stored form; None when absent."""
        concepts = self.cache.get('concepts', {})
        for mnemonic in self.stored_mnemonics(code):
            concept = concepts.get((self.source.id, mnemonic))
            if concept:
                return concept
        return None
