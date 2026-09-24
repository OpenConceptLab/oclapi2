from django.contrib.auth.models import Permission
from mock import patch

from core.common.constants import ACCESS_TYPE_NONE
from core.common.search import get_visible_repo_criteria
from core.common.tests import OCLAPITestCase
from core.concepts.search import ConceptFuzzySearch
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.url_registry.factories import GlobalURLRegistryFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class SearchResolutionAccessBaseTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.private_org = OrganizationFactory(mnemonic='ResolveOrg', public_access=ACCESS_TYPE_NONE)
        self.member = UserProfileFactory(username='resolvemember')
        self.private_org.members.add(self.member)
        self.outsider = UserProfileFactory(username='resolveoutsider')
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.private_source = OrganizationSourceFactory(
            organization=self.private_org, mnemonic='ResolveSource', public_access=ACCESS_TYPE_NONE,
            canonical_url='http://private.example.com/cs')


class VisibleRepoCriteriaTest(SearchResolutionAccessBaseTest):
    def test_criteria(self):
        self.assertIsNone(get_visible_repo_criteria(self.admin))

        anonymous = get_visible_repo_criteria(None).to_dict()
        self.assertEqual(anonymous['bool']['should'], [{'term': {'public_can_view': True}}])

        member = get_visible_repo_criteria(self.member).to_dict()
        self.assertEqual(member['bool']['minimum_should_match'], 1)
        self.assertIn({'bool': {'must': [
            {'term': {'owner_type': 'User'}}, {'term': {'owner': 'resolvemember'}}]}}, member['bool']['should'])
        self.assertIn({'bool': {'must': [
            {'term': {'owner_type': 'Organization'}}, {'terms': {'owner': ['resolveorg']}}]}}, member['bool']['should'])

        outsider = get_visible_repo_criteria(self.outsider).to_dict()
        self.assertEqual(len(outsider['bool']['should']), 2)


class StopSearch(Exception):
    pass


class MatchAccessTest(SearchResolutionAccessBaseTest):
    @patch('core.concepts.views.MetadataToConceptsListView.get_repo_params')
    def test_match_search_is_limited_to_visible_repos(self, get_repo_params_mock):
        get_repo_params_mock.return_value = {'owner': 'ResolveOrg', 'source': 'ResolveSource'}
        self.outsider.user_permissions.add(Permission.objects.get(codename='mapper_use'))

        with patch.object(ConceptFuzzySearch, 'search', side_effect=StopSearch) as search_mock:
            with self.assertRaises(StopSearch):
                self.client.post(
                    '/concepts/$match/',
                    {'rows': [{'name': 'foo'}], 'target_repo': {'owner': 'ResolveOrg', 'source': 'ResolveSource'}},
                    HTTP_AUTHORIZATION='Token ' + self.outsider.get_token(),
                    format='json'
                )

        additional_filter_criterion = search_mock.call_args[0][8]
        self.assertEqual(additional_filter_criterion, get_visible_repo_criteria(self.outsider))


class ResolveReferenceAccessTest(SearchResolutionAccessBaseTest):
    def resolve(self, user):
        response = self.client.post(
            '/$resolveReference/', [self.private_source.uri], HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json')
        self.assertEqual(response.status_code, 200)
        return response.data[0]

    def test_private_repo_is_unresolved_for_outsider(self):
        resolution = self.resolve(self.outsider)

        self.assertFalse(resolution['resolved'])
        self.assertNotIn('result', resolution)

    def test_private_repo_is_resolved_for_member_and_staff(self):
        for user in [self.member, self.admin]:
            resolution = self.resolve(user)

            self.assertTrue(resolution['resolved'])
            self.assertEqual(resolution['result']['url'], self.private_source.uri)


class URLRegistryAccessTest(SearchResolutionAccessBaseTest):
    def setUp(self):
        super().setUp()
        self.entry = GlobalURLRegistryFactory(url='http://private.example.com/cs', namespace=self.private_org.uri)
        self.entry.lookup_entry()

    def test_lookup_hides_private_repo_from_outsider(self):
        response = self.client.post(
            '/url-registry/$lookup/', {'url': 'http://private.example.com/cs'},
            HTTP_AUTHORIZATION='Token ' + self.outsider.get_token(), format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'url_registry_entry': self.entry.relative_uri})

    def test_lookup_returns_private_repo_to_member(self):
        response = self.client.post(
            '/url-registry/$lookup/', {'url': 'http://private.example.com/cs'},
            HTTP_AUTHORIZATION='Token ' + self.member.get_token(), format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], self.private_source.uri)

    def test_entry_hides_private_repo_from_outsider(self):
        response = self.client.get(f'/url-registry/{self.entry.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['repo'])

        response = self.client.get(
            f'/url-registry/{self.entry.id}/', HTTP_AUTHORIZATION='Token ' + self.member.get_token())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['repo']['url'], self.private_source.uri)
