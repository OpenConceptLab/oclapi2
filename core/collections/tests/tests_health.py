from django.db import connection
from django.db.models import F
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from mock import patch, Mock

from core.collections.health import (
    ExpansionHealth, RELEASE, UNRELEASED, HEAD_MEMBER, INTERIM, MISMATCH, AMBIGUOUS,
    UNATTRIBUTED, EMPTY, UNHEALTHY, HEAD_RESOLVED, HEALTHY)
from core.collections.models import CollectionReference, Expansion
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW
from core.common.tasks import seed_children_to_new_version, recheck_expansion_health
from core.common.tests import OCLTestCase, OCLAPITestCase
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

    def test_a_pinned_repositorys_versions_are_read_only_for_the_members_pinned_references_judge(self):
        releases = [self.release(f'v{number}') for number in range(1, 5)]
        self.expansion.evaluated_source_versions.add(releases[0])
        unpinned = []
        for _ in range(3):
            concept = ConceptFactory(parent=self.source)
            version = concept.get_latest_version()
            version.sources.add(*releases)
            self.reference(concept, version)
            unpinned.append(version)
        concept = ConceptFactory(parent=self.source)
        pinned = concept.get_latest_version()
        pinned.sources.add(*releases)
        reference = CollectionReference(
            expression=pinned.uri, collection=self.collection, system=self.source.uri, code=concept.mnemonic,
            resource_version=pinned.version)
        reference.save()
        reference.concepts.add(pinned)
        self.expansion.concepts.add(pinned)

        health = ExpansionHealth(self.expansion)
        members = health.get_member_rows('concepts')
        member_rows = list(members.values_list('id', 'versioned_object_id'))
        attributions = health.get_attributions('concepts', members)
        pinned_member_ids = health.get_pinned_member_ids(member_rows, attributions)
        containments = health.get_containments(
            'concepts', members, sorted(set().union(*attributions.values())), pinned_member_ids)

        self.assertEqual(pinned_member_ids, [pinned.id])
        # rows grow with the pinned members times their repository's versions, not with every member
        for version in unpinned:
            self.assertEqual(containments[version.id], {('source', releases[0].id)})
        self.assertEqual(len(containments[pinned.id]), 5)  # HEAD and the four releases
        self.assertEqual(self.classify()[pinned.id], RELEASE)

    def test_queries_do_not_grow_with_a_pinned_repositorys_versions(self):
        other = OrganizationSourceFactory()
        concept = ConceptFactory(parent=other)
        version = concept.get_latest_version()
        reference = CollectionReference(
            expression=version.uri, collection=self.collection, system=other.uri, code=concept.mnemonic,
            resource_version=version.version)
        reference.save()
        reference.concepts.add(version)
        self.expansion.concepts.add(version)

        def count_queries(releases_count):
            for number in range(releases_count):
                version.sources.add(OrganizationSourceFactory(
                    mnemonic=other.mnemonic, organization=other.organization, version=f'{releases_count}.{number}',
                    released=True))
            health = ExpansionHealth(Expansion.objects.get(id=self.expansion.id))
            with CaptureQueriesContext(connection) as context:
                self.assertEqual(health.classify('concepts')[version.id][0], RELEASE)
            return len(context.captured_queries)

        # another owner's public versions: their visibility is one query, not one per version
        self.assertEqual(count_queries(2), count_queries(6))


class ExpansionHealthStorageTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        # the default before it seeds, as a version's own build makes it, so the seed stores health
        self.expansion = Expansion.persist(
            index=False, collection_version=self.collection, mnemonic='autoexpand-HEAD', claim_default=True)
        self.assertEqual(self.collection.expansion_uri, self.expansion.uri)

    def add(self, *concepts):
        self.collection.add_expressions(
            {'expressions': [concept.uri for concept in concepts]}, self.collection.created_by)

    def stored(self):
        return Expansion.objects.values_list('health', 'health_is_current').get(id=self.expansion.id)

    def test_build_stores_health(self):
        health, is_current = self.stored()
        self.assertTrue(is_current)
        self.assertEqual(health['state'], EMPTY)
        self.assertEqual(health['rules_version'], 1)

    def test_adding_references_recomputes_health(self):
        self.add(ConceptFactory())

        health, is_current = self.stored()
        self.assertTrue(is_current)
        self.assertNotEqual(health['state'], EMPTY)

    def test_removing_references_recomputes_health(self):
        concept = ConceptFactory()
        self.add(concept)

        self.collection.delete_references([concept.uri])

        health, is_current = self.stored()
        self.assertTrue(is_current)
        self.assertEqual(health['state'], EMPTY)

    def test_one_add_of_many_references_recomputes_once(self):
        with patch.object(ExpansionHealth, 'compute', return_value={'state': HEALTHY}) as compute:
            self.add(ConceptFactory(), ConceptFactory(), ConceptFactory())

        compute.assert_called_once()

    def test_an_expansion_already_out_of_date_queues_no_second_recompute(self):
        Expansion.objects.filter(id=self.expansion.id).update(health_is_current=False)

        with patch.object(ExpansionHealth, 'compute') as compute:
            self.add(ConceptFactory())

        compute.assert_not_called()
        self.assertFalse(self.stored()[1])

    def test_toggle_off_marks_the_change_and_computes_nothing(self):
        before = self.stored()[0]
        with patch.object(Expansion, 'is_health_on', return_value=False), \
                patch.object(ExpansionHealth, 'compute') as compute:
            self.add(ConceptFactory())

        # marked not current, so the backfill finds it once health is on again; the stored summary is untouched
        self.assertEqual(self.stored(), (before, None))
        compute.assert_not_called()

    def test_health_is_kept_for_the_default_expansion_only(self):
        other = Expansion.persist(index=False, collection_version=self.collection, mnemonic='other')

        self.assertIsNone(other.update_health())
        self.assertEqual(Expansion.objects.values_list('health', 'health_is_current').get(id=other.id), (None, None))
        self.assertTrue(self.stored()[1])

    def test_a_change_while_it_computes_is_not_stored_as_current(self):
        computed = []

        def compute(_health):
            if not computed:  # the first run is overtaken by a change
                Expansion.objects.filter(id=self.expansion.id).update(health_revision=F('health_revision') + 1)
            computed.append(1)
            return {'state': HEALTHY, 'run': len(computed)}

        with patch.object(ExpansionHealth, 'compute', autospec=True, side_effect=compute):
            Expansion.objects.get(id=self.expansion.id).update_health()

        # the overtaken run stored nothing; the run it queued read the change and is current
        self.assertEqual(self.stored(), ({'state': HEALTHY, 'run': 2}, True))

    def test_a_failure_to_queue_leaves_health_not_current_so_the_next_change_queues(self):
        with override_settings(TEST_MODE=False), \
                patch('core.collections.models.compute_expansion_health.apply_async', side_effect=OSError('down')), \
                self.captureOnCommitCallbacks(execute=True):
            Expansion.objects.get(id=self.expansion.id).mark_health_out_of_date()

        self.assertIsNone(self.stored()[1])

    def test_a_failure_leaves_health_out_of_date_and_the_build_done(self):
        before = self.stored()[0]
        with patch.object(ExpansionHealth, 'compute', side_effect=ValueError('boom')):
            self.expansion.seed_children(index=False)
            self.expansion.update_health()

        expansion = Expansion.objects.get(id=self.expansion.id)
        self.assertFalse(expansion.is_processing)
        self.assertEqual((expansion.health, expansion.health_is_current), (before, None))

    def test_a_change_after_a_failure_recomputes(self):
        with patch.object(ExpansionHealth, 'compute', side_effect=ValueError('boom')):
            self.add(ConceptFactory())
        self.assertIsNone(self.stored()[1])

        self.add(ConceptFactory())

        health, is_current = self.stored()
        self.assertTrue(is_current)
        self.assertNotEqual(health['state'], EMPTY)

    def recorded_source_version(self, **kwargs):
        source = OrganizationSourceFactory()
        version = OrganizationSourceFactory(
            mnemonic=source.mnemonic, organization=source.organization, version='v1', is_latest_version=False,
            **kwargs)
        self.expansion.evaluated_source_versions.add(source)
        # the default expansion of a collection that records nothing of the source
        other_collection = OrganizationCollectionFactory()
        other = Expansion.persist(index=False, collection_version=other_collection, mnemonic='autoexpand-HEAD')
        other_collection.expansion_uri = other.uri
        other_collection.save()
        other.update_health()
        return version, other

    def test_releasing_a_version_of_a_recorded_repository_leaves_health_not_current(self):
        version, other = self.recorded_source_version(released=False)

        version.released = True
        version.save()

        self.assertIsNone(self.stored()[1])
        self.assertTrue(Expansion.objects.get(id=other.id).health_is_current)

    def test_retiring_a_version_of_a_recorded_repository_leaves_health_not_current(self):
        version, _ = self.recorded_source_version(released=True)

        version.retired = True
        version.save()

        self.assertIsNone(self.stored()[1])

    def test_making_a_recorded_repository_private_leaves_health_not_current(self):
        version, _ = self.recorded_source_version(released=True)

        version.public_access = ACCESS_TYPE_NONE
        version.save()

        self.assertIsNone(self.stored()[1])

    def test_a_save_that_changes_no_health_field_keeps_health_current(self):
        version, _ = self.recorded_source_version(released=True)

        version.description = 'changed'
        version.save()

        self.assertTrue(self.stored()[1])

    @patch('core.common.models.delete_s3_objects', Mock())
    def test_deleting_a_version_of_a_recorded_repository_leaves_health_not_current(self):
        version, _ = self.recorded_source_version(released=True)

        version.delete()

        self.assertIsNone(self.stored()[1])

    @patch('core.common.models.delete_s3_objects', Mock())
    def test_a_recompute_that_ran_before_a_delete_committed_is_not_left_current(self):
        version, _ = self.recorded_source_version(released=True)

        with self.captureOnCommitCallbacks(execute=True):
            version.delete()
            # a recompute stores its result before the delete commits
            Expansion.objects.filter(id=self.expansion.id).update(health_is_current=True)

        self.assertIsNone(self.stored()[1])

    def test_a_change_to_a_value_set_leaves_expansions_judged_against_it_not_current(self):
        value_set = OrganizationCollectionFactory()
        value_set_expansion = Expansion.persist(index=False, collection_version=value_set, mnemonic='autoexpand-HEAD')
        value_set.expansion_uri = value_set_expansion.uri
        value_set.save()
        self.expansion.evaluated_collection_versions.add(value_set)
        self.assertTrue(self.stored()[1])

        value_set.add_expressions({'expressions': [ConceptFactory().uri]}, value_set.created_by)

        self.assertIsNone(self.stored()[1])

    def test_editing_a_member_in_head_recomputes_the_expansions_holding_it(self):
        concept = ConceptFactory()
        member = concept.get_latest_version()
        # a reference to the version itself: one to the concept would hold the HEAD concept
        self.add(member)
        self.assertEqual(self.stored()[0]['members']['concepts'][HEAD_MEMBER], 1)

        # the edit takes the old version out of HEAD, so the member it was is now interim
        member.unmark_latest_version()

        health, is_current = self.stored()
        self.assertTrue(is_current)
        self.assertEqual(health['members']['concepts'][INTERIM], 1)

    def test_a_seed_that_ends_before_its_expansion_is_the_default_still_stores_health(self):
        collection = OrganizationCollectionFactory()
        CollectionReference(
            expression='/orgs/missing/sources/missing/concepts/c1/', collection=collection,
            system='/orgs/missing/sources/missing/', code='c1').save()
        with patch.object(CollectionReference, 'is_expansion_locked_repo_version_on', return_value=False):
            collection.cascade_children_to_expansion(index=False)

        expansion = collection.expansion
        self.assertTrue(expansion.health_is_current)
        # computed from what the seed saved, the reference it couldn't resolve among it
        reasons = {reason['code']: reason['count'] for reason in expansion.health['reasons']}
        self.assertEqual(reasons.get('unresolved_references'), 1)

    def test_dropping_unseen_children_from_head_recomputes_the_expansions_holding_them(self):
        concept = ConceptFactory()
        member = concept.get_latest_version()
        self.add(member)
        self.assertEqual(self.stored()[0]['members']['concepts'][HEAD_MEMBER], 1)

        with patch('core.sources.models.Source.queue_index_dropped_children'):
            concept.parent.drop_unseen_children([], [])

        health, is_current = self.stored()
        self.assertTrue(is_current)
        self.assertEqual(health['members']['concepts'][INTERIM], 1)

    def test_a_hard_deleted_member_leaves_the_expansions_holding_it_not_current(self):
        concept = ConceptFactory()
        self.add(concept)
        self.assertTrue(self.stored()[1])

        concept.delete()

        self.assertIsNone(self.stored()[1])

    def test_the_nightly_recheck_recomputes_default_expansions_that_are_not_current(self):
        other = Expansion.persist(index=False, collection_version=self.collection, mnemonic='other')
        Expansion.objects.filter(id__in=[self.expansion.id, other.id]).update(health=None, health_is_current=None)

        self.assertEqual(recheck_expansion_health(), 1)

        self.assertTrue(self.stored()[1])
        self.assertEqual(Expansion.objects.values_list('health_is_current', flat=True).get(id=other.id), None)

    def test_deleting_a_reference_that_did_not_resolve_clears_what_it_left_unresolved(self):
        expression = '/orgs/missing/sources/missing/concepts/c1/'
        reference = CollectionReference(
            expression=expression, collection=self.collection, system='/orgs/missing/sources/missing/', code='c1')
        reference.save()
        expansion = Expansion.objects.get(id=self.expansion.id)
        expansion.add_references([reference], index=False)
        expansion.mark_health_out_of_date()
        self.assertEqual([reason['code'] for reason in self.stored()[0]['reasons']], ['unresolved_references'])

        self.collection.delete_references([expression])

        health, is_current = self.stored()
        self.assertTrue(is_current)
        self.assertEqual((health['state'], health['reasons']), (EMPTY, []))
        self.assertEqual(Expansion.objects.get(id=self.expansion.id).unresolved_repo_versions, [])

    @patch('core.sources.models.Source.index_resources_for_self_as_latest_released', Mock(return_value=True))
    @patch('core.sources.models.Source.index_children', Mock())
    @patch('core.common.tasks.export_source', Mock())
    def test_a_version_created_released_leaves_health_not_current_once_it_is_seeded(self):
        concept = ConceptFactory()
        member = concept.get_latest_version()
        self.add(member)
        self.expansion.evaluated_source_versions.add(concept.parent)
        self.assertEqual(self.stored()[0]['members']['concepts'][HEAD_MEMBER], 1)
        release = OrganizationSourceFactory(
            mnemonic=concept.parent.mnemonic, organization=concept.parent.organization, version='v1', released=True)
        # created, but holding nothing until the seed copies HEAD's members
        self.assertTrue(self.stored()[1])

        seed_children_to_new_version('source', release.id, False)  # pylint: disable=no-value-for-parameter

        self.assertIsNone(self.stored()[1])
        recheck_expansion_health()
        self.assertEqual(self.stored()[0]['members']['concepts'][RELEASE], 1)

    def test_a_run_that_ends_during_a_build_stores_nothing_and_leaves_it_to_the_build(self):
        def compute(_health):
            # a rebuild starts, and clears the members, while it computes
            Expansion.objects.filter(id=self.expansion.id).update(is_processing=True)
            return {'state': HEALTHY}

        with patch.object(ExpansionHealth, 'compute', autospec=True, side_effect=compute) as computed:
            self.assertIsNone(Expansion.objects.get(id=self.expansion.id).update_health())

        computed.assert_called_once()
        self.assertNotEqual(self.stored()[0]['state'], HEALTHY)

    def test_a_reevaluate_leaves_health_stored_before_not_current_whatever_the_toggle(self):
        with patch.object(Expansion, 'is_health_on', return_value=False):
            Expansion.objects.get(id=self.expansion.id).seed_children(index=False, force_reevaluate=True)

        self.assertIsNone(self.stored()[1])

    def test_the_nightly_recheck_also_takes_ones_whose_queued_run_never_came(self):
        Expansion.objects.filter(id=self.expansion.id).update(health_is_current=False)

        self.assertEqual(recheck_expansion_health(), 1)
        self.assertTrue(self.stored()[1])

    def test_a_mark_bumps_the_revision_before_anything_else(self):
        Expansion.objects.filter(id=self.expansion.id).update(health_is_current=False)
        expansion = Expansion.objects.get(id=self.expansion.id)

        with CaptureQueriesContext(connection) as context:
            expansion.mark_health_out_of_date()

        # a run that read the old revision can't store its result between the mark's statements
        updates = [query['sql'] for query in context.captured_queries if query['sql'].startswith('UPDATE')]
        self.assertIn('"health_revision"', updates[0])
        self.assertNotIn('"health_is_current"', updates[0].split(' WHERE ')[0])

    def test_an_older_run_that_ends_after_a_newer_store_does_not_overwrite_it(self):
        computed = []
        real_compute = ExpansionHealth.compute

        def compute(health):
            computed.append(1)
            if len(computed) == 1:
                # a newer run (a rebuild's) stores while this one computes
                Expansion.objects.get(id=self.expansion.id).update_health()
                return {'state': HEALTHY, 'stale': True}
            return real_compute(health)

        with patch.object(ExpansionHealth, 'compute', autospec=True, side_effect=compute):
            Expansion.objects.get(id=self.expansion.id).update_health()

        health, is_current = self.stored()
        self.assertTrue(is_current)
        self.assertEqual(health['state'], EMPTY)

    def test_a_save_from_an_older_copy_keeps_the_health(self):
        older = Expansion.objects.get(id=self.expansion.id)
        Expansion.objects.filter(id=self.expansion.id).update(health={'state': HEALTHY}, health_is_current=True)

        older.save()

        self.assertEqual(self.stored(), ({'state': HEALTHY}, True))

    @patch('core.common.tasks.export_collection')
    def test_version_created_from_head_stores_health(self, _):
        self.add(ConceptFactory())
        version = OrganizationCollectionFactory(
            organization=self.collection.organization, version='v1', mnemonic=self.collection.mnemonic)

        seed_children_to_new_version('collection', version.id, False)  # pylint: disable=no-value-for-parameter

        version.refresh_from_db()
        self.assertTrue(version.expansion.health_is_current)
        self.assertIsNotNone(version.expansion.health['state'])


