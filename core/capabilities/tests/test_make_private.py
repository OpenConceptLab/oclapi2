import json
from unittest.mock import Mock, patch

from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from core.capabilities.constants import WRITE_ACTION_EDIT, WRITE_ACTION_MAKE_PRIVATE, WRITE_ACTION_PUBLISH
from core.capabilities.exceptions import WriteRefused
from core.capabilities.policy import GroupLimitPolicy
from core.capabilities.writes import makes_private
from core.code_systems.serializers import CodeSystemDetailSerializer
from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.models import CelerySignalProcessor
from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW
from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory
from core.importers.importer import ImportRequest, ResourceImporter
from core.importers.models import BulkImportInline
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.tests.factories import UserProfileFactory

PRIVATE_GATE_POLICY = 'core.capabilities.tests.test_make_private.PrivateGatePolicy'
INDEX_PATCHES = [
    patch(f'core.sources.models.{task}', Mock(__name__=task))
    for task in ('index_source_concepts', 'index_source_mappings')
]


class PrivateGatePolicy(GroupLimitPolicy):
    """Refuses every write that makes a repository private, with `body`, while `refuse` is set. Records questions."""
    calls = []
    refuse = True
    body = {'error_code': 'private_refused', 'detail': 'Not here.', 'code': 3}

    def get_write_refusal(self, user, content, action):
        self.calls.append((user, content, action))
        return dict(self.body) if self.refuse and action == WRITE_ACTION_MAKE_PRIVATE else None


class MakePrivateTestMixin:
    def setUp(self):
        super().setUp()
        PrivateGatePolicy.calls = []
        PrivateGatePolicy.refuse = True
        self.user = UserProfileFactory()
        self.org = OrganizationFactory()
        self.org.members.add(self.user)
        self.source = OrganizationSourceFactory(organization=self.org)
        self.collection = OrganizationCollectionFactory(organization=self.org)
        for patcher in INDEX_PATCHES:
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, method, url, data=None):
        return getattr(self.client, method)(
            url, data, HTTP_AUTHORIZATION='Token ' + self.user.get_token(), format='json')

    @staticmethod
    def take_calls():
        """The questions asked since the last call, as (user, content, action), and forgets them."""
        calls, PrivateGatePolicy.calls = PrivateGatePolicy.calls, []
        return calls

    def assert_asked_to_make_private(self, calls, row_id=None, owner=None, mnemonic=None):
        """The last question is `make_private`, about the row `row_id`, or a new row of `owner` named `mnemonic`."""
        user, content, action = calls[-1]
        self.assertEqual((user, action), (self.user, WRITE_ACTION_MAKE_PRIVATE))
        self.assertFalse(content.public_can_view)
        if row_id is not None:
            self.assertEqual(content.id, row_id)
        else:
            self.assertEqual((content.mnemonic, content.parent), (mnemonic, owner))
        self.assertNotIn(WRITE_ACTION_MAKE_PRIVATE, [call[2] for call in calls[:-1]])


class DefaultPolicyAllowsPrivateRepositoriesTest(MakePrivateTestMixin, OCLAPITestCase):
    def test_the_default_policy_allows_making_a_repository_private(self):
        self.assertIsNone(GroupLimitPolicy().get_write_refusal(self.user, self.source, WRITE_ACTION_MAKE_PRIVATE))
        response = self.request('post', self.org.uri + 'sources/', {'id': 'priv', 'name': 'p', 'public_access': 'None'})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.request('put', self.collection.uri, {'public_access': 'None'}).status_code, 200)

        self.assertFalse(Source.objects.get(mnemonic='priv').public_can_view)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.public_access, ACCESS_TYPE_NONE)


class MakesPrivateTest(OCLAPITestCase):
    def test_a_row_is_made_private_when_others_could_view_it_by_its_own_access_before(self):
        user, other_user, org = UserProfileFactory(), UserProfileFactory(), OrganizationFactory()

        def row(public_access, **owner):
            return Source(mnemonic='repo', public_access=public_access, **(owner or {'organization': org}))

        for new, stored, expected in (
                (row(ACCESS_TYPE_NONE), None, True),
                (row(''), None, True),
                (row(ACCESS_TYPE_VIEW), None, False),
                (row('Edit'), None, False),
                (row(ACCESS_TYPE_NONE), row(ACCESS_TYPE_VIEW), True),
                (row(ACCESS_TYPE_NONE), row('Edit'), True),
                (row(ACCESS_TYPE_NONE), row(ACCESS_TYPE_NONE), False),
                (row(ACCESS_TYPE_VIEW), row(ACCESS_TYPE_NONE), False),
                (row(ACCESS_TYPE_NONE, user=user), row(ACCESS_TYPE_NONE, user=user), False),
                (row(ACCESS_TYPE_NONE, user=other_user), row(ACCESS_TYPE_NONE, user=user), True),
                (row(ACCESS_TYPE_NONE, user=user), row(ACCESS_TYPE_NONE), True),
        ):
            with self.subTest(new=(new.public_access, new.parent), stored=stored and stored.public_access):
                self.assertEqual(makes_private(new, stored), expected)


