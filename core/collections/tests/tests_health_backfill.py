import json
import os
import tempfile
from io import StringIO

from django.core.management import call_command, CommandError
from mock import patch

from core.collections.health import EMPTY, RULES_VERSION, ExpansionHealth
from core.collections.models import Expansion
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.management.commands.backfill_expansion_health import Command
from core.common.tests import OCLTestCase


class ExpansionHealthBackfillTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        self.expansion = Expansion.persist(index=False, collection_version=self.collection, mnemonic='autoexpand-HEAD')
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
        self.clear(self.expansion)

    @staticmethod
    def clear(expansion, **kwargs):
        Expansion.objects.filter(id=expansion.id).update(**{'health': None, 'health_is_current': None, **kwargs})

    @staticmethod
    def stored(expansion):
        return Expansion.objects.values_list('health', 'health_is_current').get(id=expansion.id)

    def another_expansion(self):
        collection = OrganizationCollectionFactory()
        expansion = Expansion.persist(index=False, collection_version=collection, mnemonic='autoexpand-HEAD')
        collection.expansion_uri = expansion.uri
        collection.save()
        return expansion

    def run_command(self, *args, **kwargs):
        out = StringIO()
        call_command('backfill_expansion_health', *args, stdout=out, **kwargs)
        return out.getvalue()

    def test_dry_run_computes_and_stores_nothing(self):
        out = self.run_command(self.expansion.uri)

        self.assertIn('DRY RUN. Rules version', out)
        self.assertIn(f'{self.expansion.uri}: None -> {EMPTY}', out)
        self.assertIn('"expansions": 1', out)
        self.assertEqual(self.stored(self.expansion), (None, None))

    def test_apply_stores_current_health(self):
        self.run_command(self.expansion.uri, apply=True)

        health, is_current = self.stored(self.expansion)
        self.assertTrue(is_current)
        self.assertEqual(health['state'], EMPTY)
        self.assertEqual(health['rules_version'], RULES_VERSION)

    def test_apply_needs_the_toggle_on(self):
        with patch.object(Expansion, 'is_health_on', return_value=False):
            with self.assertRaises(CommandError):
                self.run_command(self.expansion.uri, apply=True)

        self.assertEqual(self.stored(self.expansion), (None, None))

    def test_all_takes_only_expansions_that_need_it(self):
        current = self.another_expansion()
        not_current = self.another_expansion()
        older_rules = self.another_expansion()
        Expansion.objects.filter(id=not_current.id).update(health_is_current=False)
        Expansion.objects.filter(id=older_rules.id).update(health={'state': EMPTY, 'rules_version': 0})

        out = self.run_command(all=True)

        self.assertIn(self.expansion.uri, out)
        self.assertIn(not_current.uri, out)
        self.assertIn(older_rules.uri, out)
        self.assertNotIn(f'{current.uri}:', out)

        self.assertIn(f'{current.uri}:', self.run_command(all=True, force=True))

    def test_all_skips_and_reports_expansions_being_built(self):
        Expansion.objects.filter(id=self.expansion.id).update(is_processing=True)

        out = self.run_command(all=True, apply=True)

        self.assertIn(f'SKIPPED {self.expansion.uri}: being built', out)
        self.assertIn('"skipped_being_built": 1', out)
        self.assertEqual(self.stored(self.expansion), (None, None))

    def test_a_uri_being_built_is_skipped_too(self):
        Expansion.objects.filter(id=self.expansion.id).update(is_processing=True)

        out = self.run_command(self.expansion.uri, apply=True)

        self.assertIn(f'SKIPPED {self.expansion.uri}: being built', out)
        self.assertEqual(self.stored(self.expansion), (None, None))

    def test_all_skips_an_expansion_a_build_made_current_since_the_run_listed_it(self):
        current = self.another_expansion()
        with patch.object(Command, 'get_expansion_ids', return_value=[current.id]), \
                patch.object(ExpansionHealth, 'compute') as compute:
            out = self.run_command(all=True)

        compute.assert_not_called()
        self.assertIn(f'SKIPPED {current.uri}: now current', out)
        self.assertIn('"skipped_now_current": 1', out)

    def test_all_resumes_after_an_id_and_stops_at_the_limit(self):
        second = self.another_expansion()
        third = self.another_expansion()
        self.clear(second)
        self.clear(third)

        out = self.run_command(all=True, start_after_id=self.expansion.id, limit=1)

        self.assertNotIn(f'{self.expansion.uri}:', out)
        self.assertIn(f'{second.uri}:', out)
        self.assertNotIn(f'{third.uri}:', out)

    def test_collection_version_uri_takes_its_default_expansion(self):
        self.assertIn(f'{self.expansion.uri}:', self.run_command(self.collection.uri))

    def test_a_failure_is_reported_and_the_run_goes_on(self):
        other = self.another_expansion()
        results = [ValueError('boom'), {'state': EMPTY, 'reasons': []}]
        with patch.object(ExpansionHealth, 'compute', side_effect=results):
            out = self.run_command(self.expansion.uri, other.uri)

        self.assertIn(f'FAILED {self.expansion.uri}: ValueError: boom', out)
        self.assertIn(f'{other.uri}: ', out)
        self.assertIn('"failed": 1', out)

    def test_a_failure_while_applying_leaves_health_not_current(self):
        with patch.object(ExpansionHealth, 'compute', side_effect=ValueError('boom')):
            out = self.run_command(self.expansion.uri, apply=True)

        self.assertIn(f'FAILED {self.expansion.uri}: computing health failed', out)
        self.assertEqual(self.stored(self.expansion), (None, None))

    def test_output_has_a_line_per_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, 'report.jsonl')
            self.run_command(self.expansion.uri, output=output)
            with open(output, encoding='utf-8') as file:
                reports = [json.loads(line) for line in file]

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['before'], {'state': None, 'is_current': None, 'rules_version': None})
        self.assertEqual(reports[0]['health']['state'], EMPTY)
        self.assertTrue(reports[0]['is_default'])

    def test_needs_uris_or_all(self):
        with self.assertRaises(CommandError):
            self.run_command()
        with self.assertRaises(CommandError):
            self.run_command(self.expansion.uri, all=True)
