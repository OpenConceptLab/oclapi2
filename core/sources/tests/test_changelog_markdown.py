from django.test import SimpleTestCase

from core.sources.changelog_markdown import ChangelogMarkdownGenerator


def _make_data(
    v1_uri='/orgs/CIEL/sources/CIEL/v20250101/',
    v2_uri='/orgs/CIEL/sources/CIEL/v20260101/',
    v1_concepts=100,
    v2_concepts=102,
    v1_mappings=50,
    v2_mappings=52,
    concepts=None,
    mappings=None,
):
    return {
        'meta': {
            'version1': {'uri': v1_uri, 'concepts': v1_concepts, 'mappings': v1_mappings},
            'version2': {'uri': v2_uri, 'concepts': v2_concepts, 'mappings': v2_mappings},
        },
        'concepts': concepts or {},
        'mappings': mappings or {},
    }


class TestChangelogMarkdownGeneratorHeader(SimpleTestCase):
    def test_header_contains_version_labels(self):
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('v20260101', md)
        self.assertIn('v20250101', md)

    def test_header_h1(self):
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('# v20260101 Changelog', md)


class TestChangelogMarkdownGeneratorSummaryTable(SimpleTestCase):
    def test_summary_table_present(self):
        data = _make_data(v1_concepts=100, v2_concepts=102, v1_mappings=50, v2_mappings=52)
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('## Summary', md)
        self.assertIn('| Concepts |', md)
        self.assertIn('| Mappings |', md)

    def test_summary_counts(self):
        data = _make_data(
            v1_concepts=100,
            v2_concepts=103,
            concepts={
                'new': {'c1': {'id': 'c1', 'display_name': 'Foo'}},
                'removed': {'c2': {'id': 'c2', 'display_name': 'Bar'}, 'c3': {'id': 'c3', 'display_name': 'Baz'}},
            }
        )
        md = ChangelogMarkdownGenerator(data).generate()
        # New breakdown format: per-category rows + totals table
        self.assertIn('| New concepts | 1 |', md)
        self.assertIn('| Concepts | 100 | 103 |', md)

    def test_summary_breakdown_rows(self):
        data = _make_data(
            concepts={
                'changed_major': {'c1': {'id': 'c1', 'display_name': 'Major'}},
                'changed_minor': {'c2': {'id': 'c2', 'display_name': 'Minor'}},
                'changed_retired': {'c3': {'id': 'c3', 'display_name': 'Retired'}},
                'changed_mappings_only': {'c4': {'id': 'c4', 'display_name': 'Map only'}},
            },
            mappings={
                'new': {'m1': {'id': 'm1', 'from_concept': 'c1', 'to_concept': 'x', 'to_source': None, 'map_type': 'SAME-AS'}},
                'removed': {'m2': {'id': 'm2', 'from_concept': 'c2', 'to_concept': 'y', 'to_source': None, 'map_type': 'SAME-AS'}},
            },
        )
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('| Major changes | 1 |', md)
        self.assertIn('| Minor changes | 1 |', md)
        self.assertIn('| Retired concepts | 1 |', md)
        self.assertIn('| Mapping-only changes | 1 |', md)
        self.assertIn('| Mappings added | 1 |', md)
        self.assertIn('| Mappings removed | 1 |', md)

    def test_summary_net_change(self):
        data = _make_data(
            concepts={
                'new': {'c1': {'id': 'c1', 'display_name': 'A'}, 'c2': {'id': 'c2', 'display_name': 'B'}},
                'removed': {'c3': {'id': 'c3', 'display_name': 'C'}},
            }
        )
        md = ChangelogMarkdownGenerator(data).generate()
        # net = +2 - 1 = +1
        self.assertIn('+1 concepts', md)

    def test_json_diff_download_link(self):
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('Download full JSON diff', md)
        self.assertIn('/sources/$changelog/', md)


class TestChangelogMarkdownGeneratorTOC(SimpleTestCase):
    def test_toc_shows_only_present_sections(self):
        data = _make_data(
            concepts={
                'new': {'c1': {'id': 'c1', 'display_name': 'New concept'}},
            },
            mappings={
                'new': {'m1': {'id': 'm1', 'from_concept': 'c1', 'to_concept': 'x', 'to_source': None, 'map_type': 'SAME-AS'}},
            }
        )
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('[Concepts]', md)
        self.assertIn('[Mappings]', md)
        # No names/descriptions/translations data → should not appear in TOC
        self.assertNotIn('[Names]', md)
        self.assertNotIn('[Descriptions]', md)
        self.assertNotIn('[Translations]', md)


