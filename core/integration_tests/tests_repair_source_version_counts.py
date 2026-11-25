import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError

from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory


class RepairSourceVersionCountsTest(OCLTestCase):
    """A stored count that no longer matches the version's members is reported, and with --apply recounted"""

    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory(mnemonic='MySource')
        concept = ConceptFactory(parent=self.source)
        retired_concept = ConceptFactory(parent=self.source, retired=True)
        mapping = MappingFactory(parent=self.source, from_concept=concept, to_concept=concept)
        self.version = OrganizationSourceFactory(
            mnemonic='MySource', organization=self.source.organization, version='v1', released=True)
        self.version.concepts.add(concept.get_latest_version(), retired_concept.get_latest_version())
        self.version.mappings.add(mapping.get_latest_version())
        self.version.update_children_counts(sync=True)
        Source.objects.filter(id=self.version.id).update(active_mappings=9)  # rows went without a recount

    def run_command(self, *args, **options):
        out = StringIO()
        call_command('repair_source_version_counts', *args, stdout=out, **options)
        lines = out.getvalue().splitlines()
        return [json.loads(line) for line in lines if line.startswith('{')], lines[-1]

    def get_counts(self):
        version = Source.objects.get(id=self.version.id)
        return version.active_concepts, version.active_mappings

    def test_a_dry_run_reports_a_stale_count_and_changes_nothing(self):
        stale, totals = self.run_command(all=True)

        self.assertEqual(
            stale, [{'id': self.version.id, 'uri': self.version.uri, 'released': True,
                     'active_mappings': {'stored': 9, 'members': 1}}])
        self.assertIn('"repaired": 0', totals)
        self.assertIn('"stale": 1', totals)
        self.assertEqual(self.get_counts(), (1, 9))

    @patch('core.sources.models.Source.clear_mappings_cache')
    def test_apply_recounts_the_stale_version_and_clears_its_cached_lists(self, clear_mappings_cache):
        _, totals = self.run_command(self.version.uri, apply=True)

        self.assertIn('"repaired": 1', totals)
        self.assertEqual(self.get_counts(), (1, 1))
        clear_mappings_cache.assert_called_once_with()
        stale, _ = self.run_command(all=True)
        self.assertEqual(stale, [])

    def test_head_uncounted_and_processing_versions_are_left_alone(self):
        Source.objects.filter(id=self.source.id).update(active_mappings=9)
        uncounted = OrganizationSourceFactory(
            mnemonic='MySource', organization=self.source.organization, version='v2', released=True)
        Source.objects.filter(id=uncounted.id).update(active_concepts=None, active_mappings=None)
        Source.objects.filter(id=self.version.id).update(_background_process_ids=['task-id'])

        stale, totals = self.run_command(all=True, apply=True)

        self.assertEqual(stale, [])
        self.assertIn('"skipped_processing": 1', totals)
        self.assertIn('"uncounted": 1', totals)
        self.assertEqual(Source.objects.get(id=self.source.id).active_mappings, 9)

    def test_needs_uris_or_all(self):
        with self.assertRaises(CommandError):
            self.run_command()
        with self.assertRaises(CommandError):
            self.run_command(self.source.uri)  # HEAD
