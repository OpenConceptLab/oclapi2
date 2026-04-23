# pylint: disable=protected-access,too-many-arguments

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


def _make_mapping(mapping_id='m1', from_concept='c1', to_concept='x', map_type='SAME-AS', to_source=None):
    return {
        'id': mapping_id,
        'from_concept': from_concept,
        'to_concept': to_concept,
        'to_source': to_source,
        'map_type': map_type,
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
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('## Summary', md)

    def test_summary_counts(self):
        data = _make_data(
            concepts={
                'new': {'c1': {'id': 'c1', 'display_name': 'Foo'}},
                'removed': {'c2': {'id': 'c2', 'display_name': 'Bar'}, 'c3': {'id': 'c3', 'display_name': 'Baz'}},
            }
        )
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('| New concepts | 1 |', md)
        self.assertIn('| Removed concepts | 2 |', md)

    def test_summary_breakdown_rows(self):
        data = _make_data(
            concepts={
                'changed_major': {'c1': {'id': 'c1', 'display_name': 'Major'}},
                'changed_minor': {'c2': {'id': 'c2', 'display_name': 'Minor'}},
                'changed_retired': {'c3': {'id': 'c3', 'display_name': 'Retired'}},
                'changed_mappings_only': {'c4': {'id': 'c4', 'display_name': 'Map only'}},
            },
            mappings={
                'new': {'m1': _make_mapping('m1', 'c1', 'x')},
                'removed': {'m2': _make_mapping('m2', 'c2', 'y')},
            },
        )
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('| Major changes | 1 |', md)
        self.assertIn('| Minor changes | 1 |', md)
        self.assertIn('| Retired concepts | 1 |', md)
        self.assertIn('| Mapping-only changes | 1 |', md)
        self.assertIn('| Mappings added | 1 |', md)
        self.assertIn('| Mappings removed | 1 |', md)

    def test_summary_counts_embedded_mappings(self):
        data = _make_data(
            concepts={
                'changed_major': {
                    'c1': {
                        'id': 'c1',
                        'display_name': 'Major',
                        'mappings': {
                            'new': [
                                {'id': 'm1', 'to_concept': 'x', 'to_source': None, 'map_type': 'SAME-AS'},
                                {'id': 'm2', 'to_concept': 'y', 'to_source': None, 'map_type': 'NARROWER-THAN'},
                            ],
                        },
                    },
                },
                'changed_mappings_only': {
                    'c2': {
                        'id': 'c2',
                        'display_name': 'Mapping only',
                        'mappings': {
                            'new': [
                                {'id': 'm3', 'to_concept': 'z', 'to_source': None, 'map_type': 'SAME-AS'},
                            ],
                        },
                    },
                },
            },
        )
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('| Mappings added | 3 |', md)

    def test_summary_no_version_range_header(self):
        # Version range belongs to Overview only; it must not repeat in Summary.
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        summary_start = md.index('## Summary')
        summary_block = md[summary_start:]
        self.assertNotIn('→', summary_block)

    def test_summary_no_totals_table(self):
        # The v1/v2 totals table belongs to Overview only; it must not repeat in Summary.
        data = _make_data(v1_concepts=100, v2_concepts=102, v1_mappings=50, v2_mappings=52)
        md = ChangelogMarkdownGenerator(data).generate()
        summary_start = md.index('## Summary')
        summary_block = md[summary_start:]
        self.assertNotIn('| Concepts |', summary_block)
        self.assertNotIn('| Mappings |', summary_block)

    def test_json_diff_download_link(self):
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('Download full JSON diff', md)
        self.assertIn('/sources/$changelog/', md)


class TestChangelogMarkdownGeneratorOverview(SimpleTestCase):
    def test_overview_section_present(self):
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('## Overview', md)

    def test_overview_before_summary(self):
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertLess(md.index('## Overview'), md.index('## Summary'))

    def test_overview_version_range_header(self):
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('**v20250101 → v20260101**', md)

    def test_overview_table_columns(self):
        data = _make_data(v1_concepts=80_000, v2_concepts=80_200, v1_mappings=120_000, v2_mappings=120_450)
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('80,000', md)
        self.assertIn('80,200', md)
        self.assertIn('120,000', md)
        self.assertIn('120,450', md)

    def test_overview_concepts_counts(self):
        data = _make_data(
            concepts={
                'new': {'c1': {'id': 'c1', 'display_name': 'A'}, 'c2': {'id': 'c2', 'display_name': 'B'}},
                'removed': {'c3': {'id': 'c3', 'display_name': 'C'}},
                'changed_retired': {'c4': {'id': 'c4', 'display_name': 'D'}},
                'changed_major': {'c5': {'id': 'c5', 'display_name': 'E'}},
                'changed_minor': {'c6': {'id': 'c6', 'display_name': 'F'}},
                'changed_mappings_only': {'c7': {'id': 'c7', 'display_name': 'G'}},
            }
        )
        md = ChangelogMarkdownGenerator(data).generate()
        # Added=2, Removed=1+1=2 (removed+retired), Changed=1+1+1=3
        self.assertIn('| Concepts |', md)
        overview_line = [l for l in md.splitlines() if l.startswith('| Concepts |')][0]
        self.assertIn('| 2 |', overview_line)  # Added
        self.assertIn('| 2 |', overview_line)  # Removed (removed + retired)
        self.assertIn('| 3 |', overview_line)  # Changed

    def test_overview_mappings_counts(self):
        data = _make_data(
            mappings={
                'new': {'m1': _make_mapping('m1', 'c1', 'x')},
                'removed': {'m2': _make_mapping('m2', 'c2', 'y')},
                'changed_minor': {'m3': _make_mapping('m3', 'c3', 'z')},
            }
        )
        md = ChangelogMarkdownGenerator(data).generate()
        mappings_line = [l for l in md.splitlines() if l.startswith('| Mappings |')][0]
        # Added=1, Removed=1, Changed=1
        self.assertIn('| 1 |', mappings_line)

    def test_overview_counts_embedded_mappings(self):
        data = _make_data(
            concepts={
                'new': {
                    'c1': {
                        'id': 'c1',
                        'display_name': 'New',
                        'mappings': {
                            'new': [
                                {'id': 'm1', 'to_concept': 'x', 'to_source': None, 'map_type': 'SAME-AS'},
                            ],
                        },
                    },
                },
                'changed_major': {
                    'c2': {
                        'id': 'c2',
                        'display_name': 'Major',
                        'mappings': {
                            'new': [
                                {'id': 'm2', 'to_concept': 'y', 'to_source': None, 'map_type': 'SAME-AS'},
                            ],
                        },
                    },
                },
            },
        )
        md = ChangelogMarkdownGenerator(data).generate()
        mappings_line = [l for l in md.splitlines() if l.startswith('| Mappings |')][0]
        self.assertIn('| 2 | 0 | 0 |', mappings_line)


class TestChangelogMarkdownGeneratorTOC(SimpleTestCase):
    def test_toc_omitted_without_following_sections(self):
        data = _make_data()
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertNotIn('## Contents', md)

    def test_toc_shows_only_present_sections(self):
        data = _make_data(
            concepts={
                'new': {'c1': {'id': 'c1', 'display_name': 'New concept'}},
            },
            mappings={
                'new': {'m1': _make_mapping('m1', 'c1', 'x')},
            }
        )
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('[Concepts]', md)
        self.assertIn('[Mappings]', md)
        # No names/descriptions/translations data → should not appear in TOC
        self.assertNotIn('[Names]', md)
        self.assertNotIn('[Descriptions]', md)
        self.assertNotIn('[Translations]', md)

    def test_toc_excludes_sections_before_contents(self):
        data = _make_data(
            concepts={'new': {'c1': {'id': 'c1', 'display_name': 'A'}}},
            mappings={
                'new': {
                    'm1': {
                        'id': 'm1',
                        'from_concept': 'c1',
                        'to_concept': 'x',
                        'to_source': None,
                        'map_type': 'SAME-AS',
                    }
                }
            },
        )
        md = ChangelogMarkdownGenerator(data).generate()
        toc_block = md[md.index('## Contents'):md.index('## Concepts')]
        self.assertNotIn('Changelog](#', toc_block)
        self.assertNotIn('[Overview]', toc_block)
        self.assertNotIn('[Summary]', toc_block)
        self.assertNotIn('[Contents]', toc_block)
        self.assertIn('- [Concepts](#concepts)', toc_block)
        self.assertIn('- [Mappings](#mappings)', toc_block)

    def test_toc_lists_concept_subsections(self):
        data = _make_data(
            concepts={
                'new': {'c1': {'id': 'c1', 'display_name': 'A'}},
                'changed_retired': {'c2': {'id': 'c2', 'display_name': 'B'}},
                'changed_major': {'c3': {'id': 'c3', 'display_name': 'C'}},
            }
        )
        md = ChangelogMarkdownGenerator(data).generate()
        # Top-level entry points to #concepts; subitems use section-prefixed anchors
        self.assertIn('- [Concepts](#concepts)', md)
        self.assertIn('  - [Added](#concepts-added)', md)
        self.assertIn('  - [Retired](#concepts-retired)', md)
        self.assertIn('  - [Updated (Major)](#concepts-updated-major)', md)
        # 'Removed' and 'Updated (Minor)' are absent in the data → absent from TOC
        self.assertNotIn('[Removed](#concepts-removed)', md)
        self.assertNotIn('[Updated (Minor)]', md)

    def test_toc_lists_mapping_subsections(self):
        data = _make_data(mappings={
            'new': {'m1': _make_mapping('m1', 'c1', 'x')},
            'changed_minor': {'m2': _make_mapping('m2', 'c2', 'y')},
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('- [Mappings](#mappings)', md)
        self.assertIn('  - [Added](#mappings-added)', md)
        self.assertIn('  - [Updated](#mappings-updated)', md)
        self.assertNotIn('[Removed](#mappings-removed)', md)

    def test_toc_lists_embedded_mapping_subsections(self):
        data = _make_data(concepts={
            'changed_major': {
                'c1': {
                    'id': 'c1',
                    'display_name': 'Major',
                    'mappings': {
                        'new': [
                            {'id': 'm1', 'to_concept': 'x', 'to_source': None, 'map_type': 'SAME-AS'},
                        ],
                    },
                },
            },
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('- [Mappings](#mappings)', md)
        self.assertIn('  - [Added](#mappings-added)', md)

    def test_subsection_anchors_emitted_in_body(self):
        data = _make_data(
            concepts={'new': {'c1': {'id': 'c1', 'display_name': 'A'}}},
            mappings={'new': {'m1': _make_mapping('m1', 'c1', 'x')}},
        )
        md = ChangelogMarkdownGenerator(data).generate()
        # Each subsection TOC link must resolve to an anchor in the body
        self.assertIn('<a id="concepts-added"></a>', md)
        self.assertIn('<a id="mappings-added"></a>', md)

    def test_toc_lists_name_subsections_when_enriched(self):
        data = _make_data(concepts={
            'new': {'c1': {
                'id': 'c1', 'display_name': 'New',
                'names': [{'name': 'Foo', 'type': 'FULLY_SPECIFIED', 'locale': 'en'}],
                'descriptions': [],
            }},
            'changed_minor': {'c2': {
                'id': 'c2', 'display_name': 'Changed',
                'names': [{'external_id': 'e1', 'name': 'Bar v2', 'type': 'FULLY_SPECIFIED', 'locale': 'en'}],
                'prev_names': [{'external_id': 'e1', 'name': 'Bar v1', 'type': 'FULLY_SPECIFIED', 'locale': 'en'}],
                'descriptions': [],
            }},
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('- [Names](#names)', md)
        self.assertIn('  - [Added](#names-added)', md)
        self.assertIn('  - [Updated](#names-updated)', md)
        self.assertIn('<a id="names-added"></a>', md)
        self.assertIn('<a id="names-updated"></a>', md)


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
                        {
                            'name': 'English name',
                            'type': 'FULLY_SPECIFIED',
                            'locale': 'en',
                            'locale_preferred': True,
                        },
                        {
                            'name': 'Nome em português',
                            'type': 'FULLY_SPECIFIED',
                            'locale': 'pt',
                            'locale_preferred': False,
                        },
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

    def test_mappings_updated_table_shows_prev_fields(self):
        data = _make_data(mappings={
            'changed_major': {
                'm99': {
                    'id': 'm99',
                    'external_id': '250260ABBBBBBBBBBBBBBBBBBBBBBBBBBBBB',
                    'from_concept': 'c10',
                    'from_source': None,
                    'to_concept': '719709',
                    'to_source': 'IMO',
                    'map_type': 'SAME-AS',
                    'prev_to_concept': '719700',
                    'prev_to_source': 'IMO',
                    'prev_map_type': 'NARROWER-THAN',
                }
            }
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('### Updated', md)
        # Previous and current values both appear
        self.assertIn('719700', md)
        self.assertIn('719709', md)
        self.assertIn('NARROWER-THAN', md)
        self.assertIn('SAME-AS', md)
        # Column headers indicate before/after
        self.assertIn('Previous To Concept', md)
        self.assertIn('Updated To Concept', md)
        self.assertIn('Previous Map Type', md)
        self.assertIn('Updated Map Type', md)

    def test_mappings_updated_without_prev_falls_back_to_current(self):
        # Legacy data without prev_* fields (verbosity<4) still renders gracefully
        data = _make_data(mappings={
            'changed_minor': {
                'm1': {
                    'id': 'm1',
                    'from_concept': 'c1',
                    'to_concept': 'xyz',
                    'to_source': None,
                    'map_type': 'SAME-AS',
                }
            }
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('### Updated', md)
        self.assertIn('xyz', md)
        self.assertIn('SAME-AS', md)

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

    def test_embedded_mappings_from_changed_major_are_rendered(self):
        data = _make_data(concepts={
            'changed_major': {
                '170670': {
                    'id': '170670',
                    'display_name': 'Dolutegravir / emtricitabine / tenofovir alafenamide',
                    'names': [],
                    'prev_names': [],
                    'descriptions': [],
                    'prev_descriptions': [],
                    'mappings': {
                        'new': [
                            {
                                'id': '17333738',
                                'to_concept': '714767001',
                                'to_source': '/orgs/IHTSDO/sources/SNOMED-CT/',
                                'map_type': 'NARROWER-THAN',
                            },
                        ],
                    },
                },
            },
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('## Mappings', md)
        self.assertIn('<a id="mappings-170670"></a>[#170670]', md)
        self.assertIn('714767001', md)
        self.assertIn('NARROWER-THAN', md)

    def test_embedded_mapping_lists_do_not_invent_numeric_ids(self):
        data = _make_data(concepts={
            'changed_mappings_only': {
                'c1': {
                    'id': 'c1',
                    'display_name': 'Concept 1',
                    'mappings': {
                        'new': [
                            {'to_concept': 'x', 'to_source': None, 'map_type': 'SAME-AS'},
                            {'to_concept': 'y', 'to_source': None, 'map_type': 'SAME-AS'},
                        ],
                    },
                },
            },
        })
        added, _, _ = ChangelogMarkdownGenerator(data)._mapping_collections()
        self.assertNotIn(1, added)


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


class TestChangelogMarkdownGeneratorEnrichmentDetection(SimpleTestCase):
    def test_not_enriched_when_no_names_or_prev(self):
        data = _make_data(concepts={
            'changed_major': {'c1': {'id': 'c1', 'display_name': 'A'}},
            'changed_minor': {'c2': {'id': 'c2', 'display_name': 'B'}},
        })
        gen = ChangelogMarkdownGenerator(data)
        self.assertFalse(gen.is_enriched)

    def test_enriched_when_names_present(self):
        data = _make_data(concepts={
            'changed_major': {
                'c1': {
                    'id': 'c1', 'display_name': 'A',
                    'names': [{'name': 'Foo', 'type': 'FULLY_SPECIFIED', 'locale': 'en'}],
                    'prev_names': [],
                },
            },
        })
        self.assertTrue(ChangelogMarkdownGenerator(data).is_enriched)

    def test_enriched_when_mapping_has_external_id(self):
        data = _make_data(mappings={
            'new': {'m1': {
                'id': 'm1', 'from_concept': 'c1', 'to_concept': 'x', 'to_source': None,
                'map_type': 'SAME-AS', 'external_id': 'abc-123',
            }},
        })
        self.assertTrue(ChangelogMarkdownGenerator(data).is_enriched)

    def test_enriched_when_embedded_mapping_has_prev_fields(self):
        data = _make_data(concepts={
            'changed_mappings_only': {
                'c1': {
                    'id': 'c1',
                    'display_name': 'Concept 1',
                    'mappings': {
                        'changed_minor': [
                            {
                                'id': 'm1',
                                'to_concept': 'new',
                                'prev_to_concept': 'old',
                                'map_type': 'SAME-AS',
                            },
                        ],
                    },
                },
            },
        })
        self.assertTrue(ChangelogMarkdownGenerator(data).is_enriched)

    def test_notice_banner_present_only_when_not_enriched(self):
        non_enriched = _make_data(concepts={
            'changed_major': {'c1': {'id': 'c1', 'display_name': 'A'}},
        })
        self.assertIn('without enrichment', ChangelogMarkdownGenerator(non_enriched).generate())

        enriched = _make_data(concepts={
            'changed_major': {'c1': {
                'id': 'c1', 'display_name': 'A',
                'names': [{'name': 'Foo', 'type': 'FULLY_SPECIFIED', 'locale': 'en'}],
                'prev_names': [],
            }},
        })
        self.assertNotIn('without enrichment', ChangelogMarkdownGenerator(enriched).generate())

    def test_changed_concepts_still_rendered_without_enrichment(self):
        # Key guarantee: non-enriched input must not produce an empty "Updated" section.
        data = _make_data(concepts={
            'changed_major': {
                'c1': {'id': 'c1', 'display_name': 'Concept 1'},
                'c2': {'id': 'c2', 'display_name': 'Concept 2'},
            },
            'changed_minor': {
                'c3': {'id': 'c3', 'display_name': 'Concept 3'},
            },
        })
        md = ChangelogMarkdownGenerator(data).generate()
        self.assertIn('### Updated (Major)', md)
        self.assertIn('### Updated (Minor)', md)
        self.assertIn('#c1', md)
        self.assertIn('#c2', md)
        self.assertIn('#c3', md)
        # Without enrichment, the concepts table must not have a "Changed" column.
        # The Overview table does have a "Changed" header, so we check specifically
        # for the concept-table header pattern (which includes "Concept ID").
        self.assertNotIn('| Concept ID | Display Name | Concept Class | Changed |', md)


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
