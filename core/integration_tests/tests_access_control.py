from mock import patch, Mock

from core.client_configs.models import ClientConfig
from core.collections.documents import CollectionDocument
from core.collections.models import Collection, CollectionReference
from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW, ACCESS_TYPE_EDIT
from core.common.permissions import HasOwnership
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory, ConceptDescriptionFactory
from core.map_projects.tests.factories import MapProjectFactory
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.pins.models import Pin
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.url_registry.factories import GlobalURLRegistryFactory, OrganizationURLRegistryFactory, UserURLRegistryFactory
from core.url_registry.models import URLRegistry
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class AccessTestMixin:
    """
    Staff, an org with one member, the owner of user-owned resources, and an outsider: a logged-in user who belongs
    to no org and owns nothing under test.
    """
    def setUp(self):
        super().setUp()
        self.staff = UserProfile.objects.get(username='ocladmin')
        self.org = OrganizationFactory(public_access=ACCESS_TYPE_VIEW)
        self.member = UserProfileFactory(organizations=[self.org])
        self.owner = UserProfileFactory()
        self.outsider = UserProfileFactory()

    def request(self, method, url, user=None, data=None):
        kwargs = {'HTTP_AUTHORIZATION': 'Token ' + user.get_token()} if user else {}
        if method == 'get':
            return self.client.get(url, **kwargs)
        return getattr(self.client, method)(url, data, format='json', **kwargs)


class OrganizationDetailAccessTest(AccessTestMixin, OCLAPITestCase):
    def rename(self, user):
        return self.request('put', self.org.uri, user, {'name': 'Renamed'})

    def assert_renamed(self, response):
        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, 'Renamed')

    def assert_not_renamed(self, response, status_code):
        self.assertEqual(response.status_code, status_code)
        self.org.refresh_from_db()
        self.assertNotEqual(self.org.name, 'Renamed')

    def test_outsider_cannot_edit(self):
        self.assert_not_renamed(self.rename(self.outsider), 403)

    def test_anonymous_cannot_edit(self):
        self.assert_not_renamed(self.rename(None), 401)

    def test_member_can_edit(self):
        self.assert_renamed(self.rename(self.member))

    def test_staff_can_edit(self):
        self.assert_renamed(self.rename(self.staff))

    def test_anonymous_cannot_post(self):
        response = self.request('post', self.org.uri, None, {'id': 'new-org', 'name': 'New Org'})

        self.assertEqual(response.status_code, 401)
        self.assertFalse(Organization.objects.filter(mnemonic='new-org').exists())

    def test_private_org_is_visible_to_members_and_staff_only(self):
        self.org.public_access = ACCESS_TYPE_NONE
        self.org.save()

        self.assertEqual(self.request('get', self.org.uri, self.outsider).status_code, 403)
        self.assertEqual(self.request('get', self.org.uri).status_code, 401)
        self.assertEqual(self.request('get', self.org.uri, self.member).status_code, 200)
        self.assertEqual(self.request('get', self.org.uri, self.staff).status_code, 200)


class OrganizationOverviewAccessTest(AccessTestMixin, OCLAPITestCase):
    def get_url(self):
        return self.org.uri + 'overview/'

    def edit(self, user):
        return self.request('put', self.get_url(), user, {'overview': {'text': 'Replaced'}})

    def assert_edited(self, response):
        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.overview, {'text': 'Replaced'})

    def assert_not_edited(self, response, status_code):
        self.assertEqual(response.status_code, status_code)
        self.org.refresh_from_db()
        self.assertNotEqual(self.org.overview, {'text': 'Replaced'})

    def assert_not_deleted(self, response, status_code):
        self.assertEqual(response.status_code, status_code)
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_active)

    def test_outsider_cannot_edit(self):
        self.assert_not_edited(self.edit(self.outsider), 403)

    def test_anonymous_cannot_edit(self):
        self.assert_not_edited(self.edit(None), 401)

    def test_member_can_edit(self):
        self.assert_edited(self.edit(self.member))

    def test_staff_can_edit(self):
        self.assert_edited(self.edit(self.staff))

    def test_outsider_cannot_delete(self):
        self.assert_not_deleted(self.request('delete', self.get_url(), self.outsider), 403)

    def test_anonymous_cannot_delete(self):
        self.assert_not_deleted(self.request('delete', self.get_url()), 401)

    def test_member_and_staff_delete_clears_overview_only(self):
        for user in [self.member, self.staff]:
            self.org.overview = {'text': 'Existing'}
            self.org.save()

            response = self.request('delete', self.get_url(), user)

            self.assertEqual(response.status_code, 204)
            self.org.refresh_from_db()
            self.assertEqual(self.org.overview, {})
            self.assertTrue(self.org.is_active)
            self.assertEqual(self.org.updated_by, user)

    def test_public_org_overview_is_visible_to_anyone(self):
        self.assertEqual(self.request('get', self.get_url()).status_code, 200)
        self.assertEqual(self.request('get', self.get_url(), self.outsider).status_code, 200)

    def test_private_org_overview_is_visible_to_members_and_staff_only(self):
        self.org.public_access = ACCESS_TYPE_NONE
        self.org.save()

        self.assertEqual(self.request('get', self.get_url(), self.outsider).status_code, 403)
        self.assertEqual(self.request('get', self.get_url()).status_code, 401)
        self.assertEqual(self.request('get', self.get_url(), self.member).status_code, 200)
        self.assertEqual(self.request('get', self.get_url(), self.staff).status_code, 200)


