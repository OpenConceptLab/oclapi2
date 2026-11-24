from django.db import connection
from django.test.utils import CaptureQueriesContext
from mock import patch

from core.collections.health import (
    ExpansionHealth, RELEASE, UNRELEASED, HEAD_MEMBER, INTERIM, MISMATCH, AMBIGUOUS,
    UNATTRIBUTED, EMPTY, UNHEALTHY, HEAD_RESOLVED, HEALTHY)
from core.collections.models import CollectionReference, Expansion
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory


class ExpansionHealthTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory()
        self.collection = OrganizationCollectionFactory(organization=self.source.organization)
        self.expansion = ExpansionFactory(collection_version=self.collection, mnemonic='e1')

    def release(self, version, *members):
        release = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.source.organization, version=version, released=True)
        for member in members:
            member.sources.add(release)
        return release

    def concept_with_replaced_version(self):
        """old: a version no release and not HEAD holds; new: its replacement, in HEAD"""
        concept = ConceptFactory(parent=self.source)
        old = concept.get_latest_version()
        new = ConceptFactory(parent=self.source, mnemonic=concept.mnemonic, versioned_object=concept, version='new')
        old.sources.remove(self.source)
        return concept, old, new

    def reference(self, concept, *members, **kwargs):
        """An include reference to concept whose resolved resources are members, all added to the expansion"""
        reference = CollectionReference(
            expression=f'{self.source.uri}concepts/{concept.mnemonic}/', collection=self.collection,
            system=self.source.uri, code=concept.mnemonic, **kwargs)
        reference.save()
        reference.concepts.add(*members)
        self.expansion.concepts.add(*members)
        return reference

    def classify(self):
        return {
            member_id: member_class
            for member_id, (member_class, _, _) in ExpansionHealth(self.expansion).classify('concepts').items()}

    def health(self):
        return ExpansionHealth(self.expansion).compute()

    @staticmethod
    def release_or_head_queries(context):
        return [
            query for query in context.captured_queries
            if '"concepts_sources"' in query['sql'] and '"released"' in query['sql']]

    def reason_codes(self, health):
        return {reason['code']: reason['count'] for reason in health['reasons']}

    def test_member_of_the_release_its_reference_resolved_to(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        release = self.release('v1', version)
        self.expansion.evaluated_source_versions.add(release)
        self.reference(concept, version)

        self.assertEqual(self.classify(), {version.id: RELEASE})
        health = self.health()
        self.assertEqual(health['state'], HEALTHY)
        self.assertEqual(health['reasons'], [])
        self.assertEqual(health['members']['concepts'][RELEASE], 1)
        self.assertEqual(health['rules_version'], 1)

    def test_member_of_head_makes_the_expansion_head_resolved(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        self.reference(concept, version)

        self.assertEqual(self.classify(), {version.id: HEAD_MEMBER})
        health = self.health()
        self.assertEqual(health['state'], HEAD_RESOLVED)
        self.assertEqual(health['head_resolved_repositories'], [self.source.uri])

    def test_member_from_a_resource_versioned_reference(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        self.release('v1', version)
        self.reference(concept, version, resource_version=version.version)

        # judged by the versions of its repository that hold it, as any member is
        self.assertEqual(self.classify(), {version.id: RELEASE})
        health = self.health()
        self.assertEqual(health['state'], UNHEALTHY)
        self.assertEqual(self.reason_codes(health), {'resource_versioned_references': 1})

    def test_a_pinned_version_no_release_or_head_holds_is_interim(self):
        concept, old, _ = self.concept_with_replaced_version()
        self.reference(concept, old, resource_version=old.version)

        self.assertEqual(self.classify(), {old.id: INTERIM})
        self.assertEqual(
            self.reason_codes(self.health()), {'resource_versioned_references': 1, 'interim_resource_versions': 1})

    def test_a_head_carrying_the_legacy_released_flag_is_not_a_release(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        Source.objects.filter(id=self.source.id).update(released=True)
        self.reference(concept, version)

        self.assertEqual(self.classify(), {version.id: HEAD_MEMBER})

    def test_interim_resource_version_no_release_or_head_holds(self):
        concept, old, new = self.concept_with_replaced_version()
        release = self.release('v1', new)
        self.expansion.evaluated_source_versions.add(release)
        self.reference(concept, old)

        self.assertEqual(self.classify(), {old.id: INTERIM})
        self.assertEqual(self.reason_codes(self.health()), {'interim_resource_versions': 1})

    def test_version_mismatch_when_another_release_holds_the_member(self):
        concept, old, new = self.concept_with_replaced_version()
        self.release('v1', old)
        release = self.release('v2', new)
        self.expansion.evaluated_source_versions.add(release)
        self.reference(concept, old)

        self.assertEqual(self.classify(), {old.id: MISMATCH})
        mismatch = next(reason for reason in self.health()['reasons'] if reason['code'] == 'version_mismatches')
        self.assertEqual(mismatch, {'code': 'version_mismatches', 'count': 1, 'head_concept': 0, 'head_mapping': 0})

    def test_head_concept_as_a_member_is_a_mismatch_counted_apart(self):
        head_concept = ConceptFactory(parent=self.source)
        self.reference(head_concept, head_concept)

        self.assertEqual(self.classify(), {head_concept.id: MISMATCH})
        mismatch = next(reason for reason in self.health()['reasons'] if reason['code'] == 'version_mismatches')
        self.assertEqual(mismatch['head_concept'], 1)

    def test_head_concept_named_by_a_pinned_reference_too_stays_a_mismatch(self):
        head_concept = ConceptFactory(parent=self.source)
        self.reference(head_concept, head_concept)
        pinned = CollectionReference(
            expression=head_concept.uri, collection=self.collection, system=self.source.uri,
            code=head_concept.mnemonic, resource_version=head_concept.version)
        pinned.save()
        pinned.concepts.add(head_concept)

        # a HEAD object is a mismatch whichever reference judges it, so it is never ambiguous
        self.assertEqual(self.classify(), {head_concept.id: MISMATCH})
        reasons = {reason['code']: reason for reason in self.health()['reasons']}
        self.assertEqual(reasons['version_mismatches']['head_concept'], 1)
        self.assertNotIn('ambiguous_members', reasons)

    def test_member_two_references_judge_differently_is_ambiguous(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        release = self.release('v1', version)
        draft = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.source.organization, version='draft', released=False)
        version.sources.add(draft)
        self.expansion.explicit_source_versions.add(release, draft)
        self.reference(concept, version, version='v1')
        self.reference(concept, version, version='draft')

        classes = ExpansionHealth(self.expansion).classify('concepts')
        self.assertEqual(classes[version.id][0], AMBIGUOUS)
        self.assertEqual(len(classes[version.id][1]), 2)
        self.assertEqual(self.reason_codes(self.health())['ambiguous_members'], 1)

    def test_member_no_reference_resolved_to_is_unattributed(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        self.expansion.concepts.add(version)

        self.assertEqual(self.classify(), {version.id: UNATTRIBUTED})
        self.assertEqual(self.reason_codes(self.health()), {'unattributed_members': 1})

    def test_exclude_reference_attributes_nothing(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        self.reference(concept, version, include=False)

        self.assertEqual(self.classify(), {version.id: UNATTRIBUTED})

    def test_value_set_member_is_judged_against_that_collection_version_expansion(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        other = OrganizationCollectionFactory(organization=self.source.organization)
        other_release = OrganizationCollectionFactory(
            mnemonic=other.mnemonic, organization=other.organization, version='v1', released=True)
        other_expansion = ExpansionFactory(collection_version=other_release, mnemonic='e1')
        other_release.expansion_uri = other_expansion.uri
        other_release.save()
        other_expansion.concepts.add(version)
        reference = CollectionReference(
            expression=other_release.uri, collection=self.collection, valueset=[other_release.uri])
        reference.save()
        reference.concepts.add(version)
        self.expansion.concepts.add(version)

        self.assertEqual(self.classify(), {version.id: RELEASE})

        other_expansion.concepts.remove(version)
        self.assertEqual(self.classify(), {version.id: MISMATCH})

    def value_set_release(self, value_set, version, *members):
        release = OrganizationCollectionFactory(
            mnemonic=value_set.mnemonic, organization=value_set.organization, version=version, released=True)
        expansion = ExpansionFactory(collection_version=release, mnemonic=f'e-{version}')
        release.expansion_uri = expansion.uri
        release.save()
        expansion.concepts.add(*members)
        return release

    def value_set_reference(self, valueset, *members, include=True):
        reference = CollectionReference(
            expression=valueset, collection=self.collection, valueset=[valueset], include=include)
        reference.save()
        reference.concepts.add(*members)
        if include:
            self.expansion.concepts.add(*members)
        return reference

    def test_a_retired_named_value_set_version_stays_what_the_reference_resolved_to(self):
        version = ConceptFactory(parent=self.source).get_latest_version()
        value_set = OrganizationCollectionFactory(organization=self.source.organization)
        release = self.value_set_release(value_set, 'v1', version)
        self.expansion.explicit_collection_versions.add(release)
        self.value_set_reference(release.uri, version)

        self.assertEqual(self.classify(), {version.id: RELEASE})
        type(release).objects.filter(id=release.id).update(retired=True)
        self.expansion = Expansion.objects.get(id=self.expansion.id)
        self.assertEqual(self.classify(), {version.id: RELEASE})

    def test_two_named_value_set_versions_are_not_inconsistent(self):
        first, second = (ConceptFactory(parent=self.source).get_latest_version() for _ in range(2))
        value_set = OrganizationCollectionFactory(organization=self.source.organization)
        v1, v2 = self.value_set_release(value_set, 'v1', first), self.value_set_release(value_set, 'v2', second)
        self.expansion.explicit_collection_versions.add(v1, v2)
        self.value_set_reference(v1.uri, first)
        self.value_set_reference(v2.uri, second)

        health = self.health()
        self.assertNotIn('inconsistent_resolution', self.reason_codes(health))
        self.assertEqual(health['multiple_named_versions'], [value_set.uri])

    def test_an_exclude_against_a_value_set_with_no_expansion_is_counted(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        self.expansion.evaluated_source_versions.add(self.release('v1', version))
        self.reference(concept, version)
        value_set = OrganizationCollectionFactory(organization=self.source.organization)
        unexpanded = OrganizationCollectionFactory(
            mnemonic=value_set.mnemonic, organization=value_set.organization, version='v1', released=True)
        self.value_set_reference(unexpanded.uri, include=False)

        self.assertEqual(self.reason_codes(self.health()), {'value_set_without_expansion': 1})

    def test_repository_recorded_at_two_versions_by_unversioned_references_is_a_duplicate(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        self.expansion.evaluated_source_versions.add(self.release('v1', version), self.release('v2', version))
        self.reference(concept, version)

        health = self.health()
        reasons = {reason['code']: reason for reason in health['reasons']}
        self.assertEqual(reasons['duplicate_repo_versions']['repositories'], [self.source.uri])
        self.assertNotIn('inconsistent_resolution', reasons)
        self.assertEqual(health['multiple_named_versions'], [])

    def test_unversioned_references_taking_members_from_different_versions_are_inconsistent(self):
        first, second = ConceptFactory(parent=self.source), ConceptFactory(parent=self.source)
        first_version, second_version = first.get_latest_version(), second.get_latest_version()
        self.expansion.evaluated_source_versions.add(
            self.release('v1', first_version), self.release('v2', second_version))
        self.reference(first, first_version)
        self.reference(second, second_version)

        reasons = {reason['code']: reason for reason in self.health()['reasons']}
        self.assertEqual(reasons['inconsistent_resolution']['repositories'], [self.source.uri])
        self.assertEqual(reasons['duplicate_repo_versions']['repositories'], [self.source.uri])

    def test_member_of_an_unreleased_named_version(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        draft = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.source.organization, version='draft', released=False)
        version.sources.add(draft)
        self.expansion.explicit_source_versions.add(draft)
        self.reference(concept, version, version='draft')

        self.assertEqual(self.classify(), {version.id: UNRELEASED})
        # references can't resolve to an unreleased version yet, so its owner needs to see it
        health = self.health()
        self.assertEqual(health['state'], UNHEALTHY)
        self.assertEqual(self.reason_codes(health), {'unreleased_versions': 1})

    def mapping_reference(self, mapping, *members):
        reference = CollectionReference(
            expression=f'{self.source.uri}mappings/{mapping.mnemonic}/', collection=self.collection,
            system=self.source.uri, code=mapping.mnemonic, reference_type='mappings')
        reference.save()
        reference.mappings.add(*members)
        self.expansion.mappings.add(*members)
        return reference

    def test_mappings_follow_the_same_rules_and_head_mappings_count_apart(self):
        from_concept, to_concept = ConceptFactory(parent=self.source), ConceptFactory(parent=self.source)
        mapping = MappingFactory(parent=self.source, from_concept=from_concept, to_concept=to_concept)
        version = MappingFactory(
            parent=self.source, versioned_object=mapping, mnemonic=mapping.mnemonic, version='1',
            from_concept=from_concept, to_concept=to_concept)
        self.expansion.evaluated_source_versions.add(self.release('v1', version))
        self.mapping_reference(mapping, version)
        head_mapping = MappingFactory(parent=self.source, from_concept=from_concept, to_concept=to_concept)
        self.mapping_reference(head_mapping, head_mapping)

        classes = ExpansionHealth(self.expansion).classify('mappings')
        self.assertEqual(
            {member_id: member_class for member_id, (member_class, _, _) in classes.items()},
            {version.id: RELEASE, head_mapping.id: MISMATCH})
        health = self.health()
        self.assertEqual(health['members']['mappings'][RELEASE], 1)
        mismatch = next(reason for reason in health['reasons'] if reason['code'] == 'version_mismatches')
        self.assertEqual((mismatch['head_concept'], mismatch['head_mapping']), (0, 1))

    def test_two_versions_named_by_versioned_references_are_information_only(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        v1, v2 = self.release('v1', version), self.release('v2', version)
        self.expansion.explicit_source_versions.add(v1, v2)
        for release in (v1, v2):
            reference = CollectionReference(
                expression=f'{release.uri}concepts/{concept.mnemonic}/', collection=self.collection,
                system=self.source.uri, version=release.version, code=concept.mnemonic)
            reference.save()
            reference.concepts.add(version)
        self.expansion.concepts.add(version)

        health = self.health()
        self.assertEqual(health['state'], HEALTHY)
        self.assertEqual(health['multiple_named_versions'], [self.source.uri])

    def test_unresolved_references_and_system_version_parameters_are_counted_apart(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        self.expansion.evaluated_source_versions.add(self.release('v1', version))
        self.reference(concept, version)
        self.expansion.unresolved_repo_versions = [
            {'url': '/orgs/nobody/sources/nothing/', 'type': 'reference.system'},
            {'url': '/orgs/nobody/sources/nothing/', 'type': 'reference.valueset'},
            {'url': 'http://nothing.org|1', 'type': 'expansion.parameters.system-version'},
        ]
        self.expansion.save()

        self.assertEqual(
            self.reason_codes(self.health()), {'unresolved_references': 2, 'system_version_unresolved': 1})

    def test_expansion_without_members_is_empty(self):
        health = self.health()
        self.assertEqual(health['state'], EMPTY)
        self.assertEqual(health['members']['concepts'][RELEASE], 0)

    def test_a_release_the_collection_owner_cannot_see_does_not_turn_interim_into_mismatch(self):
        other = OrganizationSourceFactory(public_access=ACCESS_TYPE_NONE)
        concept = ConceptFactory(parent=other)
        old = concept.get_latest_version()
        new = ConceptFactory(parent=other, mnemonic=concept.mnemonic, versioned_object=concept, version='new')
        old.sources.remove(other)
        hidden = OrganizationSourceFactory(
            mnemonic=other.mnemonic, organization=other.organization, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        old.sources.add(hidden)
        latest = OrganizationSourceFactory(
            mnemonic=other.mnemonic, organization=other.organization, version='v2', released=True,
            public_access=ACCESS_TYPE_NONE)
        new.sources.add(latest)
        self.expansion.evaluated_source_versions.add(latest)
        reference = CollectionReference(
            expression=f'{other.uri}concepts/{concept.mnemonic}/', collection=self.collection, system=other.uri,
            code=concept.mnemonic)
        reference.save()
        reference.concepts.add(old)
        self.expansion.concepts.add(old)

        self.assertEqual(self.classify(), {old.id: INTERIM})

        # a public release under a private HEAD is still private
        Source.objects.filter(id=hidden.id).update(public_access=ACCESS_TYPE_VIEW)
        self.assertEqual(self.classify(), {old.id: INTERIM})

        Source.objects.filter(id=other.id).update(public_access=ACCESS_TYPE_VIEW)
        self.assertEqual(self.classify(), {old.id: MISMATCH})

    def test_value_set_without_a_default_expansion_is_a_reason_and_judges_nothing(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        other = OrganizationCollectionFactory(organization=self.source.organization)
        other_release = OrganizationCollectionFactory(
            mnemonic=other.mnemonic, organization=other.organization, version='v1', released=True)
        reference = CollectionReference(
            expression=other_release.uri, collection=self.collection, valueset=[other_release.uri])
        reference.save()
        reference.concepts.add(version)
        self.expansion.concepts.add(version)

        self.assertEqual(self.classify(), {version.id: UNATTRIBUTED})
        self.assertEqual(self.reason_codes(self.health())['value_set_without_expansion'], 1)

    def test_a_resource_versioned_exclude_reference_counts_as_one(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        self.expansion.evaluated_source_versions.add(self.release('v1', version))
        self.reference(concept, version)
        excluded = CollectionReference(
            expression=version.uri, collection=self.collection, system=self.source.uri, code=concept.mnemonic,
            resource_version=version.version, include=False)
        excluded.save()

        self.assertEqual(self.classify(), {version.id: RELEASE})
        self.assertEqual(self.reason_codes(self.health()), {'resource_versioned_references': 1})

    def test_a_build_records_what_unversioned_references_resolve_to(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        release = self.release('v1', version)
        collection = OrganizationCollectionFactory(organization=self.source.organization)
        expansion = Expansion.persist(index=False, collection_version=collection, mnemonic='e1')
        collection.expansion_uri = expansion.uri
        collection.save()

        collection.add_expressions({'expressions': [concept.uri]}, collection.created_by)

        self.assertIn(release.id, expansion.evaluated_source_versions.values_list('id', flat=True))
        classes = ExpansionHealth(expansion).classify('concepts')
        self.assertEqual({member_class for member_class, _, _ in classes.values()}, {RELEASE})

    def test_a_reference_with_a_system_and_value_sets_is_judged_against_its_system(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        self.expansion.evaluated_source_versions.add(self.release('v1', version))
        other = OrganizationCollectionFactory(organization=self.source.organization)
        other_release = OrganizationCollectionFactory(
            mnemonic=other.mnemonic, organization=other.organization, version='v1', released=True)
        other_expansion = ExpansionFactory(collection_version=other_release, mnemonic='e1')
        other_release.expansion_uri = other_expansion.uri
        other_release.save()
        self.reference(concept, version, valueset=[other_release.uri])

        self.assertEqual(self.classify(), {version.id: RELEASE})

    def test_a_member_held_only_by_an_inactive_release_is_interim(self):
        concept, old, new = self.concept_with_replaced_version()
        inactive = self.release('v1', old)
        Source.objects.filter(id=inactive.id).update(is_active=False)
        self.expansion.evaluated_source_versions.add(self.release('v2', new))
        self.reference(concept, old)

        self.assertEqual(self.classify(), {old.id: INTERIM})

    def test_classify_a_page_of_members(self):
        first, second = ConceptFactory(parent=self.source), ConceptFactory(parent=self.source)
        self.reference(first, first.get_latest_version())
        self.reference(second, second.get_latest_version())

        first_reference = self.collection.references.get(code=first.mnemonic)
        health = ExpansionHealth(self.expansion)
        classes = health.classify('concepts', [first.get_latest_version().id])
        self.assertEqual(list(classes), [first.get_latest_version().id])
        # only the references the page's members are attributed to are resolved
        self.assertEqual(list(health._resolved), [first_reference.id])  # pylint: disable=protected-access

    def test_an_empty_page_is_classified_without_any_work(self):
        concept = ConceptFactory(parent=self.source)
        self.reference(concept, concept.get_latest_version())
        health = ExpansionHealth(self.expansion)

        with CaptureQueriesContext(connection) as context:
            self.assertEqual(health.classify('concepts', []), {})
        self.assertEqual(len(context.captured_queries), 0)

    def test_release_or_head_check_runs_only_for_members_their_versions_dont_hold(self):
        concept = ConceptFactory(parent=self.source)
        version = concept.get_latest_version()
        self.expansion.evaluated_source_versions.add(self.release('v1', version))
        self.reference(concept, version)

        with CaptureQueriesContext(connection) as context:
            self.assertEqual(self.classify(), {version.id: RELEASE})
        self.assertEqual(self.release_or_head_queries(context), [])

    def test_release_or_head_check_looks_members_up_in_batches(self):
        first, first_old, first_new = self.concept_with_replaced_version()
        second, second_old, second_new = self.concept_with_replaced_version()
        self.release('v1', first_old)
        self.expansion.evaluated_source_versions.add(self.release('v2', first_new, second_new))
        self.reference(first, first_old)
        self.reference(second, second_old)

        with patch.object(ExpansionHealth, 'RELEASED_OR_HEAD_BATCH_SIZE', 1), \
                CaptureQueriesContext(connection) as context:
            classes = self.classify()
        self.assertEqual(classes, {first_old.id: MISMATCH, second_old.id: INTERIM})
        self.assertEqual(len(self.release_or_head_queries(context)), 2)

    def test_queries_do_not_grow_with_references(self):
        def count_queries(references_count):
            self.expansion.concepts.clear()
            self.collection.references.all().delete()
            for _ in range(references_count):
                concept = ConceptFactory(parent=self.source)
                self.reference(concept, concept.get_latest_version())
            expansion = Expansion.objects.get(id=self.expansion.id)
            with CaptureQueriesContext(connection) as context:
                ExpansionHealth(expansion).compute()
            return len(context.captured_queries)

        self.assertEqual(count_queries(2), count_queries(6))
