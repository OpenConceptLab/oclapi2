"""
Markdown changelog generator for source version diffs.

Transforms $changelog JSON into a human-readable markdown document structured
after LOINC/SNOMED release notes. Requests with ``verbosity>=4`` include
enriched before/after details for names, descriptions, and mappings.

LLM-based "Release Note Highlights" and per-section narrative summaries are
intentionally NOT implemented here.
# TODO: Integrate litellm (anthropic/claude-haiku-4.5) once ANTHROPIC_API_KEY
#       is available in this deployment.  Replace _static_highlight() calls with
#       LLM completions (max_tokens=1000 for the top-level highlight,
#       max_tokens=500 per section highlight, temperature=0.3, English output).
"""

from datetime import date
from urllib.parse import unquote

from django.conf import settings


class ChangelogMarkdownGenerator:
    """
    Generates a markdown changelog from enriched $changelog JSON data.

    Usage::

        generator = ChangelogMarkdownGenerator(changelog_data)
        markdown_string = generator.generate()

    The ``changelog_data`` dict is the value returned by ``Source.changelog()``.
    With ``verbosity>=4`` it carries extra fields used for detail tables.  It has
    the shape::

        {
            "meta": {
                "version1": {"uri": "...", "concepts": N, "mappings": M},
                "version2": {"uri": "...", "concepts": N, "mappings": M},
            },
            "concepts": {
                "new": {"id": {"id": "...", "display_name": "...", "names": [...], ...}},
                "removed": {...},
                "changed_retired": {...},
                "changed_major": {...},
                "changed_minor": {...},
                "changed_mappings_only": {...},
            },
            "mappings": {
                "new": {"id": {"id": "...", "from_concept": "...", ...}},
                ...
            },
        }
    """

    def __init__(self, changelog_data, default_locale='en'):
        self.data = changelog_data
        self.meta = changelog_data.get('meta', {})
        self.concepts = changelog_data.get('concepts', {})
        self.mappings = changelog_data.get('mappings', {})
        self.default_locale = default_locale
        self._v1_meta = self.meta.get('version1', {})
        self._v2_meta = self.meta.get('version2', {})
        self._source_prefix = self._extract_source_prefix(self._v2_meta.get('uri', ''))
        self._mapping_collections_cache = None
        self.is_enriched = self._detect_enrichment()

    def _detect_enrichment(self):
        """
        Detect whether the input JSON carries verbosity>=4 enrichment data
        (names[], descriptions[], prev_*).  Drives section-by-section adaptive
        rendering so a low-verbosity input still yields a useful document
        (Summary + Added/Removed/Retired/Updated lists) rather than an empty one.
        """
        for key in ('new', 'changed_major', 'changed_minor', 'removed', 'changed_retired'):
            for info in (self.concepts.get(key) or {}).values():
                if info.get('names') is not None or info.get('prev_names') is not None:
                    return True
        for key in ('changed_major', 'changed_minor', 'new'):
            for m in (self.mappings.get(key) or {}).values():
                if self._mapping_is_enriched(m):
                    return True
        for concepts in self.concepts.values():
            if not isinstance(concepts, dict):
                continue
            for info in concepts.values():
                for mappings in (info.get('mappings') or {}).values():
                    for _, mapping in self._mapping_items(mappings):
                        if self._mapping_is_enriched(mapping):
                            return True
        return False

    @staticmethod
    def _mapping_is_enriched(mapping):
        """Return whether a mapping summary includes verbosity>=4 details."""
        return (
            mapping.get('external_id') is not None
            or mapping.get('prev_to_concept') is not None
            or mapping.get('prev_to_source') is not None
            or mapping.get('prev_map_type') is not None
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self):
        # Precompute row collections once — used by both TOC (to decide which
        # subsections to list) and the section renderers (to emit tables).
        empty_rows = ([], [], [])
        self._name_rows = self._collect_name_rows(self.default_locale, 'names') if self.is_enriched else empty_rows
        self._description_rows = self._collect_description_rows() if self.is_enriched else empty_rows
        self._translation_rows = self._collect_translation_rows() if self.is_enriched else empty_rows

        sections = [
            self._header(),
            self._notice(),
            self._overview(),
            self._summary_table(),
            self._toc(),
            self._concepts_section(),
            self._names_section(),
            self._descriptions_section(),
            self._translations_section(),
            self._mappings_section(),
        ]
        return '\n\n'.join(s for s in sections if s)

    def _visible_changed(self, items):
        """Filter out checksum-only changes when enriched data lets us detect them."""
        return (
            {cid: info for cid, info in items.items() if self._changed_axes(info)}
            if self.is_enriched else items
        )

    @staticmethod
    def _anchor(anchor_id):
        return f'<a id="{anchor_id}"></a>'

    def _notice(self):
        """Warn the reader when enrichment data is missing — only when it matters."""
        if self.is_enriched:
            return ''
        return (
            '> **Note:** this changelog was generated without enrichment data '
            '(`verbosity<4`). Name, description, translation, and before/after '
            'mapping details are not included. Re-run with `?verbosity=4` for '
            'full detail.'
        )

    # ------------------------------------------------------------------
    # Header / meta
    # ------------------------------------------------------------------

    def _header(self):
        v1_uri = self._v1_meta.get('uri', '')
        v2_uri = self._v2_meta.get('uri', '')
        v1_version = self._version_label(v1_uri)
        v2_version = self._version_label(v2_uri)
        today = date.today().isoformat()
        return (
            f'# {v2_version} Changelog\n\n'
            f'*Compared to {v1_version} — Generated on {today}*'
        )

    def _overview(self):
        """
        High-level version comparison table placed immediately after the header.

        Shows total concept and mapping counts for each version side-by-side with
        add/remove/change deltas so readers can instantly gauge the release scope.
        """
        v1_label = self._version_label(self._v1_meta.get('uri', ''))
        v2_label = self._version_label(self._v2_meta.get('uri', ''))

        v1_concepts = self._v1_meta.get('concepts', 0)
        v2_concepts = self._v2_meta.get('concepts', 0)
        v1_mappings = self._v1_meta.get('mappings', 0)
        v2_mappings = self._v2_meta.get('mappings', 0)

        concepts_added   = len(self.concepts.get('new') or {})
        # "Removed" in the overview encompasses both hard-removes and retirements.
        concepts_removed = (
            len(self.concepts.get('removed') or {}) +
            len(self.concepts.get('changed_retired') or {})
        )
        concepts_changed = (
            len(self.concepts.get('changed_major') or {}) +
            len(self.concepts.get('changed_minor') or {}) +
            len(self.concepts.get('changed_mappings_only') or {})
        )

        mappings_added, mappings_removed, mappings_changed = self._mapping_collections()

        lines = [
            '## Overview',
            '',
            f'**{v1_label} → {v2_label}**',
            '',
            f'| Category | {v1_label} | {v2_label} | Added | Removed | Changed |',
            '|----------|----------:|----------:|------:|--------:|--------:|',
            (
                f'| Concepts | {v1_concepts:,} | {v2_concepts:,} | {concepts_added:,} '
                f'| {concepts_removed:,} | {concepts_changed:,} |'
            ),
            (
                f'| Mappings | {v1_mappings:,} | {v2_mappings:,} | {len(mappings_added):,} '
                f'| {len(mappings_removed):,} | {len(mappings_changed):,} |'
            ),
            '',
            '---',
        ]
        return '\n'.join(lines)

    def _summary_table(self):
        concepts_new           = len(self.concepts.get('new') or {})
        concepts_removed       = len(self.concepts.get('removed') or {})
        concepts_retired       = len(self.concepts.get('changed_retired') or {})
        concepts_major         = len(self.concepts.get('changed_major') or {})
        concepts_minor         = len(self.concepts.get('changed_minor') or {})
        concepts_mappings_only = len(self.concepts.get('changed_mappings_only') or {})

        mappings_added, mappings_removed, _ = self._mapping_collections()

        v1_uri = self._v1_meta.get('uri', '')
        v2_uri = self._v2_meta.get('uri', '')
        base = getattr(settings, 'API_BASE_URL', '')
        diff_url = f'{base}/sources/$changelog/?version1={v1_uri}&version2={v2_uri}'

        lines = [
            '## Summary',
            '',
            '| Category | Count |',
            '|----------|------:|',
            f'| New concepts | {concepts_new:,} |',
            f'| Removed concepts | {concepts_removed:,} |',
            f'| Retired concepts | {concepts_retired:,} |',
            f'| Major changes | {concepts_major:,} |',
            f'| Minor changes | {concepts_minor:,} |',
            f'| Mapping-only changes | {concepts_mappings_only:,} |',
            f'| Mappings added | {len(mappings_added):,} |',
            f'| Mappings removed | {len(mappings_removed):,} |',
            '',
            f'[Download full JSON diff]({diff_url})',
            '',
            '---',
        ]
        return '\n'.join(lines)

    def _toc(self):
        entries = []
        for label, anchor, subs in self._toc_sections():
            if not subs:
                continue
            entries.append(f'- [{label}](#{anchor})')
            for sub_label, sub_anchor in subs:
                entries.append(f'  - [{sub_label}](#{sub_anchor})')
        if not entries:
            return ''
        return '\n'.join(['## Contents', '', *entries, '', '---'])

    def _toc_sections(self):
        """Return only post-Contents sections, each with visible subsections."""
        return [
            ('Concepts', 'concepts', self._concept_subsections()),
            ('Names', 'names', self._name_subsections()),
            ('Descriptions', 'descriptions', self._description_subsections()),
            ('Translations', 'translations', self._translation_subsections()),
            ('Mappings', 'mappings', self._mapping_subsections()),
        ]

    def _concept_subsections(self):
        subs = []
        if self.concepts.get('new'):
            subs.append(('Added', 'concepts-added'))
        if self.concepts.get('removed'):
            subs.append(('Removed', 'concepts-removed'))
        if self.concepts.get('changed_retired'):
            subs.append(('Retired', 'concepts-retired'))
        if self._visible_changed(self.concepts.get('changed_major') or {}):
            subs.append(('Updated (Major)', 'concepts-updated-major'))
        if self._visible_changed(self.concepts.get('changed_minor') or {}):
            subs.append(('Updated (Minor)', 'concepts-updated-minor'))
        return subs

    def _name_subsections(self):
        added, updated, removed = self._name_rows
        subs = []
        if added:
            subs.append(('Added', 'names-added'))
        if updated:
            subs.append(('Updated', 'names-updated'))
        if removed:
            subs.append(('Removed', 'names-removed'))
        return subs

    def _description_subsections(self):
        added, updated, removed = self._description_rows
        subs = []
        if added:
            subs.append(('Added', 'descriptions-added'))
        if updated:
            subs.append(('Updated', 'descriptions-updated'))
        if removed:
            subs.append(('Removed', 'descriptions-removed'))
        return subs

    def _translation_subsections(self):
        added, updated, removed = self._translation_rows
        subs = []
        if added:
            subs.append(('Added', 'translations-added'))
        if updated:
            subs.append(('Updated', 'translations-updated'))
        if removed:
            subs.append(('Removed', 'translations-removed'))
        return subs

    def _mapping_subsections(self):
        added, removed, changed = self._mapping_collections()
        subs = []
        if added:
            subs.append(('Added', 'mappings-added'))
        if removed:
            subs.append(('Removed', 'mappings-removed'))
        if changed:
            subs.append(('Updated', 'mappings-updated'))
        return subs

    # ------------------------------------------------------------------
    # Concepts section
    # ------------------------------------------------------------------

    def _concepts_section(self):
        if not self._has_concepts():
            return ''

        added = self.concepts.get('new') or {}
        removed = self.concepts.get('removed') or {}
        retired = self.concepts.get('changed_retired') or {}
        changed_major = self.concepts.get('changed_major') or {}
        changed_minor = self.concepts.get('changed_minor') or {}

        visible_major = self._visible_changed(changed_major)
        visible_minor = self._visible_changed(changed_minor)

        highlight = self._static_highlight(
            'Concepts',
            added=len(added),
            removed=len(removed),
            retired=len(retired),
            changed=len(visible_major) + len(visible_minor),
        )

        parts = ['## Concepts', '', f'*{highlight}*']

        if added:
            parts += ['', self._anchor('concepts-added'), '### Added', '']
            parts += self._concept_table(added)
        if removed:
            parts += ['', self._anchor('concepts-removed'), '### Removed', '']
            parts += self._concept_table(removed)
        if retired:
            parts += ['', self._anchor('concepts-retired'), '### Retired', '']
            parts += self._concept_table(retired)
        if visible_major:
            parts += ['', self._anchor('concepts-updated-major'), '### Updated (Major)', '']
            parts += self._concept_table(
                visible_major, show_changes=self.is_enriched, axes_as_links=self.is_enriched
            )
        if visible_minor:
            parts += ['', self._anchor('concepts-updated-minor'), '### Updated (Minor)', '']
            parts += self._concept_table(
                visible_minor, show_changes=self.is_enriched, axes_as_links=self.is_enriched
            )

        parts += ['', '---']
        return '\n'.join(parts)

    def _concept_table(self, concepts_dict, show_changes=False, axes_as_links=False):
        if show_changes:
            rows = [
                '| Concept ID | Display Name | Concept Class | Changed |',
                '|-----------:|-------------|---------------|---------|',
            ]
        else:
            rows = [
                '| Concept ID | Display Name | Concept Class |',
                '|-----------:|-------------|---------------|',
            ]
        for concept_id, info in concepts_dict.items():
            display = info.get('display_name') or ''
            concept_class = info.get('concept_class') or ''
            link = self._concept_link(concept_id)
            if show_changes:
                axes = self._changed_axes(
                    info,
                    as_links=axes_as_links,
                    concept_id=concept_id if axes_as_links else None
                )
                changed_str = ', '.join(axes) if axes else '—'
                rows.append(
                    f'| {link} | {self._escape(display)} | {self._escape(concept_class)} | {changed_str} |'
                )
            else:
                rows.append(f'| {link} | {self._escape(display)} | {self._escape(concept_class)} |')
        return rows

    @staticmethod
    def _names_changed(prev_names, curr_names, locale, invert=False):
        """
        Return True if there is any real name change between prev and curr for
        the given locale (or all non-locale names when invert=True).

        Uses external_id as stable key when available; falls back to comparing
        the full set of (type, locale, text) tuples otherwise.
        """
        def _matches(n):
            return n.get('locale') != locale if invert else n.get('locale') == locale

        prev = [n for n in prev_names if _matches(n)]
        curr = [n for n in curr_names if _matches(n)]

        prev_eid = {n['external_id']: n.get('name') for n in prev if n.get('external_id')}
        curr_eid = {n['external_id']: n.get('name') for n in curr if n.get('external_id')}

        # Any addition/removal/change in external_id space?
        if set(prev_eid) != set(curr_eid):
            return True
        for eid in prev_eid:
            if prev_eid[eid] != curr_eid.get(eid):
                return True

        # Fallback for names without external_id
        prev_fallback = frozenset(
            (n.get('type'), n.get('locale'), n.get('name'))
            for n in prev if not n.get('external_id')
        )
        curr_fallback = frozenset(
            (n.get('type'), n.get('locale'), n.get('name'))
            for n in curr if not n.get('external_id')
        )
        return prev_fallback != curr_fallback

    def _changed_axes(self, info, as_links=False, concept_id=None):
        """
        Compute which axes changed for a concept in changed_major/changed_minor.

        When ``as_links=True`` and ``concept_id`` is provided, each axis label
        becomes a markdown anchor link pointing directly to the first row for
        that concept in the relevant section table
        (e.g. ``[Names](#names-139061)``).

        Default-locale names and non-default-locale names (Translations) are
        checked separately so the link targets the correct section.
        Metadata has no dedicated section and is kept as plain text.
        """
        axes = []

        def _link(label, section):
            if as_links and concept_id:
                return f'[{label}](#{section}-{concept_id})'
            if as_links:
                return f'[{label}](#{section})'
            return label

        # Default-locale names → Names section
        if self._names_changed(info.get('prev_names') or [], info.get('names') or [], self.default_locale):
            axes.append(_link('Names', 'names'))

        # Non-default-locale names → Translations section
        if self._names_changed(info.get('prev_names') or [], info.get('names') or [], self.default_locale, invert=True):
            axes.append(_link('Translations', 'translations'))

        # Descriptions
        prev_descs = frozenset(
            (d.get('type'), d.get('locale'), d.get('description'))
            for d in info.get('prev_descriptions') or []
        )
        curr_descs = frozenset(
            (d.get('type'), d.get('locale'), d.get('description'))
            for d in info.get('descriptions') or []
        )
        if prev_descs != curr_descs:
            axes.append(_link('Descriptions', 'descriptions'))

        # Metadata (class or datatype changed) — no dedicated section
        if (
            info.get('prev_concept_class') is not None
            and info.get('concept_class') != info.get('prev_concept_class')
        ) or (
            info.get('prev_datatype') is not None
            and info.get('datatype') != info.get('prev_datatype')
        ):
            axes.append('Metadata')

        # Mappings
        if info.get('mappings'):
            axes.append(_link('Mappings', 'mappings'))

        return axes

    # ------------------------------------------------------------------
    # Names section
    # ------------------------------------------------------------------

    def _names_section(self):
        added_rows, updated_rows, removed_rows = self._name_rows
        if not (added_rows or updated_rows or removed_rows):
            return ''

        highlight = self._static_highlight(
            'Names',
            added=len(added_rows), updated=len(updated_rows), removed=len(removed_rows),
        )
        parts = ['## Names', '', f'*{highlight}*']
        if added_rows:
            parts += ['', self._anchor('names-added'), '### Added', '']
            parts += self._names_added_table(added_rows)
        if updated_rows:
            parts += ['', self._anchor('names-updated'), '### Updated', '']
            parts += self._names_updated_table(updated_rows)
        if removed_rows:
            parts += ['', self._anchor('names-removed'), '### Removed', '']
            parts += self._names_added_table(removed_rows)
        parts += ['', '---']
        return '\n'.join(parts)

    def _collect_name_rows(self, locale_filter, section='names'):
        """Return (added, updated, removed) rows for names at the given locale."""
        return self._collect_name_like_rows(
            lambda n: n.get('locale') == locale_filter, section
        )

    def _collect_translation_name_rows(self):
        """Return (added, updated, removed) rows for names in non-default locales."""
        return self._collect_name_like_rows(
            lambda n: n.get('locale') != self.default_locale, 'translations'
        )

    def _collect_name_like_rows(self, matches_locale, section):  # pylint: disable=too-many-locals,too-many-branches
        """
        Collect name add/update/remove rows grouped by change type.

        Comparison strategy for changed concepts:
          1. Primary: ``external_id`` is the stable key across versions. Same
             external_id with different text → Updated; only-in-v1 → Removed;
             only-in-v2 → Added.
          2. Fallback (for names lacking ``external_id``): group by
             ``(type, locale)`` and compare text sets. A single replacement
             within a slot (1 removed + 1 added) is treated as Updated.

        The first row per concept carries an HTML anchor so the Concepts "Changed"
        column can deep-link to it.
        """
        from collections import defaultdict

        added_rows, updated_rows, removed_rows = [], [], []
        anchored = set()

        def anchored_link(concept_id):
            base = self._concept_link(concept_id)
            if concept_id in anchored:
                return base
            anchored.add(concept_id)
            return f'<a id="{section}-{concept_id}"></a>{base}'

        def row_tuple(cid, name, ntype, locale):
            return (anchored_link(cid), name or '', ntype or '', locale or '')

        # Added/Removed from new/removed concepts (all their names at this locale)
        for concept_id, info in (self.concepts.get('new') or {}).items():
            for n in info.get('names') or []:
                if matches_locale(n):
                    added_rows.append(row_tuple(concept_id, n.get('name'), n.get('type'), n.get('locale')))
        for concept_id, info in (self.concepts.get('removed') or {}).items():
            for n in info.get('names') or []:
                if matches_locale(n):
                    removed_rows.append(row_tuple(concept_id, n.get('name'), n.get('type'), n.get('locale')))

        # Compare prev vs current names for changed concepts
        for key in ('changed_major', 'changed_minor'):
            for concept_id, info in (self.concepts.get(key) or {}).items():
                prev_names = [n for n in (info.get('prev_names') or []) if matches_locale(n)]
                curr_names = [n for n in (info.get('names') or []) if matches_locale(n)]

                prev_eid = {n['external_id']: n for n in prev_names if n.get('external_id')}
                curr_eid = {n['external_id']: n for n in curr_names if n.get('external_id')}

                for eid in set(prev_eid) | set(curr_eid):
                    p, c = prev_eid.get(eid), curr_eid.get(eid)
                    if p and c:
                        if p.get('name') != c.get('name'):
                            updated_rows.append((
                                anchored_link(concept_id),
                                p.get('name', ''), c.get('name', ''),
                                c.get('type') or '', c.get('locale') or '',
                            ))
                    elif c:
                        added_rows.append(row_tuple(concept_id, c.get('name'), c.get('type'), c.get('locale')))
                    else:
                        removed_rows.append(row_tuple(concept_id, p.get('name'), p.get('type'), p.get('locale')))

                # Fallback for names without external_id
                prev_no_eid = [n for n in prev_names if not n.get('external_id')]
                curr_no_eid = [n for n in curr_names if not n.get('external_id')]
                if not (prev_no_eid or curr_no_eid):
                    continue

                prev_by_key = defaultdict(set)
                for n in prev_no_eid:
                    prev_by_key[(n.get('type'), n.get('locale'))].add(n.get('name', ''))
                curr_by_key = defaultdict(set)
                for n in curr_no_eid:
                    curr_by_key[(n.get('type'), n.get('locale'))].add(n.get('name', ''))
                for (ntype, nloc) in set(prev_by_key) | set(curr_by_key):
                    prev_texts = prev_by_key.get((ntype, nloc), set())
                    curr_texts = curr_by_key.get((ntype, nloc), set())
                    if prev_texts == curr_texts:
                        continue
                    added_texts = curr_texts - prev_texts
                    removed_texts = prev_texts - curr_texts
                    if len(added_texts) == 1 and len(removed_texts) == 1:
                        updated_rows.append((
                            anchored_link(concept_id),
                            next(iter(removed_texts)), next(iter(added_texts)),
                            ntype or '', nloc or '',
                        ))
                    else:
                        for text in added_texts:
                            added_rows.append(row_tuple(concept_id, text, ntype, nloc))
                        for text in removed_texts:
                            removed_rows.append(row_tuple(concept_id, text, ntype, nloc))

        return added_rows, updated_rows, removed_rows

    @staticmethod
    def _names_added_table(rows):
        lines = [
            '| Concept ID | Name | Name Type | Locale |',
            '|-----------:|------|-----------|--------|',
        ]
        for link, name, name_type, locale in rows:
            lines.append(
                f'| {link} | {ChangelogMarkdownGenerator._escape(name)} '
                f'| {ChangelogMarkdownGenerator._escape(name_type)} | {locale} |'
            )
        return lines

    @staticmethod
    def _names_updated_table(rows):
        lines = [
            '| Concept ID | Previous Name | Updated Name | Name Type | Locale |',
            '|-----------:|--------------|-------------|-----------|--------|',
        ]
        for link, prev, curr, name_type, locale in rows:
            lines.append(
                f'| {link} | {ChangelogMarkdownGenerator._escape(prev)} '
                f'| {ChangelogMarkdownGenerator._escape(curr)} '
                f'| {ChangelogMarkdownGenerator._escape(name_type)} | {locale} |'
            )
        return lines

    # ------------------------------------------------------------------
    # Descriptions section
    # ------------------------------------------------------------------

    def _descriptions_section(self):
        added_rows, updated_rows, removed_rows = self._description_rows
        if not (added_rows or updated_rows or removed_rows):
            return ''

        highlight = self._static_highlight(
            'Descriptions',
            added=len(added_rows), updated=len(updated_rows), removed=len(removed_rows),
        )
        parts = ['## Descriptions', '', f'*{highlight}*']
        if added_rows:
            parts += ['', self._anchor('descriptions-added'), '### Added', '']
            parts += self._descriptions_added_table(added_rows)
        if updated_rows:
            parts += ['', self._anchor('descriptions-updated'), '### Updated', '']
            parts += self._descriptions_updated_table(updated_rows)
        if removed_rows:
            parts += ['', self._anchor('descriptions-removed'), '### Removed', '']
            parts += self._descriptions_added_table(removed_rows)
        parts += ['', '---']
        return '\n'.join(parts)

    def _collect_description_rows(self):  # pylint: disable=too-many-locals
        added_rows = []
        updated_rows = []
        removed_rows = []
        anchored = set()

        def anchored_link(concept_id):
            base = self._concept_link(concept_id)
            if concept_id not in anchored:
                anchored.add(concept_id)
                return f'<a id="descriptions-{concept_id}"></a>{base}'
            return base

        for concept_id, info in (self.concepts.get('new') or {}).items():
            first = True
            for desc in info.get('descriptions') or []:
                link = anchored_link(concept_id) if first else self._concept_link(concept_id)
                first = False
                added_rows.append((link, desc.get('description', ''), desc.get('type', ''), desc.get('locale', '')))

        for concept_id, info in (self.concepts.get('removed') or {}).items():
            first = True
            for desc in info.get('descriptions') or []:
                link = anchored_link(concept_id) if first else self._concept_link(concept_id)
                first = False
                removed_rows.append((link, desc.get('description', ''), desc.get('type', ''), desc.get('locale', '')))

        for key in ('changed_major', 'changed_minor'):
            for concept_id, info in (self.concepts.get(key) or {}).items():
                prev_descs = info.get('prev_descriptions') or []
                curr_descs = info.get('descriptions') or []
                prev_map = {(d.get('type'), d.get('locale')): d.get('description', '') for d in prev_descs}
                curr_map = {(d.get('type'), d.get('locale')): d.get('description', '') for d in curr_descs}
                for key_tuple, curr_text in curr_map.items():
                    prev_text = prev_map.get(key_tuple)
                    dtype = key_tuple[0] or ''
                    dloc = key_tuple[1] or ''
                    link = anchored_link(concept_id)
                    if prev_text is None:
                        added_rows.append((link, curr_text, dtype, dloc))
                    elif prev_text != curr_text:
                        updated_rows.append((link, prev_text, curr_text, dtype, dloc))
                for key_tuple, prev_text in prev_map.items():
                    if key_tuple not in curr_map:
                        link = anchored_link(concept_id)
                        removed_rows.append((link, prev_text, key_tuple[0] or '', key_tuple[1] or ''))

        return added_rows, updated_rows, removed_rows

    @staticmethod
    def _descriptions_added_table(rows):
        lines = [
            '| Concept ID | Description | Description Type | Locale |',
            '|-----------:|------------|-----------------|--------|',
        ]
        for link, desc, dtype, locale in rows:
            lines.append(
                f'| {link} | {ChangelogMarkdownGenerator._escape(desc)} '
                f'| {ChangelogMarkdownGenerator._escape(dtype)} | {locale} |'
            )
        return lines

    @staticmethod
    def _descriptions_updated_table(rows):
        lines = [
            '| Concept ID | Previous Description | Updated Description | Locale |',
            '|-----------:|---------------------|-------------------|--------|',
        ]
        for link, prev, curr, dtype, locale in rows:  # pylint: disable=unused-variable
            lines.append(
                f'| {link} | {ChangelogMarkdownGenerator._escape(prev)} '
                f'| {ChangelogMarkdownGenerator._escape(curr)} | {locale} |'
            )
        return lines

    # ------------------------------------------------------------------
    # Translations section
    # ------------------------------------------------------------------

    def _translations_section(self):
        added_rows, updated_rows, removed_rows = self._translation_rows
        if not (added_rows or updated_rows or removed_rows):
            return ''

        # Group by locale for the highlight.
        # added/removed rows have 4 elements (locale at index 3);
        # updated rows have 5 elements (locale at index 4 == last).
        # Using row[-1] (last element) works for both, and filtering None/empty.
        all_locales = sorted({row[-1] for row in added_rows + updated_rows + removed_rows if row[-1]})
        locales_str = ', '.join(all_locales) if all_locales else ''
        highlight = self._static_highlight(
            'Translations',
            added=len(added_rows), updated=len(updated_rows), removed=len(removed_rows),
            extra=f'Locales: {locales_str}' if locales_str else None,
        )
        parts = ['## Translations', '', f'*{highlight}*']
        if added_rows:
            parts += ['', self._anchor('translations-added'), '### Added', '']
            parts += self._translations_table(added_rows)
        if updated_rows:
            parts += ['', self._anchor('translations-updated'), '### Updated', '']
            parts += self._translations_updated_table(updated_rows)
        if removed_rows:
            parts += ['', self._anchor('translations-removed'), '### Removed', '']
            parts += self._translations_table(removed_rows)
        parts += ['', '---']
        return '\n'.join(parts)

    def _collect_translation_rows(self):
        """Alias for backward-compat / clarity — delegates to the unified helper."""
        return self._collect_translation_name_rows()

    @staticmethod
    def _translations_table(rows):
        lines = [
            '| Concept ID | Name | Locale | Name Type |',
            '|-----------:|------|--------|-----------|',
        ]
        for link, name, name_type, locale in rows:
            lines.append(
                f'| {link} | {ChangelogMarkdownGenerator._escape(name)} | {locale} '
                f'| {ChangelogMarkdownGenerator._escape(name_type)} |'
            )
        return lines

    @staticmethod
    def _translations_updated_table(rows):
        lines = [
            '| Concept ID | Previous Name | Updated Name | Locale | Name Type |',
            '|-----------:|--------------|-------------|--------|-----------|',
        ]
        for link, prev, curr, name_type, locale in rows:
            lines.append(
                f'| {link} | {ChangelogMarkdownGenerator._escape(prev)} '
                f'| {ChangelogMarkdownGenerator._escape(curr)} '
                f'| {locale} | {ChangelogMarkdownGenerator._escape(name_type)} |'
            )
        return lines

    # ------------------------------------------------------------------
    # Mappings section
    # ------------------------------------------------------------------

    @staticmethod
    def _mapping_bucket_for(change_key):
        if change_key == 'new':
            return 'added'
        if change_key == 'removed':
            return 'removed'
        return 'changed'

    @staticmethod
    def _mapping_items(mapping_list_or_dict):
        """Yield ``(id, mapping)`` pairs without inventing ids for list items."""
        if isinstance(mapping_list_or_dict, dict):
            return mapping_list_or_dict.items()
        return ((m.get('id'), m) for m in (mapping_list_or_dict or []))

    def _normalize_mapping(self, mapping, mapping_id=None, from_concept=None):
        normalized = dict(mapping or {})
        if mapping_id and not normalized.get('id'):
            normalized['id'] = mapping_id
        if from_concept and not normalized.get('from_concept'):
            normalized['from_concept'] = from_concept
        return normalized

    def _add_mapping_to_collection(self, collection, mapping, mapping_id=None, from_concept=None):
        normalized = self._normalize_mapping(mapping, mapping_id=mapping_id, from_concept=from_concept)
        key = normalized.get('id') or (
            f'{from_concept or ""}:{normalized.get("to_source") or ""}:'
            f'{normalized.get("to_concept") or ""}:{normalized.get("map_type") or ""}'
        )
        if key in collection:
            # Prefer the richer normalized record when the same mapping appears both
            # top-level and embedded under a concept.
            collection[key] = {**collection[key], **normalized}
        else:
            collection[key] = normalized

    def _mapping_collections(self):
        """
        Return added/removed/changed mappings across both top-level mapping diffs
        and mappings embedded inside concept sections.

        Enriched changelogs intentionally attach mapping diffs to their owning
        concepts so concept rows can deep-link to the relevant mapping rows.  The
        overview, summary, TOC, and Mappings section must therefore count/render
        embedded mappings as first-class mapping changes too.
        """
        if self._mapping_collections_cache is not None:
            return self._mapping_collections_cache

        added, removed, changed = {}, {}, {}
        collections = {'added': added, 'removed': removed, 'changed': changed}

        for change_key, bucket in (
            ('new', added),
            ('removed', removed),
            ('changed_major', changed),
            ('changed_minor', changed),
            ('changed_retired', changed),
        ):
            for mapping_id, mapping in (self.mappings.get(change_key) or {}).items():
                self._add_mapping_to_collection(bucket, mapping, mapping_id=mapping_id)

        for concepts in self.concepts.values():
            if not isinstance(concepts, dict):
                continue
            for concept_id, info in concepts.items():
                for change_key, mapping_list in (info.get('mappings') or {}).items():
                    bucket = collections[self._mapping_bucket_for(change_key)]
                    for mapping_id, mapping in self._mapping_items(mapping_list):
                        self._add_mapping_to_collection(
                            bucket, mapping, mapping_id=mapping_id, from_concept=concept_id
                        )

        self._mapping_collections_cache = added, removed, changed
        return self._mapping_collections_cache

    def _mappings_section(self):
        if not self._has_mappings():
            return ''

        added, removed, changed = self._mapping_collections()

        highlight = self._static_highlight(
            'Mappings',
            added=len(added),
            removed=len(removed),
            changed=len(changed),
        )

        parts = ['## Mappings', '', f'*{highlight}*']
        if added:
            parts += ['', self._anchor('mappings-added'), '### Added', '']
            parts += self._mappings_table(added)
        if removed:
            parts += ['', self._anchor('mappings-removed'), '### Removed', '']
            parts += self._mappings_table(removed)
        if changed:
            parts += ['', self._anchor('mappings-updated'), '### Updated', '']
            parts += self._mappings_updated_table(changed)

        return '\n'.join(parts)

    def _from_link_builder(self):
        """Build a function that emits anchored from_concept links once per concept."""
        anchored = set()

        def builder(from_concept):
            if not from_concept:
                return ''
            base = self._concept_link(from_concept)
            if from_concept in anchored:
                return base
            anchored.add(from_concept)
            return f'<a id="mappings-{from_concept}"></a>{base}'
        return builder

    def _mappings_table(self, mappings_dict):
        rows = [
            '| From Concept | To Concept | To Source | Map Type |',
            '|-------------|-----------|----------|---------|',
        ]
        make_link = self._from_link_builder()
        for m in mappings_dict.values():
            rows.append(
                f'| {make_link(m.get("from_concept"))} '
                f'| {self._display_code(m.get("to_concept"))} '
                f'| {self._escape(m.get("to_source") or "")} '
                f'| {self._escape(m.get("map_type") or "")} |'
            )
        return rows

    def _mappings_updated_table(self, mappings_dict):
        """Before/after table for changed mappings, highlighting fields that changed."""
        rows = [
            '| From Concept | Previous To Concept | Updated To Concept | '
            'Previous Map Type | Updated Map Type | To Source |',
            '|-------------|--------------------|--------------------|'
            '------------------|------------------|----------|',
        ]
        make_link = self._from_link_builder()
        for m in mappings_dict.values():
            to_concept = m.get('to_concept') or ''
            map_type = m.get('map_type') or ''
            prev_to = m.get('prev_to_concept')
            prev_mt = m.get('prev_map_type')
            # Fall back to current value when prev is missing (e.g. verbosity<4 consumer).
            prev_to_display = prev_to if prev_to is not None else to_concept
            prev_mt_display = prev_mt if prev_mt is not None else map_type
            rows.append(
                f'| {make_link(m.get("from_concept"))} '
                f'| {self._display_code(prev_to_display)} '
                f'| {self._display_code(to_concept)} '
                f'| {self._escape(prev_mt_display)} '
                f'| {self._escape(map_type)} '
                f'| {self._escape(m.get("to_source") or "")} |'
            )
        return rows

    # ------------------------------------------------------------------
    # Presence checks
    # ------------------------------------------------------------------

    def _has_concepts(self):
        for key in ('new', 'removed', 'changed_retired', 'changed_major', 'changed_minor'):
            if self.concepts.get(key):
                return True
        return False

    def _has_mappings(self):
        added, removed, changed = self._mapping_collections()
        return bool(added or removed or changed)

    # ------------------------------------------------------------------
    # Deterministic highlight (placeholder for future LLM integration)
    # ------------------------------------------------------------------

    @staticmethod
    def _static_highlight(  # pylint: disable=too-many-arguments
        section, added=0, removed=0, changed=0, updated=0, retired=0, extra=None
    ):
        """
        Returns a short descriptive string for a changelog section.

        # TODO: Replace with LLM-generated summary (anthropic/claude-haiku-4.5,
        #       max_tokens=500, temperature=0.3) once ANTHROPIC_API_KEY is available.
        """
        parts = []
        if added:
            parts.append(f'{added:,} addition{"s" if added != 1 else ""}')
        if updated or changed:
            count = updated or changed
            parts.append(f'{count:,} update{"s" if count != 1 else ""}')
        if removed:
            parts.append(f'{removed:,} removal{"s" if removed != 1 else ""}')
        if retired:
            parts.append(f'{retired:,} retirement{"s" if retired != 1 else ""}')
        summary = ', '.join(parts) if parts else 'No changes'
        if extra:
            summary += f'. {extra}.'
        return f'{section}: {summary}.'

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_source_prefix(version_uri):
        """
        Extract the base source path from a version URI.
        E.g. '/orgs/CIEL/sources/CIEL/v20260101/' → '/orgs/CIEL/sources/CIEL/'
        """
        if not version_uri:
            return ''
        parts = version_uri.strip('/').split('/')
        try:
            src_idx = parts.index('sources')
            return '/' + '/'.join(parts[:src_idx + 2]) + '/'
        except (ValueError, IndexError):
            return ''

    def _concept_link(self, concept_id):
        if not concept_id:
            return ''
        base = getattr(settings, 'API_BASE_URL', '')
        if self._source_prefix:
            url = f'{base}{self._source_prefix}concepts/{concept_id}/'
            return f'[#{concept_id}]({url})'
        return f'#{concept_id}'

    @staticmethod
    def _version_label(uri):
        """Extract the version identifier from a version URI."""
        if not uri:
            return 'Unknown'
        parts = uri.strip('/').split('/')
        return parts[-1] if parts else uri

    @staticmethod
    def _escape(text):
        """Escape pipe characters so they don't break markdown tables."""
        if not text:
            return ''
        return str(text).replace('|', '\\|')

    @staticmethod
    def _display_code(code):
        """
        URL-decode a concept/mapping code for human-readable display.

        ICD-11 extension codes use ``&`` as a separator (e.g. ``2B31.2Z&XH75E6``),
        but they are stored in the database URL-encoded (``2B31.2Z%26XH75E6``).
        Decoding here ensures the markdown shows the canonical human-readable form.
        """
        if not code:
            return ''
        return ChangelogMarkdownGenerator._escape(unquote(str(code)))