@override_settings(CAPABILITY_POLICY=PRIVATE_GATE_POLICY)
class CreatePrivateRepositoryTest(MakePrivateTestMixin, OCLAPITestCase):
    def test_creating_a_private_repository_is_refused_with_the_body(self):
        for owner in (self.org, self.user):
            for kind, model in (('sources', Source), ('collections', Collection)):
                with self.subTest(owner=owner, kind=kind):
                    mnemonic = f'{kind}-{owner.mnemonic}'
                    response = self.request(
                        'post', f'{owner.uri}{kind}/', {'id': mnemonic, 'name': 'Private', 'public_access': 'None'})

                    self.assertEqual(response.status_code, 403, response.data)
                    self.assertEqual(response.json(), PrivateGatePolicy.body)  # as the policy sent it
                    self.assertFalse(model.objects.filter(mnemonic=mnemonic).exists())
                    self.assert_asked_to_make_private(self.take_calls(), owner=owner, mnemonic=mnemonic)

    def test_a_private_repository_the_policy_allows_is_created(self):
        PrivateGatePolicy.refuse = False

        response = self.request('post', self.org.uri + 'collections/', {
            'id': 'allowed', 'name': 'Allowed', 'public_access': 'None'})

        self.assertEqual(response.status_code, 201, response.data)
        created = Collection.objects.get(mnemonic='allowed')
        self.assertEqual(created.public_access, ACCESS_TYPE_NONE)
        self.assert_asked_to_make_private(self.take_calls(), row_id=created.id)

    def test_creating_a_repository_others_can_view_is_not_asked(self):
        for data in ({'public_access': 'View'}, {'public_access': 'Edit'}, {}):
            with self.subTest(data=data):
                mnemonic = f'public-{len(data) and data["public_access"]}'
                response = self.request('post', self.org.uri + 'sources/', {'id': mnemonic, 'name': 'Public', **data})

                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(self.take_calls(), [])


