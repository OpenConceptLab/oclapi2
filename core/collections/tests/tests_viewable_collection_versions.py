from core.collections.models import Collection, CollectionReference
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class ViewableCollectionVersionsTest(OCLAPITestCase):
    """A public release under a private HEAD is private: the lists naming the collection versions that hold a
    concept or mapping leave it out for anyone who can't view the HEAD"""

    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory()
        self.concept = ConceptFactory(parent=self.source)
        self.mapping = MappingFactory(parent=self.source, from_concept=self.concept)
        self.collection = OrganizationCollectionFactory(organization=self.source.organization)
        self.release = OrganizationCollectionFactory(
            mnemonic=self.collection.mnemonic, organization=self.collection.organization, version='v1',
            released=True)
        expansion = ExpansionFactory(collection_version=self.release)
        self.release.expansion_uri = expansion.uri
        self.release.save()
        expansion.concepts.add(self.concept, self.concept.get_latest_version())
        expansion.mappings.add(self.mapping, self.mapping.get_latest_version())
        CollectionReference.objects.create(
            expression=self.concept.uri, collection=self.release, system=self.source.uri, code=self.concept.mnemonic)
        self.member = UserProfileFactory()
        self.source.organization.members.add(self.member)
        self.outsider = UserProfileFactory()
        self.admin = UserProfile.objects.get(username='ocladmin')

    def make_head_private(self):
        Collection.objects.filter(id=self.collection.id).update(public_access=ACCESS_TYPE_NONE)

    def get(self, url, user=None):
        kwargs = {'HTTP_AUTHORIZATION': f'Token {user.get_token()}'} if user else {}
        response = self.client.get(url, **kwargs)
        self.assertEqual(response.status_code, 200, url)
        return response.data

    def detail_versions(self, resource, user=None):
        return self.get(resource.get_latest_version().uri, user)['collection_versions']

    def test_detail_lists_the_release_while_its_head_is_public(self):
        self.assertEqual(self.detail_versions(self.concept), [self.release.uri])
        self.assertEqual(self.detail_versions(self.mapping), [self.release.uri])

    def test_detail_hides_a_release_under_a_private_head_from_outsiders_only(self):
        self.make_head_private()

        for resource in (self.concept, self.mapping):
            self.assertEqual(self.detail_versions(resource), [])
            self.assertEqual(self.detail_versions(resource, self.outsider), [])
            self.assertEqual(self.detail_versions(resource, self.member), [self.release.uri])
            self.assertEqual(self.detail_versions(resource, self.admin), [self.release.uri])

    def test_versions_lists_hide_a_release_under_a_private_head(self):
        self.make_head_private()

        for resource in (self.concept, self.mapping):
            url = f'{resource.uri}versions/?includeCollectionVersions=true'
            self.assertEqual([item['collection_versions'] for item in self.get(url)], [[]])
            self.assertEqual(
                [item['collection_versions'] for item in self.get(url, self.member)], [[self.release.uri]])

    def test_collection_versions_route_hides_a_release_under_a_private_head(self):
        self.make_head_private()

        for resource in (self.concept, self.mapping):
            url = f'{resource.uri}collection-versions/'
            self.assertEqual(self.get(url), [])
            self.assertEqual([item['version_url'] for item in self.get(url, self.member)], [self.release.uri])

    def test_contains_leaves_out_a_release_under_a_private_head(self):
        def holders():
            return list(Collection.get_base_queryset({'contains': self.concept.uri}).values_list('uri', flat=True))

        self.assertEqual(holders(), [self.release.uri])
        self.make_head_private()
        self.assertEqual(holders(), [])