class OrganizationLogoAccessTest(AccessTestMixin, OCLAPITestCase):
    def upload(self, user):
        with patch('core.services.storages.cloud.aws.S3.upload_base64') as upload_mock:
            upload_mock.return_value = f'orgs/{self.org.mnemonic}/logo.png'
            return self.request('post', self.org.uri + 'logo/', user, {'base64': 'base64-data'})

    def assert_uploaded(self, response):
        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.logo_path, f'orgs/{self.org.mnemonic}/logo.png')

    def assert_not_uploaded(self, response, status_code):
        self.assertEqual(response.status_code, status_code)
        self.org.refresh_from_db()
        self.assertIsNone(self.org.logo_path)

    def test_outsider_cannot_upload(self):
        self.assert_not_uploaded(self.upload(self.outsider), 403)

    def test_anonymous_cannot_upload(self):
        self.assert_not_uploaded(self.upload(None), 401)

    def test_member_can_upload(self):
        self.assert_uploaded(self.upload(self.member))

    def test_staff_can_upload(self):
        self.assert_uploaded(self.upload(self.staff))

    def test_delete_clears_logo_only(self):
        for user, status_code in [(None, 401), (self.outsider, 403)]:
            self.org.logo_path = f'orgs/{self.org.mnemonic}/logo.png'
            self.org.save()

            self.assertEqual(self.request('delete', self.org.uri + 'logo/', user).status_code, status_code)
            self.org.refresh_from_db()
            self.assertEqual(self.org.logo_path, f'orgs/{self.org.mnemonic}/logo.png')

        for user in [self.member, self.staff]:
            self.org.logo_path = f'orgs/{self.org.mnemonic}/logo.png'
            self.org.save()

            self.assertEqual(self.request('delete', self.org.uri + 'logo/', user).status_code, 204)
            self.org.refresh_from_db()
            self.assertIsNone(self.org.logo_path)
            self.assertTrue(self.org.is_active)
            self.assertEqual(self.org.updated_by, user)


class OrganizationResourceListAccessMixin(AccessTestMixin):
    """
    /users/<user>/orgs/<resources>/ lists what the orgs of <user> own, and shows the requester only what the
    requester can view.
    """
    resources = None
    key = 'url'
    query = ''
    has_current_user_url = True

    def create_resource(self, public_access):
        raise NotImplementedError

    @staticmethod
    def get_key(resource):
        return resource.uri

    def setUp(self):
        super().setUp()
        self.other_member = UserProfileFactory(organizations=[self.org])
        self.public_resource = self.create_resource(ACCESS_TYPE_VIEW)
        self.private_resource = self.create_resource(ACCESS_TYPE_NONE)

    def list_keys(self, url, user):
        response = self.request('get', url + self.query, user)
        self.assertEqual(response.status_code, 200)
        return sorted(item[self.key] for item in response.data)

    def get_member_list_url(self):
        return f'{self.member.uri}orgs/{self.resources}/'

    def assert_lists_all(self, url, user):
        self.assertEqual(
            self.list_keys(url, user),
            sorted([self.get_key(self.public_resource), self.get_key(self.private_resource)])
        )

    def test_outsider_sees_only_public_resources(self):
        self.assertEqual(
            self.list_keys(self.get_member_list_url(), self.outsider), [self.get_key(self.public_resource)])

    def test_member_sees_all_resources_of_own_orgs(self):
        self.assert_lists_all(self.get_member_list_url(), self.member)
        if self.has_current_user_url:
            self.assert_lists_all(f'/user/orgs/{self.resources}/', self.member)

    def test_other_member_sees_all_resources_of_shared_org(self):
        self.assert_lists_all(self.get_member_list_url(), self.other_member)

    def test_staff_sees_all_resources(self):
        self.assert_lists_all(self.get_member_list_url(), self.staff)

    def test_anonymous_is_refused(self):
        self.assertEqual(self.request('get', self.get_member_list_url()).status_code, 401)


class OrganizationResourceSearchAccessMixin:
    """The same lists when a search sends them through the search index."""
    document = None

    def search_keys(self, user):
        self.document().update([self.public_resource, self.private_resource])  # pylint: disable=not-callable
        return self.list_keys(f'{self.get_member_list_url()}?q={self.private_resource.mnemonic}', user)

    def test_outsider_search_excludes_private_resources(self):
        self.assertNotIn(self.get_key(self.private_resource), self.search_keys(self.outsider))

    def test_member_search_includes_private_resources_of_own_orgs(self):
        self.assertIn(self.get_key(self.private_resource), self.search_keys(self.member))


class OrganizationSourceListAccessTest(
        OrganizationResourceSearchAccessMixin, OrganizationResourceListAccessMixin, OCLAPITestCase):
    resources = 'sources'
    document = SourceDocument

    def create_resource(self, public_access):
        return OrganizationSourceFactory(organization=self.org, public_access=public_access)


class OrganizationCollectionListAccessTest(
        OrganizationResourceSearchAccessMixin, OrganizationResourceListAccessMixin, OCLAPITestCase):
    resources = 'collections'
    document = CollectionDocument

    def create_resource(self, public_access):
        return OrganizationCollectionFactory(organization=self.org, public_access=public_access)


class OrganizationMapProjectListAccessTest(OrganizationResourceListAccessMixin, OCLAPITestCase):
    resources = 'map-projects'
    key = 'id'
    query = '?verbose=true'
    has_current_user_url = False

    @staticmethod
    def get_key(resource):
        return resource.id

    def create_resource(self, public_access):
        return MapProjectFactory(organization=self.org, public_access=public_access)


