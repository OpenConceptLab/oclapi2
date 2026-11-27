from django.contrib.auth.models import Group
from django.test import tag
from mock import patch

from core.common.constants import ACCESS_TYPE_NONE
from core.common.search import get_visible_repo_criteria
from core.common.tests import OCLAPITestCase, PREVIEW_GROUP_NAME
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.search import ConceptFuzzySearch
from core.concepts.tests.factories import ConceptFactory
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
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
            {'term': {'owner_type': 'User'}}, {'term': {'owner_text.keyword': 'resolvemember'}}]}},
            member['bool']['should'])
        self.assertIn({'bool': {'must': [
            {'term': {'owner_type': 'Organization'}}, {'terms': {'owner_text.keyword': ['ResolveOrg']}}]}},
            member['bool']['should'])

        outsider = get_visible_repo_criteria(self.outsider).to_dict()
        self.assertEqual(len(outsider['bool']['should']), 2)


class StopSearch(Exception):
    pass


@tag('es')
class MatchAccessTest(SearchResolutionAccessBaseTest):
    @patch('core.concepts.views.MetadataToConceptsListView.get_repo_params')
    def test_match_search_is_limited_to_visible_repos(self, get_repo_params_mock):
        get_repo_params_mock.return_value = {'owner': 'ResolveOrg', 'source': 'ResolveSource'}
        self.outsider.groups.add(Group.objects.get(name=PREVIEW_GROUP_NAME))

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

    def match(self, user, target_repo_url, target_repo=None, row=None, url='/concepts/$match/'):  # pylint: disable=too-many-arguments
        data = {'rows': [row or {'name': 'foo'}]}
        if target_repo_url:
            data['target_repo_url'] = target_repo_url
        if target_repo:
            data['target_repo'] = target_repo
        return self.client.post(url, data, HTTP_AUTHORIZATION='Token ' + user.get_token(), format='json')

    def test_match_target_repo_needs_view_access(self):
        preview = Group.objects.get(name=PREVIEW_GROUP_NAME)
        public_source = OrganizationSourceFactory(mnemonic='PublicMatchSource')
        private_target_repo = {
            'owner': 'ResolveOrg', 'owner_type': 'Organization', 'source': 'ResolveSource', 'source_version': 'HEAD'}
        for user in [self.outsider, self.member]:
            user.groups.add(preview)

        for target_repo_url, target_repo in [
                (self.private_source.uri, None), (None, private_target_repo),
                ('/orgs/NoSuchOrg/sources/NoSuchSource/', private_target_repo)]:
            response = self.match(self.outsider, target_repo_url, target_repo)
            self.assertEqual(response.status_code, 400, (target_repo_url, target_repo))

        # a resolved target_repo_url is what gets searched; target_repo is then ignored
        with patch.object(ConceptFuzzySearch, 'search', side_effect=StopSearch) as search_mock:
            with self.assertRaises(StopSearch):
                self.match(self.outsider, public_source.uri, private_target_repo)
        self.assertEqual(search_mock.call_args[0][2]['source'], 'PublicMatchSource')

        for user in [self.member, self.admin]:
            for target_repo_url, target_repo in [
                    (self.private_source.uri, None), (None, private_target_repo)]:
                with patch.object(ConceptFuzzySearch, 'search', side_effect=StopSearch) as search_mock:
                    with self.assertRaises(StopSearch):
                        self.match(user, target_repo_url, target_repo)
                self.assertEqual(search_mock.call_args[0][2], private_target_repo)

    def test_match_target_repo_is_searched_by_its_stored_names(self):
        self.member.groups.add(Group.objects.get(name=PREVIEW_GROUP_NAME))

        with patch.object(ConceptFuzzySearch, 'search', side_effect=StopSearch) as search_mock:
            with self.assertRaises(StopSearch):
                self.match(
                    self.member, None,
                    {'owner': 'ResolveOrg', 'owner_type': 'org', 'source': 'ResolveSource', 'version': 'HEAD'})
        self.assertEqual(
            search_mock.call_args[0][2],
            {'owner': 'ResolveOrg', 'owner_type': 'Organization', 'source': 'ResolveSource', 'source_version': 'HEAD'})

    def test_match_tells_apart_owners_whose_names_differ_only_in_case(self):
        self.patch_concept_es_mapping_for_ci()
        self.outsider.groups.add(Group.objects.get(name=PREVIEW_GROUP_NAME))
        own_org = OrganizationFactory(mnemonic='resolveorg', public_access=ACCESS_TYPE_NONE)
        own_org.members.add(self.outsider)
        own_source = OrganizationSourceFactory(
            organization=own_org, mnemonic='ResolveSource', public_access=ACCESS_TYPE_NONE)
        for source in [own_source, self.private_source]:
            concept = ConceptFactory(parent=source, mnemonic='case-collision', public_access=ACCESS_TYPE_NONE)
            source.concepts.add(concept.get_latest_version())
        ConceptDocument().update(Concept.objects.filter(mnemonic='case-collision'), refresh=True)

        for target_repo_url, target_repo in [
                (own_source.uri, None),
                (None, {'owner': 'resolveorg', 'owner_type': 'Organization', 'source': 'ResolveSource'})]:
            response = self.match(
                self.outsider, target_repo_url, target_repo, row={'id': 'case-collision'},
                url='/concepts/$match/?limit=10')

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                {result['url'] for result in response.data[0]['results']},
                {'/orgs/resolveorg/sources/ResolveSource/concepts/case-collision/'},
                (target_repo_url, target_repo))

    def test_match_head_target_repo_with_bare_url_searches_head(self):
        # the HEAD version_url is the bare repo URL, which resolves to the latest released version
        OrganizationSourceFactory(
            organization=self.private_org, mnemonic='ResolveSource', version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        head_target_repo = {
            'owner': 'ResolveOrg', 'owner_type': 'Organization', 'source': 'ResolveSource', 'source_version': 'HEAD'}

        with patch.object(ConceptFuzzySearch, 'search', side_effect=StopSearch) as search_mock:
            with self.assertRaises(StopSearch):
                self.match(self.admin, self.private_source.uri, head_target_repo)
        self.assertEqual(search_mock.call_args[0][2]['source_version'], 'HEAD')

    def searched_version(self, user, target_repo_url, target_repo=None):
        """The source version a $match searches."""
        with patch.object(ConceptFuzzySearch, 'search', side_effect=StopSearch) as search_mock:
            with self.assertRaises(StopSearch):
                self.match(user, target_repo_url, target_repo)
        return search_mock.call_args[0][2]['source_version']

    def test_match_resolves_latest_head_and_no_version(self):
        """
        latest, and a target with no version, search the newest release the requester can view, else HEAD; HEAD and
        a named version search themselves (OpenConceptLab/ocl_online#779).
        """
        preview = Group.objects.get(name=PREVIEW_GROUP_NAME)
        for user in [self.outsider, self.member]:
            user.groups.add(preview)
        org = OrganizationFactory(mnemonic='LatestOrg')
        org.members.add(self.member)
        head = OrganizationSourceFactory(organization=org, mnemonic='LatestSource')
        OrganizationSourceFactory(organization=org, mnemonic='LatestSource', version='v1', released=True)
        OrganizationSourceFactory(
            organization=org, mnemonic='LatestSource', version='v2', released=True, public_access=ACCESS_TYPE_NONE)
        OrganizationSourceFactory(organization=org, mnemonic='LatestSource', version='v3')  # not released
        target_repo = {'owner': 'LatestOrg', 'owner_type': 'Organization', 'source': 'LatestSource'}
        latest_requests = [
            (head.uri, None), (head.uri + 'latest/', None), (None, target_repo),
            (None, {**target_repo, 'source_version': 'latest'}), (head.uri, {**target_repo, 'version': 'latest'})]
        head_requests = [
            (head.uri + 'HEAD/', None), (head.uri, {**target_repo, 'source_version': 'HEAD'}),
            (None, {**target_repo, 'source_version': 'HEAD'})]

        # v2, the newest release, is private: the outsider gets v1
        for user, newest_viewable in [(self.outsider, 'v1'), (self.member, 'v2'), (self.admin, 'v2')]:
            for target_repo_url, target in latest_requests:
                self.assertEqual(
                    self.searched_version(user, target_repo_url, target), newest_viewable,
                    (user.username, target_repo_url, target))
            for target_repo_url, target in head_requests:
                self.assertEqual(
                    self.searched_version(user, target_repo_url, target), 'HEAD', (target_repo_url, target))
            for target_repo_url, target in [(head.uri + 'v3/', None), (None, {**target_repo, 'source_version': 'v3'})]:
                self.assertEqual(self.searched_version(user, target_repo_url, target), 'v3')

        # a named version the requester can't view stays unresolvable
        for target_repo_url, target in [(head.uri + 'v2/', None), (None, {**target_repo, 'source_version': 'v2'})]:
            self.assertEqual(self.match(self.outsider, target_repo_url, target).status_code, 400)

        # with no release, latest is HEAD
        self.member.groups.add(preview)
        for target_repo_url, target in [
                (self.private_source.uri + 'latest/', None),
                (None, {'owner': 'ResolveOrg', 'owner_type': 'Organization', 'source': 'ResolveSource'})]:
            self.assertEqual(self.searched_version(self.member, target_repo_url, target), 'HEAD')

    def test_match_owner_shared_by_an_org_and_a_user_asks_for_owner_type(self):
        """With no owner_type, an owner name both an org and a user have, at different newest releases, is ambiguous."""
        self.member.groups.add(Group.objects.get(name=PREVIEW_GROUP_NAME))
        org = OrganizationFactory(mnemonic='SharedName')
        user = UserProfileFactory(username='SharedName')
        OrganizationSourceFactory(organization=org, mnemonic='SharedSource')
        OrganizationSourceFactory(organization=org, mnemonic='SharedSource', version='v1', released=True)
        UserSourceFactory(user=user, mnemonic='SharedSource')
        target_repo = {'owner': 'SharedName', 'source': 'SharedSource'}

        response = self.match(self.member, None, target_repo)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['detail'],
            '"target_repo" owner "SharedName" names both an organization and a user with this source: set '
            '"owner_type" to "Organization" or "User"')
        self.assertEqual(self.searched_version(self.member, None, {**target_repo, 'owner_type': 'Organization'}), 'v1')
        self.assertEqual(self.searched_version(self.member, None, {**target_repo, 'owner_type': 'User'}), 'HEAD')

    def test_semantic_match_checks_the_version_searched(self):
        """
        Each repo version keeps its own match algorithms: a semantic $match that resolves to a release without the
        semantic algorithm says so and points to HEAD.
        """
        self.member.groups.add(Group.objects.get(name=PREVIEW_GROUP_NAME))
        semantic = [Source.TOKEN_MATCH_ALGORITHM, Source.SEMANTIC_MATCH_ALGORITHM]
        head = OrganizationSourceFactory(organization=self.private_org, mnemonic='SemanticSource')
        Source.objects.filter(id=head.id).update(match_algorithms=semantic)
        release = OrganizationSourceFactory(
            organization=self.private_org, mnemonic='SemanticSource', version='v1', released=True)
        Source.objects.filter(id=release.id).update(match_algorithms=[Source.TOKEN_MATCH_ALGORITHM])
        target_repo = {'owner': 'ResolveOrg', 'owner_type': 'Organization', 'source': 'SemanticSource'}
        url = '/concepts/$match/?semantic=true'

        for target_repo_url, target in [(head.uri, None), (None, target_repo)]:
            response = self.match(self.member, target_repo_url, target, url=url)
            self.assertEqual(response.status_code, 400, (target_repo_url, target))
            self.assertEqual(
                response.data['detail'],
                'This repo version does not support semantic search: no version, or "latest", resolved to the '
                'release "v1"; name "HEAD" to search HEAD')

        response = self.match(self.member, head.uri + 'v1/', None, url=url)  # named, so the plain message
        self.assertEqual(response.data['detail'], 'This repo version does not support semantic search')

        for target_repo_url, target in [(head.uri + 'HEAD/', None), (None, {**target_repo, 'source_version': 'HEAD'})]:
            with patch.object(ConceptFuzzySearch, 'search', side_effect=StopSearch):
                with self.assertRaises(StopSearch):
                    self.match(self.member, target_repo_url, target, url=url)