@override_settings(CAPABILITY_POLICY=PRIVATE_GATE_POLICY)
class UpdateToPrivateTest(MakePrivateTestMixin, OCLAPITestCase):
    def assert_refused(self, response, row):
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.json(), PrivateGatePolicy.body)
        self.assert_asked_to_make_private(self.take_calls(), row_id=row.id)
        row.refresh_from_db()
        self.assertEqual(row.public_access, ACCESS_TYPE_VIEW)

    def test_making_a_head_private_is_refused_on_every_route(self):
        for repo in (self.source, self.collection, UserSourceFactory(user=self.user)):
            for url in (repo.uri, repo.uri + 'HEAD/'):
                with self.subTest(url=url):
                    self.assert_refused(self.request('put', url, {'public_access': 'None'}), repo)
                    self.assert_refused(self.request('patch', url, {'public_access': 'None', 'name': 'x'}), repo)

    def test_making_a_version_private_is_refused_by_its_own_access(self):
        repos = ((self.source, OrganizationSourceFactory), (self.collection, OrganizationCollectionFactory))
        for repo, factory in repos:
            version = factory(organization=self.org, mnemonic=repo.mnemonic, version='v1', released=True)
            with self.subTest(repo=repo):
                self.assert_refused(self.request('put', version.uri, {'public_access': 'None'}), version)
                self.assert_refused(self.request('put', repo.uri + 'latest/', {'public_access': 'None'}), version)

                # under a private HEAD too: the version keeps its own access, which others view once the HEAD is public
                type(repo).objects.filter(id=repo.id).update(public_access=ACCESS_TYPE_NONE)
                self.assert_refused(self.request('put', version.uri, {'public_access': 'None'}), version)

    def test_private_rows_edited_or_made_public_are_not_asked(self):
        PrivateGatePolicy.refuse = True
        for repo in (self.source, self.collection):
            type(repo).objects.filter(id=repo.id).update(public_access=ACCESS_TYPE_NONE)
            with self.subTest(repo=repo):
                for data, action in (
                        ({'name': 'Renamed', 'public_access': 'None'}, WRITE_ACTION_EDIT),
                        ({'name': 'Renamed'}, WRITE_ACTION_EDIT),
                        ({'public_access': 'View'}, WRITE_ACTION_PUBLISH)):
                    self.assertEqual(self.request('put', repo.uri, data).status_code, 200)
                    self.assertEqual([call[2] for call in self.take_calls()], [action])

    def test_a_new_version_takes_its_head_s_access_and_is_not_asked(self):
        for repo in (self.source, self.collection):
            type(repo).objects.filter(id=repo.id).update(public_access=ACCESS_TYPE_NONE)
            with self.subTest(repo=repo):
                response = self.request('post', repo.uri + 'versions/', {'id': 'v2', 'public_access': 'View'})

                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(repo.versions.get(version='v2').public_access, ACCESS_TYPE_NONE)
                self.assertEqual([call[2] for call in self.take_calls()], [WRITE_ACTION_EDIT])

    def fhir_update(self, repo, factory, resource_type, release_access, **extra):
        """PUTs a FHIR update of `repo` that resolves to a new release with `release_access`. The new version's id."""
        version = f'r-{release_access}'
        factory(
            organization=self.org, mnemonic=repo.mnemonic, version=version, released=True,
            public_access=release_access)
        resource = {
            'resourceType': resource_type, 'id': repo.mnemonic, 'status': 'draft', 'version': f'{version}-2',
            'name': 'Renamed', 'title': 'Renamed', **extra
        }
        return self.request('put', f'{self.org.uri}{resource_type}/{repo.mnemonic}/', resource), resource

    def fhir_repositories(self):
        return (
            (self.source, OrganizationSourceFactory, 'CodeSystem', {'content': 'fragment'}),
            (OrganizationSourceFactory(organization=self.org), OrganizationSourceFactory, 'ConceptMap', {}),
            (self.collection, OrganizationCollectionFactory, 'ValueSet', {}),
        )

    def test_a_fhir_update_never_makes_a_private_head_viewable(self):
        """A FHIR resource carries no access: a public release's isn't copied onto a private HEAD."""
        for repo, factory, resource_type, extra in self.fhir_repositories():
            with self.subTest(resource_type=resource_type):
                type(repo).objects.filter(id=repo.id).update(public_access=ACCESS_TYPE_NONE)

                response, resource = self.fhir_update(repo, factory, resource_type, ACCESS_TYPE_VIEW, **extra)

                self.assertEqual(response.status_code, 200, response.content)
                repo.refresh_from_db()
                self.assertEqual(repo.public_access, ACCESS_TYPE_NONE)
                self.assertEqual(repo.versions.get(version=resource['version']).public_access, ACCESS_TYPE_NONE)
                self.assertEqual([call[2] for call in self.take_calls()], [WRITE_ACTION_EDIT])

    def test_a_fhir_update_that_would_copy_a_private_release_onto_a_viewable_head_is_refused(self):
        """
        It copies the release's other values too, so HEAD takes the release's access and asks make_private. A blank
        access is private (public_can_view), so a blank release's is copied as it is, not as View.
        """
        for release_access in (ACCESS_TYPE_NONE, ''):
            for repo, factory, resource_type, extra in self.fhir_repositories():
                with self.subTest(resource_type=resource_type, release_access=release_access):
                    response, resource = self.fhir_update(repo, factory, resource_type, release_access, **extra)

                    # FHIR routes answer every refusal as an OperationOutcome: the body's values are its issues
                    self.assertEqual(response.status_code, 403, response.content)
                    self.assertEqual(response.json()['resourceType'], 'OperationOutcome')
                    issues = json.dumps(response.json()['issue'])
                    for value in PrivateGatePolicy.body.values():
                        self.assertIn(str(value), issues)
                    self.assert_asked_to_make_private(self.take_calls(), row_id=repo.id)
                    repo.refresh_from_db()
                    self.assertEqual(repo.public_access, ACCESS_TYPE_VIEW)
                    self.assertFalse(repo.versions.filter(version=resource['version']).exists())

        for release, version in (('r-None', 'v3'), ('r-', 'v4')):
            with self.subTest(imported_onto=release):
                result = ResourceImporter.save_fhir_resource(
                    CodeSystemDetailSerializer, self.source.versions.get(version=release), self.org,
                    {**resource, 'resourceType': 'CodeSystem', 'id': self.source.mnemonic, 'content': 'fragment',
                     'version': version},
                    ImportRequest('orgs', self.org.mnemonic, self.user.username, 'CodeSystem'))

                self.assertIsInstance(result, WriteRefused)
                self.assertEqual(result.detail, PrivateGatePolicy.body)
                self.source.refresh_from_db()
                self.assertEqual(self.source.public_access, ACCESS_TYPE_VIEW)

    def test_a_blank_release_the_policy_allows_leaves_head_and_the_new_version_private(self):
        PrivateGatePolicy.refuse = False
        for repo, factory, resource_type, extra in self.fhir_repositories():
            with self.subTest(resource_type=resource_type):
                response, resource = self.fhir_update(repo, factory, resource_type, '', **extra)

                self.assertEqual(response.status_code, 200, response.content)
                # HEAD's row: the serializer then saves the same object as the new version, so it's not checked by id
                self.assertEqual(
                    [call[2] for call in self.take_calls()], [WRITE_ACTION_EDIT, WRITE_ACTION_MAKE_PRIVATE])
                repo.refresh_from_db()
                self.assertFalse(repo.public_can_view)
                self.assertFalse(repo.versions.get(version=resource['version']).public_can_view)

    @staticmethod
    def made_private_first(model, method):
        """`model`'s `method`, run after a concurrent write that makes its row (the first argument) private commits."""
        original = getattr(model, method)

        def call(row, *args, **kwargs):
            model.objects.filter(id=row.id).update(public_access=ACCESS_TYPE_NONE)
            return original(row, *args, **kwargs)
        return call

    def test_a_fhir_update_keeps_a_head_made_private_after_the_update_read_it(self):
        """The update decides HEAD's access from the row it saves over, not from an earlier read."""
        for repo, factory, resource_type, extra in self.fhir_repositories():
            model = type(repo)
            with self.subTest(resource_type=resource_type):
                with patch.object(model, 'persist_changes', self.made_private_first(model, 'persist_changes')):
                    response, resource = self.fhir_update(repo, factory, resource_type, ACCESS_TYPE_VIEW, **extra)

                self.assertEqual(response.status_code, 200, response.content)
                repo.refresh_from_db()
                self.assertEqual(repo.public_access, ACCESS_TYPE_NONE)
                self.assertFalse(repo.versions.get(version=resource['version']).public_can_view)

    def test_a_value_set_update_s_references_keep_a_head_made_private_after_head_was_saved(self):
        """Adding the resource's references saves HEAD again, after the lock: only who changed it, not its access."""
        source = OrganizationSourceFactory(organization=self.org, canonical_url='http://example.org/cs')
        concept = ConceptFactory(parent=source)
        compose = {'include': [{'system': source.canonical_url, 'concept': [{'code': concept.mnemonic}]}]}

        with patch.object(Collection, 'add_references', self.made_private_first(Collection, 'add_references')):
            response, resource = self.fhir_update(
                self.collection, OrganizationCollectionFactory, 'ValueSet', ACCESS_TYPE_VIEW, compose=compose)

        self.assertEqual(response.status_code, 200, response.content)
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.public_access, ACCESS_TYPE_NONE)
        self.assertTrue(self.collection.references.exists())
        self.assertFalse(self.collection.versions.get(version=resource['version']).public_can_view)

    def test_a_fhir_update_reads_head_for_update_in_the_transaction_that_saves_it(self):
        """A concurrent write that makes HEAD private waits for the save, so it isn't overwritten."""
        for repo, factory, resource_type, extra in self.fhir_repositories():
            table = type(repo)._meta.db_table  # pylint: disable=protected-access
            row = f'"{table}"."id" = {repo.id}'
            with self.subTest(resource_type=resource_type):
                with CaptureQueriesContext(connection) as queries:
                    response, _ = self.fhir_update(repo, factory, resource_type, ACCESS_TYPE_VIEW, **extra)

                self.assertEqual(response.status_code, 200, response.content)
                sqls = [query['sql'] for query in queries.captured_queries]
                save = next(index for index, sql in enumerate(sqls) if sql.startswith(f'UPDATE "{table}" SET') and
                            '"public_access" = ' in sql and sql.endswith(row))
                lock = next((index for index, sql in enumerate(sqls[:save]) if sql.startswith('SELECT') and
                             f'WHERE {row}' in sql and sql.endswith('FOR UPDATE')), None)
                self.assertIsNotNone(lock, 'HEAD is not read FOR UPDATE before it is saved')
                savepoint = sqls[lock - 1]
                self.assertTrue(savepoint.startswith('SAVEPOINT '), savepoint)
                self.assertGreater(sqls.index(f'RELEASE {savepoint}'), save)

    def test_an_edit_that_sends_no_access_keeps_a_blank_row_private(self):
        """A blank access is private (public_can_view), so an edit that sends no access doesn't publish the row."""
        repos = ((self.source, OrganizationSourceFactory), (self.collection, OrganizationCollectionFactory))
        for repo, factory in repos:
            version = factory(organization=self.org, mnemonic=repo.mnemonic, version='v1', released=True)
            for row in (repo, version):
                type(row).objects.filter(id=row.id).update(public_access='')
                for method in ('put', 'patch'):
                    with self.subTest(uri=row.uri, method=method):
                        response = self.request(method, row.uri, {'name': 'Renamed'})

                        self.assertEqual(response.status_code, 200, response.data)
                        row.refresh_from_db()
                        self.assertEqual(row.public_access, '')
                        self.assertEqual([call[2] for call in self.take_calls()], [WRITE_ACTION_EDIT])


