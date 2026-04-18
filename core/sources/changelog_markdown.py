"""
Markdown changelog generator for source version diffs.

Transforms enriched $changelog JSON (produced with enrich=True) into a
human-readable markdown document structured after LOINC/SNOMED release notes.

LLM-based "Release Note Highlights" and per-section narrative summaries are
intentionally NOT implemented here.
# TODO: Integrate litellm (anthropic/claude-haiku-4.5) once ANTHROPIC_API_KEY
#       is available in this deployment.  Replace _static_highlight() calls with
#       LLM completions (max_tokens=1000 for the top-level highlight,
#       max_tokens=500 per section highlight, temperature=0.3, English output).
"""

from datetime import date

from django.conf import settings


class ChangelogMarkdownGenerator:
    """
    Generates a markdown changelog from enriched $changelog JSON data.

    Usage::

        generator = ChangelogMarkdownGenerator(changelog_data)
        markdown_string = generator.generate()

    The ``changelog_data`` dict is the value returned by ``Source.changelog()``
    when called with ``enrich=True``.  It has the shape::

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self):
        sections = [
            self._header(),
            self._summary_table(),
            self._toc(),
            self._concepts_section(),
            self._names_section(),
            self._descriptions_section(),
            self._translations_section(),
            self._mappings_section(),
        ]
        return '\n\n'.join(s for s in sections if s)

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

    def _summary_table(self):
        v1_concepts = self._v1_meta.get('concepts', 0)
        v2_concepts = self._v2_meta.get('concepts', 0)
        v1_mappings = self._v1_meta.get('mappings', 0)
        v2_mappings = self._v2_meta.get('mappings', 0)

        concepts_new      = len(self.concepts.get('new') or {})
        concepts_removed  = len(self.concepts.get('removed') or {})
        concepts_retired  = len(self.concepts.get('changed_retired') or {})
        concepts_major    = len(self.concepts.get('changed_major') or {})
        concepts_minor    = len(self.concepts.get('changed_minor') or {})
        concepts_mappings_only = len(self.concepts.get('changed_mappings_only') or {})

        mappings_added   = len(self.mappings.get('new') or {})
        mappings_removed = len(self.mappings.get('removed') or {})

        net_concepts = concepts_new - concepts_removed - concepts_retired
        net_mappings = mappings_added - mappings_removed

        def _net(n):
            return f'+{n:,}' if n > 0 else (f'{n:,}' if n < 0 else '0')

        v1_label = self._version_label(self._v1_meta.get('uri', ''))
        v2_label = self._version_label(self._v2_meta.get('uri', ''))

        v1_uri = self._v1_meta.get('uri', '')
        v2_uri = self._v2_meta.get('uri', '')
        base = getattr(settings, 'API_BASE_URL', '')
        diff_url = f'{base}/sources/$changelog/?version1={v1_uri}&version2={v2_uri}'

        lines = [
            '## Summary',
            '',
            f'**{v1_label} → {v2_label}**',
            '',
            '| Category | Count |',
            '|----------|------:|',
            f'| New concepts | {concepts_new:,} |',
            f'| Major changes | {concepts_major:,} |',
            f'| Minor changes | {concepts_minor:,} |',
            f'| Mapping-only changes | {concepts_mappings_only:,} |',
            f'| Retired concepts | {concepts_retired:,} |',
            f'| Mappings added | {mappings_added:,} |',
            f'| Mappings removed | {mappings_removed:,} |',
            f'| **Net change** | **{_net(net_concepts)} concepts'
            + (f', {_net(net_mappings)} mappings' if net_mappings != 0 else '') + '** |',
            '',
            '| | ' + v1_label + ' | ' + v2_label + ' |',
            '|--|--:|--:|',
            f'| Concepts | {v1_concepts:,} | {v2_concepts:,} |',
            f'| Mappings | {v1_mappings:,} | {v2_mappings:,} |',
            '',
            f'[Download full JSON diff]({diff_url})',
            '',
            '---',
        ]
        return '\n'.join(lines)

    def _toc(self):
        entries = ['## Contents', '']
        section_checks = [
            ('Concepts', self._has_concepts()),
            ('Names', self._has_names()),
            ('Descriptions', self._has_descriptions()),
            ('Translations', self._has_translations()),
            ('Mappings', self._has_mappings()),
        ]
        for label, present in section_checks:
            if present:
                anchor = label.lower()
                entries.append(f'- [{label}](#{anchor})')
        entries.append('')
        entries.append('---')
        return '\n'.join(entries)

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

        total_added = len(added)
        total_removed = len(removed)
        total_retired = len(retired)
        # Only count concepts where at least one data axis changed (names, descriptions,
        # metadata, or mappings). Pure checksum-only changes carry no visible content change.
        total_changed = (
            sum(1 for info in changed_major.values() if self._changed_axes(info))
            + sum(1 for info in changed_minor.values() if self._changed_axes(info))
        )

        highlight = self._static_highlight(
            'Concepts',
            added=total_added,
            removed=total_removed,
            retired=total_retired,
            changed=total_changed,
        )

        parts = ['## Concepts', '', f'*{highlight}*']

        if added:
            parts += ['', '### Added', '']
            parts += self._concept_table(added)
        if removed:
            parts += ['', '### Removed', '']
            parts += self._concept_table(removed)
        if retired:
            parts += ['', '### Retired', '']
            parts += self._concept_table(retired)
        if changed_major:
            visible_major = {cid: info for cid, info in changed_major.items() if self._changed_axes(info)}
            if visible_major:
                parts += ['', '### Updated (Major)', '']
                parts += self._concept_table(visible_major, show_changes=True, axes_as_links=True)
        if changed_minor:
            visible_minor = {cid: info for cid, info in changed_minor.items() if self._changed_axes(info)}
            if visible_minor:
                parts += ['', '### Updated (Minor)', '']
                parts += self._concept_table(visible_minor, show_changes=True, axes_as_links=True)

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
                axes = self._changed_axes(info, as_links=axes_as_links, concept_id=concept_id if axes_as_links else None)
                changed_str = ', '.join(axes) if axes else '—'
                rows.append(
                    f'| {link} | {self._escape(display)} | {self._escape(concept_class)} | {changed_str} |'
                )
            else:
                rows.append(f'| {link} | {self._escape(display)} | {self._escape(concept_class)} |')
        return rows

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
        prev_names_default = frozenset(
            (n.get('type'), n.get('locale'), n.get('name'))
            for n in (info.get('prev_names') or [])
            if n.get('locale') == self.default_locale
        )
        curr_names_default = frozenset(
            (n.get('type'), n.get('locale'), n.get('name'))
            for n in (info.get('names') or [])
            if n.get('locale') == self.default_locale
        )
        if prev_names_default != curr_names_default:
            axes.append(_link('Names', 'names'))

        # Non-default-locale names → Translations section
        prev_names_tr = frozenset(
            (n.get('type'), n.get('locale'), n.get('name'))
            for n in (info.get('prev_names') or [])
            if n.get('locale') != self.default_locale
        )
        curr_names_tr = frozenset(
            (n.get('type'), n.get('locale'), n.get('name'))
            for n in (info.get('names') or [])
            if n.get('locale') != self.default_locale
        )
        if prev_names_tr != curr_names_tr:
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
        if not self._has_names():
            return ''

        added_rows, updated_rows, removed_rows = self._collect_name_rows(self.default_locale, section='names')

        highlight = self._static_highlight(
            'Names',
            added=len(added_rows),
            updated=len(updated_rows),
            removed=len(removed_rows),
        )

        parts = ['## Names', '', f'*{highlight}*']
        if added_rows:
            parts += ['', '### Added', '']
            parts += self._names_added_table(added_rows)
        if updated_rows:
            parts += ['', '### Updated', '']
            parts += self._names_updated_table(updated_rows)
        if removed_rows:
            parts += ['', '### Removed', '']
            parts += self._names_added_table(removed_rows)

        parts += ['', '---']
        return '\n'.join(parts)

    def _collect_name_rows(self, locale_filter, section='names'):
        """
        Return (added_rows, updated_rows, removed_rows) for the given locale filter.

        The concept-ID cell of the *first* row for each concept carries an HTML
        anchor (``<a id="names-{id}"></a>``) so that the "Changed" column in the
        Concepts table can link directly to that row.
        """
        added_rows = []
        updated_rows = []
        removed_rows = []
        anchored = set()

        def anchored_link(concept_id):
            base = self._concept_link(concept_id)
            if concept_id not in anchored:
                anchored.add(concept_id)
                return f'<a id="{section}-{concept_id}"></a>{base}'
            return base

        # Added: from new concepts
        for concept_id, info in (self.concepts.get('new') or {}).items():
            link = anchored_link(concept_id)
            first = True
            for name in info.get('names') or []:
                if name.get('locale') == locale_filter:
                    added_rows.append((link if first else self._concept_link(concept_id),
                                       name.get('name', ''), name.get('type', ''), name.get('locale', '')))
                    first = False

        # Removed: from removed concepts (these are v1 names)
        for concept_id, info in (self.concepts.get('removed') or {}).items():
            link = anchored_link(concept_id)
            first = True
            for name in info.get('names') or []:
                if name.get('locale') == locale_filter:
                    removed_rows.append((link if first else self._concept_link(concept_id),
                                         name.get('name', ''), name.get('type', ''), name.get('locale', '')))
                    first = False

        # Added/Updated/Removed: compare prev_names vs names on changed concepts.
        #
        # We group names by (type, locale) and compare the SET of texts within
        # each group.  This avoids false positives when multiple names share the
        # same (type, locale) pair — the old single-dict approach would silently
        # overwrite all but the last name in such a group, producing spurious
        # "updated" rows.
        #
        # Update detection: if a (type, locale) group had exactly 1 text in v1
        # and exactly 1 different text in v2, we treat it as a text correction
        # ("Updated").  Any other add/remove within a group is reported literally.
        from collections import defaultdict
        for key in ('changed_major', 'changed_minor'):
            for concept_id, info in (self.concepts.get(key) or {}).items():
                prev_names = info.get('prev_names') or []
                curr_names = info.get('names') or []

                # Group texts by (type, locale) as sets (handles duplicates correctly)
                prev_by_key = defaultdict(set)
                for n in prev_names:
                    if n.get('locale') == locale_filter:
                        prev_by_key[(n.get('type'), n.get('locale'))].add(n.get('name', ''))

                curr_by_key = defaultdict(set)
                for n in curr_names:
                    if n.get('locale') == locale_filter:
                        curr_by_key[(n.get('type'), n.get('locale'))].add(n.get('name', ''))

                all_keys = set(prev_by_key) | set(curr_by_key)
                for (ntype, nloc) in all_keys:
                    prev_texts = prev_by_key.get((ntype, nloc), set())
                    curr_texts = curr_by_key.get((ntype, nloc), set())
                    if prev_texts == curr_texts:
                        continue

                    added_texts = curr_texts - prev_texts
                    removed_texts = prev_texts - curr_texts

                    # Exactly 1 removal + 1 addition in the same (type, locale) slot →
                    # treat as a text correction (Updated) rather than separate rows.
                    if len(added_texts) == 1 and len(removed_texts) == 1:
                        link = anchored_link(concept_id)
                        updated_rows.append((
                            link,
                            next(iter(removed_texts)),
                            next(iter(added_texts)),
                            ntype or '',
                            nloc or '',
                        ))
                    else:
                        for text in added_texts:
                            link = anchored_link(concept_id)
                            added_rows.append((link, text, ntype or '', nloc or ''))
                        for text in removed_texts:
                            link = anchored_link(concept_id)
                            removed_rows.append((link, text, ntype or '', nloc or ''))

        return added_rows, updated_rows, removed_rows

    @staticmethod
    def _names_added_table(rows):
        lines = [
            '| Concept ID | Name | Name Type | Locale |',
            '|-----------:|------|-----------|--------|',
        ]
        for link, name, name_type, locale in rows:
            lines.append(f'| {link} | {ChangelogMarkdownGenerator._escape(name)} | {ChangelogMarkdownGenerator._escape(name_type)} | {locale} |')
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
        if not self._has_descriptions():
            return ''

        added_rows, updated_rows, removed_rows = self._collect_description_rows()

        highlight = self._static_highlight(
            'Descriptions',
            added=len(added_rows),
            updated=len(updated_rows),
            removed=len(removed_rows),
        )

        parts = ['## Descriptions', '', f'*{highlight}*']
        if added_rows:
            parts += ['', '### Added', '']
            parts += self._descriptions_added_table(added_rows)
        if updated_rows:
            parts += ['', '### Updated', '']
            parts += self._descriptions_updated_table(updated_rows)
        if removed_rows:
            parts += ['', '### Removed', '']
            parts += self._descriptions_added_table(removed_rows)

        parts += ['', '---']
        return '\n'.join(parts)

    def _collect_description_rows(self):
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
            lines.append(f'| {link} | {ChangelogMarkdownGenerator._escape(desc)} | {ChangelogMarkdownGenerator._escape(dtype)} | {locale} |')
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
        if not self._has_translations():
            return ''

        added_rows, updated_rows, removed_rows = self._collect_translation_rows()
        if not any([added_rows, updated_rows, removed_rows]):
            return ''

        # Group by locale for the highlight.
        # added/removed rows have 4 elements (locale at index 3);
        # updated rows have 5 elements (locale at index 4 == last).
        # Using row[-1] (last element) works for both, and filtering None/empty.
        all_locales = sorted({row[-1] for row in added_rows + updated_rows + removed_rows if row[-1]})
        locales_str = ', '.join(all_locales) if all_locales else ''
        highlight = self._static_highlight(
            'Translations',
            added=len(added_rows),
            updated=len(updated_rows),
            removed=len(removed_rows),
            extra=f'Locales: {locales_str}' if locales_str else None,
        )

        parts = ['## Translations', '', f'*{highlight}*']
        if added_rows:
            parts += ['', '### Added', '']
            parts += self._translations_table(added_rows)
        if updated_rows:
            parts += ['', '### Updated', '']
            parts += self._translations_updated_table(updated_rows)
        if removed_rows:
            parts += ['', '### Removed', '']
            parts += self._translations_table(removed_rows)

        parts += ['', '---']
        return '\n'.join(parts)

    def _collect_translation_rows(self):
        """
        Same logic as _collect_name_rows but for non-default locales.
        Anchors use the ``translations-{concept_id}`` prefix.
        """
        added_rows = []
        updated_rows = []
        removed_rows = []
        anchored = set()

        def anchored_link(concept_id):
            base = self._concept_link(concept_id)
            if concept_id not in anchored:
                anchored.add(concept_id)
                return f'<a id="translations-{concept_id}"></a>{base}'
            return base

        for concept_id, info in (self.concepts.get('new') or {}).items():
            for name in info.get('names') or []:
                if name.get('locale') != self.default_locale:
                    added_rows.append((anchored_link(concept_id), name.get('name', ''), name.get('type', ''), name.get('locale', '')))

        for concept_id, info in (self.concepts.get('removed') or {}).items():
            for name in info.get('names') or []:
                if name.get('locale') != self.default_locale:
                    removed_rows.append((anchored_link(concept_id), name.get('name', ''), name.get('type', ''), name.get('locale', '')))

        for key in ('changed_major', 'changed_minor'):
            for concept_id, info in (self.concepts.get(key) or {}).items():
                prev_names = info.get('prev_names') or []
                curr_names = info.get('names') or []

                from collections import defaultdict
                prev_by_key = defaultdict(set)
                for n in prev_names:
                    if n.get('locale') != self.default_locale:
                        prev_by_key[(n.get('type'), n.get('locale'))].add(n.get('name', ''))

                curr_by_key = defaultdict(set)
                for n in curr_names:
                    if n.get('locale') != self.default_locale:
                        curr_by_key[(n.get('type'), n.get('locale'))].add(n.get('name', ''))

                all_keys = set(prev_by_key) | set(curr_by_key)
                for (ntype, nloc) in all_keys:
                    prev_texts = prev_by_key.get((ntype, nloc), set())
                    curr_texts = curr_by_key.get((ntype, nloc), set())
                    if prev_texts == curr_texts:
                        continue
                    added_texts = curr_texts - prev_texts
                    removed_texts = prev_texts - curr_texts
                    if len(added_texts) == 1 and len(removed_texts) == 1:
                        link = anchored_link(concept_id)
                        updated_rows.append((
                            link, next(iter(removed_texts)), next(iter(added_texts)),
                            ntype or '', nloc or '',
                        ))
                    else:
                        for text in added_texts:
                            link = anchored_link(concept_id)
                            added_rows.append((link, text, ntype or '', nloc or ''))
                        for text in removed_texts:
                            link = anchored_link(concept_id)
                            removed_rows.append((link, text, ntype or '', nloc or ''))

        return added_rows, updated_rows, removed_rows

    @staticmethod
    def _translations_table(rows):
        lines = [
            '| Concept ID | Name | Locale | Name Type |',
            '|-----------:|------|--------|-----------|',
        ]
        for link, name, name_type, locale in rows:
            lines.append(f'| {link} | {ChangelogMarkdownGenerator._escape(name)} | {locale} | {ChangelogMarkdownGenerator._escape(name_type)} |')
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

    def _mappings_section(self):
        if not self._has_mappings():
            return ''

        added = self.mappings.get('new') or {}
        removed = self.mappings.get('removed') or {}
        changed_major = self.mappings.get('changed_major') or {}
        changed_minor = self.mappings.get('changed_minor') or {}
        changed_retired = self.mappings.get('changed_retired') or {}

        # Also collect mappings from changed_mappings_only concepts
        inline_added = {}
        inline_changed = {}
        for concept_id, info in (self.concepts.get('changed_mappings_only') or {}).items():
            for change_key, mapping_list in (info.get('mappings') or {}).items():
                for m in mapping_list:
                    if change_key == 'new':
                        inline_added[m['id']] = m
                    else:
                        inline_changed[m['id']] = m

        all_added = {**added, **inline_added}
        all_changed = {**changed_major, **changed_minor, **changed_retired, **inline_changed}

        total_added = len(all_added)
        total_removed = len(removed)
        total_changed = len(all_changed)

        highlight = self._static_highlight(
            'Mappings',
            added=total_added,
            removed=total_removed,
            changed=total_changed,
        )

        parts = ['## Mappings', '', f'*{highlight}*']
        if all_added:
            parts += ['', '### Added', '']
            parts += self._mappings_table(all_added)
        if removed:
            parts += ['', '### Removed', '']
            parts += self._mappings_table(removed)
        if all_changed:
            parts += ['', '### Updated', '']
            parts += self._mappings_table(all_changed)

        return '\n'.join(parts)

    def _mappings_table(self, mappings_dict):
        rows = [
            '| From Concept | To Concept | To Source | Map Type |',
            '|-------------|-----------|----------|---------|',
        ]
        anchored = set()
        for _, m in mappings_dict.items():
            from_concept = m.get('from_concept') or ''
            to_concept = m.get('to_concept') or ''
            to_source = m.get('to_source') or ''
            map_type = m.get('map_type') or ''
            if from_concept:
                base_link = self._concept_link(from_concept)
                if from_concept not in anchored:
                    anchored.add(from_concept)
                    from_link = f'<a id="mappings-{from_concept}"></a>{base_link}'
                else:
                    from_link = base_link
            else:
                from_link = ''
            rows.append(
                f'| {from_link} | {self._escape(to_concept)} '
                f'| {self._escape(to_source)} | {self._escape(map_type)} |'
            )
        return rows

    # ------------------------------------------------------------------
    # Presence checks (used for TOC and section skipping)
    # ------------------------------------------------------------------

    def _has_concepts(self):
        for key in ('new', 'removed', 'changed_retired', 'changed_major', 'changed_minor'):
            if self.concepts.get(key):
                return True
        return False

    def _has_names(self):
        for key in ('new', 'removed', 'changed_major', 'changed_minor'):
            for info in (self.concepts.get(key) or {}).values():
                if info.get('names') or info.get('prev_names'):
                    return True
        return False

    def _has_descriptions(self):
        for key in ('new', 'removed', 'changed_major', 'changed_minor'):
            for info in (self.concepts.get(key) or {}).values():
                if info.get('descriptions') or info.get('prev_descriptions'):
                    return True
        return False

    def _has_translations(self):
        for key in ('new', 'removed', 'changed_major', 'changed_minor'):
            for info in (self.concepts.get(key) or {}).values():
                for name in info.get('names') or []:
                    if name.get('locale') != self.default_locale:
                        return True
                for name in info.get('prev_names') or []:
                    if name.get('locale') != self.default_locale:
                        return True
        return False

    def _has_mappings(self):
        if any(self.mappings.get(k) for k in ('new', 'removed', 'changed_retired', 'changed_major', 'changed_minor')):
            return True
        for info in (self.concepts.get('changed_mappings_only') or {}).values():
            if info.get('mappings'):
                return True
        return False

    # ------------------------------------------------------------------
    # Deterministic highlight (placeholder for future LLM integration)
    # ------------------------------------------------------------------

    @staticmethod
    def _static_highlight(section, added=0, removed=0, changed=0, updated=0, retired=0, extra=None):
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
