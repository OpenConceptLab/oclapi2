import json
import os
import tempfile
from io import StringIO

from django.core.management import call_command, CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from mock import patch

from core.collections.models import CollectionReference, Expansion
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.collections.version_lists import ExpansionVersionLists
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLTestCase
from core.common.utils import drop_version
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
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

    def members_split_across_v1_and_v2(self):
        other = ConceptFactory(parent=self.source, mnemonic='c2').get_latest_version()
        self.member.sources.remove(self.v2)
        other.sources.remove(self.source)
        other.sources.add(self.v2)
        self.expansion.concepts.add(other)
        return other

    def test_reference_no_one_version_holds_records_each_recorded_version_holding_some_of_its_members(self):
        other = self.members_split_across_v1_and_v2()
        self.reference([self.member, other])
        third = self.release('v3')
        self.expansion.explicit_source_versions.set([self.v1, self.v2, third])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v1.id, self.v2.id})
        self.assertEqual(self.ids(lists, 'explicit_source_versions'), set())
        self.assertEqual(lists.notes[0]['reason'], 'no one version holds all its members')
        self.assertEqual(lists.notes[0]['partial_holders'], [self.v2.uri, self.v1.uri])

    def test_reference_no_one_version_holds_records_only_versions_that_add_members(self):
        other = self.members_split_across_v1_and_v2()
        third = self.release('v3')
        self.member.sources.add(third)
        self.reference([self.member, other])
        self.expansion.explicit_source_versions.set([self.v1, self.v2, third])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {third.id, self.v2.id})
        self.assertEqual(lists.notes[0]['partial_holders'], [third.uri, self.v2.uri])

    def test_reference_no_recorded_version_holds_keeps_what_is_recorded(self):
        other = self.members_split_across_v1_and_v2()
        self.reference([self.member, other])
        third = self.release('v3')
        self.expansion.explicit_source_versions.set([third])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {third.id})
        self.assertEqual(lists.notes[0]['kept'], [third.uri])
        self.assertFalse(lists.to_report()['changed'])

    def test_reference_without_members_keeps_what_is_recorded(self):
        self.reference([])
        self.expansion.explicit_source_versions.set([self.v1])
        self.expansion.evaluated_source_versions.set([self.v2])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {self.v1.id})
        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v2.id})
        self.assertEqual(lists.notes[0]['reason'], 'it has no members')

    def test_reference_without_members_keeps_nothing_when_another_reference_records_its_repo(self):
        self.reference([self.member])
        self.reference([])
        self.expansion.explicit_source_versions.set([self.v1, self.v2])

        lists = self.rebuild()
        report = lists.to_report()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v2.id})
        self.assertEqual(self.ids(lists, 'explicit_source_versions'), set())
        self.assertEqual(report['in_both'], [])
        self.assertEqual(lists.notes[-1]['kept'], [])
        self.assertEqual(lists.notes[-1]['reason'], 'it has no members; other references record its repo')

    def test_reference_without_members_keeps_its_lock_when_another_reference_names_a_version(self):
        self.reference([])
        self.reference([self.member], version='v2')
        self.expansion.explicit_source_versions.set([self.v2])
        self.expansion.evaluated_source_versions.set([self.v1])

        lists = self.rebuild()
        report = lists.to_report()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v1.id})
        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {self.v2.id})
        self.assertEqual(report['loses_lock'], [])
        self.assertFalse(report['changed'])

    def test_versionless_reference_keeps_its_lock_over_a_newer_version_another_reference_names(self):
        self.reference([self.member])
        self.reference([self.member], version='v2')
        self.expansion.evaluated_source_versions.set([self.v1])
        self.expansion.explicit_source_versions.set([self.v2])

        lists = self.rebuild()
        report = lists.to_report()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v1.id})
        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {self.v2.id})
        self.assertEqual(report['changes_lock'], [])
        self.assertFalse(report['changed'])

    def test_a_lock_moving_to_another_version_is_reported(self):
        self.member.sources.remove(self.v1)
        self.reference([self.member])
        self.expansion.evaluated_source_versions.set([self.v1])
        self.expansion.explicit_source_versions.set([self.v2])

        report = self.rebuild().to_report()

        self.assertEqual(report['after']['evaluated_source_versions'], [self.v2.uri])
        self.assertEqual(report['changes_lock'], [drop_version(self.v1.uri)])
        self.assertEqual((report['gains_lock'], report['loses_lock']), ([], []))

    def test_mapping_members_are_judged_like_concepts(self):
        mapping = MappingFactory(parent=self.source).get_latest_version()
        mapping.sources.add(self.v1)
        self.expansion.mappings.add(mapping)
        reference = CollectionReference.objects.create(
            expression=mapping.uri, collection=self.collection, system=self.source.uri, reference_type='mappings')
        reference.mappings.set([mapping])
        self.expansion.explicit_source_versions.set([self.v1])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v1.id})

    def test_system_version_parameter_names_the_version_recorded(self):
        self.reference([self.member])
        Expansion.objects.filter(id=self.expansion.id).update(parameters={'system-version': self.v1.uri})

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v1.id})

    def test_query_count_does_not_grow_with_references(self):
        self.reference([self.member])
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])

        def count():
            with CaptureQueriesContext(connection) as context:
                self.rebuild().to_report()
            return len(context.captured_queries)

        two = count()
        for index in range(4):
            member = ConceptFactory(parent=self.source, mnemonic=f'more{index}').get_latest_version()
            member.sources.add(self.v1)
            self.expansion.concepts.add(member)
            self.reference([member])

        self.assertEqual(count(), two)

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

    def test_apply_writes_nothing_when_the_lists_changed_since_the_build(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])
        lists = self.rebuild()
        self.expansion.evaluated_source_versions.add(self.v2)

        with self.assertRaises(RuntimeError):
            lists.apply()

        self.assertEqual(list(self.expansion.explicit_source_versions.all()), [self.v1])
        self.assertEqual(list(self.expansion.evaluated_source_versions.all()), [self.v2])

    def value_set_release(self):
        value_set = OrganizationCollectionFactory()
        release = OrganizationCollectionFactory(
            mnemonic=value_set.mnemonic, organization=value_set.organization, version='v1', released=True)
        release_expansion = ExpansionFactory(collection_version=release)
        release.expansion_uri = release_expansion.uri
        release.save()
        release_expansion.concepts.add(self.member)
        return value_set, release

    def value_set_reference(self, value_set, members):
        reference = CollectionReference.objects.create(
            expression=value_set.uri, collection=self.collection, valueset=[value_set.uri], reference_type='concepts')
        reference.concepts.set(members)
        return reference

    def test_versionless_value_set_held_by_a_recorded_version_looks_up_no_releases(self):
        value_set, release = self.value_set_release()
        self.value_set_reference(value_set, [self.member])
        self.value_set_reference(value_set, [self.member])
        self.expansion.explicit_collection_versions.set([release])

        with patch.object(ExpansionVersionLists, 'releases_then_head') as releases_then_head:
            lists = self.rebuild()

        releases_then_head.assert_not_called()
        self.assertEqual(self.ids(lists, 'evaluated_collection_versions'), {release.id})

    def test_versionless_value_set_with_nothing_recorded_takes_the_release_holding_its_members(self):
        value_set, release = self.value_set_release()
        self.value_set_reference(value_set, [self.member])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'evaluated_collection_versions'), {release.id})
        self.assertEqual(lists.notes[0]['chosen_by'], 'release')

    def test_versionless_value_set_without_members_notes_the_version_it_resolves_to(self):
        value_set, _ = self.value_set_release()
        self.value_set_reference(value_set, [])

        lists = self.rebuild()

        self.assertEqual(len(lists.after['evaluated_collection_versions']), 1)
        self.assertEqual(lists.notes[0]['chosen_by'], 'resolved')
        self.assertEqual(lists.notes[0]['reason'], 'it has no members')

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

    def test_command_reports_a_failure_and_goes_on(self):
        out = StringIO()
        with patch.object(ExpansionVersionLists, 'build', side_effect=ValueError('boom')):
            call_command('rebuild_expansion_version_lists', self.expansion.uri, stdout=out)

        self.assertIn(f'FAILED {self.expansion.id}: ValueError: boom', out.getvalue())
        self.assertIn('"failed": 1', out.getvalue())

    def private_repo_reference(self, organization=None):
        """A version-less reference to a private source whose only holders are a private release and HEAD"""
        source = OrganizationSourceFactory(public_access=ACCESS_TYPE_NONE, **(
            {'organization': organization} if organization else {}))
        member = ConceptFactory(parent=source, mnemonic='p1').get_latest_version()
        release = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        member.sources.add(release)
        self.expansion.concepts.add(member)
        reference = CollectionReference.objects.create(
            expression=source.uri + 'concepts/p1/', collection=self.collection, system=source.uri, code='p1',
            reference_type='concepts')
        reference.concepts.set([member])
        return release

    def test_another_owners_private_release_is_not_a_candidate(self):
        self.private_repo_reference()

        lists = self.rebuild()

        self.assertEqual(lists.after['evaluated_source_versions'], {})
        self.assertEqual(lists.notes[0]['kept'], [])

    def test_the_collection_owners_own_private_release_is_a_candidate(self):
        release = self.private_repo_reference(self.collection.organization)

        self.assertEqual(self.ids(self.rebuild(), 'evaluated_source_versions'), {release.id})

    def test_report_counts_members_whose_reported_release_changes(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v2])
        self.expansion.evaluated_source_versions.set([self.v1])

        report = self.rebuild().to_report()

        # FHIR reported v2 (explicit pin first); the rebuild keeps the v1 lock and drops the unused v2
        self.assertEqual(report['after']['evaluated_source_versions'], [self.v1.uri])
        self.assertEqual(report['reported_release_changes'], 1)

    def test_report_counts_no_reported_release_change_when_the_pick_stays(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])

        report = self.rebuild().to_report()

        self.assertTrue(report['changed'])
        self.assertEqual(report['reported_release_changes'], 0)

    def released_expansion(self):
        released = OrganizationCollectionFactory(
            mnemonic=self.collection.mnemonic, organization=self.collection.organization, version='v1', released=True)
        expansion = ExpansionFactory(collection_version=released)
        released.expansion_uri = expansion.uri
        released.save()
        expansion.explicit_source_versions.set([self.v1])
        return expansion

    def test_command_skip_released_leaves_released_versions_out(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])
        released = self.released_expansion()

        out = StringIO()
        call_command('rebuild_expansion_version_lists', all=True, skip_released=True, stdout=out)
        call_command('rebuild_expansion_version_lists', released.uri, skip_released=True, stdout=out)

        self.assertIn(f'{self.expansion.uri}:', out.getvalue())
        self.assertNotIn(f'{released.uri}:', out.getvalue())

    def test_command_counts_released_and_unreleased_apart(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])
        released = self.released_expansion()
        out = StringIO()

        call_command('rebuild_expansion_version_lists', self.expansion.uri, released.uri, stdout=out)

        totals = json.loads(out.getvalue().split('DRY RUN done: ')[1].splitlines()[0])
        self.assertEqual(
            {key: totals.get(key) for key in ('expansions_released', 'expansions_unreleased', 'changed_unreleased')},
            {'expansions_released': 1, 'expansions_unreleased': 1, 'changed_unreleased': 1})
        self.assertIn(f'{released.uri}: ', out.getvalue())
        self.assertIn('released', out.getvalue().split(f'{released.uri}: ')[1].splitlines()[0])

    def test_a_resource_versioned_reference_never_picks_another_owners_private_release(self):
        private = OrganizationSourceFactory(public_access=ACCESS_TYPE_NONE)
        member = ConceptFactory(parent=private, mnemonic='p1').get_latest_version()
        release = OrganizationSourceFactory(
            mnemonic=private.mnemonic, organization=private.organization, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        member.sources.add(release)
        reference = CollectionReference.objects.create(
            expression=member.uri, collection=self.collection, system=private.uri, code='p1',
            resource_version=member.version, reference_type='concepts')
        reference.concepts.set([member])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'explicit_source_versions') & {release.id, private.id}, set())

    def test_a_value_set_reference_without_members_never_records_another_owners_private_version(self):
        value_set = OrganizationCollectionFactory(public_access=ACCESS_TYPE_NONE)
        OrganizationCollectionFactory(
            mnemonic=value_set.mnemonic, organization=value_set.organization, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        self.value_set_reference(value_set, [])

        self.assertEqual(self.rebuild().after['evaluated_collection_versions'], {})

    def reference_with_system(self, system):
        reference = CollectionReference.objects.create(
            expression=self.concept.uri, collection=self.collection, system=system, code=self.concept.mnemonic,
            reference_type='concepts')
        reference.concepts.set([self.member])
        return reference

    def test_a_system_that_no_longer_resolves_keeps_the_versions_its_members_came_from(self):
        self.reference_with_system('/orgs/gone/sources/gone/')
        self.expansion.explicit_source_versions.set([self.v1])

        lists = self.rebuild()

        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {self.v1.id})
        self.assertEqual(
            (lists.notes[0]['kept'], lists.notes[0]['reason']), ([self.v1.uri], 'its system does not resolve'))
        self.assertFalse(lists.to_report()['changed'])

    def test_a_system_resolving_to_another_repository_keeps_the_versions_its_members_came_from(self):
        elsewhere = OrganizationSourceFactory()
        self.reference_with_system(elsewhere.uri)
        self.expansion.explicit_source_versions.set([self.v1])
        out = StringIO()

        lists = self.rebuild()
        call_command('rebuild_expansion_version_lists', self.expansion.uri, stdout=out)

        self.assertEqual(self.ids(lists, 'explicit_source_versions'), {self.v1.id})
        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), set())
        self.assertEqual(
            lists.notes[0]['reason'], 'its system resolves to a repository that holds none of its members')
        self.assertIn('"with_references_resolving_elsewhere": 1', out.getvalue())

    def test_a_second_rebuild_picks_the_same_evaluated_versions(self):
        # r1's member is in v1 and v2, r2's only in v2: both settle on v2, as a second rebuild would pick
        other = ConceptFactory(parent=self.source, mnemonic='c2').get_latest_version()
        other.sources.add(self.v2)
        self.expansion.concepts.add(other)
        self.reference([self.member])
        second = CollectionReference.objects.create(
            expression=other.uri, collection=self.collection, system=self.source.uri, code='c2',
            reference_type='concepts')
        second.concepts.set([other])
        self.expansion.evaluated_source_versions.set([self.v1])
        self.expansion.explicit_source_versions.set([self.v2])

        lists = self.rebuild()
        self.assertEqual(self.ids(lists, 'evaluated_source_versions'), {self.v2.id})
        lists.apply()

        self.assertFalse(self.rebuild().to_report()['changed'])

    def test_command_skip_released_rechecks_a_version_released_after_selection(self):
        released = self.released_expansion()
        out = StringIO()

        with patch(
                'core.common.management.commands.rebuild_expansion_version_lists.Command.get_expansion_ids',
                return_value=[released.id]):
            call_command('rebuild_expansion_version_lists', all=True, skip_released=True, apply=True, stdout=out)

        self.assertIn('"skipped_released": 1', out.getvalue())
        self.assertEqual(list(released.explicit_source_versions.all()), [self.v1])

    def test_command_fails_on_a_bad_output_path_before_writing_anything(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])

        with self.assertRaises(OSError):
            call_command(
                'rebuild_expansion_version_lists', self.expansion.uri, apply=True,
                output='/no-such-directory/report.jsonl', stdout=StringIO())

        self.assertEqual(list(self.expansion.explicit_source_versions.all()), [self.v1])

    def test_command_says_how_many_expansions_gain_a_lock(self):
        self.reference([self.member])
        self.expansion.explicit_source_versions.set([self.v1])
        out = StringIO()

        call_command('rebuild_expansion_version_lists', self.expansion.uri, stdout=out)

        self.assertIn('Gaining a lock: 1 expansion(s), 1 of them unreleased collection versions, 1 of those HEAD',
                      out.getvalue())

    def test_command_needs_uris_or_all(self):
        with self.assertRaises(CommandError):
            call_command('rebuild_expansion_version_lists', stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('rebuild_expansion_version_lists', self.expansion.uri, all=True, stdout=StringIO())
