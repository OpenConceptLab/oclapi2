import threading
import time

from django.db import connection
from django.test import override_settings, tag
from mock import patch
from mock.mock import Mock, ANY
from rest_framework.exceptions import ErrorDetail
from rest_framework.test import APIClient

from core.collections.documents import CollectionDocument
from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory
from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW, RETIRED_ACCESS_TYPE_EDIT
from core.common.tests import OCLAPITestCase, OCLAPITransactionTestCase
from core.events.models import Event
from core.orgs.constants import ROLE_ADMIN, ROLE_MEMBER
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
        self.org_public_edit = OrganizationFactory(mnemonic='public-edit-org', public_access=RETIRED_ACCESS_TYPE_EDIT)
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
        self.user = UserProfileFactory()
        self.org.set_member_role(self.user, ROLE_ADMIN)
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

    def test_put_200_by_staff_does_not_add_member(self):
        staff = UserProfileFactory(is_staff=True)
        response = self.client.put(
            f'/orgs/{self.org.mnemonic}/',
            {'name': 'Wayne Corporation'},
            HTTP_AUTHORIZATION='Token ' + staff.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.updated_by, staff)
        self.assertFalse(self.org.is_member(staff))

    def test_put_200_keeps_omitted_text_and_location(self):
        self.org.text = '<p>About Stark</p>'
        self.org.location = 'New York'
        self.org.website = 'https://stark.com'
        self.org.save()

        response = self.client.put(
            f'/orgs/{self.org.mnemonic}/',
            {'name': 'Wayne Corporation'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, 'Wayne Corporation')
        self.assertEqual(self.org.text, '<p>About Stark</p>')
        self.assertEqual(self.org.location, 'New York')
        self.assertEqual(self.org.website, 'https://stark.com')

    def test_put_200_clears_text_and_location_when_sent_empty(self):
        self.org.text = '<p>About Stark</p>'
        self.org.location = 'New York'
        self.org.save()

        response = self.client.put(
            f'/orgs/{self.org.mnemonic}/',
            {'text': '', 'location': ''},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.text, '')
        self.assertEqual(self.org.location, '')

    def test_patch_200_keeps_omitted_text_and_location(self):
        self.org.text = '<p>About Stark</p>'
        self.org.location = 'New York'
        self.org.website = 'https://stark.com'
        self.org.save()

        response = self.client.patch(
            f'/orgs/{self.org.mnemonic}/',
            {'name': 'Wayne Corporation'},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'Wayne Corporation')
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, 'Wayne Corporation')
        self.assertEqual(self.org.text, '<p>About Stark</p>')
        self.assertEqual(self.org.location, 'New York')
        self.assertEqual(self.org.website, 'https://stark.com')

    def test_patch_200_clears_text_and_location_when_sent_empty(self):
        self.org.text = '<p>About Stark</p>'
        self.org.location = 'New York'
        self.org.save()

        response = self.client.patch(
            f'/orgs/{self.org.mnemonic}/',
            {'text': '', 'location': ''},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.text, '')
        self.assertEqual(self.org.location, '')

    def test_patch_403(self):
        stranger = UserProfileFactory()
        response = self.client.patch(
            f'/orgs/{self.org.mnemonic}/',
            {'name': 'Wayne Corporation'},
            HTTP_AUTHORIZATION='Token ' + stranger.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 403)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, 'Stark Enterprises')

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
        delete_organization_mock.__name__ = 'delete_organization'
        delete_organization_mock.apply_async = Mock(return_value=Mock(task_id='task-id', state='PENDING'))

        response = self.client.delete(
            f'/orgs/{self.org.mnemonic}/?async=true',
            HTTP_AUTHORIZATION='Token ' + self.superuser.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        delete_organization_mock.apply_async.assert_called_once_with((self.org.id, ), task_id=ANY, queue='default')

    @patch('core.orgs.views.delete_organization')
    def test_delete_202_by_owner(self, delete_organization_mock):
        delete_organization_mock.__name__ = 'delete_organization'
        delete_organization_mock.apply_async = Mock(return_value=Mock(task_id='task-id', state='PENDING'))

        response = self.client.delete(
            f'/orgs/{self.org.mnemonic}/?async=true',
            HTTP_AUTHORIZATION='Token ' + self.user.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 202)
        delete_organization_mock.apply_async.assert_called_once_with((self.org.id, ), task_id=ANY, queue='default')

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

    def test_get_verbose_hides_private_fields_from_others(self):
        response = self.client.get('/orgs/OCL/members/?verbose=true', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['username'], 'ocladmin')
        self.assertNotIn('email', response.data[0])
        self.assertNotIn('is_superuser', response.data[0])

    @patch('core.common.mixins.ListWithHeadersMixin.get_csv')
    def test_get_csv_is_not_exported(self, get_csv_mock):
        response = self.client.get('/orgs/OCL/members/?csv=true', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['username'], 'ocladmin')
        get_csv_mock.assert_not_called()

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

    def test_delete_204_creator(self):
        creator = UserProfileFactory()
        org = OrganizationFactory(created_by=creator, updated_by=creator)
        other_admin = UserProfileFactory()
        org.set_member_role(other_admin, ROLE_ADMIN)

        response = self.client.delete(
            f'/orgs/{org.mnemonic}/members/{creator.username}/',
            HTTP_AUTHORIZATION='Token ' + other_admin.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(org.is_member(creator))

    def test_delete_204_last_editor(self):
        self.org.updated_by = self.user
        self.org.save()
        admin = UserProfileFactory()
        self.org.set_member_role(admin, ROLE_ADMIN)

        response = self.client.delete(
            f'/orgs/{self.org.mnemonic}/members/{self.user.username}/',
            HTTP_AUTHORIZATION='Token ' + admin.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(self.org.is_member(self.user))

    def test_delete_409_only_member(self):
        creator = UserProfileFactory()
        org = OrganizationFactory(created_by=creator, updated_by=creator)

        response = self.client.delete(
            f'/orgs/{org.mnemonic}/members/{creator.username}/',
            HTTP_AUTHORIZATION='Token ' + creator.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data, {'detail': 'Cannot remove the only member of an organization.'})
        self.assertTrue(org.is_member(creator))

    def test_delete_409_only_member_by_staff(self):
        creator = UserProfileFactory()
        org = OrganizationFactory(created_by=creator, updated_by=creator)
        staff = UserProfileFactory(is_staff=True)

        response = self.client.delete(
            f'/orgs/{org.mnemonic}/members/{creator.username}/',
            HTTP_AUTHORIZATION='Token ' + staff.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 409)
        self.assertTrue(org.is_member(creator))

    def test_delete_404(self):
        response = self.client.delete(
            f'/orgs/{self.org.mnemonic}/members/foobar/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 404)



class OrganizationMemberRemovalConcurrencyTest(OCLAPITransactionTestCase):
    """Runs two removals at once, on separate connections, to check that every org keeps a member."""

    def test_two_members_leaving_at_once_leave_one_member(self):
        alice = UserProfileFactory(username='alice')
        bob = UserProfileFactory(username='bob')
        org = OrganizationFactory(mnemonic='race-org', created_by=alice, updated_by=alice)
        org.set_member_role(bob, ROLE_ADMIN)
        tokens = {'alice': alice.get_token(), 'bob': bob.get_token()}
        is_only_member = Organization.is_only_member

        def slow_is_only_member(organization, user_profile):
            result = is_only_member(organization, user_profile)
            time.sleep(0.5)  # widens the gap between the check and the removal
            return result

        barrier = threading.Barrier(2)
        statuses = {}

        def leave(actor):
            try:
                client = APIClient()
                barrier.wait()
                response = client.delete(
                    f'/orgs/race-org/members/{actor}/', HTTP_AUTHORIZATION='Token ' + tokens[actor])
                statuses[actor] = response.status_code
            finally:
                connection.close()

        with patch.object(Organization, 'is_only_member', slow_is_only_member):
            threads = [
                threading.Thread(target=leave, args=('alice', )),
                threading.Thread(target=leave, args=('bob', )),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(sorted(statuses.values()), [204, 409])
        self.assertEqual(org.members.count(), 1)


class OrganizationMemberRepositoryAccessTest(OCLAPITestCase):
    """A plain member keeps everything they have on the org's repositories, private ones included."""
    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory(mnemonic='stark', public_access=ACCESS_TYPE_NONE)
        self.member = UserProfileFactory(organizations=[self.org])
        self.source = OrganizationSourceFactory(
            organization=self.org, mnemonic='private-source', public_access=ACCESS_TYPE_NONE)
        self.auth = {'HTTP_AUTHORIZATION': 'Token ' + self.member.get_token()}

    def test_views_the_private_org_and_its_private_repository(self):
        self.assertEqual(self.client.get('/orgs/stark/', **self.auth).status_code, 200)
        self.assertEqual(self.client.get('/orgs/stark/sources/private-source/', **self.auth).status_code, 200)

    def test_creates_a_repository(self):
        response = self.client.post(
            '/orgs/stark/sources/',
            {'id': 'new-source', 'name': 'New', 'short_code': 'new', 'public_access': ACCESS_TYPE_NONE},
            format='json', **self.auth
        )

        self.assertEqual(response.status_code, 201)

    def test_edits_a_private_repository(self):
        collection = OrganizationCollectionFactory(
            organization=self.org, mnemonic='private-collection', public_access=ACCESS_TYPE_NONE)

        for method, url, repo in (
                ('put', '/orgs/stark/sources/private-source/', self.source),
                ('patch', '/orgs/stark/sources/private-source/', self.source),
                ('put', '/orgs/stark/collections/private-collection/', collection),
                ('patch', '/orgs/stark/collections/private-collection/', collection),
        ):
            with self.subTest(method=method, url=url):
                name = f'Renamed by {method}'

                response = getattr(self.client, method)(url, {'name': name}, format='json', **self.auth)

                self.assertEqual(response.status_code, 200)
                repo.refresh_from_db()
                self.assertEqual(repo.name, name)

    def test_manages_exports_of_a_repository(self):
        self.assertTrue(self.member.is_admin_for(self.source))
        self.assertFalse(UserProfileFactory().is_admin_for(self.source))

    def test_is_refused_outside_the_org(self):
        stranger = {'HTTP_AUTHORIZATION': 'Token ' + UserProfileFactory().get_token()}

        self.assertEqual(self.client.get('/orgs/stark/sources/private-source/', **stranger).status_code, 403)


class OrganizationMemberRoleTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.admin = UserProfileFactory()
        self.org = OrganizationFactory(mnemonic='stark', created_by=self.admin, updated_by=self.admin)
        self.member = UserProfileFactory(organizations=[self.org])

    def put(self, target, data=None, actor=None):
        return self.client.put(
            f'/orgs/stark/members/{target.username}/', data, format='json',
            HTTP_AUTHORIZATION='Token ' + (actor or self.admin).get_token())

    def delete(self, target, actor=None):
        return self.client.delete(
            f'/orgs/stark/members/{target.username}/', HTTP_AUTHORIZATION='Token ' + (actor or self.admin).get_token())

    def get_role(self, user):
        return self.org.get_member_role(user)

    def test_put_adds_a_new_member_as_member_by_default(self):
        user = UserProfileFactory()

        response = self.put(user)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.get_role(user), ROLE_MEMBER)
        self.assertEqual(Event.objects.filter(event_type=Event.JOINED, object_url=user.uri).count(), 1)

    def test_put_adds_a_new_member_as_admin(self):
        user = UserProfileFactory()

        self.assertEqual(self.put(user, {'role': ROLE_ADMIN}).status_code, 204)
        self.assertEqual(self.get_role(user), ROLE_ADMIN)

    def test_put_without_a_role_keeps_an_existing_admin_as_admin(self):
        self.org.set_member_role(self.member, ROLE_ADMIN)

        self.assertEqual(self.put(self.member).status_code, 204)
        self.assertEqual(self.get_role(self.member), ROLE_ADMIN)

    def test_put_on_an_existing_member_records_no_second_joined_event(self):
        self.assertEqual(self.put(self.member, {'role': ROLE_ADMIN}).status_code, 204)
        self.assertEqual(self.put(self.member).status_code, 204)

        self.assertFalse(Event.objects.filter(event_type=Event.JOINED, object_url=self.member.uri).exists())

    def test_put_promotes_and_demotes(self):
        self.assertEqual(self.put(self.member, {'role': ROLE_ADMIN}).status_code, 204)
        self.assertEqual(self.get_role(self.member), ROLE_ADMIN)

        self.assertEqual(self.put(self.member, {'role': ROLE_MEMBER}).status_code, 204)
        self.assertEqual(self.get_role(self.member), ROLE_MEMBER)

    def test_put_400_for_a_role_that_does_not_exist(self):
        user = UserProfileFactory()
        for role in ('owner', '', 1, ['admin'], {'name': 'admin'}):
            with self.subTest(role=role):
                response = self.put(user, {'role': role})

                self.assertEqual(response.status_code, 400)
                self.assertEqual(list(response.data), ['role'])
                self.assertFalse(self.org.is_member(user))

    def test_put_403_for_a_member(self):
        user = UserProfileFactory()

        self.assertEqual(self.put(user, actor=self.member).status_code, 403)
        self.assertFalse(self.org.is_member(user))

    def test_put_403_for_a_member_promoting_themselves(self):
        self.assertEqual(self.put(self.member, {'role': ROLE_ADMIN}, actor=self.member).status_code, 403)
        self.assertEqual(self.get_role(self.member), ROLE_MEMBER)

    def test_put_by_staff_who_is_not_a_member(self):
        staff = UserProfileFactory(is_staff=True)

        self.assertEqual(self.put(self.member, {'role': ROLE_ADMIN}, actor=staff).status_code, 204)
        self.assertEqual(self.get_role(self.member), ROLE_ADMIN)
        self.assertFalse(self.org.is_member(staff))

    def test_put_409_demoting_the_last_admin(self):
        for actor in (self.admin, UserProfileFactory(is_staff=True)):
            with self.subTest(actor=actor.username):
                response = self.put(self.admin, {'role': ROLE_MEMBER}, actor=actor)

                self.assertEqual(response.status_code, 409)
                self.assertEqual(
                    response.data, {'detail': 'Cannot remove or demote the last admin of an organization.'})
                self.assertEqual(self.get_role(self.admin), ROLE_ADMIN)

    def test_put_409_demoting_the_last_active_admin(self):
        deactivated = UserProfileFactory(organizations=[self.org], is_active=False)
        self.org.set_member_role(deactivated, ROLE_ADMIN)

        self.assertEqual(self.put(self.admin, {'role': ROLE_MEMBER}).status_code, 409)

    def test_delete_403_for_a_member_removing_someone_else(self):
        other = UserProfileFactory(organizations=[self.org])

        self.assertEqual(self.delete(other, actor=self.member).status_code, 403)
        self.assertEqual(self.delete(self.admin, actor=self.member).status_code, 403)
        self.assertTrue(self.org.is_member(other))
        self.assertTrue(self.org.is_member(self.admin))

    def test_delete_409_for_the_last_admin(self):
        for actor in (self.admin, UserProfileFactory(is_staff=True)):
            with self.subTest(actor=actor.username):
                response = self.delete(self.admin, actor=actor)

                self.assertEqual(response.status_code, 409)
                self.assertEqual(
                    response.data, {'detail': 'Cannot remove or demote the last admin of an organization.'})
                self.assertTrue(self.org.is_member(self.admin))

    def test_delete_removes_a_deactivated_admin(self):
        deactivated = UserProfileFactory(organizations=[self.org], is_active=False)
        self.org.set_member_role(deactivated, ROLE_ADMIN)

        self.assertEqual(self.delete(deactivated).status_code, 204)
        self.assertFalse(self.org.is_member(deactivated))

class OrganizationMemberRoleReadTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.admin = UserProfileFactory(username='the-admin')
        self.org = OrganizationFactory(mnemonic='stark', created_by=self.admin, updated_by=self.admin)
        self.member = UserProfileFactory(username='the-member', organizations=[self.org])

    def test_members_list_has_each_role_in_every_shape(self):
        for params in ('', '?verbose=true', '?summary=true'):
            with self.subTest(params=params):
                response = self.client.get(f'/orgs/stark/members/{params}')

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    {row['username']: row['role'] for row in response.data},
                    {'the-admin': 'admin', 'the-member': 'member'}
                )

    def test_role_is_from_this_org_not_another(self):
        other = OrganizationFactory(mnemonic='other', created_by=self.member, updated_by=self.member)
        other.members.add(self.admin)

        response = self.client.get('/orgs/other/members/')

        self.assertEqual({row['username']: row['role'] for row in response.data},
                         {'the-admin': 'member', 'the-member': 'admin'})

    def test_user_lists_outside_an_org_have_no_role(self):
        response = self.client.get(
            '/users/', HTTP_AUTHORIZATION='Token ' + self.admin.get_token())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data)
        self.assertFalse(any('role' in row for row in response.data))

    def test_org_detail_has_the_role_of_the_requester(self):
        for user, expected in (
                (self.admin, 'admin'),
                (self.member, 'member'),
                (UserProfileFactory(), None),
                (UserProfileFactory(is_staff=True), None),
        ):
            with self.subTest(user=user.username):
                response = self.client.get('/orgs/stark/', HTTP_AUTHORIZATION='Token ' + user.get_token())

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['user_role'], expected)

    def test_org_detail_has_no_role_for_anonymous(self):
        response = self.client.get('/orgs/stark/')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['user_role'])

    def test_creating_an_org_makes_the_creator_its_admin(self):
        user = UserProfileFactory()

        response = self.client.post(
            '/orgs/', {'id': 'new-org', 'name': 'New'}, format='json',
            HTTP_AUTHORIZATION='Token ' + user.get_token())

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['user_role'], 'admin')
        self.assertTrue(Organization.objects.get(mnemonic='new-org').is_admin(user))