class URLRegistryAccessTest(AccessTestMixin, OCLAPITestCase):
    """
    Org entries can be changed by members and staff, a user's entries by that user and staff, and global entries by
    staff only.
    """
    def setUp(self):
        super().setUp()
        self.org_entry = OrganizationURLRegistryFactory(organization=self.org, url='https://org.example.com')
        self.user_entry = UserURLRegistryFactory(user=self.owner, url='https://user.example.com')
        self.global_entry = GlobalURLRegistryFactory(url='https://global.example.com')

    def edit(self, entry, user):
        return self.request('put', entry.relative_uri, user, {'name': 'Renamed', 'url': entry.url})

    def delete(self, entry, user):
        return self.request('delete', entry.relative_uri, user)

    def assert_can_edit_and_delete(self, entry, user):
        self.assertEqual(self.edit(entry, user).status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.name, 'Renamed')
        self.assertEqual(self.delete(entry, user).status_code, 204)
        entry.refresh_from_db()
        self.assertFalse(entry.is_active)

    def assert_cannot_edit_or_delete(self, entry, user, status_code=403):
        self.assertEqual(self.edit(entry, user).status_code, status_code)
        self.assertEqual(self.delete(entry, user).status_code, status_code)
        entry.refresh_from_db()
        self.assertNotEqual(entry.name, 'Renamed')
        self.assertTrue(entry.is_active)

    def create(self, url, user):
        return self.request('post', url, user, {'name': 'New', 'url': 'https://new.example.com'})

    @staticmethod
    def created_count():
        return URLRegistry.objects.filter(url='https://new.example.com').count()

    def test_org_entry(self):
        self.assert_cannot_edit_or_delete(self.org_entry, self.outsider)
        self.assert_cannot_edit_or_delete(self.org_entry, self.owner)
        self.assert_cannot_edit_or_delete(self.org_entry, None, 401)
        self.assert_can_edit_and_delete(self.org_entry, self.member)

    def test_org_entry_by_staff(self):
        self.assert_can_edit_and_delete(self.org_entry, self.staff)

    def test_user_entry(self):
        self.assert_cannot_edit_or_delete(self.user_entry, self.outsider)
        self.assert_cannot_edit_or_delete(self.user_entry, self.member)
        self.assert_cannot_edit_or_delete(self.user_entry, None, 401)
        self.assert_can_edit_and_delete(self.user_entry, self.owner)

    def test_user_entry_by_staff(self):
        self.assert_can_edit_and_delete(self.user_entry, self.staff)

    def test_global_entry(self):
        self.assert_cannot_edit_or_delete(self.global_entry, self.outsider)
        self.assert_cannot_edit_or_delete(self.global_entry, self.member)
        self.assert_cannot_edit_or_delete(self.global_entry, None, 401)
        self.assert_can_edit_and_delete(self.global_entry, self.staff)

    def test_create_org_entry(self):
        url = self.org.uri + 'url-registry/'
        self.assertEqual(self.create(url, self.outsider).status_code, 403)
        self.assertEqual(self.create(url, None).status_code, 401)
        self.assertEqual(self.created_count(), 0)

        self.assertEqual(self.create(url, self.member).status_code, 201)
        self.assertEqual(self.created_count(), 1)

    def test_create_org_entry_by_staff(self):
        self.assertEqual(self.create(self.org.uri + 'url-registry/', self.staff).status_code, 201)

    def test_create_user_entry(self):
        url = self.owner.uri + 'url-registry/'
        self.assertEqual(self.create(url, self.outsider).status_code, 403)
        self.assertEqual(self.create(url, self.member).status_code, 403)
        self.assertEqual(self.create(url, None).status_code, 401)
        self.assertEqual(self.created_count(), 0)

        self.assertEqual(self.create(url, self.owner).status_code, 201)
        self.assertEqual(self.created_count(), 1)

    def test_create_own_entry_through_current_user_url(self):
        self.assertEqual(self.create('/user/url-registry/', self.owner).status_code, 201)
        self.assertTrue(URLRegistry.objects.filter(user=self.owner, url='https://new.example.com').exists())

    def test_create_user_entry_by_staff(self):
        self.assertEqual(self.create(self.owner.uri + 'url-registry/', self.staff).status_code, 201)

    def test_create_global_entry(self):
        self.assertEqual(self.create('/url-registry/', self.outsider).status_code, 403)
        self.assertEqual(self.create('/url-registry/', self.member).status_code, 403)
        self.assertEqual(self.create('/url-registry/', None).status_code, 401)
        self.assertEqual(self.created_count(), 0)

        self.assertEqual(self.create('/url-registry/', self.staff).status_code, 201)
        self.assertEqual(self.created_count(), 1)

    def test_entries_stay_readable(self):
        for entry in [self.org_entry, self.user_entry, self.global_entry]:
            self.assertEqual(self.request('get', entry.relative_uri).status_code, 200)
            self.assertEqual(self.request('get', entry.relative_uri, self.outsider).status_code, 200)


class ProcessingFlagAccessMixin(AccessTestMixin):
    """
    Clearing a processing flag needs staff or edit access to the repo. Reading it needs view access.
    """
    def create_org_repo(self, public_access):
        raise NotImplementedError

    def create_user_repo(self, user, public_access):
        raise NotImplementedError

    def get_url(self, repo):
        raise NotImplementedError

    def set_processing(self, repo):
        raise NotImplementedError

    def is_processing(self, repo):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.repo = self.create_org_repo(ACCESS_TYPE_VIEW)
        self.set_processing(self.repo)

    def clear(self, repo, user):
        return self.request('post', self.get_url(repo), user)

    def assert_cleared(self, repo, user):
        self.assertEqual(self.clear(repo, user).status_code, 200)
        self.assertFalse(self.is_processing(repo))

    def assert_not_cleared(self, repo, user, status_code):
        self.assertEqual(self.clear(repo, user).status_code, status_code)
        self.assertTrue(self.is_processing(repo))

    def test_outsider_cannot_clear(self):
        self.assert_not_cleared(self.repo, self.outsider, 403)

    def test_anonymous_cannot_clear(self):
        self.assert_not_cleared(self.repo, None, 401)

    def test_member_can_clear(self):
        self.assert_cleared(self.repo, self.member)

    def test_staff_can_clear(self):
        self.assert_cleared(self.repo, self.staff)

    def test_owner_can_clear(self):
        repo = self.create_user_repo(self.owner, ACCESS_TYPE_VIEW)
        self.set_processing(repo)

        self.assert_not_cleared(repo, self.outsider, 403)
        self.assert_cleared(repo, self.owner)

    def test_private_repo_flag_is_visible_to_members_and_staff_only(self):
        repo = self.create_org_repo(ACCESS_TYPE_NONE)

        self.assertEqual(self.request('get', self.get_url(repo), self.outsider).status_code, 403)
        self.assertEqual(self.request('get', self.get_url(repo)).status_code, 401)
        self.assertEqual(self.request('get', self.get_url(repo), self.member).status_code, 200)
        self.assertEqual(self.request('get', self.get_url(repo), self.staff).status_code, 200)