@tag('es')
class RepoSearchOwnerCaseTest(SearchResolutionAccessBaseTest):
    def test_repo_search_excludes_private_content_of_owner_whose_name_differs_only_in_case(self):
        self.patch_concept_es_mapping_for_ci()
        own_org = OrganizationFactory(mnemonic='resolveorg', public_access=ACCESS_TYPE_NONE)
        own_org.members.add(self.outsider)
        own_source = OrganizationSourceFactory(
            organization=own_org, mnemonic='ResolveSource', public_access=ACCESS_TYPE_NONE)
        for source in [own_source, self.private_source]:
            concept = ConceptFactory(parent=source, mnemonic='case-twin', public_access=ACCESS_TYPE_NONE)
            source.concepts.add(concept.get_latest_version())
            mapping = MappingFactory(
                parent=source, from_concept=concept, to_concept=concept, public_access=ACCESS_TYPE_NONE)
            source.mappings.add(mapping.get_latest_version())
        ConceptDocument().update(Concept.objects.filter(mnemonic='case-twin'), refresh=True)
        MappingDocument().update(Mapping.objects.filter(parent__mnemonic='ResolveSource'), refresh=True)

        def search(resource):
            response = self.client.get(
                f'{own_source.uri}{resource}/?sortAsc=last_update&limit=100',
                HTTP_AUTHORIZATION='Token ' + self.outsider.get_token())
            self.assertEqual(response.status_code, 200)
            return {result['url'].split('/sources/')[0] for result in response.data}

        self.assertEqual(search('concepts'), {'/orgs/resolveorg'})
        # mappings have no exact-case owner field: with such an owner, only public mappings are searched
        self.assertEqual(search('mappings'), set())


@tag('es')
class RepoListSearchOwnerCaseTest(SearchResolutionAccessBaseTest):
    def test_repo_list_search_excludes_private_repos_of_owner_whose_name_differs_only_in_case(self):
        own_org = OrganizationFactory(mnemonic='resolveorg', public_access=ACCESS_TYPE_NONE)
        own_org.members.add(self.outsider)
        own_source = OrganizationSourceFactory(organization=own_org, mnemonic='ResolveSource')
        SourceDocument().update(Source.objects.filter(mnemonic='ResolveSource'), refresh=True)

        for url in ['/orgs/resolveorg/sources/?q=ResolveSource', '/user/orgs/sources/?q=ResolveSource']:
            response = self.client.get(url, HTTP_AUTHORIZATION='Token ' + self.outsider.get_token())

            self.assertEqual(response.status_code, 200)
            self.assertEqual([source['url'] for source in response.data], [own_source.uri], url)

    def test_org_names_are_unique_regardless_of_case(self):
        response = self.client.post(
            '/orgs/', {'id': 'RESOLVEORG', 'name': 'Twin'}, HTTP_AUTHORIZATION='Token ' + self.outsider.get_token(),
            format='json')

        self.assertEqual(response.status_code, 400)


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
