import datetime

from django.test import override_settings
from django.utils import timezone

from core.collections.documents import CollectionDocument
from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory
from core.common.constants import HEAD
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.orgs.tests.factories import OrganizationFactory
from core.repos.models import RepoExternalExport, Repository
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.tests.factories import UserProfileFactory


class ReposListViewTest(OCLAPITestCase):
    def test_get_200(self):
        CollectionDocument._index.delete()  # pylint: disable=protected-access
        SourceDocument._index.delete()  # pylint: disable=protected-access
        CollectionDocument.init()
        SourceDocument.init()

        org1 = OrganizationFactory(mnemonic='org1')
        OrganizationSourceFactory(organization=org1, mnemonic='repo-source1', source_type='Dictionary')
        OrganizationCollectionFactory(organization=org1, mnemonic='repo-coll1', collection_type='Dictionary')

        org2 = OrganizationFactory(mnemonic='org2')
        OrganizationSourceFactory(organization=org2, mnemonic='repo-source2', source_type='Dictionary')
        OrganizationCollectionFactory(organization=org2, mnemonic='repo-coll2', collection_type='Dictionary')

        SourceDocument().update(Source.objects.all())
        CollectionDocument().update(Collection.objects.all())

        response = self.client.get('/repos/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)

        response = self.client.get('/repos/?q=repo')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)

        response = self.client.get('/repos/?q=coll')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

        response = self.client.get('/repos/?source_type=Dictionary')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

        response = self.client.get(org1.uri + 'repos/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

        response = self.client.get(org1.uri + 'repos/?q=repo')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

        response = self.client.get(org1.uri + 'repos/?q=coll')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        response = self.client.get(org1.uri + 'repos/?collection_type=Dictionary')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)


class RepoExternalExportTest(OCLTestCase):
    def test_uri(self):
        source_v1 = UserSourceFactory(version='v1', mnemonic='source1')
        instance = RepoExternalExport(resource=source_v1, key='openmrs23-sql', file_path='foo/bar.zip',)

        self.assertEqual(instance.uri, source_v1.uri + 'export/openmrs23-sql/')

    def test_get_external_export_path(self):
        source_v1 = UserSourceFactory(version='v1', mnemonic='source1')
        owner_mnemonic = source_v1.parent.mnemonic

        path = source_v1.get_external_export_path('openmrs23-sql', 'openmrs23.sql.zip')

        self.assertEqual(
            path, f"users/{owner_mnemonic}/{owner_mnemonic}_source1_v1/external/openmrs23-sql_openmrs23.sql.zip"
        )

    def test_get_external_export_path_sanitizes_filename(self):
        source_v1 = UserSourceFactory(version='v1', mnemonic='source1')
        owner_mnemonic = source_v1.parent.mnemonic

        path = source_v1.get_external_export_path('openmrs23-sql', '../etc/passwd')

        self.assertEqual(
            path, f"users/{owner_mnemonic}/{owner_mnemonic}_source1_v1/external/openmrs23-sql_..etcpasswd"
        )

    def test_get_external_export_path_normalizes_invalid_filename_characters(self):
        source_v1 = UserSourceFactory(version='v1', mnemonic='source1')
        owner_mnemonic = source_v1.parent.mnemonic

        path = source_v1.get_external_export_path('openmrs23-sql', 'foo bar:baz*.zip')

        self.assertEqual(
            path, f"users/{owner_mnemonic}/{owner_mnemonic}_source1_v1/external/openmrs23-sql_foo_barbaz.zip"
        )


class UserOrganizationRepoListViewTest(OCLAPITestCase):
    def test_get(self):
        CollectionDocument._index.delete()  # pylint: disable=protected-access
        SourceDocument._index.delete()  # pylint: disable=protected-access
        CollectionDocument.init()
        SourceDocument.init()

        user = UserProfileFactory(username='batman')
        token = user.get_token()
        org1 = OrganizationFactory(mnemonic='gotham')
        org2 = OrganizationFactory(mnemonic='wayne-enterprise')
        org1.members.add(user)
        org2.members.add(user)
        coll1 = OrganizationCollectionFactory(mnemonic='city', organization=org1)
        coll2 = OrganizationCollectionFactory(mnemonic='corporate', organization=org2)
        coll3 = UserCollectionFactory(mnemonic='bat-cave', user=user)
        source1 = OrganizationSourceFactory(mnemonic='city', organization=org1)
        source2 = OrganizationSourceFactory(mnemonic='corporate', organization=org2)
        source3 = UserSourceFactory(mnemonic='bat-cave', user=user)

        CollectionDocument().update([coll1, coll2, coll3])
        SourceDocument().update([source1, source2, source3])

        response = self.client.get(
            '/users/batman/orgs/repos/',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)
        self.assertEqual(
            sorted([data['url'] for data in response.data]),
            sorted(['/orgs/wayne-enterprise/collections/corporate/', '/orgs/gotham/collections/city/',
                    '/orgs/wayne-enterprise/sources/corporate/', '/orgs/gotham/sources/city/'])
        )

        response = self.client.get(
            '/users/batman/orgs/repos/?q=city',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data]),
            sorted(['/orgs/gotham/collections/city/', '/orgs/gotham/sources/city/'])
        )

        response = self.client.get(
            '/user/orgs/repos/?q=city',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([data['url'] for data in response.data]),
            sorted(['/orgs/gotham/collections/city/', '/orgs/gotham/sources/city/'])
        )

        response = self.client.get(
            '/user/orgs/repos/?q=batman',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)