class RepoVersionProcessingFlagAccessMixin(ProcessingFlagAccessMixin):  # pylint: disable=abstract-method
    def get_url(self, repo):
        return repo.uri + 'processing/'

    def set_processing(self, repo):
        repo.add_processing('Task123')

    def is_processing(self, repo):
        repo.refresh_from_db()
        return bool(repo._background_process_ids)  # pylint: disable=protected-access


class SourceVersionProcessingAccessTest(RepoVersionProcessingFlagAccessMixin, OCLAPITestCase):
    def create_org_repo(self, public_access):
        head = OrganizationSourceFactory(organization=self.org, public_access=public_access)
        return OrganizationSourceFactory(
            mnemonic=head.mnemonic, organization=self.org, version='v1', public_access=public_access)

    def create_user_repo(self, user, public_access):
        head = UserSourceFactory(user=user, public_access=public_access)
        return UserSourceFactory(mnemonic=head.mnemonic, user=user, version='v1', public_access=public_access)


class CollectionVersionProcessingAccessTest(RepoVersionProcessingFlagAccessMixin, OCLAPITestCase):
    def create_org_repo(self, public_access):
        head = OrganizationCollectionFactory(organization=self.org, public_access=public_access)
        return OrganizationCollectionFactory(
            mnemonic=head.mnemonic, organization=self.org, version='v1', public_access=public_access)

    def create_user_repo(self, user, public_access):
        head = UserCollectionFactory(user=user, public_access=public_access)
        return UserCollectionFactory(mnemonic=head.mnemonic, user=user, version='v1', public_access=public_access)


class CollectionVersionExpansionProcessingAccessTest(ProcessingFlagAccessMixin, OCLAPITestCase):
    def create_org_repo(self, public_access):
        collection = OrganizationCollectionFactory(organization=self.org, public_access=public_access)
        return ExpansionFactory(collection_version=collection)

    def create_user_repo(self, user, public_access):
        collection = UserCollectionFactory(user=user, public_access=public_access)
        return ExpansionFactory(collection_version=collection)

    def get_url(self, repo):
        return repo.url + 'processing/'

    def set_processing(self, repo):
        repo.is_processing = True
        repo.save(update_fields=['is_processing'])

    def is_processing(self, repo):
        repo.refresh_from_db()
        return repo.is_processing


class UserOrganizationListAccessTest(AccessTestMixin, OCLAPITestCase):
    """/users/<user>/orgs/ lists the orgs of <user> that the requester can view."""
    def setUp(self):
        super().setUp()
        self.private_org = OrganizationFactory(public_access=ACCESS_TYPE_NONE)
        self.member.organizations.add(self.private_org)
        self.other_member = UserProfileFactory(organizations=[self.private_org])

    def list_orgs(self, url, user):
        response = self.request('get', url, user)
        self.assertEqual(response.status_code, 200)
        return sorted(org['id'] for org in response.data)

    def test_outsider_sees_only_public_orgs(self):
        self.assertEqual(self.list_orgs(self.member.uri + 'orgs/', self.outsider), [self.org.mnemonic])
        self.assertEqual(self.list_orgs(self.member.uri + 'orgs/', None), [self.org.mnemonic])

    def test_members_and_staff_see_private_orgs(self):
        all_orgs = sorted([self.org.mnemonic, self.private_org.mnemonic])
        self.assertEqual(self.list_orgs(self.member.uri + 'orgs/', self.member), all_orgs)
        self.assertEqual(self.list_orgs('/user/orgs/', self.member), all_orgs)
        self.assertEqual(self.list_orgs(self.member.uri + 'orgs/', self.staff), all_orgs)
        self.assertEqual(
            self.list_orgs(self.member.uri + 'orgs/', self.other_member), all_orgs)