class SearchIndexOnCommitTest(OCLAPITestCase):
    @patch('core.common.models.handle_save')
    def test_a_save_queues_its_search_index_once_its_transaction_commits(self, handle_save_task):
        """A save inside a transaction, such as a FHIR update's locked one, is indexed as committed, not as it was."""
        source = OrganizationSourceFactory()
        with override_settings(TEST_MODE=False, ES_SYNC=True), self.captureOnCommitCallbacks(execute=True):
            CelerySignalProcessor.handle_save(None, Source, source)
            handle_save_task.apply_async.assert_not_called()

        handle_save_task.apply_async.assert_called_once_with(
            (source.app_name, source.model_name, source.id), queue='indexing', permanent=False)

    @patch('core.common.models.handle_save')
    def test_a_failed_index_enqueue_doesn_t_stop_the_transaction_s_other_callbacks(self, handle_save_task):
        """Such as the seed task of a version created in the same transaction."""
        handle_save_task.apply_async.side_effect = ConnectionError('broker down')
        source = OrganizationSourceFactory()
        after = Mock()
        with override_settings(TEST_MODE=False, ES_SYNC=True), self.assertLogs('django.test', 'ERROR'):
            with self.captureOnCommitCallbacks(execute=True):
                CelerySignalProcessor.handle_save(None, Source, source)
                transaction.on_commit(after)

        handle_save_task.apply_async.assert_called_once()
        after.assert_called_once_with()


