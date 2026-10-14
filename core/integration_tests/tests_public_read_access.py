from django.core.cache import cache
from django.test import override_settings

from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.feeds import FeedFilterMixin, MAX_LIMIT, DEFAULT_LIMIT
from core.common.tests import OCLAPITestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class PrivateRepoReadAccessBaseTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory(mnemonic='ReadOrg')
        self.member = UserProfileFactory(username='readmember')
        self.org.members.add(self.member)
        self.outsider = UserProfileFactory(username='readoutsider')
        self.private_source = OrganizationSourceFactory(
            organization=self.org, mnemonic='PrivateRead', public_access=ACCESS_TYPE_NONE)
        self.private_concept = ConceptFactory(
            parent=self.private_source, mnemonic='hidden', public_access=ACCESS_TYPE_NONE)
        self.public_source = OrganizationSourceFactory(organization=self.org, mnemonic='PublicRead')
        self.public_concept = ConceptFactory(parent=self.public_source, mnemonic='shown')


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ConceptLookupAccessTest(PrivateRepoReadAccessBaseTest):
    def setUp(self):
        super().setUp()
        cache.clear()

    def lookup(self, source, user=None):
        headers = {'HTTP_AUTHORIZATION': 'Token ' + user.get_token()} if user else {}
        return self.client.get(source.uri + 'concepts/lookup/', **headers)

    def test_private_source_lookup_only_for_members(self):
        response = self.lookup(self.private_source, self.member)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([concept['id'] for concept in response.data], ['hidden'])

        # a member's response must not be served to others from the cache
        for user in [None, self.outsider]:
            response = self.lookup(self.private_source, user)

            self.assertEqual(response.status_code, 404)

    def test_public_source_lookup_for_anyone(self):
        for user in [None, self.outsider, self.member]:
            response = self.lookup(self.public_source, user)

            self.assertEqual(response.status_code, 200)
            self.assertEqual([concept['id'] for concept in response.data], ['shown'])


class CollectionVersionsAccessTest(PrivateRepoReadAccessBaseTest):
    def setUp(self):
        super().setUp()
        self.mapping = MappingFactory(
            parent=self.public_source, from_concept=self.public_concept, to_concept=self.public_concept)
        self.collection_urls = {}
        for mnemonic, public_access in [('PublicColl', 'View'), ('PrivateColl', ACCESS_TYPE_NONE)]:
            collection = OrganizationCollectionFactory(
                organization=self.org, mnemonic=mnemonic, version='v1', public_access=public_access)
            expansion = ExpansionFactory(collection_version=collection)
            expansion.concepts.add(self.public_concept, self.public_concept.get_latest_version())
            expansion.mappings.add(self.mapping, self.mapping.get_latest_version())
            collection.refresh_from_db()
            self.collection_urls[mnemonic] = collection.uri

    def get(self, url, user):
        headers = {'HTTP_AUTHORIZATION': 'Token ' + user.get_token()} if user else {}
        response = self.client.get(url, **headers)
        self.assertEqual(response.status_code, 200, url)
        return response.data

    def test_private_collection_versions_only_for_members_and_staff(self):
        public_only = [self.collection_urls['PublicColl']]
        both = sorted(self.collection_urls.values())
        admin = UserProfile.objects.get(username='ocladmin')

        for user, expected in [(None, public_only), (self.outsider, public_only), (self.member, both), (admin, both)]:
            for resource in [self.public_concept, self.mapping]:
                version = resource.get_latest_version()

                self.assertEqual(self.get(version.uri, user)['collection_versions'], expected, (user, resource))
                versions = self.get(resource.uri + 'versions/?includeCollectionVersions=true', user)
                self.assertEqual(
                    [data['collection_versions'] for data in versions if data['uuid'] == str(version.id)],
                    [expected], (user, resource))
                self.assertEqual(
                    sorted(data['version_url'] for data in self.get(resource.uri + 'collection-versions/', user)),
                    expected, (user, resource))


class FeedAccessTest(PrivateRepoReadAccessBaseTest):
    def test_source_feed_only_for_public_sources(self):
        self.assertEqual(self.client.get(self.private_source.uri + 'atom/').status_code, 404)
        self.assertEqual(self.client.get(self.public_source.uri + 'atom/').status_code, 200)

    def test_concept_feed_only_for_public_sources(self):
        self.assertEqual(self.client.get(self.private_concept.uri + 'atom/').status_code, 404)
        self.assertEqual(self.client.get(self.public_concept.uri + 'atom/').status_code, 200)

    def test_collection_feed_only_for_public_collections(self):
        private_collection = OrganizationCollectionFactory(
            organization=self.org, mnemonic='PrivateColl', public_access=ACCESS_TYPE_NONE)
        public_collection = OrganizationCollectionFactory(organization=self.org, mnemonic='PublicColl')

        self.assertEqual(self.client.get(private_collection.uri + 'concepts/atom/').status_code, 404)
        self.assertEqual(self.client.get(public_collection.uri + 'concepts/atom/').status_code, 200)

    def test_feed_limit_is_capped(self):
        feed = FeedFilterMixin()
        feed.updated_since = None

        for limit, expected in [(None, DEFAULT_LIMIT), ('0', MAX_LIMIT), ('-1', MAX_LIMIT),
                                (str(MAX_LIMIT + 1), MAX_LIMIT), ('5', 5)]:
            feed.limit = limit

            self.assertEqual(feed.filter_queryset(Concept.objects.all()).query.high_mark, expected, limit)
