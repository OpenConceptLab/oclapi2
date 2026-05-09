"""
Lexical Variant Dictionary lookup.

Loads a dictionary Source (one Concept per equivalence class, with each variant
as a Name on that Concept) and provides token-level variant lookup for query
expansion in concept search and matching.

The dictionary lives as a normal OCL Source (e.g. ocl/lexical-variants-en),
giving it versioning, release management, locale handling, and editability
through OCL's existing infrastructure.
"""
from dataclasses import dataclass
from threading import Lock

from django.conf import settings


DEFAULT_LEXICAL_VARIANTS_REPO = getattr(
    settings, 'DEFAULT_LEXICAL_VARIANTS_REPO', '/orgs/OCL/sources/lexical-variants-en/'
)


@dataclass(frozen=True)
class LexicalVariant:
    term: str
    name_type: str
    locale: str
    source_concept_uri: str


_cache: dict = {}
_cache_lock = Lock()


def _resolve_source(source_uri):
    from core.sources.models import Source
    if not source_uri:
        return None
    repo, _ = Source.resolve_reference_expression(source_uri)
    return repo if repo and repo.id else None


def _load_dictionary(source):
    from django.db.models import F
    from core.concepts.models import ConceptName

    names = ConceptName.objects.filter(
        concept__parent_id=source.id,
        concept__id=F('concept__versioned_object_id'),
        concept__retired=False,
        concept__is_active=True,
    ).select_related('concept')

    by_concept: dict = {}
    for cn in names:
        by_concept.setdefault(cn.concept_id, []).append(cn)

    index: dict = {}
    for group in by_concept.values():
        for source_name in group:
            siblings = [n for n in group if n.id != source_name.id]
            if not siblings:
                continue
            key = source_name.name.strip().lower()
            if not key:
                continue
            variants = [
                LexicalVariant(
                    term=sib.name,
                    name_type=sib.type or '',
                    locale=sib.locale or '',
                    source_concept_uri=sib.concept.uri,
                )
                for sib in siblings
            ]
            index.setdefault(key, []).extend(variants)
    return index


def _cache_key(source):
    return (source.uri, getattr(source, 'version', 'HEAD') or 'HEAD')


def _get_index(source):
    key = _cache_key(source)
    with _cache_lock:
        index = _cache.get(key)
        if index is None:
            index = _load_dictionary(source)
            _cache[key] = index
    return index


def invalidate_cache(source_uri=None):
    """Clear cached dictionary contents. Call after a Source version changes."""
    with _cache_lock:
        if source_uri is None:
            _cache.clear()
        else:
            for key in list(_cache.keys()):
                if key[0] == source_uri:
                    del _cache[key]


def _tokenize(text):
    if not text:
        return []
    cleaned = ''.join(ch if ch.isalnum() or ch.isspace() else ' ' for ch in text.lower())
    return [tok for tok in cleaned.split() if tok]


def get_lexical_variants(text, source_uri=None):
    """
    Return lexical variants for `text` looked up in the dictionary at
    `source_uri` (defaults to settings.DEFAULT_LEXICAL_VARIANTS_REPO).

    Tokenizes input, looks each token up in the dictionary's Names, and returns
    the sibling Names on each matching Concept. Returns [] if the dictionary
    Source can't be resolved or the token has no entry — never raises.
    """
    if not text:
        return []
    source = _resolve_source(source_uri or DEFAULT_LEXICAL_VARIANTS_REPO)
    if source is None:
        return []
    try:
        index = _get_index(source)
    except Exception:  # pylint: disable=broad-except
        return []

    seen = set()
    out: list = []
    for token in _tokenize(text):
        for variant in index.get(token, []):
            dedup_key = (variant.term, variant.locale)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            out.append(variant)
    return out


def get_variant_terms(text, source_uri=None):
    """Convenience wrapper returning just the variant strings, deduplicated."""
    seen = set()
    out: list = []
    for variant in get_lexical_variants(text, source_uri=source_uri):
        if variant.term not in seen:
            seen.add(variant.term)
            out.append(variant.term)
    return out
