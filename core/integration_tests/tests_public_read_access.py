from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.feeds import FeedFilterMixin, MAX_LIMIT, DEFAULT_LIMIT
from core.common.tests import OCLAPITestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
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
