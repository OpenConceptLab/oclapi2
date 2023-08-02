from mock import patch
from mock.mock import Mock, ANY
from rest_framework.exceptions import ErrorDetail

from core.collections.documents import CollectionDocument
from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory
from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT
from core.common.tests import OCLAPITestCase
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.sources.documents import SourceDocument
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class OrganizationListViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.superuser = UserProfile.objects.get(is_superuser=True)
        self.user = UserProfileFactory(username='user')
        self.org_private = OrganizationFactory(mnemonic='private-org', public_access=ACCESS_TYPE_NONE)
        self.org_public_view = OrganizationFactory(mnemonic='public-view-org', public_access=ACCESS_TYPE_VIEW)
        self.org_public_edit = OrganizationFactory(mnemonic='public-edit-org', public_access=ACCESS_TYPE_EDIT)
        self.user_org_public = OrganizationFactory(mnemonic='user-public-view-org', public_access=ACCESS_TYPE_VIEW)
        self.user_org_private = OrganizationFactory(mnemonic='user-private-org', public_access=ACCESS_TYPE_NONE)
        self.user.organizations.set([self.user_org_private, self.user_org_public])
        self.token = self.user.get_token()

    def test_get_200_anonymous_user(self):
        response = self.client.get('/orgs/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-public-view-org', 'public-edit-org', 'public-view-org', 'OCL']
        )

    def test_get_200_auth_user(self):
        response = self.client.get(
            '/orgs/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 5)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-private-org', 'user-public-view-org', 'public-edit-org', 'public-view-org', 'OCL']
        )

        random_user = UserProfileFactory()
        response = self.client.get(
            '/orgs/?verbose=true',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-public-view-org', 'public-edit-org', 'public-view-org', 'OCL']
        )

        response = self.client.get(
            '/user/orgs/?verbose=true',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_get_200_superuser(self):
        response = self.client.get(
            '/orgs/',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 6)
        self.assertEqual(
            sorted([org['id'] for org in response.data]),
            sorted([
                'user-private-org', 'user-public-view-org', 'public-edit-org', 'public-view-org', 'private-org', 'OCL'
            ])
        )

    def test_get_200_staff_user(self):
        staff_user = UserProfileFactory(is_staff=True)

        response = self.client.get(
            '/orgs/',
            HTTP_AUTHORIZATION='Token ' + staff_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 6)
        self.assertEqual(
            [org['id'] for org in response.data],
            ['user-private-org', 'user-public-view-org', 'public-edit-org', 'public-view-org', 'private-org', 'OCL']
        )

    def test_get_200_with_no_members(self):
        org1 = OrganizationFactory(mnemonic='org-1')
        org2 = OrganizationFactory(mnemonic='org-2')
        org1.members.set([])
        org2.members.set([])
        org3 = OrganizationFactory(mnemonic='org-3')
        self.user.organizations.add(org3)

        response = self.client.get(
            '/orgs/?noMembers=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        found_mnemonics = [org['id'] for org in response.data]
        for mnemonic in ['org-1', 'org-2']:
            self.assertTrue(mnemonic in found_mnemonics)
        for mnemonic in ['org-3']:
            self.assertFalse(mnemonic in found_mnemonics)

    def test_post_201(self):
        response = self.client.post(
            '/orgs/',
            {'id': 'test-org-1', 'name': 'Test Org 1'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['id'], 'test-org-1')
        self.assertEqual(response.data['name'], 'Test Org 1')
        self.assertIsNotNone(response.data['uuid'])
        self.assertTrue(self.user.organizations.filter(mnemonic='test-org-1').exists())

    def test_post_400(self):
        response = self.client.post(
            '/orgs/',
            {'id': 'test-org-1'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'name': [ErrorDetail(string='This field is required.', code='required')]})
        self.assertFalse(self.user.organizations.filter(mnemonic='test-org-1').exists())

        response = self.client.post(
            '/orgs/',
            {
                'id': 'OCL',
                'name': 'another ocl'
            },
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'mnemonic': 'Organization with mnemonic OCL already exists.'})


class OrganizationDetailViewTest(OCLAPITestCase):
    def setUp(self):
        self.org = OrganizationFactory(name='Stark Enterprises')
        self.user = UserProfileFactory(organizations=[self.org])
        self.superuser = UserProfile.objects.get(is_superuser=True)
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(
            f'/orgs/{self.org.mnemonic}/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.org.id))
        self.assertEqual(response.data['id'], self.org.mnemonic)
        self.assertEqual(response.data['name'], 'Stark Enterprises')
        self.assertFalse('overview' in response.data)
        self.assertFalse('client_configs' in response.data)

    def test_get_200_with_overview(self):
        response = self.client.get(
            f'/orgs/{self.org.mnemonic}/?includeOverview=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.org.id))
        self.assertEqual(response.data['id'], self.org.mnemonic)
        self.assertTrue('overview' in response.data)

    def test_get_200_with_configs(self):
        response = self.client.get(
            f'/orgs/{self.org.mnemonic}/?includeClientConfigs=true',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.org.id))
        self.assertEqual(response.data['id'], self.org.mnemonic)
        self.assertTrue('client_configs' in response.data)

    def test_get_404(self):
        response = self.client.get(
            '/orgs/foobar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        response = self.client.put(
            f'/orgs/{self.org.mnemonic}/',
            {'name': 'Wayne Corporation'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.org.id))
        self.assertEqual(response.data['id'], self.org.mnemonic)
        self.assertEqual(response.data['name'], 'Wayne Corporation')

    def test_delete_403(self):
        stranger = UserProfileFactory()
        response = self.client.delete(
            f'/orgs/{self.org.mnemonic}/',
            HTTP_AUTHORIZATION='Token ' + stranger.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)

    @patch('core.orgs.views.delete_organization')
    def test_delete_202_by_superuser(self, delete_organization_mock):
        delete_organization_mock.apply_async = Mock(return_value=Mock(task_id='task-id', state='PENDING'))

        response = self.client.delete(
            f'/orgs/{self.org.mnemonic}/?async=true',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        delete_organization_mock.apply_async.assert_called_once_with((self.org.id, ), task_id=ANY)

    @patch('core.orgs.views.delete_organization')
    def test_delete_202_by_owner(self, delete_organization_mock):
        delete_organization_mock.apply_async = Mock(return_value=Mock(task_id='task-id', state='PENDING'))

        response = self.client.delete(
            f'/orgs/{self.org.mnemonic}/?async=true',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        delete_organization_mock.apply_async.assert_called_once_with((self.org.id, ), task_id=ANY)

    def test_delete_204_inline(self):
        response = self.client.delete(
            f'/orgs/{self.org.mnemonic}/?inline=true',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Organization.objects.filter(mnemonic=self.org.mnemonic).exists())


class OrganizationUserListViewTest(OCLAPITestCase):
    def test_get_200(self):
        response = self.client.get(
            '/orgs/OCL/members/',
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['username'], 'ocladmin')

    def test_get_404(self):
        response = self.client.get(
            '/orgs/OCL1/members/',
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_get_403(self):
        private_org = OrganizationFactory(public_access=ACCESS_TYPE_NONE)

        response = self.client.get(
            f'/orgs/{private_org.mnemonic}/members/',
            format='json'
        )

        self.assertEqual(response.status_code, 403)


class OrganizationMemberViewTest(OCLAPITestCase):
    def setUp(self):
        self.org = OrganizationFactory(name='Stark Enterprises')
        self.user = UserProfileFactory(organizations=[self.org])
        self.superuser = UserProfile.objects.get(is_superuser=True)
        self.token = self.user.get_token()

    def test_get_204(self):
        response = self.client.get(
            f'/orgs/{self.org.mnemonic}/members/{self.user.username}/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)

    def test_get_403(self):
        random_user = UserProfileFactory()
        response = self.client.get(
            f'/orgs/{self.org.mnemonic}/members/{random_user.username}/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)

    def test_get_404(self):
        response = self.client.get(
            f'/orgs/foobar/members/{self.user.username}/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        self.assertEqual(self.user.organizations.count(), 1)

        new_org = OrganizationFactory()
        response = self.client.put(
            f'/orgs/{new_org.mnemonic}/members/{self.user.username}/',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.user.organizations.count(), 2)

    def test_put_403(self):
        self.assertEqual(self.user.organizations.count(), 1)

        new_org = OrganizationFactory()
        response = self.client.put(
            f'/orgs/{new_org.mnemonic}/members/{self.user.username}/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.user.organizations.count(), 1)

    def test_put_404(self):
        new_org = OrganizationFactory()
        response = self.client.put(
            f'/orgs/{new_org.mnemonic}/members/foobar/',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_403(self):
        self.assertEqual(self.user.organizations.count(), 1)
        random_user = UserProfileFactory()
        response = self.client.delete(
            f'/orgs/{self.org.mnemonic}/members/{random_user.username}/',
            HTTP_AUTHORIZATION='Token ' + random_user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.user.organizations.count(), 1)

    def test_delete_204(self):
        self.assertEqual(self.user.organizations.count(), 1)

        response = self.client.delete(
            f'/orgs/{self.org.mnemonic}/members/{self.user.username}/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.user.organizations.count(), 0)


class OrganizationExtrasViewTest(OCLAPITestCase):
    def test_get_200(self):
        org = OrganizationFactory(extras={})
        user = UserProfileFactory(organizations=[org])
        token = user.get_token()

        response = self.client.get(
            org.uri + 'extras/',
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {})

        org = OrganizationFactory(extras={'foo': 'bar'})

        response = self.client.get(
            org.uri + 'extras/',
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'foo': 'bar'})


class OrganizationExtraRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.extras = {'foo': 'bar', 'tao': 'ching'}
        self.organization = OrganizationFactory(extras=self.extras)
        self.user = UserProfileFactory(organizations=[self.organization])
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(
            self.organization.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'foo': 'bar'})

    def test_get_404(self):
        response = self.client.get(
            self.organization.uri + 'extras/bar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            '/orgs/org-foo/extras/bar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_put_200(self):
        response = self.client.put(
            self.organization.uri + 'extras/foo/',
            {'foo': 'foobar'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'foo': 'foobar'})
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.extras, {'foo': 'foobar', 'tao': 'ching'})

    def test_put_400(self):
        response = self.client.put(
            self.organization.uri + 'extras/foo/',
            {'tao': 'te-ching'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, ['Must specify foo param in body.'])

    def test_delete(self):
        response = self.client.delete(
            self.organization.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.extras, {'tao': 'ching'})

        response = self.client.delete(
            self.organization.uri + 'extras/foo/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.extras, {'tao': 'ching'})


class OrganizationLogoViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = OrganizationFactory(mnemonic='org-1')
        self.user = UserProfileFactory(organizations=[self.organization])
        self.token = self.user.get_token()

    @patch('core.common.services.S3.upload_base64')
    def test_post_200(self, upload_base64_mock):
        upload_base64_mock.return_value = 'orgs/org-1/logo.png'
        self.assertIsNone(self.organization.logo_url)
        self.assertIsNone(self.organization.logo_path)

        response = self.client.post(
            self.organization.uri + 'logo/',
            {'base64': 'base64-data'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        expected_logo_url = 'http://oclapi2-dev.s3.amazonaws.com/orgs/org-1/logo.png'
        self.assertEqual(response.data['logo_url'].replace('https://', 'http://'), expected_logo_url)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.logo_url.replace('https://', 'http://'), expected_logo_url)
        self.assertEqual(self.organization.logo_path, 'orgs/org-1/logo.png')
        upload_base64_mock.assert_called_once_with('base64-data', 'orgs/org-1/logo.png', False, True)


class OrganizationOverviewViewTest(OCLAPITestCase):
    def setUp(self):
        self.org = OrganizationFactory(name='Stark Enterprises')
        self.user = UserProfileFactory(organizations=[self.org])
        self.superuser = UserProfile.objects.get(is_superuser=True)
        self.token = self.user.get_token()

    def test_get_200(self):
        response = self.client.get(
            f'/orgs/{self.org.mnemonic}/overview/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.org.mnemonic)
        self.assertEqual(response.data['name'], 'Stark Enterprises')
        self.assertEqual(response.data['overview'], {})

    def test_put_200(self):
        response = self.client.put(
            f'/orgs/{self.org.mnemonic}/overview/',
            {'overview': {'foo': 'bar'}},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.org.mnemonic)
        self.assertEqual(response.data['name'], 'Stark Enterprises')
        self.assertEqual(response.data['overview'], {'foo': 'bar'})
        self.org.refresh_from_db()
        self.assertEqual(self.org.overview, {'foo': 'bar'})


class OrganizationSourceListViewTest(OCLAPITestCase):
    def test_get(self):
        user = UserProfileFactory(username='batman')
        token = user.get_token()
        org1 = OrganizationFactory(mnemonic='gotham')
        org2 = OrganizationFactory(mnemonic='wayne-enterprise')
        org1.members.add(user)
        org2.members.add(user)
        source1 = OrganizationSourceFactory(mnemonic='city', organization=org1)
        source2 = OrganizationSourceFactory(mnemonic='corporate', organization=org2)
        source3 = UserSourceFactory(mnemonic='bat-cave', user=user)

        SourceDocument().update([source1, source2, source3])

        response = self.client.get('/users/batman/orgs/sources/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            [data['short_code'] for data in response.data],
            ['corporate', 'city']
        )
        self.assertEqual(
            [data['owner_url'] for data in response.data],
            ['/orgs/wayne-enterprise/', '/orgs/gotham/']
        )

        response = self.client.get(
            '/user/orgs/sources/',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            [data['short_code'] for data in response.data],
            ['corporate', 'city']
        )

        response = self.client.get(
            '/user/orgs/sources/?q=city',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            [data['short_code'] for data in response.data],
            ['city']
        )

        response = self.client.get(
            '/user/orgs/sources/?q=batman',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)


class OrganizationCollectionListViewTest(OCLAPITestCase):
    def test_get(self):
        user = UserProfileFactory(username='batman')
        token = user.get_token()
        org1 = OrganizationFactory(mnemonic='gotham')
        org2 = OrganizationFactory(mnemonic='wayne-enterprise')
        org1.members.add(user)
        org2.members.add(user)
        coll1 = OrganizationCollectionFactory(mnemonic='city', organization=org1)
        coll2 = OrganizationCollectionFactory(mnemonic='corporate', organization=org2)
        coll3 = UserCollectionFactory(mnemonic='bat-cave', user=user)

        CollectionDocument().update([coll1, coll2, coll3])

        response = self.client.get('/users/batman/orgs/collections/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            [data['short_code'] for data in response.data],
            ['corporate', 'city']
        )
        self.assertEqual(
            [data['owner_url'] for data in response.data],
            ['/orgs/wayne-enterprise/', '/orgs/gotham/']
        )

        response = self.client.get(
            '/user/orgs/collections/',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            [data['short_code'] for data in response.data],
            ['corporate', 'city']
        )

        response = self.client.get(
            '/user/orgs/collections/?q=city',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            [data['short_code'] for data in response.data],
            ['city']
        )

        response = self.client.get(
            '/user/orgs/collections/?q=batman',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)
