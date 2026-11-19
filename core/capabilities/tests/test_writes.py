import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import override_settings

from core.capabilities.constants import WRITE_ACTION_DELETE, WRITE_ACTION_EDIT, WRITE_ACTION_PUBLISH
from core.capabilities.exceptions import WriteRefused
from core.capabilities.policy import GroupLimitPolicy
from core.capabilities.writes import check_write, get_write_target
from core.collections.tests.factories import ExpansionFactory, OrganizationCollectionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.importers.importer import ResourceImporter
from core.importers.models import BulkImportInline
from core.map_projects.tests.factories import AutomatchRunFactory, MapProjectFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.serializers import SourceDetailSerializer
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.tests.factories import UserProfileFactory

READ_ONLY_POLICY = 'core.capabilities.tests.test_writes.ReadOnlyPolicy'


class ReadOnlyPolicy(GroupLimitPolicy):
    """Keeps the content in `read_only` read-only, refusing writes to it with `body`. Records every question asked."""
    calls = []
    read_only = []
    body = {'error_code': 'read_only', 'detail': 'This content is read-only.', 'code': 7}

    def get_write_refusal(self, user, content, action):
        self.calls.append((user, content, action))
        return dict(self.body) if content in self.read_only else None


def concept_payload(mnemonic):
    return {
        'id': mnemonic, 'concept_class': 'Diagnosis', 'datatype': 'None',
        'names': [{'locale': 'en', 'locale_preferred': True, 'name': mnemonic, 'name_type': 'Fully Specified'}],
    }


class WriteCheckpointTestMixin:
    def setUp(self):
        super().setUp()
        ReadOnlyPolicy.calls = []
        ReadOnlyPolicy.read_only = []
        self.user = UserProfileFactory()
        self.org = OrganizationFactory()
        self.org.members.add(self.user)
        self.source = OrganizationSourceFactory(organization=self.org)
        self.collection = OrganizationCollectionFactory(organization=self.org)
        self.concept = ConceptFactory(
            parent=self.source, names=[ConceptNameFactory.build(locale='en', locale_preferred=True)])

    def request(self, method, url, data=None):
        return getattr(self.client, method)(
            url, data, HTTP_AUTHORIZATION='Token ' + self.user.get_token(), format='json')

    def assert_refused(self, response, content, action):
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.json(), ReadOnlyPolicy.body)  # sent as the policy returned it, numbers as numbers
        self.assertEqual(ReadOnlyPolicy.calls, [(self.user, content, action)])
        ReadOnlyPolicy.calls = []


class DefaultPolicyAllowsWritesTest(WriteCheckpointTestMixin, OCLAPITestCase):
    def test_the_default_policy_refuses_no_write(self):
        policy = GroupLimitPolicy()
        for content in (self.source, self.collection, MapProjectFactory(organization=self.org)):
            for action in (WRITE_ACTION_EDIT, WRITE_ACTION_DELETE, WRITE_ACTION_PUBLISH):
                with self.subTest(content=content, action=action):
                    self.assertIsNone(policy.get_write_refusal(self.user, content, action))
                    check_write(self.user, content, action)

    def test_a_write_goes_ahead(self):
        response = self.request('put', self.source.uri, {'full_name': 'Renamed'})

        self.assertEqual(response.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.full_name, 'Renamed')

    def test_the_target_is_the_repository_head_or_the_map_project(self):
        version = OrganizationSourceFactory(organization=self.org, mnemonic=self.source.mnemonic, version='v1')
        project = MapProjectFactory(organization=self.org)

        self.assertEqual(get_write_target(self.source), self.source)
        self.assertEqual(get_write_target(version), self.source)
        self.assertEqual(get_write_target(project), project)
        self.assertIsNone(get_write_target(self.concept))
        self.assertIsNone(get_write_target(self.org))