class TestChangelogMarkdownGeneratorConceptsSection(SimpleTestCase):
    def test_concepts_added_table(self):
        data = _make_data(concepts={
            'new': {
                'c1': {'id': 'c1', 'display_name': 'Malaria Test', 'concept_class': 'Test'},
            }
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('## Concepts', md)
        self.assertIn('### Added', md)
        self.assertIn('#c1', md)
        self.assertIn('Malaria Test', md)
        self.assertIn('Test', md)

    def test_concept_link_format(self):
        data = _make_data(concepts={
            'new': {'42': {'id': '42', 'display_name': 'Something'}}
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('[#42]', md)
        self.assertIn('/orgs/CIEL/sources/CIEL/concepts/42/', md)

    def test_concepts_removed_section(self):
        data = _make_data(concepts={
            'removed': {'c5': {'id': 'c5', 'display_name': 'Old concept'}}
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('### Removed', md)
        self.assertIn('#c5', md)

    def test_concepts_retired_section(self):
        data = _make_data(concepts={
            'changed_retired': {'c3': {'id': 'c3', 'display_name': 'Retired concept'}}
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('### Retired', md)

    def test_empty_concepts_section_omitted(self):
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertNotIn('## Concepts', md)

    def test_pipe_in_display_name_escaped(self):
        data = _make_data(concepts={
            'new': {'c1': {'id': 'c1', 'display_name': 'A|B name'}}
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('A\\|B name', md)


class TestChangelogMarkdownGeneratorNamesSection(SimpleTestCase):
    def _data_with_names(self):
        return _make_data(concepts={
            'new': {
                'c1': {
                    'id': 'c1',
                    'display_name': 'New concept',
                    'names': [
                        {'name': 'Lorem ipsum', 'type': 'FULLY_SPECIFIED', 'locale': 'en', 'locale_preferred': True},
                        {'name': 'Ipsum lorem', 'type': 'SHORT', 'locale': 'en', 'locale_preferred': False},
                    ],
                    'descriptions': [],
                }
            },
            'removed': {
                'c2': {
                    'id': 'c2',
                    'display_name': 'Removed concept',
                    'names': [
                        {'name': 'Old name', 'type': 'FULLY_SPECIFIED', 'locale': 'en', 'locale_preferred': True},
                    ],
                    'descriptions': [],
                }
            },
        })

    def test_names_section_present(self):
        md = ChangelogMarkdownGenerator(self._data_with_names()).generate()
        self.assertIn('## Names', md)

    def test_names_added_table(self):
        md = ChangelogMarkdownGenerator(self._data_with_names()).generate()
        self.assertIn('### Added', md)
        self.assertIn('Lorem ipsum', md)

    def test_names_removed_table(self):
        md = ChangelogMarkdownGenerator(self._data_with_names()).generate()
        self.assertIn('### Removed', md)
        self.assertIn('Old name', md)

    def test_names_updated_table(self):
        data = _make_data(concepts={
            'changed_minor': {
                'c1': {
                    'id': 'c1',
                    'display_name': 'Fixed',
                    'names': [
                        {'name': 'Lorem ipsum', 'type': 'FULLY_SPECIFIED', 'locale': 'en', 'locale_preferred': True},
                    ],
                    'prev_names': [
                        {'name': 'Lorem IPsum', 'type': 'FULLY_SPECIFIED', 'locale': 'en', 'locale_preferred': True},
                    ],
                    'descriptions': [],
                }
            }
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('## Names', md)
        self.assertIn('### Updated', md)
        self.assertIn('Lorem IPsum', md)
        self.assertIn('Lorem ipsum', md)

    def test_names_section_omitted_if_no_names(self):
        data = _make_data(concepts={
            'new': {'c1': {'id': 'c1', 'display_name': 'No names'}}
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertNotIn('## Names', md)


class TestChangelogMarkdownGeneratorTranslationsSection(SimpleTestCase):
    def test_translations_section_present(self):
        data = _make_data(concepts={
            'new': {
                'c1': {
                    'id': 'c1',
                    'display_name': 'New',
                    'names': [
                        {'name': 'English name', 'type': 'FULLY_SPECIFIED', 'locale': 'en', 'locale_preferred': True},
                        {'name': 'Nome em português', 'type': 'FULLY_SPECIFIED', 'locale': 'pt', 'locale_preferred': False},
                    ],
                    'descriptions': [],
                }
            }
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('## Translations', md)
        self.assertIn('Nome em português', md)
        self.assertNotIn('English name', md.split('## Translations')[1])

    def test_translations_omitted_if_only_default_locale(self):
        data = _make_data(concepts={
            'new': {
                'c1': {
                    'id': 'c1',
                    'display_name': 'English only',
                    'names': [
                        {'name': 'English name', 'type': 'FULLY_SPECIFIED', 'locale': 'en', 'locale_preferred': True},
                    ],
                    'descriptions': [],
                }
            }
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertNotIn('## Translations', md)


class TestChangelogMarkdownGeneratorMappingsSection(SimpleTestCase):
    def test_mappings_added_table(self):
        data = _make_data(mappings={
            'new': {
                'm1': {
                    'id': 'm1',
                    'from_concept': 'c1',
                    'from_source': None,
                    'to_concept': '12345',
                    'to_source': 'http://snomed.info/sct',
                    'map_type': 'SAME-AS',
                }
            }
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('## Mappings', md)
        self.assertIn('### Added', md)
        self.assertIn('SAME-AS', md)
        self.assertIn('12345', md)

    def test_mappings_section_omitted_if_empty(self):
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertNotIn('## Mappings', md)

    def test_changed_mappings_only_included(self):
        data = _make_data(concepts={
            'changed_mappings_only': {
                'c7': {
                    'id': 'c7',
                    'display_name': 'Concept 7',
                    'mappings': {
                        'changed_minor': [
                            {
                                'id': 'mapping7',
                                'from_concept': 'c7',
                                'from_source': None,
                                'to_concept': 'xyz',
                                'to_source': None,
                                'map_type': 'SAME-AS',
                            }
                        ]
                    }
                }
            }
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('## Mappings', md)


class TestChangelogMarkdownGeneratorStaticHighlight(SimpleTestCase):
    def test_additions_only(self):
        result = ChangelogMarkdownGenerator._static_highlight('Concepts', added=5)
        self.assertIn('5 additions', result)

    def test_all_counts(self):
        result = ChangelogMarkdownGenerator._static_highlight('Names', added=30, updated=60, removed=2)
        self.assertIn('30 additions', result)
        self.assertIn('60 updates', result)
        self.assertIn('2 removals', result)

    def test_no_changes(self):
        result = ChangelogMarkdownGenerator._static_highlight('Names')
        self.assertIn('No changes', result)

    def test_singular_forms(self):
        result = ChangelogMarkdownGenerator._static_highlight('Concepts', added=1, removed=1)
        self.assertIn('1 addition', result)
        self.assertIn('1 removal', result)
        self.assertNotIn('additions', result)
        self.assertNotIn('removals', result)


class TestChangelogMarkdownGeneratorHelpers(SimpleTestCase):
    def test_extract_source_prefix(self):
        gen = ChangelogMarkdownGenerator.__new__(ChangelogMarkdownGenerator)
        self.assertEqual(
            gen._extract_source_prefix('/orgs/CIEL/sources/CIEL/v20260101/'),
            '/orgs/CIEL/sources/CIEL/'
        )

    def test_extract_source_prefix_empty(self):
        gen = ChangelogMarkdownGenerator.__new__(ChangelogMarkdownGenerator)
        self.assertEqual(gen._extract_source_prefix(''), '')

    def test_escape_pipe(self):
        self.assertEqual(ChangelogMarkdownGenerator._escape('A|B'), 'A\\|B')
        self.assertEqual(ChangelogMarkdownGenerator._escape('Normal'), 'Normal')
        self.assertEqual(ChangelogMarkdownGenerator._escape(None), '')
        self.assertEqual(ChangelogMarkdownGenerator._escape(''), '')

    def test_version_label(self):
        gen = ChangelogMarkdownGenerator.__new__(ChangelogMarkdownGenerator)
        self.assertEqual(gen._version_label('/orgs/CIEL/sources/CIEL/v20260101/'), 'v20260101')
        self.assertEqual(gen._version_label(''), 'Unknown')
