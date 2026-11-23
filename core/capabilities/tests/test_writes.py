import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import override_settings

from core.capabilities.constants import WRITE_ACTION_DELETE, WRITE_ACTION_EDIT, WRITE_ACTION_PUBLISH
from core.capabilities.exceptions import WriteRefused
from core.capabilities.policy import GroupLimitPolicy
from core.capabilities.writes import BatchWriteRefusals, check_write, get_own_write_action
from core.code_systems.serializers import CodeSystemDetailSerializer
from core.code_systems.views import CodeSystemRetrieveUpdateView
from core.collections.tests.factories import ExpansionFactory, OrganizationCollectionFactory
from core.collections.serializers import CollectionVersionDetailSerializer
from core.collections.views import CollectionRetrieveUpdateDestroyView
from core.common.constants import ACCESS_TYPE_NONE
from core.common.permissions import ChecksWrites
from core.common.tasks import add_references
from core.common.tests import OCLAPITestCase
from core.concept_maps.serializers import ConceptMapDetailSerializer
from core.concept_maps.views import ConceptMapRetrieveUpdateView
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.importers.importer import ImportRequest, ResourceImporter
from core.importers.models import BulkImportInline
from core.map_projects.tests.factories import AutomatchRunFactory, MapProjectFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.serializers import SourceDetailSerializer, SourceVersionDetailSerializer
from core.sources.tests.factories import OrganizationSourceFactory
from core.sources.views import SourceRetrieveUpdateDestroyView
from core.users.tests.factories import UserProfileFactory
from core.value_sets.views import ValueSetRetrieveUpdateView

READ_ONLY_POLICY = 'core.capabilities.tests.test_writes.ReadOnlyPolicy'


class ReadOnlyPolicy(GroupLimitPolicy):
    """
    Keeps the content in `read_only`, and the versions of a repository listed there, read-only, refusing writes to it
    with `body`. Records every question asked.
    """
    calls = []
    read_only = []
    body = {'error_code': 'read_only', 'detail': 'This content is read-only.', 'code': 7}

    def get_write_refusal(self, user, content, action):
        self.calls.append((user, content, action))
        return dict(self.body) if content in self.read_only or getattr(content, 'head', None) in self.read_only \
            else None


