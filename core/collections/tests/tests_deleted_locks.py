from mock import patch, Mock

from core.collections.health import UNHEALTHY
from core.collections.models import Expansion
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tasks import seed_children_to_new_version
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.sources.tests.factories import OrganizationSourceFactory


# deleting a version queues its exports' removal, and CI has no broker
@patch('core.common.models.delete_s3_objects', Mock())
class DeletedLockTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory()
        self.concept = ConceptFactory(parent=self.source)
        self.v1 = self.release(self.source, 'v1')
        self.v2 = self.release(self.source, 'v2')
        self.concept.get_latest_version().sources.add(self.v1, self.v2)
        self.collection = OrganizationCollectionFactory()
        self.expansion = ExpansionFactory(collection_version=self.collection, mnemonic='autoexpand-HEAD')
        self.collection.expansion_uri = self.expansion.uri
        self.collection.save()
        self.expansion.concepts.add(self.concept.get_latest_version())

    @staticmethod
    def release(repo, version):
        factory = OrganizationSourceFactory if repo.resource_type == 'Source' else OrganizationCollectionFactory
        return factory(mnemonic=repo.mnemonic, organization=repo.organization, version=version, released=True)

    @staticmethod
    def recorded(expansion):
        return Expansion.objects.values_list('deleted_locked_repo_versions', flat=True).get(id=expansion.id)

    def test_deleting_a_locked_source_version_records_it_and_leaves_members(self):
        self.expansion.evaluated_source_versions.add(self.v1)
        listed_only = ExpansionFactory(collection_version=OrganizationCollectionFactory())
        listed_only.explicit_source_versions.add(self.v1)
        members = list(self.expansion.concepts.values_list('id', flat=True))

        self.v1.delete()

        recorded = self.recorded(self.expansion)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]['url'], self.v1.uri)
        self.assertEqual(recorded[0]['repo_url'], self.source.uri)
        self.assertEqual((recorded[0]['version'], recorded[0]['type']), ('v1', 'Source'))
        self.assertEqual((recorded[0]['owner_url'], recorded[0]['public']), (self.source.parent_url, True))
        self.assertEqual(self.recorded(listed_only), [])
        self.assertEqual(list(self.expansion.concepts.values_list('id', flat=True)), members)
        self.assertEqual(list(self.expansion.evaluated_source_versions.all()), [])

    def test_deleting_a_locked_collection_version_records_it(self):
        value_set = OrganizationCollectionFactory()
        release = self.release(value_set, 'v1')
        self.release(value_set, 'v2')
        self.expansion.evaluated_collection_versions.add(release)

        release.delete()

        recorded = self.recorded(self.expansion)
        self.assertEqual([(entry['url'], entry['type']) for entry in recorded], [(release.uri, 'Collection')])

    def test_deleting_a_source_records_each_of_its_locked_versions(self):
        self.expansion.evaluated_source_versions.add(self.v1)
        other = ExpansionFactory(collection_version=OrganizationCollectionFactory())
        other.evaluated_source_versions.add(self.v2)

        self.source.delete()

        self.assertEqual([entry['url'] for entry in self.recorded(self.expansion)], [self.v1.uri])
        self.assertEqual([entry['url'] for entry in self.recorded(other)], [self.v2.uri])

    def test_a_second_deletion_adds_to_the_record(self):
        self.expansion.evaluated_source_versions.add(self.v1)
        self.v1.delete()
        self.expansion.evaluated_source_versions.add(self.v2)

        self.v2.delete()

        self.assertEqual([entry['url'] for entry in self.recorded(self.expansion)], [self.v1.uri, self.v2.uri])

    def test_a_save_from_an_older_copy_keeps_the_record(self):
        self.expansion.evaluated_source_versions.add(self.v1)
        older = Expansion.objects.get(id=self.expansion.id)

        self.v1.delete()
        older.save()

        self.assertEqual(len(self.recorded(self.expansion)), 1)

    def test_health_reports_the_deleted_lock(self):
        self.expansion.evaluated_source_versions.add(self.v1)
        self.v1.delete()

        health = Expansion.objects.get(id=self.expansion.id).update_health()

        self.assertEqual(health['state'], UNHEALTHY)
        reason = next(reason for reason in health['reasons'] if reason['code'] == 'locked_version_deleted')
        self.assertEqual(reason, {'code': 'locked_version_deleted', 'count': 1, 'versions': [self.v1.uri]})

    def deleted_lock_reason(self):
        health = Expansion.objects.get(id=self.expansion.id).update_health()
        return next(reason for reason in health['reasons'] if reason['code'] == 'locked_version_deleted')

    def test_health_counts_but_does_not_name_another_owners_private_version(self):
        private = OrganizationSourceFactory(public_access=ACCESS_TYPE_NONE)
        private_release = OrganizationSourceFactory(
            mnemonic=private.mnemonic, organization=private.organization, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        self.expansion.evaluated_source_versions.add(self.v1, private_release)

        private_release.delete()
        self.v1.delete()

        self.assertEqual(
            self.deleted_lock_reason(), {'code': 'locked_version_deleted', 'count': 2, 'versions': [self.v1.uri]})

    def test_health_names_the_collection_owners_own_private_version(self):
        own = OrganizationSourceFactory(organization=self.collection.organization, public_access=ACCESS_TYPE_NONE)
        own_release = OrganizationSourceFactory(
            mnemonic=own.mnemonic, organization=own.organization, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        self.expansion.evaluated_source_versions.add(own_release)

        own_release.delete()

        self.assertEqual(self.deleted_lock_reason()['versions'], [own_release.uri])

    def test_health_counts_a_version_recorded_twice_once(self):
        self.expansion.evaluated_source_versions.add(self.v1)
        Expansion.record_deleted_locks(Expansion.plan_deleted_locks([self.v1]))
        Expansion.record_deleted_locks(Expansion.plan_deleted_locks([self.v1]))

        self.assertEqual(
            self.deleted_lock_reason(), {'code': 'locked_version_deleted', 'count': 1, 'versions': [self.v1.uri]})

    def test_the_delete_recomputes_health_so_the_warning_shows_at_once(self):
        self.expansion.evaluated_source_versions.add(self.v1)

        self.v1.delete()

        health, is_current = Expansion.objects.values_list('health', 'health_is_current').get(id=self.expansion.id)
        self.assertTrue(is_current)
        self.assertIn('locked_version_deleted', [reason['code'] for reason in health['reasons']])

    def test_a_public_release_under_a_private_head_is_recorded_as_private(self):
        type(self.source).objects.filter(id=self.source.id).update(public_access=ACCESS_TYPE_NONE)
        self.expansion.evaluated_source_versions.add(self.v1)

        self.v1.delete()

        self.assertEqual(self.recorded(self.expansion)[0]['public'], False)

    def test_a_delete_that_fails_records_nothing(self):
        self.expansion.evaluated_source_versions.add(self.v1)

        with patch.object(type(self.v1), 'delete_version_changelogs', side_effect=RuntimeError('boom')), \
                self.assertRaises(RuntimeError):
            self.v1.delete()

        self.assertEqual(self.recorded(self.expansion), [])

    def test_a_rebuild_that_fails_keeps_the_record(self):
        self.expansion.evaluated_source_versions.add(self.v1)
        self.v1.delete()

        with patch.object(Expansion, 'dedupe_resources', side_effect=RuntimeError('boom')), \
                self.assertRaises(RuntimeError):
            Expansion.objects.get(id=self.expansion.id).seed_children(index=False, force_reevaluate=True)

        self.assertEqual(len(self.recorded(self.expansion)), 1)

    def test_a_rebuild_from_scratch_clears_the_record(self):
        self.expansion.evaluated_source_versions.add(self.v1)
        self.v1.delete()

        Expansion.objects.get(id=self.expansion.id).seed_children(index=False, force_reevaluate=True)

        self.assertEqual(self.recorded(self.expansion), [])

    @patch('core.common.tasks.export_collection')
    def test_a_version_created_with_lock_keeps_heads_record_and_latest_does_not(self, _):
        self.expansion.evaluated_source_versions.add(self.v1)
        self.v1.delete()
        locked = OrganizationCollectionFactory(
            organization=self.collection.organization, mnemonic=self.collection.mnemonic, version='v1')
        latest = OrganizationCollectionFactory(
            organization=self.collection.organization, mnemonic=self.collection.mnemonic, version='v2')

        seed_children_to_new_version('collection', locked.id, False, expansion='lock')  # pylint: disable=no-value-for-parameter
        seed_children_to_new_version('collection', latest.id, False, expansion='latest')  # pylint: disable=no-value-for-parameter

        locked.refresh_from_db()
        latest.refresh_from_db()
        self.assertEqual([entry['url'] for entry in self.recorded(locked.expansion)], [self.v1.uri])
        self.assertEqual(self.recorded(latest.expansion), [])
