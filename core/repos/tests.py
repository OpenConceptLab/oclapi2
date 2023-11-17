from core.collections.documents import CollectionDocument
from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory
from core.common.tests import OCLAPITestCase
from core.orgs.tests.factories import OrganizationFactory
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