class OrganizationExtrasAccessTest(AccessTestMixin, OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.org.extras = {'foo': 'bar'}
        self.org.save()

    def edit(self, user):
        return self.request('put', self.org.uri + 'extras/foo/', user, {'foo': 'changed'})

    def delete(self, user):
        return self.request('delete', self.org.uri + 'extras/foo/', user)

    def assert_extras(self, extras):
        self.org.refresh_from_db()
        self.assertEqual(self.org.extras, extras)

    def test_outsider_cannot_edit_or_delete(self):
        self.assertEqual(self.edit(self.outsider).status_code, 403)
        self.assertEqual(self.delete(self.outsider).status_code, 403)
        self.assert_extras({'foo': 'bar'})

    def test_anonymous_cannot_edit_or_delete(self):
        self.assertEqual(self.edit(None).status_code, 401)
        self.assertEqual(self.delete(None).status_code, 401)
        self.assert_extras({'foo': 'bar'})

    def test_member_can_edit_and_delete(self):
        self.assertEqual(self.edit(self.member).status_code, 200)
        self.assert_extras({'foo': 'changed'})
        self.assertEqual(self.delete(self.member).status_code, 204)
        self.assert_extras({})

    def test_staff_can_edit(self):
        self.assertEqual(self.edit(self.staff).status_code, 200)
        self.assert_extras({'foo': 'changed'})

    def test_private_org_extras_are_visible_to_members_and_staff_only(self):
        self.org.public_access = ACCESS_TYPE_NONE
        self.org.save()

        for url in [self.org.uri + 'extras/', self.org.uri + 'extras/foo/']:
            self.assertEqual(self.request('get', url, self.outsider).status_code, 403)
            self.assertEqual(self.request('get', url, self.member).status_code, 200)
            self.assertEqual(self.request('get', url, self.staff).status_code, 200)


class UserExtrasAccessTest(AccessTestMixin, OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.owner.extras = {'foo': 'bar'}
        self.owner.save()

    def edit(self, url, user):
        return self.request('put', url, user, {'foo': 'changed'})

    def assert_extras(self, extras):
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.extras, extras)

    def test_other_users_cannot_edit_or_delete(self):
        url = self.owner.uri + 'extras/foo/'
        self.assertEqual(self.edit(url, self.outsider).status_code, 403)
        self.assertEqual(self.request('delete', url, self.outsider).status_code, 403)
        self.assertEqual(self.edit(url, None).status_code, 401)
        self.assert_extras({'foo': 'bar'})

    def test_owner_can_edit_and_delete(self):
        self.assertEqual(self.edit(self.owner.uri + 'extras/foo/', self.owner).status_code, 200)
        self.assert_extras({'foo': 'changed'})
        self.assertEqual(self.edit('/user/extras/foo/', self.owner).status_code, 200)
        self.assertEqual(self.request('delete', self.owner.uri + 'extras/foo/', self.owner).status_code, 204)
        self.assert_extras({})

    def test_staff_can_edit(self):
        self.assertEqual(self.edit(self.owner.uri + 'extras/foo/', self.staff).status_code, 200)
        self.assert_extras({'foo': 'changed'})


class UserLogoAccessTest(AccessTestMixin, OCLAPITestCase):
    def upload(self, user):
        with patch('core.services.storages.cloud.aws.S3.upload_base64') as upload_mock:
            upload_mock.return_value = f'users/{self.owner.username}/logo.png'
            return self.request('post', self.owner.uri + 'logo/', user, {'base64': 'base64-data'})

    def assert_logo_path(self, logo_path):
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.logo_path, logo_path)

    def test_other_users_cannot_upload(self):
        self.assertEqual(self.upload(self.outsider).status_code, 403)
        self.assertEqual(self.upload(None).status_code, 401)
        self.assert_logo_path(None)

    def test_owner_can_upload(self):
        self.assertEqual(self.upload(self.owner).status_code, 200)
        self.assert_logo_path(f'users/{self.owner.username}/logo.png')

    def test_staff_can_upload(self):
        self.assertEqual(self.upload(self.staff).status_code, 200)
        self.assert_logo_path(f'users/{self.owner.username}/logo.png')


class ClientConfigAccessTest(AccessTestMixin, OCLAPITestCase):
    """
    Adding a config to an org needs staff or membership, and to a repo staff or edit access. Changing a config needs
    staff or its creator.
    """
    payload = {'name': 'Home', 'type': 'home', 'config': {'tabs': [{'type': 'concepts', 'default': True}]}}

    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        self.collection = OrganizationCollectionFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)

    def create(self, resource, user):
        return self.request('post', resource.uri + 'client-configs/', user, self.payload)

    def assert_can_create(self, resource, user):
        response = self.create(resource, user)
        self.assertEqual(response.status_code, 201)
        return response.data['id']

    def assert_cannot_create(self, resource):
        self.assertEqual(self.create(resource, self.outsider).status_code, 403)
        self.assertEqual(self.create(resource, None).status_code, 401)
        self.assertFalse(resource.client_configs.exists())

    def test_create_on_org(self):
        self.assert_cannot_create(self.org)
        self.assert_can_create(self.org, self.member)
        self.assert_can_create(self.org, self.staff)

    def test_create_on_source(self):
        self.assert_cannot_create(self.source)
        self.assert_can_create(self.source, self.member)

    def test_create_on_collection(self):
        self.assert_cannot_create(self.collection)
        self.assert_can_create(self.collection, self.member)

    def test_edit(self):
        config_id = self.assert_can_create(self.org, self.member)
        url = f'/client-configs/{config_id}/'

        for user in [self.outsider, self.owner]:
            self.assertEqual(self.request('put', url, user, {'name': 'Renamed'}).status_code, 403)
            self.assertEqual(self.request('patch', url, user, {'name': 'Renamed'}).status_code, 403)
        self.assertEqual(self.request('put', url, None, {'name': 'Renamed'}).status_code, 401)
        self.assertEqual(ClientConfig.objects.get(id=config_id).name, 'Home')

        self.assertEqual(self.request('put', url, self.member, {'name': 'Renamed'}).status_code, 200)
        self.assertEqual(ClientConfig.objects.get(id=config_id).name, 'Renamed')
        self.assertEqual(self.request('put', url, self.staff, {'name': 'Renamed again'}).status_code, 200)
        self.assertEqual(ClientConfig.objects.get(id=config_id).name, 'Renamed again')