class ExpansionHealthAPITest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory()
        self.expansion = Expansion.persist(index=False, collection_version=self.collection, mnemonic='autoexpand-HEAD')
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
        self.concept = ConceptFactory()
        self.collection.add_expressions({'expressions': [self.concept.uri]}, self.collection.created_by)
        self.expansion.refresh_from_db()

    def test_expansion_detail_and_list_show_health_to_a_viewer(self):
        detail = self.client.get(self.expansion.uri)
        listing = self.client.get(self.collection.uri + 'HEAD/expansions/')

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['health'], self.expansion.health)
        self.assertTrue(detail.data['health_is_current'])
        self.assertEqual(listing.data[0]['health']['state'], self.expansion.health['state'])

    def test_versions_list_shows_each_default_expansion_state(self):
        version = OrganizationCollectionFactory(
            organization=self.collection.organization, version='v1', mnemonic=self.collection.mnemonic)

        response = self.client.get(self.collection.uri + 'versions/')

        self.assertEqual(response.status_code, 200)
        health = {item['id']: item['expansion_health'] for item in response.data}
        self.assertEqual(health['HEAD'], {'state': self.expansion.health['state'], 'is_current': True})
        self.assertIsNone(health[version.version])

    def test_version_detail_shows_its_default_expansion_state(self):
        response = self.client.get(self.collection.uri + 'HEAD/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['expansion_health']['state'], self.expansion.health['state'])

    def test_version_lists_nested_elsewhere_carry_no_expansion_health(self):
        Expansion.objects.filter(id=self.expansion.id).update(health={'state': HEALTHY}, health_is_current=True)
        version = OrganizationCollectionFactory(
            organization=self.collection.organization, version='v1', mnemonic=self.collection.mnemonic)
        self.expansion.evaluated_collection_versions.add(version)

        response = self.client.get(self.expansion.uri)

        self.assertNotIn('expansion_health', response.data['evaluated_collection_versions'][0])

    def test_member_list_adds_each_members_class_only_when_asked(self):
        url = self.expansion.uri + 'concepts/'

        plain = self.client.get(url)
        with_health = self.client.get(url + '?includeHealth=true')

        self.assertNotIn('health', plain.data[0])
        member = with_health.data[0]
        self.assertIn(member['health']['class'], ('head', 'mismatch', 'release', 'unreleased'))
        self.assertEqual(len(member['health']['references']), 1)
        self.assertTrue(member['health']['references'][0].startswith(self.collection.uri + 'references/'))

    def test_verbose_versions_list_shows_each_default_expansion_state(self):
        response = self.client.get(self.collection.uri + 'versions/?verbose=true')

        self.assertEqual(response.status_code, 200)
        head = next(item for item in response.data if item['id'] == 'HEAD')
        self.assertEqual(head['expansion_health']['state'], self.expansion.health['state'])

    def test_version_export_leaves_out_expansion_health(self):
        from core.collections.serializers import CollectionVersionExportSerializer
        self.assertNotIn('expansion_health', CollectionVersionExportSerializer.Meta.fields)

    def test_mapping_list_adds_each_members_class_when_asked(self):
        from_concept, to_concept = ConceptFactory(), ConceptFactory()
        mapping = MappingFactory(parent=from_concept.parent, from_concept=from_concept, to_concept=to_concept)
        self.collection.add_expressions({'expressions': [mapping.uri]}, self.collection.created_by)

        response = self.client.get(self.expansion.uri + 'mappings/?includeHealth=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertIn('class', response.data[0]['health'])
        self.assertEqual(len(response.data[0]['health']['references']), 1)

    def test_member_list_gives_each_row_its_own_class(self):
        # a member of a release, next to the HEAD member from setUp, so swapped rows would show
        second = ConceptFactory()
        release = OrganizationSourceFactory(
            mnemonic=second.parent.mnemonic, organization=second.parent.organization, version='v1', released=True)
        second.get_latest_version().sources.add(release)
        self.collection.add_expressions(
            {'expressions': [f'{release.uri}concepts/{second.mnemonic}/']}, self.collection.created_by)
        classes = ExpansionHealth(self.expansion).classify('concepts')
        self.assertEqual(len({member_class for member_class, _, _ in classes.values()}), 2)

        response = self.client.get(self.expansion.uri + 'concepts/?includeHealth=true')

        self.assertEqual(len(response.data), 2)
        for item in response.data:
            self.assertEqual(item['health']['class'], classes[int(item['uuid'])][0])

    @patch('core.common.utils.get_export_service')
    def test_versions_list_csv_export_carries_no_health_annotation(self, export_service):
        export_service.return_value.url_for.return_value = 'https://csv-url'

        response = self.client.get(
            self.collection.uri + 'versions/?csv=true',
            HTTP_AUTHORIZATION=f'Token {self.collection.created_by.get_token()}')

        # djqscsv writes the queryset's values(), which can't carry the health annotation
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'url': 'https://csv-url'})

    def test_member_list_ignores_include_health_with_the_toggle_off(self):
        with patch.object(Expansion, 'is_health_on', return_value=False):
            response = self.client.get(self.expansion.uri + 'concepts/?includeHealth=true')

        self.assertNotIn('health', response.data[0])
