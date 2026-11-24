from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class MappedSourcesAccessTest(OCLAPITestCase):
    """A public source's mapped-sources list names a private target source only to someone who can view it"""

    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory(mnemonic='subject')
        self.public_target = OrganizationSourceFactory(mnemonic='public-target')
        self.private_target = OrganizationSourceFactory(mnemonic='private-target', public_access=ACCESS_TYPE_NONE)
        concept = ConceptFactory(parent=self.source)
        mappings = [
            MappingFactory(
                parent=self.source, from_concept=concept, to_concept=to_concept,
                from_source=self.source, to_source=to_concept.parent)
            for to_concept in (
                concept, ConceptFactory(parent=self.public_target), ConceptFactory(parent=self.private_target))
        ]
        self.version = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.source.organization, version='v1')
        self.version.mappings.add(*[mapping.get_latest_version() for mapping in mappings])
        self.member = UserProfileFactory()
        self.private_target.organization.members.add(self.member)
        self.outsider = UserProfileFactory()
        self.admin = UserProfile.objects.get(username='ocladmin')

    def get(self, url, user=None):
        kwargs = {'HTTP_AUTHORIZATION': f'Token {user.get_token()}'} if user else {}
        response = self.client.get(url, **kwargs)
        self.assertEqual(response.status_code, 200, url)
        return response

    def urls(self, query=''):
        return [f'{self.source.uri}mapped-sources/{query}', f'{self.version.uri}mapped-sources/{query}']

    def assert_lists(self, user, expected, query=''):
        for url in self.urls(query):
            response = self.get(url, user)
            self.assertEqual(sorted(item['url'] for item in response.data), sorted(expected), url)
            self.assertEqual(response.headers['num_found'], str(len(expected)), url)

    def test_hides_a_private_target_from_anonymous_and_outsiders(self):
        self.assert_lists(None, [self.public_target.uri])
        self.assert_lists(self.outsider, [self.public_target.uri])
        self.assert_lists(self.outsider, [self.public_target.uri], '?verbose=true')
        self.assert_lists(self.outsider, [self.public_target.uri], '?brief=true')

    def test_lists_a_private_target_to_its_org_member_and_staff(self):
        expected = [self.public_target.uri, self.private_target.uri]
        self.assert_lists(self.member, expected)
        self.assert_lists(self.admin, expected)

    def test_paging_counts_only_viewable_targets(self):
        for url in self.urls('?limit=1'):
            response = self.get(url, self.outsider)
            self.assertEqual([item['url'] for item in response.data], [self.public_target.uri], url)
            self.assertEqual(response.headers['num_found'], '1', url)
            self.assertEqual(response.headers['pages'], '1', url)

    def test_exclude_self_leaves_out_the_source_on_head_and_version_routes(self):
        self.assert_lists(self.admin, [self.public_target.uri, self.private_target.uri])
        self.assert_lists(
            self.admin, [self.source.uri, self.public_target.uri, self.private_target.uri], '?excludeSelf=false')