class PinAccessTest(AccessTestMixin, OCLAPITestCase):
    """
    Pins on a user's profile are managed by that user, and pins on an org by its members. Staff can manage any pin.
    """
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        self.private_source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_NONE)

    def pin(self, parent_url, user, resource=None, **extra):
        resource = resource or self.source
        return self.request(
            'post', parent_url + 'pins/', user, {'resource_type': 'Source', 'resource_id': resource.id, **extra})

    def test_pin_on_user(self):
        self.assertEqual(self.pin(self.owner.uri, self.outsider).status_code, 403)
        self.assertEqual(self.pin(self.owner.uri, None).status_code, 401)
        self.assertFalse(Pin.objects.filter(user=self.owner).exists())

        self.assertEqual(self.pin(self.owner.uri, self.owner).status_code, 201)
        self.assertEqual(self.pin('/user/', self.owner, self.org_source()).status_code, 201)
        self.assertEqual(Pin.objects.filter(user=self.owner).count(), 2)

    def test_pin_on_org(self):
        self.assertEqual(self.pin(self.org.uri, self.outsider).status_code, 403)
        self.assertFalse(Pin.objects.filter(organization=self.org).exists())

        self.assertEqual(self.pin(self.org.uri, self.member).status_code, 201)
        self.assertEqual(self.pin(self.org.uri, self.staff, self.org_source()).status_code, 201)
        self.assertEqual(Pin.objects.filter(organization=self.org).count(), 2)

    def test_pin_is_created_on_the_url_owner_only(self):
        other_org = OrganizationFactory()

        response = self.pin('/user/', self.outsider, organization_id=other_org.id)

        self.assertEqual(response.status_code, 201)
        self.assertFalse(Pin.objects.filter(organization=other_org).exists())
        self.assertTrue(Pin.objects.filter(user=self.outsider).exists())

    def test_cannot_pin_what_cannot_be_viewed(self):
        self.assertEqual(self.pin('/user/', self.outsider, self.private_source).status_code, 403)
        self.assertFalse(Pin.objects.filter(user=self.outsider).exists())
        self.assertEqual(self.pin(self.org.uri, self.member, self.private_source).status_code, 201)

    def test_edit_and_delete(self):
        pin = Pin.objects.get(id=self.pin(self.owner.uri, self.owner).data['id'])
        url = f'{self.owner.uri}pins/{pin.id}/'

        self.assertEqual(self.request('put', url, self.outsider, {'order': 1}).status_code, 403)
        self.assertEqual(
            self.request('patch', url, self.outsider, {'organization_id': self.org.id}).status_code, 403)
        self.assertEqual(self.request('delete', url, self.outsider).status_code, 403)
        self.assertEqual(self.request('delete', url).status_code, 401)
        self.assertTrue(Pin.objects.filter(id=pin.id, user=self.owner, organization__isnull=True).exists())

        self.assertEqual(self.request('patch', url, self.owner, {'organization_id': self.org.id}).status_code, 200)
        self.assertTrue(Pin.objects.filter(id=pin.id, user=self.owner, organization__isnull=True).exists())
        self.assertEqual(self.request('delete', url, self.owner).status_code, 204)
        self.assertFalse(Pin.objects.filter(id=pin.id).exists())

    def org_source(self):
        return OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)


class RepoAccessMixin(AccessTestMixin):
    """
    Changing a repo's extras, versions or latest release needs staff or edit access to the repo. Reading them, or its
    version list, needs view access.
    """
    model = None
    org_repo_factory = None
    user_repo_factory = None
    read_suffixes = ('extras/', 'extras/foo/')
    version_read_suffixes = ('extras/', )
    version_payload = {'id': 'v9', 'description': 'Version 9'}

    def create_repo(self, public_access=ACCESS_TYPE_VIEW, **kwargs):
        return self.org_repo_factory(  # pylint: disable=not-callable
            organization=self.org, public_access=public_access, **kwargs)

    def create_version(self, head, **kwargs):
        return self.org_repo_factory(  # pylint: disable=not-callable
            mnemonic=head.mnemonic, organization=self.org, version='v1', public_access=head.public_access, **kwargs)

    def version_exists(self, repo, version):
        return self.model.objects.filter(mnemonic=repo.mnemonic, version=version, is_active=True).filter(
            organization_id=repo.organization_id, user_id=repo.user_id).exists()

    def test_extras_writes(self):
        repo = self.create_repo(extras={'foo': 'bar'})
        url = repo.uri + 'extras/foo/'

        for user, status_code in [(self.outsider, 403), (None, 401)]:
            self.assertEqual(self.request('put', url, user, {'foo': 'changed'}).status_code, status_code)
            self.assertEqual(self.request('delete', url, user).status_code, status_code)
        repo.refresh_from_db()
        self.assertEqual(repo.extras, {'foo': 'bar'})

        self.assertEqual(self.request('put', url, self.member, {'foo': 'changed'}).status_code, 200)
        repo.refresh_from_db()
        self.assertEqual(repo.extras, {'foo': 'changed'})
        self.assertEqual(self.request('delete', url, self.staff).status_code, 204)
        repo.refresh_from_db()
        self.assertEqual(repo.extras, {})

    def test_owner_can_edit_extras(self):
        repo = self.user_repo_factory(  # pylint: disable=not-callable
            user=self.owner, public_access=ACCESS_TYPE_VIEW, extras={'foo': 'bar'})

        self.assertEqual(self.request('put', repo.uri + 'extras/foo/', self.outsider, {'foo': 'x'}).status_code, 403)
        self.assertEqual(self.request('put', repo.uri + 'extras/foo/', self.owner, {'foo': 'x'}).status_code, 200)

    def test_private_repo_reads(self):
        repo = self.create_repo(ACCESS_TYPE_NONE, extras={'foo': 'bar'})
        version = self.create_version(repo, extras={'foo': 'bar'})
        urls = [
            *[repo.uri + suffix for suffix in self.read_suffixes],
            *[version.uri + suffix for suffix in self.version_read_suffixes],
            repo.uri + 'versions/',
        ]

        for url in urls:
            self.assertEqual(self.request('get', url, self.outsider).status_code, 403, url)
            self.assertEqual(self.request('get', url).status_code, 401, url)
            self.assertEqual(self.request('get', url, self.member).status_code, 200, url)
            self.assertEqual(self.request('get', url, self.staff).status_code, 200, url)

    def test_public_repo_reads(self):
        repo = self.create_repo(extras={'foo': 'bar'})

        self.assertEqual(self.request('get', repo.uri + 'extras/').status_code, 200)
        self.assertEqual(self.request('get', repo.uri + 'versions/').status_code, 200)

    def test_version_create(self):
        repo = self.create_repo()
        url = repo.uri + 'versions/'

        self.assertEqual(self.request('post', url, self.outsider, self.version_payload).status_code, 403)
        self.assertEqual(self.request('post', url, None, self.version_payload).status_code, 401)
        self.assertFalse(self.version_exists(repo, 'v9'))

        self.assertEqual(self.request('post', url, self.member, self.version_payload).status_code, 201)
        self.assertTrue(self.version_exists(repo, 'v9'))

    def test_version_create_on_private_repo(self):
        repo = self.create_repo(ACCESS_TYPE_NONE)

        self.assertEqual(
            self.request('post', repo.uri + 'versions/', self.outsider, self.version_payload).status_code, 403)
        self.assertFalse(self.version_exists(repo, 'v9'))

    def test_version_create_by_owner_and_staff(self):
        repo = self.user_repo_factory(user=self.owner, public_access=ACCESS_TYPE_VIEW)  # pylint: disable=not-callable

        self.assertEqual(
            self.request('post', repo.uri + 'versions/', self.owner, self.version_payload).status_code, 201)
        self.assertEqual(
            self.request('post', repo.uri + 'versions/', self.staff, {'id': 'v10', 'description': 'v10'}).status_code,
            201
        )

    def test_latest_release_update(self):
        repo = self.create_repo()
        latest = self.create_version(repo, released=True)
        url = repo.uri + 'latest/'

        for user, status_code in [(self.outsider, 403), (None, 401)]:
            self.assertEqual(self.request('put', url, user, {'external_id': 'EXT'}).status_code, status_code)
        latest.refresh_from_db()
        self.assertIsNone(latest.external_id)
        self.assertEqual(self.request('get', url, self.outsider).status_code, 200)

        self.assertEqual(self.request('put', url, self.member, {'external_id': 'EXT'}).status_code, 200)
        latest.refresh_from_db()
        self.assertEqual(latest.external_id, 'EXT')


