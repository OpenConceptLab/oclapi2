from mock import patch, PropertyMock, Mock

from core.collections.models import Collection, CollectionReference, Expansion
from core.collections.reference_conversions import ReferenceConversions
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.tasks import convert_references
from core.tasks.models import Task
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class ReferenceConversionsTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.token = f'Token {self.admin.get_token()}'
        self.source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=self.source, mnemonic='c1')
        self.old = concept.get_latest_version()
        self.new = ConceptFactory(parent=self.source, mnemonic='c1', versioned_object=concept, version='new')
        self.old.sources.remove(self.source)
        self.v1 = self.release('v1')
        self.v2 = self.release('v2')
        self.old.sources.add(self.v1)
        self.new.sources.add(self.v2)
        self.collection = OrganizationCollectionFactory()
        expansion = Expansion.persist(index=False, collection_version=self.collection, mnemonic='autoexpand-HEAD')
        self.collection.expansion_uri = expansion.uri
        self.collection.save()
        self.reference = self.add(self.old.uri)

    def release(self, version):
        return OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.source.organization, version=version, released=True)

    def add(self, expression):
        added, errors = self.collection.add_expressions({'expressions': [expression]}, self.admin)
        self.assertEqual(errors, {})
        return added[0]

    def head(self):
        return Collection.objects.get(id=self.collection.id)

    def url(self, path=''):
        return f'{self.collection.uri}references/conversions/{path}'

    def preview(self, ids):
        response = self.client.post(self.url('preview/'), {'ids': ids}, HTTP_AUTHORIZATION=self.token, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def convert(self, data):
        response = self.client.post(self.url(), data, HTTP_AUTHORIZATION=self.token, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def head_members(self):
        return list(self.head().expansion.concepts.values_list('uri', flat=True))

    def test_preview_shows_what_each_conversion_brings_in(self):
        data = self.preview([self.reference.id])

        item = data['results'][0]
        self.assertTrue(item['supported'])
        self.assertEqual(item['members'], [self.old.uri])
        self.assertEqual(item['repo_versioned']['expression'], f'{self.source.uri}v1/concepts/c1/')
        self.assertEqual(item['repo_versioned']['repo_version'], self.v1.uri)
        self.assertEqual(item['repo_versioned']['members'], [self.old.uri])
        self.assertTrue(item['repo_versioned']['keeps_members'])
        self.assertEqual(item['unversioned']['expression'], f'{self.source.uri}concepts/c1/')
        self.assertEqual(item['unversioned']['repo_version'], self.v2.uri)
        self.assertEqual(item['unversioned']['members'], [self.new.uri])
        self.assertFalse(item['unversioned']['keeps_members'])
        self.assertEqual(
            {key: item['unversioned'][key] for key in ('added', 'removed', 'added_count', 'removed_count')},
            {'added': [self.new.uri], 'removed': [self.old.uri], 'added_count': 1, 'removed_count': 1})
        self.assertTrue(item['unversioned']['creates_lock'])
        self.assertNotIn('creates_lock', item['repo_versioned'])
        self.assertEqual(data['summary']['repo_versioned_keeps_all'], True)
        self.assertEqual(data['summary']['unversioned_keeps_all'], False)

    def test_unversioned_conversion_resolves_under_the_lock(self):
        self.head().expansion.evaluated_source_versions.add(self.v1)

        item = self.preview([self.reference.id])['results'][0]

        self.assertEqual(item['unversioned']['repo_version'], self.v1.uri)
        self.assertEqual(item['unversioned']['chosen_by'], 'lock')
        self.assertTrue(item['unversioned']['keeps_members'])
        self.assertFalse(item['unversioned']['creates_lock'])

    def test_preview_lists_at_most_a_few_members_and_counts_them_all(self):
        with patch('core.collections.reference_conversions.MAX_MEMBERS_LISTED', 0):
            item = self.preview([self.reference.id])['results'][0]

        self.assertEqual((item['members'], item['members_count']), ([], 1))
        self.assertEqual((item['unversioned']['added'], item['unversioned']['added_count']), ([], 1))

    def test_preview_evaluates_as_the_requester(self):
        private = OrganizationSourceFactory(public_access=ACCESS_TYPE_NONE)
        concept = ConceptFactory(parent=private, mnemonic='p1').get_latest_version()
        release = OrganizationSourceFactory(
            mnemonic=private.mnemonic, organization=private.organization, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        concept.sources.add(release)
        reference = self.add(concept.uri)
        editor = UserProfileFactory()
        self.collection.organization.members.add(editor)

        response = self.client.post(
            self.url('preview/'), {'ids': [reference.id]}, HTTP_AUTHORIZATION=f'Token {editor.get_token()}',
            format='json')

        self.assertEqual(response.status_code, 200)
        item = response.data['results'][0]
        self.assertEqual(item['repo_versioned']['members'], [])
        self.assertFalse(item['repo_versioned']['keeps_members'])
        # judged as the editor: an unversioned target they can't view isn't offered
        self.assertIsNone(item['unversioned'])

    def test_repo_versioned_conversion_prefers_a_version_the_expansion_records(self):
        self.old.sources.add(self.v2)
        newer = self.release('v3')
        self.old.sources.add(newer)

        item = self.preview([self.reference.id])['results'][0]

        self.assertEqual(item['repo_versioned']['repo_version'], self.v1.uri)
        self.assertEqual(item['repo_versioned']['chosen_by'], 'recorded')

    def test_repo_versioned_conversion_never_picks_another_owners_private_release(self):
        private_release = self.release('v3')
        private_release.public_access = ACCESS_TYPE_NONE
        private_release.save()
        self.old.sources.add(private_release)
        self.head().expansion.explicit_source_versions.clear()

        item = self.preview([self.reference.id])['results'][0]

        self.assertEqual(item['repo_versioned']['repo_version'], self.v1.uri)
        self.assertEqual(item['repo_versioned']['chosen_by'], 'release')

    def test_a_release_whose_head_is_private_is_not_a_candidate(self):
        self.head().expansion.explicit_source_versions.clear()
        type(self.source).objects.filter(id=self.source.id).update(public_access=ACCESS_TYPE_NONE)

        item = self.preview([self.reference.id])['results'][0]

        # v1 and v2 are public, but not publicly viewable once their HEAD is private
        self.assertIsNone(item['repo_versioned'])

    def legacy_reference(self, **kwargs):
        """A resource-versioned reference saved before system was filled in, as production still has"""
        return CollectionReference.objects.create(
            expression='/orgs/CIEL/sources/CIEL/concepts/1065/5512158/', collection=self.collection, code='1065',
            resource_version='5512158', reference_type='concepts', **kwargs)

    def test_a_reference_with_no_system_is_reported_and_the_others_go_ahead(self):
        legacy = self.legacy_reference()

        data = self.preview('*')
        results = {item['id']: item for item in data['results']}
        self.assertEqual(
            (results[legacy.id]['supported'], results[legacy.id]['reason']), (False, 'it has no system'))
        self.assertTrue(results[self.reference.id]['supported'])

        statuses = {result['id']: (result['status'], result.get('reason')) for result in self.convert(
            {'all': 'repo_versioned'})}
        self.assertEqual(statuses, {
            legacy.id: ('not_converted', 'it has no system'), self.reference.id: ('converted', None)})

    def test_an_exclusion_is_not_supported_yet(self):
        exclusion = CollectionReference.objects.create(
            expression=self.old.uri, collection=self.collection, system=self.source.uri, code='c1',
            resource_version=self.old.version, reference_type='concepts', include=False)

        item = self.preview([exclusion.id])['results'][0]

        self.assertEqual((item['supported'], item['reason'], item['exclude']), (False, 'it is an exclusion', True))

    def test_a_conversion_the_collection_has_with_another_cascade_is_not_made(self):
        self.collection.remove_references(self.collection.references.all())
        added, _ = self.collection.add_expressions(
            {'expressions': [self.old.uri]}, self.admin, cascade='sourcemappings')
        cascading = next(reference for reference in added if reference.cascade)
        self.add(f'{self.source.uri}concepts/c1/')

        results = self.convert({'conversions': [{'id': cascading.id, 'to': 'unversioned'}]})

        self.assertEqual(
            (results[0]['status'], results[0]['reason']),
            ('not_converted', 'the collection already has it with another cascade'))
        self.assertTrue(self.head().references.filter(id=cascading.id).exists())

    def test_a_conversion_checks_the_version_the_preview_showed(self):
        changed = self.convert(
            {'conversions': [{'id': self.reference.id, 'to': 'repo_versioned', 'repo_version': self.v2.uri}]})
        self.assertEqual((changed[0]['status'], changed[0]['reason']), ('not_converted', 'changed since the preview'))
        self.assertTrue(self.head().references.filter(id=self.reference.id).exists())

        same = self.convert(
            {'conversions': [{'id': self.reference.id, 'to': 'repo_versioned', 'repo_version': self.v1.uri}]})
        self.assertEqual(same[0]['status'], 'converted')

    def test_a_queued_conversion_keeps_its_result_as_json(self):
        results = {'results': [{'id': self.reference.id, 'to': 'repo_versioned', 'status': 'converted'}]}
        Task.before_start('conversion-task', [], {}, name=convert_references.name)

        # the hooks a worker runs once the task returns; the once-lock's backend needs a broker CI doesn't have
        with patch('celery_once.tasks.QueueOnce.once_backend', new_callable=PropertyMock, return_value=Mock()):
            convert_references.on_success(results, 'conversion-task', [], {})
            convert_references.after_return('SUCCESS', results, 'conversion-task', [], {}, None)

        self.assertEqual(Task.objects.get(id='conversion-task').result_all, results)

    def test_a_conversion_that_resolves_to_head_says_it_follows_head(self):
        unreleased = OrganizationSourceFactory()
        concept = ConceptFactory(parent=unreleased, mnemonic='u1').get_latest_version()
        reference = self.add(concept.uri)

        item = self.preview([reference.id])['results'][0]

        self.assertEqual(item['unversioned']['repo_version'], unreleased.uri)
        self.assertTrue(item['unversioned']['follows_head'])
        self.assertTrue(item['repo_versioned']['follows_head'])
        main = self.preview([self.reference.id])['results'][0]
        self.assertFalse(main['repo_versioned']['follows_head'])
        self.assertFalse(main['unversioned']['follows_head'])

    def test_the_requester_view_check_runs_once_per_version(self):
        second = ConceptFactory(parent=self.source, mnemonic='c2').get_latest_version()
        second.sources.add(self.v1, self.v2)
        other = self.add(second.uri)
        source_class = type(self.source)

        with patch.object(source_class, 'has_view_access', autospec=True, return_value=True) as has_view_access:
            self.preview([self.reference.id, other.id])

        self.assertEqual(len({call.args[0].id for call in has_view_access.call_args_list}), has_view_access.call_count)

    def test_a_reference_that_is_not_resource_versioned_is_not_supported(self):
        plain = self.add(f'{self.source.uri}concepts/c1/')

        item = self.preview([plain.id])['results'][0]

        self.assertEqual(item, {
            'id': plain.id, 'expression': plain.expression, 'exclude': False, 'supported': False,
            'reason': 'not resource-versioned'})

    def test_mapping_references_are_covered(self):
        mapping = MappingFactory(parent=self.source).get_latest_version()
        mapping.sources.add(self.v1)
        reference = self.add(mapping.uri)

        item = self.preview([reference.id])['results'][0]

        self.assertEqual(item['repo_versioned']['members'], [mapping.uri])
        self.assertTrue(item['repo_versioned']['keeps_members'])

    def test_a_cascading_reference_is_judged_by_everything_it_brings_in(self):
        mapping = MappingFactory(parent=self.source, from_concept=self.old.versioned_object).get_latest_version()
        mapping.sources.add(self.v1)
        self.collection.remove_references(self.collection.references.all())
        added, errors = self.collection.add_expressions(
            {'expressions': [self.old.uri]}, self.admin, cascade='sourcemappings')
        self.assertEqual(errors, {})
        reference = next(ref for ref in added if ref.cascade)

        item = self.preview([reference.id])['results'][0]

        self.assertIn(mapping.uri, item['repo_versioned']['members'])
        self.assertIn(self.old.uri, item['repo_versioned']['members'])
        self.assertEqual(item['repo_versioned']['keeps_members'], set(item['members']) == set(
            item['repo_versioned']['members']))

    def test_preview_of_every_resource_versioned_reference_pages(self):
        second = self.add(MappingFactory(parent=self.source).get_latest_version().uri)
        self.add(f'{self.source.uri}concepts/c1/')

        with patch('core.collections.views.MAX_REFERENCES', 1):
            first_page = self.preview('*')
            response = self.client.post(
                self.url('preview/'), {'ids': '*', 'offset': 1}, HTTP_AUTHORIZATION=self.token, format='json')

        self.assertEqual([item['id'] for item in first_page['results']], [self.reference.id])
        self.assertEqual(
            {key: first_page['summary'][key] for key in ('total', 'offset', 'next_offset')},
            {'total': 2, 'offset': 0, 'next_offset': 1})
        self.assertEqual([item['id'] for item in response.data['results']], [second.id])
        self.assertIsNone(response.data['summary']['next_offset'])

    def test_convert_to_repo_versioned_swaps_the_reference_and_keeps_members(self):
        results = self.convert({'conversions': [{'id': self.reference.id, 'to': 'repo_versioned'}]})

        self.assertEqual(results[0]['status'], 'converted')
        self.assertEqual(results[0]['expression'], f'{self.source.uri}v1/concepts/c1/')
        expressions = list(self.head().references.values_list('expression', flat=True))
        self.assertEqual(expressions, [f'{self.source.uri}v1/concepts/c1/'])
        self.assertEqual(self.head_members(), [self.old.uri])

    def test_convert_to_unversioned_brings_in_what_the_preview_showed(self):
        results = self.convert({'conversions': [{'id': self.reference.id, 'to': 'unversioned'}]})

        self.assertEqual(results[0]['status'], 'converted')
        self.assertEqual(
            list(self.head().references.values_list('expression', flat=True)), [f'{self.source.uri}concepts/c1/'])
        self.assertEqual(self.head_members(), [self.new.uri])

    def test_convert_all_to_one_conversion(self):
        results = self.convert({'all': 'repo_versioned'})

        self.assertEqual([result['status'] for result in results], ['converted'])
        self.assertFalse(ReferenceConversions.get_candidates(self.head()).exists())

    def test_a_conversion_already_in_the_collection_only_removes_the_old_reference(self):
        self.add(f'{self.source.uri}concepts/c1/')

        results = self.convert({'conversions': [{'id': self.reference.id, 'to': 'unversioned'}]})

        self.assertEqual(results[0]['status'], 'already_present')
        self.assertEqual(
            list(self.head().references.values_list('expression', flat=True)), [f'{self.source.uri}concepts/c1/'])

    def test_a_failed_add_changes_nothing(self):
        with patch.object(Collection, 'add_references', return_value=([], {'x': ['boom']})):
            results = self.convert({'conversions': [{'id': self.reference.id, 'to': 'repo_versioned'}]})

        self.assertEqual(results[0]['status'], 'rolled_back')
        self.assertEqual(list(self.head().references.values_list('id', flat=True)), [self.reference.id])
        self.assertEqual(self.head_members(), [self.old.uri])

    def test_a_conversion_that_cannot_be_made_is_reported_and_others_go_ahead(self):
        orphan = self.add(f'{self.source.uri}concepts/c1/missing/')

        results = self.convert({'conversions': [
            {'id': orphan.id, 'to': 'repo_versioned'}, {'id': self.reference.id, 'to': 'repo_versioned'}]})

        self.assertEqual(
            [(result['status'], result.get('reason')) for result in results],
            [('not_converted', 'no version holds its resource version'), ('converted', None)])

    def test_released_versions_keep_their_references(self):
        version = OrganizationCollectionFactory(
            mnemonic=self.collection.mnemonic, organization=self.collection.organization, version='v1')
        CollectionReference.objects.create(
            expression=self.reference.expression, collection=version, system=self.reference.system,
            code=self.reference.code, resource_version=self.reference.resource_version)

        self.convert({'conversions': [{'id': self.reference.id, 'to': 'unversioned'}]})

        self.assertEqual(list(version.references.values_list('expression', flat=True)), [self.reference.expression])

    def test_needs_an_editor_and_valid_input(self):
        outsider = f'Token {UserProfileFactory().get_token()}'
        for path, data in (('preview/', {'ids': [self.reference.id]}), ('', {'all': 'unversioned'})):
            self.assertEqual(self.client.post(self.url(path), data, format='json').status_code, 401)
            self.assertEqual(
                self.client.post(self.url(path), data, HTTP_AUTHORIZATION=outsider, format='json').status_code, 403)
        for path, data in (
                ('preview/', {}), ('preview/', {'ids': 'x'}), ('', {}), ('', {'all': 'other'}),
                ('', {'conversions': [{'id': self.reference.id, 'to': 'other'}]})):
            self.assertEqual(
                self.client.post(self.url(path), data, HTTP_AUTHORIZATION=self.token, format='json').status_code,
                400, (path, data))