class RepositoryModelTest(OCLTestCase):
    def test_get_base_querysets_excludes_retired_by_default(self):
        org = OrganizationFactory(mnemonic='ret-org')
        active_source = OrganizationSourceFactory(organization=org, mnemonic='active', retired=False)
        OrganizationSourceFactory(organization=org, mnemonic='retired-src', retired=True)

        sources, collections = Repository.get_base_querysets({'org': 'ret-org', 'version': HEAD})

        self.assertEqual(list(sources), [active_source])
        self.assertEqual(list(collections), [])

    def test_get_base_querysets_includes_retired_when_requested(self):
        org = OrganizationFactory(mnemonic='ret-org2')
        active_source = OrganizationSourceFactory(organization=org, mnemonic='active2', retired=False)
        retired_source = OrganizationSourceFactory(organization=org, mnemonic='retired2', retired=True)

        sources, _collections = Repository.get_base_querysets(
            {'org': 'ret-org2', 'version': HEAD}, exclude_retired=False)

        self.assertEqual(set(sources), {active_source, retired_source})

    def test_merge_querysets_windows_each_side_by_page_and_limit(self):
        org = OrganizationFactory(mnemonic='window-org')
        old_source = OrganizationSourceFactory(organization=org, mnemonic='old-source')
        new_source = OrganizationSourceFactory(organization=org, mnemonic='new-source')
        only_collection = OrganizationCollectionFactory(organization=org, mnemonic='only-collection')

        now = timezone.now()
        Source.objects.filter(id=old_source.id).update(updated_at=now - datetime.timedelta(days=1))
        Source.objects.filter(id=new_source.id).update(updated_at=now)
        Collection.objects.filter(id=only_collection.id).update(updated_at=now - datetime.timedelta(hours=1))

        sources, collections = Repository.get_base_querysets({'org': 'window-org', 'version': HEAD})
        merged, total_count = Repository.merge_querysets(sources, collections, limit=1, page=1)

        self.assertEqual(total_count, 3)
        self.assertEqual([repo.mnemonic for repo in merged], ['new-source', 'only-collection'])

    def test_merge_querysets_returns_countable_list(self):
        sources, collections = Repository.get_base_querysets({'org': 'no-such-org', 'version': HEAD})

        merged, total_count = Repository.merge_querysets(sources, collections, limit=25, page=1)

        self.assertEqual(total_count, 0)
        self.assertEqual(merged.count(), 0)
        self.assertEqual(list(merged), [])


class ReposListViewDbFirstTest(OCLAPITestCase):
    def test_org_scoped_repo_appears_without_es_indexing(self):
        org = OrganizationFactory(mnemonic='db-first-org')
        with override_settings(ES_SYNC=False):
            OrganizationSourceFactory(organization=org, mnemonic='unindexed-source')
            OrganizationCollectionFactory(organization=org, mnemonic='unindexed-collection')

        response = self.client.get(org.uri + 'repos/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(item['id'] for item in response.data),
            sorted(['unindexed-source', 'unindexed-collection'])
        )

        q_response = self.client.get(org.uri + 'repos/?q=unindexed')
        self.assertEqual(q_response.status_code, 200)
        self.assertEqual(len(q_response.data), 0)

    def test_user_scoped_repo_appears_without_es_indexing(self):
        user = UserProfileFactory(username='db-first-user')
        with override_settings(ES_SYNC=False):
            UserSourceFactory(user=user, mnemonic='unindexed-user-source')

        response = self.client.get('/users/db-first-user/repos/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data], ['unindexed-user-source'])

    def test_self_scoped_repo_appears_without_es_indexing(self):
        user = UserProfileFactory(username='db-first-self-user')
        token = user.get_token()
        with override_settings(ES_SYNC=False):
            UserSourceFactory(user=user, mnemonic='unindexed-self-source')

        response = self.client.get('/user/repos/', HTTP_AUTHORIZATION=f'Token {token}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data], ['unindexed-self-source'])

    def test_global_repo_appears_without_es_indexing(self):
        org = OrganizationFactory(mnemonic='db-first-global-org')
        with override_settings(ES_SYNC=False):
            OrganizationSourceFactory(organization=org, mnemonic='unindexed-global-source')

        response = self.client.get('/repos/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('unindexed-global-source', [item['id'] for item in response.data])

    def test_head_request_returns_correct_num_found(self):
        org = OrganizationFactory(mnemonic='db-first-head-org')
        OrganizationSourceFactory(organization=org, mnemonic='head-source')
        OrganizationCollectionFactory(organization=org, mnemonic='head-collection')

        response = self.client.head(org.uri + 'repos/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['num_found'], '2')  # pylint: disable=unsubscriptable-object

    def test_head_request_on_empty_listing_returns_zero(self):
        org = OrganizationFactory(mnemonic='db-first-empty-head-org')

        response = self.client.head(org.uri + 'repos/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['num_found'], '0')  # pylint: disable=unsubscriptable-object


class OrganizationRepoListViewDbFirstTest(OCLAPITestCase):
    def test_member_org_repo_appears_without_es_indexing(self):
        user = UserProfileFactory(username='db-first-member')
        token = user.get_token()
        org = OrganizationFactory(mnemonic='db-first-member-org')
        org.members.add(user)

        with override_settings(ES_SYNC=False):
            OrganizationSourceFactory(organization=org, mnemonic='unindexed-member-source')

        response = self.client.get(
            '/users/db-first-member/orgs/repos/',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data], ['unindexed-member-source'])

    def test_non_member_org_repo_not_returned(self):
        user = UserProfileFactory(username='db-first-non-member')
        token = user.get_token()
        org = OrganizationFactory(mnemonic='db-first-other-org')

        OrganizationSourceFactory(organization=org, mnemonic='other-org-source')

        response = self.client.get(
            '/users/db-first-non-member/orgs/repos/',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_unauthenticated_request_returns_empty(self):
        response = self.client.get('/users/anyone/orgs/repos/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