class OrganizationDemotedAdminConcurrencyTest(OCLAPITransactionTestCase):
    """A request that was waiting for the org's lock uses the rights its sender has once it gets the lock."""

    def test_an_admin_being_demoted_cannot_promote_themselves_back(self):
        alice = UserProfileFactory(username='alice')
        bob = UserProfileFactory(username='bob')
        org = OrganizationFactory(mnemonic='race-org', created_by=alice, updated_by=alice)
        org.set_member_role(bob, ROLE_ADMIN)
        tokens = {'alice': alice.get_token(), 'bob': bob.get_token()}
        is_last_admin = Organization.is_last_admin
        alice_holds_the_lock = threading.Event()

        def slow_is_last_admin(organization, user_profile):
            result = is_last_admin(organization, user_profile)
            alice_holds_the_lock.set()
            time.sleep(1)  # bob's request starts and waits for the lock
            return result

        statuses = {}

        def send(actor, role):
            try:
                response = APIClient().put(
                    '/orgs/race-org/members/bob/', {'role': role}, format='json',
                    HTTP_AUTHORIZATION='Token ' + tokens[actor])
                statuses[actor] = response.status_code
            finally:
                connection.close()

        def promote_once_alice_holds_the_lock():
            alice_holds_the_lock.wait(timeout=5)
            send('bob', ROLE_ADMIN)

        with patch.object(Organization, 'is_last_admin', slow_is_last_admin):
            threads = [
                threading.Thread(target=send, args=('alice', ROLE_MEMBER)),
                threading.Thread(target=promote_once_alice_holds_the_lock),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(statuses, {'alice': 204, 'bob': 403})
        self.assertEqual(org.get_member_role(bob), ROLE_MEMBER)


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
        self.user = UserProfileFactory()
        self.organization.set_member_role(self.user, ROLE_ADMIN)
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
        self.user = UserProfileFactory()
        self.organization.set_member_role(self.user, ROLE_ADMIN)
        self.token = self.user.get_token()

    @patch('core.services.storages.cloud.aws.S3.upload_base64')
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
        self.user = UserProfileFactory()
        self.org.set_member_role(self.user, ROLE_ADMIN)
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

    def test_put_200_by_staff_does_not_add_member(self):
        staff = UserProfileFactory(is_staff=True)
        response = self.client.put(
            f'/orgs/{self.org.mnemonic}/overview/',
            {'overview': {'foo': 'bar'}},
            HTTP_AUTHORIZATION='Token ' + staff.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.updated_by, staff)
        self.assertFalse(self.org.is_member(staff))


@tag('es')
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

        self.assertEqual(response.status_code, 401)

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

    def test_get_with_search(self):
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


@tag('es')
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

        self.assertEqual(response.status_code, 401)

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

    def test_get_with_search(self):
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


class CompressHeaderLimitTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        OrganizationFactory()
        OrganizationFactory()

    def test_small_list_allowed_for_anyone(self):
        response = self.client.get(
            '/orgs/', HTTP_COMPRESS='true', HTTP_AUTHORIZATION='Token ' + UserProfileFactory().get_token())
        self.assertEqual(response.status_code, 200)

    @patch('core.common.mixins.MAX_UNPAGINATED_RESULTS', 1)
    def test_list_over_limit_needs_permission(self):
        response = self.client.get(
            '/orgs/', HTTP_COMPRESS='true', HTTP_AUTHORIZATION='Token ' + UserProfileFactory().get_token())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['error_code'], 'list_unpaginated_limit_reached')
        self.assertEqual(response.data['limit'], 1)

    @patch('core.common.mixins.MAX_UNPAGINATED_RESULTS', 1)
    def test_list_over_limit_allowed_for_superuser(self):
        admin = UserProfile.objects.get(username='ocladmin')
        response = self.client.get('/orgs/', HTTP_COMPRESS='true', HTTP_AUTHORIZATION='Token ' + admin.get_token())
        self.assertEqual(response.status_code, 200)

    def test_without_header_unaffected(self):
        response = self.client.get('/orgs/', HTTP_AUTHORIZATION='Token ' + UserProfileFactory().get_token())
        self.assertEqual(response.status_code, 200)

    @override_settings(LIST_MAX_LIMIT=1)
    def test_uncapped_list_ignores_max_page_size(self):
        token = 'Token ' + UserProfileFactory().get_token()
        response = self.client.get('/orgs/', HTTP_COMPRESS='true', HTTP_AUTHORIZATION=token)
        self.assertEqual(response.status_code, 200)

        response = self.client.get('/orgs/?limit=1000', HTTP_AUTHORIZATION=token)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 1)


class PaginationParamsValidationTest(OCLAPITestCase):
    def setUp(self):
        self.headers = {'HTTP_AUTHORIZATION': 'Token ' + UserProfileFactory().get_token()}

    def test_invalid_limit_or_page_returns_400(self):
        for name in ('limit', 'page'):
            for value in ('-1', '0', 'abc', '1.5', '', '99999999999999999999'):
                response = self.client.get(f'/orgs/?{name}={value}', **self.headers)
                self.assertEqual(response.status_code, 400, f'{name}={value}')
                self.assertIn(f"'{name}'", response.data['error'])

    def test_valid_limit_and_page_return_200(self):
        self.assertEqual(self.client.get('/orgs/?limit=5&page=1', **self.headers).status_code, 200)
