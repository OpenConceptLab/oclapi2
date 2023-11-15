from core.collections.documents import CollectionDocument
from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.tests import OCLAPITestCase
from core.orgs.tests.factories import OrganizationFactory
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory


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