@override_settings(CAPABILITY_POLICY=PRIVATE_GATE_POLICY)
class ImportPrivateRepositoryTest(MakePrivateTestMixin, OCLAPITestCase):
    def setUp(self):
        super().setUp()
        for task in ('index_concepts_mapped_codes', 'batch_index_resources'):
            patcher = patch(f'core.importers.models.{task}', Mock(__name__=task))
            patcher.start()
            self.addCleanup(patcher.stop)

    def line(self, resource_type, mnemonic, **kwargs):
        return {
            'type': resource_type, 'id': mnemonic, 'name': mnemonic, 'owner': self.org.mnemonic,
            'owner_type': 'Organization', **kwargs
        }

    def test_a_private_repository_line_is_denied_with_the_body_and_the_rest_imports(self):
        importer = BulkImportInline(
            '\n'.join(json.dumps(line) for line in [
                self.line('Source', 'private-source', public_access='None'),
                self.line('Collection', 'private-collection', public_access='None'),
                self.line('Source', 'public-source'),
            ]),
            self.user.username, True)
        importer.run()

        self.assertEqual([item['id'] for item in importer.created], ['public-source'])
        self.assertEqual([item['errors'] for item in importer.permission_denied], [PrivateGatePolicy.body] * 2)
        self.assertEqual(importer.failed, [])
        self.assertFalse(Source.objects.filter(mnemonic='private-source').exists())
        self.assertFalse(Collection.objects.filter(mnemonic='private-collection').exists())
        self.assertEqual(
            [(call[1].mnemonic, call[2]) for call in self.take_calls()],
            [('private-source', WRITE_ACTION_MAKE_PRIVATE), ('private-collection', WRITE_ACTION_MAKE_PRIVATE)])

    def test_a_private_repository_in_a_package_is_refused_with_the_body(self):
        result = ResourceImporter().import_resource(
            self.line('Source', 'packaged', public_access='None'), self.user.username, 'Organization',
            self.org.mnemonic)

        self.assertIsInstance(result, WriteRefused)
        self.assertEqual(result.detail, PrivateGatePolicy.body)
        self.assertFalse(Source.objects.filter(mnemonic='packaged').exists())