class SourceAccessTest(RepoAccessMixin, OCLAPITestCase):
    model = Source
    org_repo_factory = OrganizationSourceFactory
    user_repo_factory = UserSourceFactory
    read_suffixes = ('extras/', 'extras/foo/', 'properties/', 'filters/')
    version_read_suffixes = ('extras/', 'properties/', 'filters/')

    def setUp(self):
        super().setUp()
        # A new source version queues indexing of its concepts and mappings.
        for task in ['index_source_concepts', 'index_source_mappings']:
            patcher = patch(f'core.sources.models.{task}', Mock(__name__=task))
            patcher.start()
            self.addCleanup(patcher.stop)


class CollectionAccessTest(RepoAccessMixin, OCLAPITestCase):
    model = Collection
    org_repo_factory = OrganizationCollectionFactory
    user_repo_factory = UserCollectionFactory


class CollectionReferenceAccessTest(AccessTestMixin, OCLAPITestCase):
    """
    A reference is reached only through its own collection. Deleting it needs staff or edit access to the collection.
    """
    def setUp(self):
        super().setUp()
        self.collection = OrganizationCollectionFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        self.reference = CollectionReference(
            expression='/concepts/', collection=self.collection, reference_type='concepts')
        self.reference.save()
        self.outsider_collection = UserCollectionFactory(user=self.outsider, public_access=ACCESS_TYPE_VIEW)

    def get_url(self, collection):
        return f'{collection.uri}references/{self.reference.id}/'

    def reference_exists(self):
        return CollectionReference.objects.filter(id=self.reference.id).exists()

    def test_outsider_cannot_delete(self):
        self.assertEqual(self.request('delete', self.get_url(self.collection), self.outsider).status_code, 403)
        self.assertEqual(self.request('delete', self.get_url(self.outsider_collection), self.outsider).status_code, 404)
        self.assertEqual(self.request('delete', self.get_url(self.collection)).status_code, 401)
        self.assertTrue(self.reference_exists())

    def test_member_can_delete(self):
        self.assertEqual(self.request('delete', self.get_url(self.collection), self.member).status_code, 204)
        self.assertFalse(self.reference_exists())

    def test_reference_is_read_through_its_own_collection_only(self):
        self.assertEqual(self.request('get', self.get_url(self.collection), self.outsider).status_code, 200)

        for suffix in ['', 'concepts/', 'mappings/']:
            url = self.get_url(self.outsider_collection) + suffix
            self.assertEqual(self.request('get', url, self.outsider).status_code, 404, url)

    def test_private_collection_references_are_visible_to_members_only(self):
        private = OrganizationCollectionFactory(organization=self.org, public_access=ACCESS_TYPE_NONE)
        url = private.uri + 'HEAD/references/'

        self.assertEqual(self.request('get', url, self.outsider).status_code, 403)
        self.assertEqual(self.request('get', url, self.member).status_code, 200)