@override_settings(CAPABILITY_POLICY=READ_ONLY_POLICY)
class RepositoryWriteCheckpointTest(WriteCheckpointTestMixin, OCLAPITestCase):
    def setUp(self):
        super().setUp()
        ReadOnlyPolicy.read_only = [self.source, self.collection]

    def test_repository_update_is_an_edit(self):
        for repo in (self.source, self.collection):
            with self.subTest(repo=repo):
                self.assert_refused(self.request('put', repo.uri, {'full_name': 'Renamed'}), repo, WRITE_ACTION_EDIT)
                repo.refresh_from_db()
                self.assertNotEqual(repo.full_name, 'Renamed')

    def test_repository_delete_is_a_delete(self):
        for repo in (self.source, self.collection):
            with self.subTest(repo=repo):
                self.assert_refused(self.request('delete', repo.uri), repo, WRITE_ACTION_DELETE)
                repo.refresh_from_db()
                self.assertTrue(repo.is_active)

    def test_making_a_private_repository_viewable_publishes_it(self):
        self.source.public_access = ACCESS_TYPE_NONE
        self.source.save()

        for public_access in ('View', 'Edit', 'edit'):
            with self.subTest(public_access=public_access):
                self.assert_refused(
                    self.request('put', self.source.uri, {'public_access': public_access}), self.source,
                    WRITE_ACTION_PUBLISH)
        self.assert_refused(
            self.request('put', self.source.uri, {'public_access': 'None', 'full_name': 'Renamed'}), self.source,
            WRITE_ACTION_EDIT)
        self.source.refresh_from_db()
        self.assertEqual(self.source.public_access, ACCESS_TYPE_NONE)

        # a repository others can view already is only edited
        self.assert_refused(
            self.request('put', self.collection.uri, {'public_access': 'View'}), self.collection, WRITE_ACTION_EDIT)

    def test_extras_and_logo_writes_are_edits(self):
        self.assert_refused(
            self.request('put', self.source.uri + 'extras/foo/', {'foo': 'bar'}), self.source, WRITE_ACTION_EDIT)
        self.assert_refused(self.request('delete', self.source.uri + 'extras/foo/'), self.source, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('put', self.collection.uri + 'extras/foo/', {'foo': 'bar'}), self.collection,
            WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('post', self.source.uri + 'logo/', {'base64': 'abc'}), self.source, WRITE_ACTION_EDIT)

    def test_concept_writes_are_edits_of_the_repository(self):
        self.assert_refused(
            self.request('post', self.source.uri + 'concepts/', concept_payload('c1')), self.source, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('put', self.concept.uri, {'datatype': 'Text'}), self.source, WRITE_ACTION_EDIT)
        self.assert_refused(self.request('delete', self.concept.uri), self.source, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('delete', self.concept.uri + '?hardDelete=true&async=true'), self.source, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('put', self.concept.uri + 'extras/foo/', {'foo': 'bar'}), self.source, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('post', self.concept.uri + 'names/', {'name': 'Other', 'locale': 'en'}), self.source,
            WRITE_ACTION_EDIT)
        self.concept.refresh_from_db()
        self.assertFalse(self.concept.retired)
        self.assertEqual(self.concept.versions.count(), 1)

    def test_mapping_create_is_an_edit(self):
        self.assert_refused(
            self.request(
                'post', self.source.uri + 'mappings/',
                {'map_type': 'SAME-AS', 'from_concept_url': self.concept.uri, 'to_concept_url': self.concept.uri}),
            self.source, WRITE_ACTION_EDIT)

    def test_reference_add_and_delete_are_edits(self):
        self.assert_refused(
            self.request('put', self.collection.uri + 'references/', {'data': {'expressions': [self.concept.uri]}}),
            self.collection, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('delete', self.collection.uri + 'references/', {'expressions': [self.concept.uri]}),
            self.collection, WRITE_ACTION_EDIT)
        self.assertFalse(self.collection.references.exists())

    def test_version_writes_are_edits_of_the_head(self):
        version = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v1', released=True)

        self.assert_refused(
            self.request('post', self.source.uri + 'versions/', {'id': 'v2', 'description': 'Second'}), self.source,
            WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('put', version.uri, {'description': 'Changed'}), self.source, WRITE_ACTION_EDIT)
        self.assert_refused(self.request('delete', version.uri), self.source, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('put', self.source.uri + 'latest/', {'description': 'Changed'}), self.source,
            WRITE_ACTION_EDIT)
        self.assertFalse(Source.objects.filter(mnemonic=self.source.mnemonic, version='v2').exists())
        version.refresh_from_db()
        self.assertTrue(version.is_active)
        self.assertNotEqual(version.description, 'Changed')

    def test_clone_into_a_read_only_source_is_an_edit(self):
        other_concept = ConceptFactory(
            parent=OrganizationSourceFactory(organization=self.org),
            names=[ConceptNameFactory.build(locale='en', locale_preferred=True)])

        self.assert_refused(
            self.request('post', self.source.uri + 'concepts/$clone/', {'expressions': [other_concept.uri]}),
            self.source, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('post', other_concept.uri + '$clone/', {'source_uri': self.source.uri}),
            self.source, WRITE_ACTION_EDIT)
        self.assertFalse(self.source.concepts_set.filter(mnemonic=other_concept.mnemonic).exists())

    def test_reads_exports_expansions_and_processing_are_not_asked_about(self):
        version = OrganizationSourceFactory(organization=self.org, mnemonic=self.source.mnemonic, version='v1')
        collection_version = OrganizationCollectionFactory(
            organization=self.org, mnemonic=self.collection.mnemonic, version='v1')
        expansion = ExpansionFactory(collection_version=collection_version)

        self.assertEqual(self.request('get', self.source.uri).status_code, 200)
        self.assertEqual(self.request('get', self.concept.uri).status_code, 200)
        self.assertEqual(self.request('get', self.source.uri + 'versions/').status_code, 200)
        with patch('core.services.storages.cloud.aws.S3.has_path', return_value=False):
            self.assertEqual(self.request('get', version.uri + 'export/').status_code, 204)
        with patch('core.services.storages.cloud.aws.S3.has_path', return_value=True):
            self.assertEqual(self.request('post', version.uri + 'export/').status_code, 303)
        expansion_data = {'mnemonic': 'e1', 'parameters': {'activeOnly': False}}
        self.assertEqual(
            self.request('post', self.collection.uri + 'HEAD/expansions/', expansion_data).status_code, 201)
        self.assertEqual(self.request('post', version.uri + 'processing/').status_code, 200)
        self.assertEqual(self.request('post', collection_version.uri + 'processing/').status_code, 200)
        self.assertEqual(self.request('post', expansion.url + 'processing/').status_code, 200)
        self.assertEqual(
            self.request(
                'post', self.collection.uri + 'references/preview/', {'data': {'expressions': [self.concept.uri]}}
            ).status_code, 200)
        self.assertEqual(ReadOnlyPolicy.calls, [])

    def test_a_writable_repository_is_written(self):
        ReadOnlyPolicy.read_only = [self.collection]

        response = self.request('put', self.source.uri, {'full_name': 'Renamed'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ReadOnlyPolicy.calls, [(self.user, self.source, WRITE_ACTION_EDIT)])


@override_settings(CAPABILITY_POLICY=READ_ONLY_POLICY)
class MapProjectWriteCheckpointTest(WriteCheckpointTestMixin, OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.project = MapProjectFactory(organization=self.org)
        self.url = f'{self.org.uri}map-projects/{self.project.id}/'
        ReadOnlyPolicy.read_only = [self.project]

    def test_update_is_an_edit_and_delete_a_delete(self):
        self.assert_refused(self.request('patch', self.url, {'name': 'Renamed'}), self.project, WRITE_ACTION_EDIT)
        self.assert_refused(self.request('put', self.url, {'name': 'Renamed'}), self.project, WRITE_ACTION_EDIT)
        self.assert_refused(self.request('delete', self.url), self.project, WRITE_ACTION_DELETE)
        self.project.refresh_from_db()
        self.assertTrue(self.project.is_active)
        self.assertNotEqual(self.project.name, 'Renamed')

    def test_logs_and_auto_match_runs_are_edits(self):
        run = AutomatchRunFactory(map_project=self.project)

        self.assert_refused(
            self.request('post', self.url + 'logs/', {'logs': {'a': 1}}), self.project, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('post', self.url + 'auto-match-runs/', {'intended_rows': 5, 'trigger_source': 'api'}),
            self.project, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('patch', f'/auto-match-runs/{run.id}/', {'completed_rows': 5}), self.project,
            WRITE_ACTION_EDIT)
        self.assertEqual(self.project.auto_match_runs.count(), 1)

    def test_reads_are_not_asked_about(self):
        self.assertEqual(self.request('get', self.url).status_code, 200)
        self.assertEqual(self.request('get', self.url + 'logs/').status_code, 200)
        self.assertEqual(ReadOnlyPolicy.calls, [])


@override_settings(CAPABILITY_POLICY=READ_ONLY_POLICY)
class ImportWriteCheckpointTest(WriteCheckpointTestMixin, OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.writable_source = OrganizationSourceFactory(organization=self.org)
        ReadOnlyPolicy.read_only = [self.source]
        for task in ('index_concepts_mapped_codes', 'batch_index_resources'):
            patcher = patch(f'core.importers.models.{task}', Mock(__name__=task))
            patcher.start()
            self.addCleanup(patcher.stop)

    def concept_line(self, source, mnemonic):
        return {
            'type': 'Concept', 'owner': self.org.mnemonic, 'owner_type': 'Organization', 'source': source.mnemonic,
            **concept_payload(mnemonic)
        }

    def test_a_refused_line_is_denied_with_the_body_and_the_rest_imports(self):
        importer = BulkImportInline(
            '\n'.join(json.dumps(line) for line in [
                self.concept_line(self.source, 'refused'), self.concept_line(self.writable_source, 'imported'),
                {'type': 'Source', '__action': 'DELETE', 'id': self.source.mnemonic, 'name': self.source.name,
                 'owner': self.org.mnemonic, 'owner_type': 'Organization'},
            ]),
            self.user.username, True)
        importer.run()

        self.assertEqual(len(importer.created), 1)
        self.assertEqual([item['errors'] for item in importer.permission_denied], [ReadOnlyPolicy.body] * 2)
        self.assertEqual(importer.failed, [])
        self.assertFalse(self.source.concepts_set.filter(mnemonic='refused').exists())
        self.assertTrue(self.writable_source.concepts_set.filter(mnemonic='imported').exists())
        self.source.refresh_from_db()
        self.assertTrue(self.source.is_active)
        self.assertEqual(
            ReadOnlyPolicy.calls, [
                (self.user, self.source, WRITE_ACTION_EDIT), (self.user, self.writable_source, WRITE_ACTION_EDIT),
                (self.user, self.source, WRITE_ACTION_DELETE),
            ])

    def test_a_refused_fhir_update_fails_with_the_body(self):
        result = ResourceImporter.save_fhir_resource(
            SourceDetailSerializer, self.source, self.org, {'full_name': 'Renamed'}, SimpleNamespace(user=self.user))

        self.assertIsInstance(result, WriteRefused)
        self.assertEqual(result.detail, ReadOnlyPolicy.body)
        self.source.refresh_from_db()
        self.assertNotEqual(self.source.full_name, 'Renamed')


@override_settings(CAPABILITY_POLICY=READ_ONLY_POLICY)
class HierarchyWriteCheckpointTest(WriteCheckpointTestMixin, OCLAPITestCase):
    def test_a_parent_in_a_read_only_repository_is_left_as_it_is(self):
        other_source = OrganizationSourceFactory(organization=self.org)
        parents = [
            ConceptFactory(parent=source, names=[ConceptNameFactory.build(locale='en', locale_preferred=True)])
            for source in (self.source, other_source)
        ]
        child = ConceptFactory(
            parent=self.source, created_by=self.user,
            names=[ConceptNameFactory.build(locale='en', locale_preferred=True)])
        ReadOnlyPolicy.read_only = [other_source]
        versions_before = [parent.versions.count() for parent in parents]

        child._parent_concepts = parents  # pylint: disable=protected-access
        child.set_parent_concepts_from_uris()

        self.assertEqual([parent.versions.count() for parent in parents], [versions_before[0] + 1, versions_before[1]])
        self.assertEqual(ReadOnlyPolicy.calls, [(self.user, other_source, WRITE_ACTION_EDIT)])
        self.assertEqual(
            sorted(child.parent_concepts.values_list('versioned_object_id', flat=True)),
            sorted(parent.id for parent in parents))
