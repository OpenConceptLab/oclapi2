from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class SourceVersionListCountTest(OCLAPITestCase):
    """
    The lists of a released source version that isn't the latest: unfiltered, they answer with the version's stored
    counts and are cached like the latest version's; filtered, they count through the join as before.
    """

    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory(mnemonic='MySource')
        self.org = self.source.organization
        self.concept = ConceptFactory(parent=self.source, mnemonic='c1')
        self.retired_concept = ConceptFactory(parent=self.source, mnemonic='c2', retired=True)
        self.mapping = MappingFactory(parent=self.source, from_concept=self.concept, to_concept=self.retired_concept)
        self.retired_mapping = MappingFactory(
            parent=self.source, from_concept=self.concept, to_concept=self.concept, retired=True)
        self.v1 = self.new_version('v1', is_latest_version=False)
        self.v2 = self.new_version('v2', is_latest_version=True)
        cache.clear()

    def new_version(self, version, **kwargs):
        repo_version = OrganizationSourceFactory(
            mnemonic='MySource', organization=self.org, version=version, released=True, **kwargs)
        for concept in (self.concept, self.retired_concept):
            repo_version.concepts.add(concept.get_latest_version())
        for mapping in (self.mapping, self.retired_mapping):
            repo_version.mappings.add(mapping.get_latest_version())
        repo_version.update_children_counts(sync=True)
        return repo_version

    def get(self, version, resources, params=''):
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(f'{self.v1.uri.replace("v1/", version + "/")}{resources}/{params}')
        self.assertEqual(response.status_code, 200, response.data)
        return response, [query['sql'] for query in context.captured_queries]

    @staticmethod
    def join_counts(queries, resources):
        return [sql for sql in queries if 'COUNT(' in sql and f'"{resources}_sources"' in sql]

    def test_stored_counts(self):
        self.assertEqual((self.v1.active_concepts, self.v1.active_mappings), (1, 1))

    def test_unfiltered_lists_answer_with_the_stored_count(self):
        Source.objects.filter(id=self.v1.id).update(active_concepts=7, active_mappings=9)

        for resources, stored in (('concepts', 7), ('mappings', 9)):
            for params in ('', '?limit=1'):
                response, queries = self.get('v1', resources, params)

                self.assertEqual(response['num_found'], str(stored), (resources, params))
                self.assertEqual(self.join_counts(queries, resources), [], (resources, params))

    def test_filtered_lists_count_through_the_join(self):
        Source.objects.filter(id=self.v1.id).update(active_concepts=7, active_mappings=9)

        for resources in ('concepts', 'mappings'):
            response, queries = self.get('v1', resources, '?includeRetired=true')
            self.assertEqual(response['num_found'], '2', resources)
            self.assertEqual(len(self.join_counts(queries, resources)), 1, resources)

            response, queries = self.get('v1', resources, '?updatedSince=2000-01-01')
            self.assertEqual(response['num_found'], '1', resources)
            self.assertEqual(len(self.join_counts(queries, resources)), 1, resources)

        response, queries = self.get('v1', 'concepts', '?onlyParentLess=true')
        self.assertEqual(response['num_found'], '1')
        self.assertEqual(len(self.join_counts(queries, 'concepts')), 1)

    def test_an_uncounted_version_counts_through_the_join(self):
        Source.objects.filter(id=self.v1.id).update(active_concepts=None, active_mappings=None)

        for resources in ('concepts', 'mappings'):
            response, queries = self.get('v1', resources)

            self.assertEqual(response['num_found'], '1', resources)
            self.assertEqual(len(self.join_counts(queries, resources)), 1, resources)

    def test_released_versions_lists_are_cached(self):
        for resources in ('concepts', 'mappings'):
            for repo_version in (self.v1, self.v2):
                body_key, headers_key = getattr(repo_version, f'get_{resources}_cache_keys')()

                response, _ = self.get(repo_version.version, resources)

                self.assertEqual(len(response.data), 1)
                self.assertEqual(len(cache.get(body_key)), 1, (resources, repo_version.version))
                self.assertEqual(cache.get(headers_key)['num_found'], 1, (resources, repo_version.version))

                cache.set(body_key, [{'id': 'FromCache'}])
                response, queries = self.get(repo_version.version, resources)

                self.assertEqual(response.data, [{'id': 'FromCache'}])
                self.assertEqual(self.join_counts(queries, resources), [])

    def test_filtered_lists_are_cached_by_their_query(self):
        body_key, _ = self.v1.get_concepts_cache_keys()

        self.get('v1', 'concepts', '?includeRetired=true')

        self.assertIsNone(cache.get(body_key))
        self.assertEqual(len(cache.get(body_key + '?includeRetired=true')), 2)

    def test_unreleased_older_versions_lists_are_not_cached(self):
        Source.objects.filter(id=self.v1.id).update(released=False)
        body_key, headers_key = self.v1.get_concepts_cache_keys()

        self.get('v1', 'concepts')

        self.assertIsNone(cache.get(body_key))
        self.assertIsNone(cache.get(headers_key))

    def test_a_count_change_clears_the_cached_lists(self):
        for resources in ('concepts', 'mappings'):
            body_key, _ = getattr(self.v1, f'get_{resources}_cache_keys')()
            self.get('v1', resources)
            self.assertIsNotNone(cache.get(body_key))

            with patch('django.core.cache.cache.delete_pattern', create=True) as delete_pattern:
                getattr(self.v1, f'update_{resources}_count')()  # unchanged, so kept
            delete_pattern.assert_not_called()

            Source.objects.filter(id=self.v1.id).update(**{f'active_{resources}': 5})
            self.v1.refresh_from_db()
            with patch('django.core.cache.cache.delete_pattern', create=True) as delete_pattern:
                getattr(self.v1, f'update_{resources}_count')()
            delete_pattern.assert_any_call(f'{body_key}*')

    @patch('core.common.models.delete_s3_objects')
    def test_deleting_a_version_clears_its_cached_lists(self, _):
        with patch('django.core.cache.cache.delete_pattern', create=True) as delete_pattern:
            self.v1.delete()

        for resources in ('concepts', 'mappings'):
            body_key, headers_key = getattr(self.v1, f'get_{resources}_cache_keys')()
            delete_pattern.assert_any_call(f'{body_key}*')
            delete_pattern.assert_any_call(f'{headers_key}*')

    def test_hard_deleting_a_mapping_recounts_the_versions_holding_it(self):
        """Deleting a mapping takes its versions out of every release; each release's count must follow."""
        self.mapping.delete()

        for repo_version in (self.v1, self.v2):
            repo_version.refresh_from_db()
            self.assertEqual(repo_version.mappings.count(), 1)  # the retired one
            self.assertEqual(repo_version.active_mappings, 0)
        response, _ = self.get('v1', 'mappings')
        self.assertEqual(response['num_found'], '0')

    def test_hard_deleting_a_concept_recounts_the_versions_holding_it(self):
        self.concept.delete()

        for repo_version in (self.v1, self.v2):
            repo_version.refresh_from_db()
            self.assertEqual(repo_version.active_concepts, 0)
        response, _ = self.get('v1', 'concepts')
        self.assertEqual(response['num_found'], '0')

    def test_a_staff_db_hard_delete_of_a_concept_recounts_the_versions_holding_it(self):
        token = UserProfile.objects.get(username='ocladmin').get_token()

        response = self.client.delete(
            self.concept.uri + '?db=true&hardDelete=true', HTTP_AUTHORIZATION='Token ' + token)

        self.assertEqual(response.status_code, 204)
        for repo_version in (self.v1, self.v2):
            repo_version.refresh_from_db()
            self.assertEqual(repo_version.concepts.count(), 1)  # the retired one
            self.assertEqual(repo_version.active_concepts, 0)
        response, _ = self.get('v1', 'concepts')
        self.assertEqual(response['num_found'], '0')