class SourceContentAccessTest(AccessTestMixin, OCLAPITestCase):
    """
    Adding concepts or mappings to a source, and changing a concept's names or descriptions, need staff or edit access
    to the source. Reading names and descriptions needs view access.
    """
    concept_payload = {
        'id': 'c1', 'datatype': 'Coded', 'concept_class': 'Procedure',
        'names': [{'locale': 'en', 'locale_preferred': True, 'name': 'c1 name', 'name_type': 'Fully Specified'}],
    }

    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        self.concept = self.create_concept(self.source)

    @staticmethod
    def create_concept(source):
        return ConceptFactory(
            parent=source,
            names=[ConceptNameFactory.build(locale='en', name='Original')],
            descriptions=[ConceptDescriptionFactory.build(locale='en', name='Original')],
        )

    def concept_exists(self, source):
        return Concept.objects.filter(parent=source, mnemonic='c1').exists()

    def test_concept_create(self):
        url = self.source.uri + 'concepts/'

        self.assertEqual(self.request('post', url, self.outsider, self.concept_payload).status_code, 403)
        self.assertEqual(self.request('post', url, None, self.concept_payload).status_code, 401)
        self.assertFalse(self.concept_exists(self.source))

        self.assertEqual(self.request('post', url, self.member, self.concept_payload).status_code, 201)
        self.assertTrue(self.concept_exists(self.source))

    def test_concept_create_by_owner(self):
        source = UserSourceFactory(user=self.owner, public_access=ACCESS_TYPE_VIEW)

        self.assertEqual(
            self.request('post', source.uri + 'concepts/', self.outsider, self.concept_payload).status_code, 403)
        self.assertEqual(
            self.request('post', source.uri + 'concepts/', self.owner, self.concept_payload).status_code, 201)
        self.assertTrue(self.concept_exists(source))

    def test_mapping_create(self):
        url = self.source.uri + 'mappings/'
        payload = {
            'map_type': 'Same As', 'from_concept_url': self.concept.uri,
            'to_concept_url': ConceptFactory(parent=self.source).uri
        }

        self.assertEqual(self.request('post', url, self.outsider, payload).status_code, 403)
        self.assertEqual(self.request('post', url, None, payload).status_code, 401)
        self.assertFalse(self.source.mappings.exists())

        self.assertEqual(self.request('post', url, self.member, payload).status_code, 201)
        self.assertTrue(self.source.mappings.exists())

    def test_name_and_description_edits(self):
        urls = [
            f'{self.concept.uri}names/{self.concept.names.first().id}/',
            f'{self.concept.uri}descriptions/{self.concept.descriptions.first().id}/',
        ]

        for url in urls:
            for user, status_code in [(self.outsider, 403), (None, 401)]:
                self.assertEqual(self.request('put', url, user, {'name': 'Changed'}).status_code, status_code, url)
                self.assertEqual(self.request('delete', url, user).status_code, status_code, url)
        self.assertEqual(self.concept.versions.count(), 1)

        self.assertEqual(self.request('put', urls[0], self.member, {'name': 'Changed'}).status_code, 200)
        self.assertEqual(self.concept.versions.count(), 2)

    def test_private_concept_names_and_descriptions_are_visible_to_members_only(self):
        concept = self.create_concept(OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_NONE))
        urls = [
            f'{concept.uri}names/{concept.names.first().id}/',
            f'{concept.uri}descriptions/{concept.descriptions.first().id}/',
        ]

        for url in urls:
            self.assertEqual(self.request('get', url, self.outsider).status_code, 403, url)
            self.assertEqual(self.request('get', url).status_code, 401, url)
            self.assertEqual(self.request('get', url, self.member).status_code, 200, url)


class HasOwnershipTest(OCLTestCase):
    """HasOwnership answers for users and orgs only. Anything else is refused, except to staff."""
    @staticmethod
    def check(user, obj):
        return HasOwnership().has_object_permission(Mock(user=user), None, obj)

    def setUp(self):
        super().setUp()
        self.staff = UserProfile.objects.get(username='ocladmin')
        self.org = OrganizationFactory()
        self.member = UserProfileFactory(organizations=[self.org])
        self.outsider = UserProfileFactory()

    def test_users_and_orgs(self):
        self.assertTrue(self.check(self.member, self.member))
        self.assertTrue(self.check(self.member, self.org))
        self.assertTrue(self.check(self.staff, self.org))
        self.assertFalse(self.check(self.outsider, self.member))
        self.assertFalse(self.check(self.outsider, self.org))

    def test_other_objects(self):
        source = OrganizationSourceFactory(organization=self.org)

        self.assertFalse(self.check(self.member, source))
        self.assertFalse(self.check(self.outsider, source))
        self.assertTrue(self.check(self.staff, source))


class PublicEditSourceAccessTest(AccessTestMixin, OCLAPITestCase):
    """
    Any logged-in user can add content to a public-Edit source. Changing the source's own settings needs staff, the
    owner or a member, for org sources as for user sources.
    """
    def test_anyone_logged_in_can_add_content(self):
        source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_EDIT)

        response = self.request(
            'post', source.uri + 'concepts/', self.outsider, SourceContentAccessTest.concept_payload)

        self.assertEqual(response.status_code, 201)

    def test_org_source_settings(self):
        source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_EDIT)

        self.assertEqual(self.request('put', source.uri, self.outsider, {'name': 'Renamed'}).status_code, 403)
        source.refresh_from_db()
        self.assertNotEqual(source.name, 'Renamed')

        self.assertEqual(self.request('put', source.uri, self.member, {'name': 'Renamed'}).status_code, 200)
        source.refresh_from_db()
        self.assertEqual(source.name, 'Renamed')

    def test_user_source_settings(self):
        source = UserSourceFactory(user=self.owner, public_access=ACCESS_TYPE_EDIT)

        self.assertEqual(self.request('put', source.uri, self.outsider, {'name': 'Renamed'}).status_code, 403)
        self.assertEqual(self.request('put', source.uri, self.owner, {'name': 'Renamed'}).status_code, 200)