def new_concept(source, **kwargs):
    return ConceptFactory(parent=source, names=[ConceptNameFactory.build(locale='en', locale_preferred=True)], **kwargs)


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

    def test_a_public_access_that_is_not_a_string_is_a_validation_error(self):
        self.source.public_access = ACCESS_TYPE_NONE
        self.source.save()

        for public_access in (5, ['View'], {'a': 'View'}):
            with self.subTest(public_access=public_access):
                response = self.request('put', self.source.uri, {'public_access': public_access})

                self.assertEqual(response.status_code, 400)
                self.assertIn('public_access', response.data)

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_a_version_is_created_on_the_repository_in_the_url(self):
        other_source = OrganizationSourceFactory(organization=self.org)
        other_collection = OrganizationCollectionFactory(organization=self.org)
        for repo, other in ((self.source, other_source), (self.collection, other_collection)):
            with self.subTest(repo=repo):
                response = self.request('post', repo.uri + 'versions/', {'id': 'v2', 'mnemonic': other.mnemonic})

                self.assertEqual(response.status_code, 201, response.data)
                self.assertTrue(repo.versions.filter(version='v2').exists())
                self.assertFalse(other.versions.filter(version='v2').exists())

    def test_a_fhir_update_writes_only_the_repository_it_was_asked_about(self):
        other = OrganizationSourceFactory(organization=self.org, full_name='Other')
        OrganizationSourceFactory(organization=self.org, mnemonic=self.source.mnemonic, version='v1', released=True)
        resource = {
            'resourceType': 'CodeSystem', 'id': other.mnemonic, 'status': 'draft', 'content': 'fragment',
            'version': 'v2', 'name': 'Renamed', 'title': 'Renamed'
        }

        response = self.request('put', f'{self.org.uri}CodeSystem/{self.source.mnemonic}/', resource)

        self.assertEqual(response.status_code, 400)
        self.assertIn('The id must be', json.dumps(response.json()))
        for serializer_class, resource_type in (
                (CodeSystemDetailSerializer, 'CodeSystem'), (ConceptMapDetailSerializer, 'ConceptMap')):
            with self.subTest(resource_type=resource_type):
                result = ResourceImporter.save_fhir_resource(
                    serializer_class, self.source, self.org, {**resource, 'resourceType': resource_type},
                    ImportRequest('orgs', self.org.mnemonic, self.user.username, resource_type))

                self.assertIn('id', result)
        other.refresh_from_db()
        self.assertEqual(other.full_name, 'Other')
        self.assertFalse(Source.objects.filter(version='v2').exists())

    def test_a_version_update_never_moves_it_to_another_repository(self):
        repos = ((self.source, OrganizationSourceFactory), (self.collection, OrganizationCollectionFactory))
        for repo, factory in repos:
            with self.subTest(repo=repo):
                other = factory(organization=self.org)
                version = factory(organization=self.org, mnemonic=repo.mnemonic, version='v1')

                for url, data in (
                        (version.uri, {'short_code': other.mnemonic, 'description': 'Changed'}),
                        (repo.uri + '?version=v1', {'id': other.mnemonic, 'description': 'Changed'})):
                    response = self.request('put', url, data)

                    self.assertEqual(response.status_code, 200, response.data)
                    version.refresh_from_db()
                    self.assertEqual((version.mnemonic, version.description), (repo.mnemonic, 'Changed'))
                    self.assertFalse(other.versions.filter(version='v1').exists())

    def test_a_row_takes_a_new_mnemonic_only_as_a_head_that_stays_one(self):
        """A version relabelled HEAD, or a HEAD relabelled a version, keeps its repository."""
        for repo, factory, serializer in (
                (self.source, OrganizationSourceFactory, SourceVersionDetailSerializer),
                (self.collection, OrganizationCollectionFactory, CollectionVersionDetailSerializer)):
            with self.subTest(repo=repo):
                version = factory(organization=self.org, mnemonic=repo.mnemonic, version='v1')

                for row, label in ((version, 'HEAD'), (repo, 'v9')):
                    row = type(row).objects.get(id=row.id)
                    prepared = serializer().prepare_object({'version': label, 'mnemonic': 'elsewhere'}, row)
                    self.assertEqual(prepared.mnemonic, repo.mnemonic)
                head = type(repo).objects.get(id=repo.id)
                self.assertEqual(serializer().prepare_object({'mnemonic': 'renamed'}, head).mnemonic, 'renamed')

    def test_a_version_without_an_active_head_is_published_as_it_would_be_viewed(self):
        version = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v1', public_access=ACCESS_TYPE_NONE)
        data = {'public_access': 'View'}

        self.assertEqual(get_own_write_action(version, 'PUT', data), WRITE_ACTION_PUBLISH)
        Source.objects.filter(id=self.source.id).update(is_active=False)
        version = Source.objects.get(id=version.id)
        self.assertEqual(get_own_write_action(version, 'PUT', data), WRITE_ACTION_PUBLISH)
        Source.objects.filter(id=self.source.id).update(is_active=True, public_access=ACCESS_TYPE_NONE)
        version = Source.objects.get(id=version.id)
        self.assertEqual(get_own_write_action(version, 'PUT', data), WRITE_ACTION_EDIT)

    @patch('core.map_projects.models.MapProject._delete_uploaded_file', Mock())
    def test_a_delete_body_is_never_read(self):
        project = MapProjectFactory(organization=self.org)

        response = self.client.delete(
            f'{self.org.uri}map-projects/{project.id}/', 'a note', content_type='text/plain',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token())

        self.assertEqual(response.status_code, 204)


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

    def test_the_head_version_route_classifies_like_the_repository_route(self):
        self.source.public_access = ACCESS_TYPE_NONE
        self.source.save()

        self.assert_refused(
            self.request('put', self.source.uri + 'HEAD/', {'public_access': 'View'}), self.source,
            WRITE_ACTION_PUBLISH)
        for repo in (self.source, self.collection):
            with self.subTest(repo=repo):
                self.assert_refused(
                    self.request('patch', repo.uri + 'HEAD/', {'description': 'Changed'}), repo, WRITE_ACTION_EDIT)
                self.assert_refused(self.request('delete', repo.uri + 'HEAD/'), repo, WRITE_ACTION_DELETE)
                repo.refresh_from_db()
                self.assertTrue(repo.is_active)

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_a_fhir_update_is_asked_about_the_head_it_saves_onto(self):
        ReadOnlyPolicy.read_only = []
        release = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        ReadOnlyPolicy.read_only = [release]
        resource = {
            'resourceType': 'CodeSystem', 'id': self.source.mnemonic, 'status': 'draft', 'content': 'fragment',
            'version': 'v2', 'name': 'Renamed', 'title': 'Renamed'
        }

        response = self.request('put', f'{self.org.uri}CodeSystem/{self.source.mnemonic}/', resource)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(ReadOnlyPolicy.calls, [(self.user, self.source, WRITE_ACTION_EDIT)])
        ReadOnlyPolicy.calls = []
        ResourceImporter.save_fhir_resource(
            CodeSystemDetailSerializer, release, self.org, {**resource, 'version': 'v3'},
            ImportRequest('orgs', self.org.mnemonic, self.user.username, 'CodeSystem'))
        self.assertEqual(ReadOnlyPolicy.calls, [(self.user, self.source, WRITE_ACTION_EDIT)])

    def test_a_fhir_write_to_a_release_is_an_edit_and_never_publishes(self):
        self.source.public_access = ACCESS_TYPE_NONE
        self.source.save()
        for repo, factory, resource_type in (
                (self.source, OrganizationSourceFactory, 'CodeSystem'),
                (self.collection, OrganizationCollectionFactory, 'ValueSet')):
            with self.subTest(resource_type=resource_type):
                release = factory(organization=self.org, mnemonic=repo.mnemonic, version='v1', released=True)
                url = f'{self.org.uri}{resource_type}/{repo.mnemonic}/'

                for method, data, content in (
                        ('put', {'public_access': 'View', 'title': 'Renamed'}, repo), ('delete', None, release)):
                    response = self.request(method, url, data)

                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(ReadOnlyPolicy.calls, [(self.user, content, WRITE_ACTION_EDIT)])
                    ReadOnlyPolicy.calls = []
                release.refresh_from_db()
                self.assertTrue(release.is_active)

        # a CodeSystem without a release is its HEAD: its DELETE deletes the repository
        source = OrganizationSourceFactory(organization=self.org)
        ReadOnlyPolicy.read_only.append(source)
        self.assertEqual(self.request('delete', f'{self.org.uri}CodeSystem/{source.mnemonic}/').status_code, 403)
        self.assertEqual(ReadOnlyPolicy.calls, [(self.user, source, WRITE_ACTION_DELETE)])

    def test_fhir_views_never_publish(self):
        self.source.public_access = self.collection.public_access = ACCESS_TYPE_NONE
        request = SimpleNamespace(method='PUT', data={'public_access': 'View'})
        for view, repo in (
                (CodeSystemRetrieveUpdateView, self.source), (ConceptMapRetrieveUpdateView, self.source),
                (ValueSetRetrieveUpdateView, self.collection)):
            with self.subTest(view=view):
                self.assertEqual(ChecksWrites().get_write_action(request, view, repo), WRITE_ACTION_EDIT)
        for view, repo in (
                (SourceRetrieveUpdateDestroyView, self.source), (CollectionRetrieveUpdateDestroyView, self.collection)):
            with self.subTest(view=view):
                self.assertEqual(ChecksWrites().get_write_action(request, view, repo), WRITE_ACTION_PUBLISH)

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

    def test_version_writes_are_edits_of_the_version(self):
        version = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v1', released=True)

        self.assert_refused(
            self.request('post', self.source.uri + 'versions/', {'id': 'v2', 'description': 'Second'}), self.source,
            WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('put', version.uri, {'description': 'Changed'}), version, WRITE_ACTION_EDIT)
        self.assert_refused(self.request('delete', version.uri), version, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('put', self.source.uri + 'latest/', {'description': 'Changed'}), version, WRITE_ACTION_EDIT)
        self.assertFalse(Source.objects.filter(mnemonic=self.source.mnemonic, version='v2').exists())
        version.refresh_from_db()
        self.assertTrue(version.is_active)
        self.assertNotEqual(version.description, 'Changed')

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_a_version_of_a_writable_repository_never_lands_in_a_read_only_one(self):
        for read_only, writable in (
                (self.source, OrganizationSourceFactory(organization=self.org)),
                (self.collection, OrganizationCollectionFactory(organization=self.org))):
            with self.subTest(repo=writable):
                response = self.request(
                    'post', writable.uri + 'versions/', {'id': 'v2', 'mnemonic': read_only.mnemonic})

                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(ReadOnlyPolicy.calls, [(self.user, writable, WRITE_ACTION_EDIT)])
                ReadOnlyPolicy.calls = []
                self.assertFalse(read_only.versions.filter(version='v2').exists())

    def test_a_private_version_of_a_public_repository_is_asked_about_itself(self):
        ReadOnlyPolicy.read_only = []
        repos = ((self.source, OrganizationSourceFactory), (self.collection, OrganizationCollectionFactory))
        for repo, factory in repos:
            with self.subTest(repo=repo):
                version = factory(
                    organization=self.org, mnemonic=repo.mnemonic, version='v1', released=True,
                    public_access=ACCESS_TYPE_NONE)
                description = version.description
                ReadOnlyPolicy.read_only = [version]

                self.assert_refused(
                    self.request('put', version.uri, {'description': 'Changed'}), version, WRITE_ACTION_EDIT)
                self.assert_refused(
                    self.request('put', version.uri, {'public_access': 'View'}), version, WRITE_ACTION_PUBLISH)
                self.assert_refused(
                    self.request('put', repo.uri + 'latest/', {'public_access': 'View'}), version,
                    WRITE_ACTION_PUBLISH)
                self.assertEqual(self.request('put', repo.uri, {'full_name': 'Renamed'}).status_code, 200)
                self.assertEqual(ReadOnlyPolicy.calls, [(self.user, repo, WRITE_ACTION_EDIT)])
                ReadOnlyPolicy.calls = []
                version.refresh_from_db()
                self.assertEqual((version.public_access, version.description), (ACCESS_TYPE_NONE, description))

                # under a private HEAD, the version stays hidden: making it viewable is only an edit
                type(repo).objects.filter(id=repo.id).update(public_access=ACCESS_TYPE_NONE)
                self.assert_refused(
                    self.request('put', version.uri, {'public_access': 'View'}), version, WRITE_ACTION_EDIT)

    @patch('core.collections.views.seed_children_to_expansion', Mock(__name__='seed_children_to_expansion'))
    def test_every_expansion_write_is_an_edit_of_the_version(self):
        """An expansion is what the version resolves to, default or not."""
        version = OrganizationCollectionFactory(organization=self.org, mnemonic=self.collection.mnemonic, version='v1')
        default, other = ExpansionFactory(collection_version=version), ExpansionFactory(collection_version=version)
        version.expansion_uri = default.uri
        version.save()

        self.assert_refused(
            self.request('post', version.uri + 'expansions/', {'mnemonic': 'e1', 'parameters': {'activeOnly': False}}),
            version, WRITE_ACTION_EDIT)
        self.assert_refused(self.request('delete', other.url), version, WRITE_ACTION_EDIT)
        for expansion in (default, other):
            self.assert_refused(self.request('post', expansion.url + 're-evaluate/'), version, WRITE_ACTION_EDIT)
        self.assertEqual(sorted(version.expansions.values_list('id', flat=True)), sorted([default.id, other.id]))
        self.assertFalse(version.expansions.filter(is_processing=True).exists())

    def test_a_queued_reference_add_is_asked_about_again_when_it_runs(self):
        ReadOnlyPolicy.read_only = []
        data = {'expressions': [self.concept.uri]}

        with patch('core.capabilities.writes.get_write_refusal', side_effect=[None, dict(ReadOnlyPolicy.body)]):
            response = self.request('put', self.collection.uri + 'references/', {'data': data})

        self.assertEqual((response.status_code, response.json()), (403, ReadOnlyPolicy.body))
        ReadOnlyPolicy.read_only = [self.collection]
        self.assertEqual(
            add_references(self.user.id, data, self.collection.id),  # pylint: disable=no-value-for-parameter
            ([], {'write_refused': ReadOnlyPolicy.body}, []))
        self.assertFalse(self.collection.references.exists())

    def test_external_export_uploads_and_removals_are_edits_of_the_version(self):
        version = OrganizationSourceFactory(organization=self.org, mnemonic=self.source.mnemonic, version='v1')
        url = version.uri + 'export/fhir/'

        self.assert_refused(self.request('post', url, {'file': 'x'}), version, WRITE_ACTION_EDIT)
        self.assert_refused(self.request('delete', url), version, WRITE_ACTION_EDIT)
        self.assertFalse(version.external_exports.exists())

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

    def test_reads_exports_and_processing_are_not_asked_about(self):
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
        self.assertEqual(self.request('get', collection_version.uri + 'expansions/').status_code, 200)
        self.assertEqual(self.request('get', expansion.url).status_code, 200)
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

    def test_logs_and_starting_an_auto_match_run_are_edits(self):
        self.assert_refused(
            self.request('post', self.url + 'logs/', {'logs': {'a': 1}}), self.project, WRITE_ACTION_EDIT)
        self.assert_refused(
            self.request('post', self.url + 'auto-match-runs/', {'intended_rows': 5, 'trigger_source': 'api'}),
            self.project, WRITE_ACTION_EDIT)
        self.assertFalse(self.project.auto_match_runs.exists())

    def test_a_started_run_records_its_progress_and_finishes_without_being_asked(self):
        """The policy's answer for a project can change during a run: the run still closes."""
        run = AutomatchRunFactory(map_project=self.project)

        response = self.request(
            'patch', f'/auto-match-runs/{run.id}/', {'completed_rows': 5, 'completion_status': 'completed'})

        self.assertEqual(response.status_code, 200, response.data)
        run.refresh_from_db()
        self.assertEqual((run.completed_rows, run.completion_status), (5, 'completed'))
        self.assertEqual(ReadOnlyPolicy.calls, [])

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

    def test_a_batch_asks_once_per_repository_and_action(self):
        importer = BulkImportInline(
            '\n'.join(
                json.dumps(self.concept_line(source, f'{source.mnemonic}-{i}'))
                for source in (self.source, self.writable_source) for i in range(3)
            ),
            self.user.username, True)
        importer.run()

        self.assertEqual(len(importer.created), 3)
        self.assertEqual([item['errors'] for item in importer.permission_denied], [ReadOnlyPolicy.body] * 3)
        self.assertEqual(
            ReadOnlyPolicy.calls,
            [(self.user, self.source, WRITE_ACTION_EDIT), (self.user, self.writable_source, WRITE_ACTION_EDIT)])

    def test_a_batch_asks_again_once_an_answer_is_a_minute_old(self):
        """A change in the policy's answer (a date passing, an override) reaches a long import within the minute."""
        write_refusals = BatchWriteRefusals()
        ReadOnlyPolicy.read_only = []

        with patch('core.capabilities.writes.monotonic', side_effect=[0, 59, 60]):
            self.assertIsNone(write_refusals.get(self.user, self.source, WRITE_ACTION_EDIT))
            ReadOnlyPolicy.read_only = [self.source]
            self.assertIsNone(write_refusals.get(self.user, self.source, WRITE_ACTION_EDIT))
            self.assertEqual(write_refusals.get(self.user, self.source, WRITE_ACTION_EDIT), ReadOnlyPolicy.body)
        self.assertEqual(len(ReadOnlyPolicy.calls), 2)

    def test_an_answer_asked_again_is_about_the_row_as_stored_now(self):
        """A batch keeps the rows it loaded: one made public during the batch is asked about as public."""
        write_refusals = BatchWriteRefusals()
        Source.objects.filter(id=self.source.id).update(public_access=ACCESS_TYPE_NONE)
        loaded = Source.objects.get(id=self.source.id)

        with patch('core.capabilities.writes.monotonic', side_effect=[0, 60]):
            write_refusals.get(self.user, loaded, WRITE_ACTION_EDIT)
            Source.objects.filter(id=self.source.id).update(public_access='View')
            write_refusals.get(self.user, loaded, WRITE_ACTION_EDIT)

        self.assertEqual([call[1].public_access for call in ReadOnlyPolicy.calls], [ACCESS_TYPE_NONE, 'View'])

    def test_the_rows_of_a_package_file_share_their_answers(self):
        importer = ResourceImporter(write_refusals=BatchWriteRefusals())

        for mnemonic in ('a', 'b'):
            importer.import_resource(
                self.concept_line(self.writable_source, mnemonic), self.user.username, 'Organization',
                self.org.mnemonic)

        self.assertEqual(ReadOnlyPolicy.calls, [(self.user, self.writable_source, WRITE_ACTION_EDIT)])

    def test_a_line_deleting_a_release_is_an_edit(self):
        release = OrganizationSourceFactory(organization=self.org, mnemonic=self.source.mnemonic, version='v1')
        importer = BulkImportInline(
            json.dumps({'type': 'Source', '__action': 'DELETE', 'id': self.source.mnemonic, 'name': 'x',
                        'owner': self.org.mnemonic, 'owner_type': 'Organization'}),
            self.user.username, True)

        with patch('core.importers.models.SourceImporter.get_queryset', return_value=Source.objects.filter(
                id=release.id)):
            importer.run()

        self.assertEqual([item['errors'] for item in importer.permission_denied], [ReadOnlyPolicy.body])
        self.assertEqual(ReadOnlyPolicy.calls, [(self.user, release, WRITE_ACTION_EDIT)])
        self.assertTrue(Source.objects.filter(id=release.id).exists())

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

    def test_a_parent_s_new_version_is_linked_to_children_in_read_only_repositories(self):
        """The system keeps a child's link to its parent's latest version current: no write of the child's owner."""
        read_only_source, parent_source = (OrganizationSourceFactory(organization=self.org) for _ in range(2))
        parent = new_concept(parent_source)
        children = [new_concept(source) for source in (self.source, read_only_source)]
        for child in children:
            child.get_latest_version().parent_concepts.add(parent.get_latest_version())
        ReadOnlyPolicy.read_only = [read_only_source]
        new_child = new_concept(self.source, created_by=self.user)

        new_child._parent_concepts = [parent]  # pylint: disable=protected-access
        new_child.set_parent_concepts_from_uris()

        new_version = parent.get_latest_version()
        self.assertEqual(
            [new_version in child.get_latest_version().parent_concepts.all() for child in children], [True, True])
        self.assertEqual(ReadOnlyPolicy.calls, [(self.user, parent_source, WRITE_ACTION_EDIT)])

    def test_an_import_s_hierarchy_leaves_children_in_read_only_repositories_as_they_are(self):
        read_only_source = OrganizationSourceFactory(organization=self.org)
        parent = new_concept(self.source)
        children = [new_concept(source) for source in (self.source, read_only_source)]
        ReadOnlyPolicy.read_only = [read_only_source]

        Concept.reconcile_import_hierarchy({child.uri: [parent.uri] for child in children}, self.user)

        parent_version = parent.get_latest_version()
        self.assertEqual(
            [child.parent_concepts.filter(id=parent_version.id).exists() for child in children], [True, False])
        self.assertEqual({call[1] for call in ReadOnlyPolicy.calls}, {self.source, read_only_source})
