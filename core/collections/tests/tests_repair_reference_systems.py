import json
import os
import tempfile
from io import StringIO

from django.core.management import call_command

from core.collections.models import CollectionReference, Expansion
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.sources.tests.factories import OrganizationSourceFactory


class RepairReferenceSystemsTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory()
        self.concept = ConceptFactory(parent=self.source, mnemonic='1065').get_latest_version()
        self.collection = OrganizationCollectionFactory()

    def legacy(self, expression, **kwargs):
        return CollectionReference.objects.create(
            expression=expression, collection=self.collection, system=None, **kwargs)

    def run_command(self, **kwargs):
        out = StringIO()
        call_command('repair_reference_systems', stdout=out, **kwargs)
        return out.getvalue()

    @staticmethod
    def fields(reference):
        reference.refresh_from_db()
        return reference.system, reference.code, reference.resource_version, reference.reference_type

    def test_a_concept_version_reference_gets_its_system(self):
        reference = self.legacy(self.concept.uri, code='1065', resource_version=self.concept.version)

        self.run_command(apply=True)

        self.assertEqual(
            self.fields(reference), (self.source.uri, '1065', self.concept.version, 'concepts'))

    def test_a_second_run_changes_nothing(self):
        reference = self.legacy(self.concept.uri)
        self.run_command(apply=True)
        repaired = self.fields(reference)

        out = self.run_command(apply=True)

        self.assertNotIn(f'{reference.id} ', out)
        self.assertNotIn('"repaired"', out)
        self.assertEqual(self.fields(reference), repaired)

    def test_an_encoded_code_agrees_with_its_decoded_form(self):
        # the repair only reads the expression, so no concept needs to exist
        reference = self.legacy(f'{self.source.uri}concepts/A%2FB/7/', code='A/B', resource_version='7')

        self.run_command(apply=True)

        self.assertEqual(self.fields(reference)[:2], (self.source.uri, 'A/B'))

    def test_code_and_resource_version_are_filled_in_where_missing(self):
        reference = self.legacy(self.concept.uri)

        self.run_command(apply=True)

        self.assertEqual(
            self.fields(reference), (self.source.uri, '1065', self.concept.version, 'concepts'))

    def test_a_mapping_version_reference_gets_the_mappings_type(self):
        mapping = MappingFactory(parent=self.source).get_latest_version()
        reference = self.legacy(mapping.uri)

        self.run_command(apply=True)

        self.assertEqual(self.fields(reference), (self.source.uri, mapping.mnemonic, mapping.version, 'mappings'))

    def test_the_dry_run_lists_the_change_and_saves_nothing(self):
        reference = self.legacy(self.concept.uri, code='1065', resource_version=self.concept.version)

        out = self.run_command()

        self.assertIn('DRY RUN.', out)
        line = f'{reference.id} {self.collection.uri} {self.concept.uri}: {{"system": "{self.source.uri}"}}'
        self.assertIn(line, out)
        self.assertIn('"would repair": 1', out)
        self.assertEqual(self.fields(reference)[0], None)

    def test_references_that_are_not_concept_or_mapping_versions_are_left(self):
        unversioned = self.legacy(f'{self.source.uri}concepts/1065/')
        named_source_version = self.legacy(f'{self.source.uri}v1/concepts/1065/{self.concept.version}/')
        disagreeing = self.legacy(self.concept.uri, code='other')

        out = self.run_command(apply=True)

        # counted, not listed: most candidates are like it
        self.assertNotIn(f'LEFT {unversioned.id} ', out)
        self.assertIn('"left: its expression does not name a concept or mapping version": 1', out)
        self.assertIn(f'LEFT {named_source_version.id} ', out)
        self.assertIn('its expression names a source version too', out)
        self.assertIn(f'LEFT {disagreeing.id} ', out)
        self.assertIn('its code disagrees with its expression', out)
        for reference in (unversioned, named_source_version, disagreeing):
            self.assertIsNone(self.fields(reference)[0])

    def test_references_with_a_system_or_a_value_set_are_not_touched(self):
        with_system = CollectionReference.objects.create(
            expression=self.concept.uri, collection=self.collection, system='/orgs/X/sources/Y/')
        with_valueset = self.legacy(self.concept.uri, valueset=['/orgs/X/collections/V/'])

        out = self.run_command(apply=True)

        self.assertNotIn(str(with_system.id) + ' ', out)
        self.assertEqual(self.fields(with_system)[0], '/orgs/X/sources/Y/')
        self.assertIsNone(self.fields(with_valueset)[0])

    def test_no_member_or_expansion_changes(self):
        expansion = Expansion.persist(index=False, collection_version=self.collection, mnemonic='autoexpand-HEAD')
        reference = self.legacy(self.concept.uri)
        other = ConceptFactory(parent=self.source).get_latest_version()
        expansion.concepts.add(other)
        reference.concepts.add(other)

        self.run_command(apply=True)

        self.assertEqual(list(expansion.concepts.all()), [other])
        self.assertEqual(list(reference.concepts.all()), [other])

    def test_output_has_a_line_per_reference(self):
        reference = self.legacy(self.concept.uri)
        left = self.legacy(f'{self.source.uri}concepts/1065/')
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, 'report.jsonl')
            self.run_command(output=output)
            with open(output, encoding='utf-8') as file:
                reports = {report['id']: report for report in map(json.loads, file)}

        self.assertEqual(reports[reference.id]['before']['system'], None)
        self.assertEqual(reports[reference.id]['after']['system'], self.source.uri)
        self.assertEqual(reports[left.id]['left'], 'its expression does not name a concept or mapping version')
