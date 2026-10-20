from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW
from core.map_projects.tests.factories import MapProjectFactory
from core.map_projects.tests.tests import MapProjectAbstractViewTest
from core.orgs.tests.factories import OrganizationFactory
from core.users.tests.factories import UserProfileFactory


class OrganizationMapProjectListTest(MapProjectAbstractViewTest):
    def setUp(self):
        super().setUp()
        owner = UserProfileFactory(username='mp-owner')
        shared = OrganizationFactory(mnemonic='mp-shared')
        owner_only = OrganizationFactory(mnemonic='mp-owner-org')
        viewer_only = OrganizationFactory(mnemonic='mp-viewer-org')
        shared.members.add(self.user, owner)
        owner_only.members.add(owner)
        viewer_only.members.add(self.user)
        for org, name in [(shared, 'shared'), (owner_only, 'owneronly'), (viewer_only, 'vieweronly')]:
            MapProjectFactory(organization=org, name=f'{name}-pub', public_access=ACCESS_TYPE_VIEW)
            MapProjectFactory(organization=org, name=f'{name}-priv', public_access=ACCESS_TYPE_NONE)

    def get_names(self, url, user):
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Token {user.get_token()}')
        self.assertEqual(response.status_code, 200)
        return sorted(item['name'] for item in response.data)

    def test_other_user_orgs_visible_to_requester(self):
        self.assertEqual(
            self.get_names('/users/mp-owner/orgs/map-projects/', self.user),
            ['owneronly-pub', 'shared-priv', 'shared-pub'])

    def test_own_orgs(self):
        expected = ['shared-priv', 'shared-pub', 'vieweronly-priv', 'vieweronly-pub']
        self.assertEqual(self.get_names(f'/users/{self.user.username}/orgs/map-projects/', self.user), expected)

    def test_staff_requester_sees_all_of_other_user_orgs(self):
        staff = UserProfileFactory(is_staff=True)
        self.assertEqual(
            self.get_names('/users/mp-owner/orgs/map-projects/', staff),
            ['owneronly-priv', 'owneronly-pub', 'shared-priv', 'shared-pub'])

    def test_unknown_user_404(self):
        response = self.client.get(
            '/users/no-such-user/orgs/map-projects/', HTTP_AUTHORIZATION=f'Token {self.user.get_token()}')
        self.assertEqual(response.status_code, 404)
