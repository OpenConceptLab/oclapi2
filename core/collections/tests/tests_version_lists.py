import json
import os
import tempfile
from io import StringIO

from django.core.management import call_command, CommandError

from core.collections.models import CollectionReference, Expansion
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.collections.version_lists import ExpansionVersionLists
from core.common.tests import OCLTestCase
from core.common.utils import drop_version
from core.concepts.tests.factories import ConceptFactory
from core.sources.tests.factories import OrganizationSourceFactory


class ExpansionVersionListsTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory()
        self.concept = ConceptFactory(parent=self.source, mnemonic='c1')
        self.member = self.concept.get_latest_version()
        self.v1 = self.release('v1')
        self.v2 = self.release('v2')
        self.member.sources.add(self.v1, self.v2)
        self.collection = OrganizationCollectionFactory()
        self.expansion = ExpansionFactory(collection_version=self.collection)
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
        self.expansion.concepts.add(self.member)

    def release(self, version, source=None):
        source = source or self.source
        return OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version=version, released=True)

    def reference(self, members, include=True, **kwargs):
        reference = CollectionReference.objects.create(
            expression=self.concept.uri, collection=self.collection, system=self.source.uri, code=self.concept.mnemonic,
            reference_type='concepts', include=include, **kwargs)
        reference.concepts.set(members)
        return reference

    def rebuild(self):
        return ExpansionVersionLists(Expansion.objects.get(id=self.expansion.id)).build()

    @staticmethod
    def ids(lists, relation):
        return set(lists.after[relation])

    def test_versionless_reference_moves_its_recorded_version_to_evaluated(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])

        lists = self.rebuild()
        report = lists.to_report()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v1.id})
        self.assertEqual(self.ids(lists, 'explicit_source_versions'), set())
        self.assertEqual(report['gains_lock'], [drop_version(self.v1.uri)])
        self.assertEqual(report['changes']['explicit_source_versions'], {'added': [], 'removed': [self.v1.uri]})
        self.assertEqual(report['references'], [])

    def test_versionless_reference_takes_the_newest_recorded_version_holding_all_its_members(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1, self.v2])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v2.id})
        self.assertEqual(self.ids(lists, 'explicit_source_versions'), set())
        self.assertEqual(lists.notes[0]['holders'], [self.v2.uri, self.v1.uri])
        self.assertEqual(lists.notes[0]['chosen_by'], 'recorded')

    def test_versionless_reference_with_nothing_recorded_takes_the_newest_release_holding_its_members(self):
        self.reference([self.member])
        newer = self.release('v3')

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v2.id})
        self.assertNotIn(newer.id, self.ids(lists, 'evaluated_source_versions'))
        self.assertEqual(lists.notes[0]['chosen_by'], 'release')

    def test_versionless_reference_falls_back_to_head(self):
        self.member.sources.remove(self.v1, self.v2)
        self.reference([self.member])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.source.id})
        self.assertEqual(lists.notes[0]['chosen_by'], 'HEAD')

    def test_reference_no_one_version_holds_keeps_what_is_recorded(self):
        other = ConceptFactory(parent=self.source, mnemonic='c2').get_latest_version()
        self.member.sources.remove(self.v2)
        other.sources.remove(self.source)
        other.sources.add(self.v2)
        self.expansion.concepts.add(other)
        self.reference([self.member, other])
        self.expansion.explicit_source_versions.set([self.v1, self.v2])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {self.v1.id, self.v2.id})
        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), set())
        self.assertEqual(lists.notes[0]['reason'], 'no one version holds all its members')
        self.assertEqual(lists.to_report()['changed'], False)

    def test_reference_without_members_keeps_what_is_recorded(self):
        self.reference([])
        self.expansion.explicit_source_versions.set([self.v1])
        self.expansion.evaluated_source_versions.set([self.v2])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {self.v1.id})
        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v2.id})
        self.assertEqual(lists.notes[0]['reason'], 'it has no members')

    def test_reference_naming_a_version_records_it_as_explicit(self):
        self.reference([self.member], version='v1')
        self.expansion.evaluated_source_versions.set([self.v1])

        lists = self.rebuild()
        report = lists.to_report()

        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {self.v1.id})
        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), set())
        self.assertEqual(report['loses_lock'], [drop_version(self.v1.uri)])

    def test_version_both_kinds_of_reference_use_is_in_both_lists(self):
        self.reference([self.member])
        self.reference([self.member], version='v1')
        self.expansion.explicit_source_versions.set([self.v1])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {self.v1.id})
        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v1.id})
        self.assertEqual(lists.to_report()['in_both'], [self.v1.uri])

    def test_resource_versioned_reference_records_the_recorded_version_holding_its_resource_version(self):
        self.reference([self.member], resource_version=self.member.version)
        self.expansion.explicit_source_versions.set([self.v1])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {self.v1.id})
        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), set())

    def test_resource_versioned_reference_with_nothing_recorded_takes_the_release_adding_would_pick(self):
        self.reference([self.member], resource_version=self.member.version)

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {self.v2.id})

    def test_exclude_reference_is_judged_by_its_own_members(self):
        excluded = ConceptFactory(parent=self.source, mnemonic='c2').get_latest_version()
        excluded.sources.add(self.v1)
        self.reference([self.member])
        self.reference([excluded], include=False)
        self.expansion.explicit_source_versions.set([self.v1])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v1.id})

    def test_version_no_reference_uses_is_dropped(self):
        self.reference([self.member])
        other_source = OrganizationSourceFactory()
        other_release = self.release('v1', other_source)
        self.expansion.explicit_source_versions.set([self.v1, other_release])

        report = self.rebuild().to_report()

        self.assertEqual(
            report['changes']['explicit_source_versions']['removed'], sorted([other_release.uri, self.v1.uri]))
        self.assertEqual(report['after']['evaluated_source_versions'], [self.v1.uri])

    def test_versionless_value_set_records_its_recorded_version_holding_the_members(self):
        value_set = OrganizationCollectionFactory()
        release = OrganizationCollectionFactory(
            mnemonic=value_set.mnemonic, organization=value_set.organization, version='v1', released=True)
        release_expansion = ExpansionFactory(collection_version=release)
        release.expansion_uri = release_expansion.uri
        release.save()
        release_expansion.concepts.add(self.member)
        reference = CollectionReference.objects.create(
            expression=value_set.uri, collection=self.collection, valueset=[value_set.uri], reference_type='concepts')
        reference.concepts.set([self.member])
        self.expansion.explicit_collection_versions.set([release])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'evaluated_collection_versions'), {release.id})
        self.assertEqual(self.ids(lists, 'explicit_collection_versions'), set())

    def test_apply_writes_the_lists_and_leaves_members_as_they_are(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])
        members = (list(self.expansion.concepts.all()), list(self.expansion.mappings.all()))

        self.rebuild().apply()

        self.assertEqual(list(self.expansion.explicit_source_versions.all()), [])
        self.assertEqual(list(self.expansion.evaluated_source_versions.all()), [self.v1])
        self.assertEqual((list(self.expansion.concepts.all()), list(self.expansion.mappings.all())), members)

    def test_rebuilding_rebuilt_lists_changes_nothing(self):
        self.reference([self.member])
        self.reference([self.member], version='v2')
        self.expansion.explicit_source_versions.set([self.v1, self.v2])
        self.rebuild().apply()

        self.assertFalse(self.rebuild().to_report()['changed'])

    def test_command_dry_run_reports_and_writes_nothing(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])
        out = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, 'report.jsonl')
            call_command('rebuild_expansion_version_lists', self.expansion.uri, output=output, stdout=out)
            with open(output, encoding='utf-8') as file:
                reports = [json.loads(line) for line in file]

        self.assertIn('DRY RUN. Rule:', out.getvalue())
        self.assertIn('"changed": 1', out.getvalue())
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['before']['explicit_source_versions'], [self.v1.uri])
        self.assertEqual(reports[0]['after']['evaluated_source_versions'], [self.v1.uri])
        self.assertEqual(list(self.expansion.explicit_source_versions.all()), [self.v1])
        self.assertEqual(list(self.expansion.evaluated_source_versions.all()), [])

    def test_command_apply_takes_a_collection_version_for_its_default_expansion(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])

        call_command('rebuild_expansion_version_lists', self.collection.uri, apply=True, stdout=StringIO())

        self.assertEqual(list(self.expansion.explicit_source_versions.all()), [])
        self.assertEqual(list(self.expansion.evaluated_source_versions.all()), [self.v1])

    def test_command_all_resumes_after_an_id(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])
        out = StringIO()

        call_command('rebuild_expansion_version_lists', all=True, start_after_id=self.expansion.id, stdout=out)

        self.assertNotIn(self.expansion.uri, out.getvalue())

    def test_command_needs_uris_or_all(self):
        with self.assertRaises(CommandError):
            call_command('rebuild_expansion_version_lists', stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('rebuild_expansion_version_lists', self.expansion.uri, all=True, stdout=StringIO())
