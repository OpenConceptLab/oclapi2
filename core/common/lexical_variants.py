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

from django.conf import settings
from django.core.cache import cache


@dataclass(frozen=True)
class LexicalVariant:
    term: str
    name_type: str
    locale: str
    source_concept_uri: str


class LexicalVariantDictionary:
    CACHE_KEY_PREFIX = 'lexical_variants'
    CACHE_TIMEOUT = settings.LEXICAL_VARIANTS_CACHE_TIMEOUT

    @classmethod
    def get_lexical_variants(cls, text, source_uri=None):
        """
        Return lexical variants for `text` looked up in the dictionary at
        `source_uri` (defaults to settings.DEFAULT_LEXICAL_VARIANTS_REPO).

        Tokenizes input, looks each token up in the dictionary's Names, and returns
        the sibling Names on each matching Concept. Returns [] if the dictionary
        Source can't be resolved or the token has no entry — never raises.
        """
        if not text:
            return []
        source = cls._resolve_source(source_uri or settings.DEFAULT_LEXICAL_VARIANTS_REPO)
        if source is None:
            return []
        try:
            index = cls._get_index(source)
        except Exception:  # pylint: disable=broad-except
            return []

        seen = set()
        out = []
        for token in cls.tokenize(text):
            for variant in index.get(token, []):
                dedup_key = (variant.term, variant.locale)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                out.append(variant)
        return out

    @classmethod
    def get_variant_terms(cls, text, source_uri=None):
        """Convenience wrapper returning just the variant strings, deduplicated."""
        seen = set()
        out = []
        for variant in cls.get_lexical_variants(text, source_uri=source_uri):
            if variant.term not in seen:
                seen.add(variant.term)
                out.append(variant.term)
        return out

    @classmethod
    def _cache_key(cls, source):
        # HEAD edits reuse the same cache key and may stay stale until TTL expiry.
        version = getattr(source, 'version', 'HEAD') or 'HEAD'
        return f'{cls.CACHE_KEY_PREFIX}|{source.uri}|{version}'

    @classmethod
    def invalidate_cache(cls, source_uri=None):
        """Clear cached dictionary contents. Call after a Source version changes."""
        pattern = f'{cls.CACHE_KEY_PREFIX}|'
        pattern += '*' if source_uri is None else f'{source_uri}|*'
        cache.delete_pattern(pattern)

    @classmethod
    def _get_index(cls, source):
        key = cls._cache_key(source)
        raw = cache.get(key)
        if raw is None:
            index = cls._load_dictionary(source)
            cache.set(key, cls._serialize_index(index), timeout=cls.CACHE_TIMEOUT)
            return index
        return cls._deserialize_index(raw)

    @staticmethod
    def _resolve_source(source_uri):
        from core.sources.models import Source
        if not source_uri:
            return None
        repo, _ = Source.resolve_reference_expression(source_uri)
        return repo if repo and repo.id else None

    @staticmethod
    def _load_dictionary(source):
        from django.db.models import F
        from core.concepts.models import ConceptName

        names = ConceptName.objects.filter(
            concept__parent_id=source.id,
            concept__id=F('concept__versioned_object_id'),
            concept__retired=False,
            concept__is_active=True,
        ).select_related('concept')

        by_concept = {}
        for cn in names:
            by_concept.setdefault(cn.concept_id, []).append(cn)

        index = {}
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

    @staticmethod
    def _serialize_index(index):
        return {
            token: [
                {
                    'term': v.term,
                    'name_type': v.name_type,
                    'locale': v.locale,
                    'source_concept_uri': v.source_concept_uri,
                }
                for v in variants
            ]
            for token, variants in index.items()
        }

    @staticmethod
    def _deserialize_index(raw):
        return {
            token: [LexicalVariant(**d) for d in variants]
            for token, variants in raw.items()
        }

    @staticmethod
    def tokenize(text):
        """Return normalized alphanumeric tokens for variant matching."""
        return LexicalVariantDictionary._tokenize(text)

    @staticmethod
    def _tokenize(text):
        """Normalize text for token-based dictionary lookups."""
        if not text:
            return []
        cleaned = ''.join(ch if ch.isalnum() or ch.isspace() else ' ' for ch in text.lower())
        return [tok for tok in cleaned.split() if tok]
