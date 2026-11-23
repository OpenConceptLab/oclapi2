import csv
import io
import itertools
import uuid
import zipfile

from mock import patch, Mock

from django.core.cache import cache
from django.test import tag, override_settings
from core.client_configs.models import ClientConfig
from core.collections.documents import CollectionDocument
from core.collections.models import Collection, CollectionReference
from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW, RETIRED_ACCESS_TYPE_EDIT, ROLE_ADMIN
from core.common.permissions import HasOwnership
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.common.views import BaseAPIView
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory, ConceptDescriptionFactory
from core.map_projects.tests.factories import MapProjectFactory, AutomatchRunFactory
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.documents import OrganizationDocument
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.pins.models import Pin
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.tasks.models import Task
from core.url_registry.factories import GlobalURLRegistryFactory, OrganizationURLRegistryFactory, UserURLRegistryFactory
from core.url_registry.models import URLRegistry
from core.users.documents import UserProfileDocument
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class AccessTestMixin:
    """
    Staff, an org with one member and one admin, the owner of user-owned resources, and an outsider: a logged-in user
    who belongs to no org and owns nothing under test.
    """
    def setUp(self):
        super().setUp()
        self.staff = UserProfile.objects.get(username='ocladmin')
        self.org = OrganizationFactory(public_access=ACCESS_TYPE_VIEW)
        self.member = UserProfileFactory(organizations=[self.org])
        self.admin = UserProfileFactory()
        self.org.set_member_role(self.admin, ROLE_ADMIN)
        self.owner = UserProfileFactory()
        self.outsider = UserProfileFactory()

    def request(self, method, url, user=None, data=None):
        kwargs = {'HTTP_AUTHORIZATION': 'Token ' + user.get_token()} if user else {}
        if method in ('get', 'head'):
            return getattr(self.client, method)(url, **kwargs)
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

    def test_member_cannot_edit(self):
        self.assert_not_renamed(self.rename(self.member), 403)
        self.assert_not_renamed(self.request('patch', self.org.uri, self.member, {'name': 'Renamed'}), 403)

    def test_member_cannot_delete(self):
        self.assertEqual(self.request('delete', self.org.uri + '?inline=true', self.member).status_code, 403)
        self.assertTrue(Organization.objects.filter(id=self.org.id).exists())

    def test_admin_can_edit(self):
        self.assert_renamed(self.rename(self.admin))

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

    def test_member_cannot_edit(self):
        self.assert_not_edited(self.edit(self.member), 403)

    def test_admin_can_edit(self):
        self.assert_edited(self.edit(self.admin))

    def test_staff_can_edit(self):
        self.assert_edited(self.edit(self.staff))

    def test_outsider_cannot_delete(self):
        self.assert_not_deleted(self.request('delete', self.get_url(), self.outsider), 403)

    def test_anonymous_cannot_delete(self):
        self.assert_not_deleted(self.request('delete', self.get_url()), 401)

    def test_member_cannot_delete(self):
        self.org.overview = {'text': 'Existing'}
        self.org.save()

        self.assertEqual(self.request('delete', self.get_url(), self.member).status_code, 403)
        self.org.refresh_from_db()
        self.assertEqual(self.org.overview, {'text': 'Existing'})

    def test_admin_and_staff_delete_clears_overview_only(self):
        for user in [self.admin, self.staff]:
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

    def test_member_cannot_upload(self):
        self.assert_not_uploaded(self.upload(self.member), 403)

    def test_admin_can_upload(self):
        self.assert_uploaded(self.upload(self.admin))

    def test_staff_can_upload(self):
        self.assert_uploaded(self.upload(self.staff))

    def test_delete_clears_logo_only(self):
        for user, status_code in [(None, 401), (self.outsider, 403), (self.member, 403)]:
            self.org.logo_path = f'orgs/{self.org.mnemonic}/logo.png'
            self.org.save()

            self.assertEqual(self.request('delete', self.org.uri + 'logo/', user).status_code, status_code)
            self.org.refresh_from_db()
            self.assertEqual(self.org.logo_path, f'orgs/{self.org.mnemonic}/logo.png')

        for user in [self.admin, self.staff]:
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


@tag('es')
class OrganizationSourceListAccessTest(
        OrganizationResourceSearchAccessMixin, OrganizationResourceListAccessMixin, OCLAPITestCase):
    resources = 'sources'
    document = SourceDocument

    def create_resource(self, public_access):
        return OrganizationSourceFactory(organization=self.org, public_access=public_access)


@tag('es')
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


class PrivateHeadVersionAccessMixin(AccessTestMixin):
    """A version is only as viewable as its HEAD: making the HEAD private hides the versions that still say View."""
    model = None
    org_repo_factory = None
    user_repo_factory = None
    # pylint: disable=not-callable
    version_suffixes = ('', 'concepts/', 'mappings/', 'summary/', 'export/')
    member_suffixes = ('', 'concepts/', 'mappings/', 'summary/')  # an export request would reach the export service

    def create_org_version(self, **head_kwargs):
        head = self.org_repo_factory(organization=self.org, public_access=ACCESS_TYPE_VIEW, **head_kwargs)
        version = self.org_repo_factory(
            mnemonic=head.mnemonic, organization=self.org, version='v1', public_access=ACCESS_TYPE_VIEW)
        self.make_private(head)
        return head, version

    def make_private(self, head):
        # Bypasses the source signal, as a version made before the signal covered it would be
        self.model.objects.filter(id=head.id).update(public_access=ACCESS_TYPE_NONE)

    def assert_hidden_from_non_members(self, url):
        self.assertEqual(self.request('get', url, self.outsider).status_code, 403, url)
        self.assertEqual(self.request('get', url).status_code, 401, url)

    def assert_visible_to_members(self, url):
        for user in [self.member, self.staff]:
            self.assertNotIn(self.request('get', url, user).status_code, [401, 403], url)

    def test_version_endpoints_are_hidden_when_head_is_private(self):
        _, version = self.create_org_version()

        for suffix in self.version_suffixes:
            self.assert_hidden_from_non_members(version.uri + suffix)

    def add_concept(self, head, version):
        raise NotImplementedError

    def test_cascade_on_version_is_hidden_when_head_is_private(self):
        head, version = self.create_org_version()
        concept = self.add_concept(head, version)
        url = f'{version.uri}concepts/{concept.mnemonic}/$cascade/'

        for user in [self.outsider, None]:
            self.assertIn(self.request('get', url, user).status_code, [401, 403, 404], url)
        self.assertEqual(self.request('get', url, self.member).status_code, 200)

    def test_cascade_on_version_of_public_head_stays_visible(self):
        head = self.org_repo_factory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        version = self.org_repo_factory(
            mnemonic=head.mnemonic, organization=self.org, version='v1', public_access=ACCESS_TYPE_VIEW)
        concept = self.add_concept(head, version)

        self.assertEqual(
            self.request('get', f'{version.uri}concepts/{concept.mnemonic}/$cascade/', self.outsider).status_code, 200)

    latest_content_suffixes = ('latest/concepts/', 'latest/mappings/')

    def test_latest_released_version_is_hidden_when_head_is_private(self):
        head, version = self.create_org_version()
        self.model.objects.filter(id=version.id).update(released=True)

        for suffix in ('latest/', 'latest/summary/', *self.latest_content_suffixes):
            self.assert_hidden_from_non_members(head.uri + suffix)

    def test_latest_released_version_content_is_visible_to_members_when_head_is_private(self):
        head, version = self.create_org_version()
        concept = self.add_concept(head, version)
        self.model.objects.filter(id=version.id).update(released=True)

        for suffix in self.latest_content_suffixes:
            self.assert_visible_to_members(head.uri + suffix)
        response = self.request('get', head.uri + 'latest/concepts/', self.member)
        self.assertEqual([item['id'] for item in response.data], [concept.mnemonic])

    def test_private_latest_released_version_of_public_head_stays_hidden(self):
        head = self.org_repo_factory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        self.org_repo_factory(
            mnemonic=head.mnemonic, organization=self.org, version='v1', released=True, public_access=ACCESS_TYPE_NONE)

        for suffix in self.latest_content_suffixes:
            self.assert_hidden_from_non_members(head.uri + suffix)
            self.assert_visible_to_members(head.uri + suffix)

    def test_latest_released_version_of_public_head_stays_visible(self):
        head = self.org_repo_factory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        self.org_repo_factory(
            mnemonic=head.mnemonic, organization=self.org, version='v1', released=True, public_access=ACCESS_TYPE_VIEW)

        for suffix in self.latest_content_suffixes:
            self.assertEqual(self.request('get', head.uri + suffix, self.outsider).status_code, 200, suffix)
            self.assertEqual(self.request('get', head.uri + suffix).status_code, 200, suffix)

    def test_version_content_counts_are_hidden_when_head_is_private(self):
        head, version = self.create_org_version()
        self.add_concept(head, version)
        self.model.objects.filter(id=version.id).update(released=True)
        urls = [
            version.uri + 'concepts/', version.uri + 'mappings/', *(head.uri + s for s in self.latest_content_suffixes)]

        for url in urls:
            self.assertEqual(
                self.client.head(url, HTTP_AUTHORIZATION=f'Token {self.member.get_token()}').status_code, 200, url)
        for url in [*urls, *(url + '?q=c' for url in urls)]:
            self.assertEqual(
                self.client.head(url, HTTP_AUTHORIZATION=f'Token {self.outsider.get_token()}').status_code, 403, url)
            self.assertEqual(self.client.head(url).status_code, 401, url)
        for url in (version.uri + 'concepts/', head.uri + 'latest/concepts/'):
            response = self.client.head(url, HTTP_AUTHORIZATION=f'Token {self.member.get_token()}')
            self.assertEqual(response.headers['num_found'], '1', url)

    def test_latest_is_hidden_when_released_version_is_private_beside_a_version_named_latest(self):
        head = self.org_repo_factory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        self.org_repo_factory(
            mnemonic=head.mnemonic, organization=self.org, version='latest', public_access=ACCESS_TYPE_VIEW)
        self.org_repo_factory(
            mnemonic=head.mnemonic, organization=self.org, version='v1', released=True, public_access=ACCESS_TYPE_NONE)

        for suffix in self.latest_content_suffixes:
            self.assert_hidden_from_non_members(head.uri + suffix)

    def test_latest_released_version_of_user_owned_private_head_is_visible_to_owner_only(self):
        head = self.user_repo_factory(user=self.owner, public_access=ACCESS_TYPE_NONE)
        self.user_repo_factory(
            mnemonic=head.mnemonic, user=self.owner, version='v1', released=True, public_access=ACCESS_TYPE_NONE)

        for suffix in self.latest_content_suffixes:
            self.assert_hidden_from_non_members(head.uri + suffix)
            self.assertEqual(self.request('get', head.uri + suffix, self.owner).status_code, 200, suffix)

    def test_version_endpoints_are_visible_to_members_when_head_is_private(self):
        _, version = self.create_org_version()

        for suffix in self.member_suffixes:
            self.assert_visible_to_members(version.uri + suffix)

    def test_version_of_user_owned_private_head_is_visible_to_owner_only(self):
        head = self.user_repo_factory(user=self.owner, public_access=ACCESS_TYPE_VIEW)
        version = self.user_repo_factory(
            mnemonic=head.mnemonic, user=self.owner, version='v1', public_access=ACCESS_TYPE_VIEW)
        self.make_private(head)

        for suffix in self.version_suffixes:
            self.assert_hidden_from_non_members(version.uri + suffix)
        for suffix in self.member_suffixes:
            self.assertNotIn(self.request('get', version.uri + suffix, self.owner).status_code, [401, 403])

    def test_version_of_public_head_stays_visible(self):
        head = self.org_repo_factory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        version = self.org_repo_factory(
            mnemonic=head.mnemonic, organization=self.org, version='v1', public_access=ACCESS_TYPE_VIEW)

        for suffix in ('', 'concepts/', 'mappings/', 'summary/'):
            self.assertEqual(self.request('get', version.uri + suffix, self.outsider).status_code, 200, suffix)
            self.assertEqual(self.request('get', version.uri + suffix).status_code, 200, suffix)

    def test_private_version_of_public_head_stays_hidden(self):
        head = self.org_repo_factory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        version = self.org_repo_factory(
            mnemonic=head.mnemonic, organization=self.org, version='v1', public_access=ACCESS_TYPE_NONE)

        self.assert_hidden_from_non_members(version.uri)


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


class OrganizationMemberListAccessMixin(AccessTestMixin):
    """A private org's members list, at /orgs/<org>/members/ and /orgs/<org>/users/."""
    def setUp(self):
        super().setUp()
        self.private_org = OrganizationFactory(public_access=ACCESS_TYPE_NONE)
        self.member.organizations.add(self.private_org)
        self.urls = [f'/orgs/{self.private_org.mnemonic}/members/', f'/orgs/{self.private_org.mnemonic}/users/']


class OrganizationMemberListAccessTest(OrganizationMemberListAccessMixin, OCLAPITestCase):
    """A private org's members list and its count answer the same."""
    def test_members_list_and_count_are_hidden_from_non_members(self):
        for url, method, user in itertools.product(self.urls, ('get', 'head'), (None, self.outsider)):
            self.assertEqual(self.request(method, url, user).status_code, 403, (url, method, user))

    def test_members_count_answers_as_the_list(self):
        for url, user in itertools.product(self.urls, (self.member, self.staff)):
            response = self.request('get', url, user)
            head_response = self.request('head', url, user)
            self.assertEqual(head_response.status_code, 200, (url, user))
            self.assertEqual(head_response['num_found'], response['num_found'], (url, user))
            self.assertEqual(head_response['num_found'], str(len(response.data)), (url, user))

    def test_missing_org_members_answer_404(self):
        for method in ('get', 'head'):
            self.assertEqual(self.request(method, '/orgs/NoSuchOrg/members/').status_code, 404, method)


@tag('es')
class OrganizationMemberSearchAccessTest(OrganizationMemberListAccessMixin, OCLAPITestCase):
    """A private org's members list, searched or sorted, and its count, answer the same."""
    def setUp(self):
        super().setUp()
        UserProfileDocument().update(UserProfile.objects.all(), refresh=True)
        self.queries = ['?sortAsc=username', f'?q={self.member.username}']

    def test_member_searches_and_counts_are_hidden_from_non_members(self):
        for url, query, method in itertools.product(self.urls, self.queries, ('get', 'head')):
            for user in (None, self.outsider):
                self.assertEqual(self.request(method, url + query, user).status_code, 403, (url + query, method, user))

    def test_member_searches_and_counts_answer_members(self):
        for url, query in itertools.product(self.urls, self.queries):
            response = self.request('get', url + query, self.member)
            self.assertEqual(response.status_code, 200, url + query)
            self.assertIn(self.member.username, [user['username'] for user in response.data], url + query)
            head_response = self.request('head', url + query, self.member)
            self.assertEqual(head_response.status_code, 200, url + query)
            self.assertEqual(head_response['num_found'], response['num_found'], url + query)


class CsvExportAccessTest(AccessTestMixin, OCLAPITestCase):
    """A ?csv=true export holds only rows the requester can list, and one requester's export is never served to
    another."""
    def setUp(self):
        super().setUp()
        self.exports = {}
        export_service = Mock(upload_file=self.upload, url_for=lambda key: 'https://exports.test/' + key)
        patcher = patch('core.common.utils.get_export_service', return_value=export_service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def upload(self, key, file_path=None, **kwargs):  # pylint: disable=unused-argument
        with open(file_path, 'rb') as file:
            self.exports[key] = file.read()

    def export(self, url, user, column):
        """The `column` values of the CSV export at `url` for `user`."""
        response = self.request('get', url, user)
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(self.exports[response.data['url'].removeprefix('https://exports.test/')])) \
                as zip_file, zip_file.open(zip_file.namelist()[0]) as csv_file:
            return sorted(row[column] for row in csv.DictReader(io.TextIOWrapper(csv_file, 'utf-8-sig')))

    def test_org_list(self):
        private_org = OrganizationFactory(public_access=ACCESS_TYPE_NONE)

        self.assertIn(private_org.mnemonic, self.export('/orgs/?csv=true', self.staff, 'mnemonic'))
        for url in ['/orgs/?csv=true', '/orgs/?csv=true&type=Organization']:
            mnemonics = self.export(url, None, 'mnemonic')
            self.assertIn(self.org.mnemonic, mnemonics)
            self.assertNotIn(private_org.mnemonic, mnemonics)

    def test_task_list(self):
        Task.objects.create(name='csv-owner-task', created_by=self.owner)
        url = self.owner.uri + 'tasks/?csv=true'

        self.assertEqual(self.export(url, self.owner, 'name'), ['csv-owner-task'])
        self.assertEqual(self.export(url, self.outsider, 'name'), [])

    def test_task_list_search_without_type(self):
        # a list that applies q itself exports all its matches, not a search's first 100
        for index in range(101):
            Task.objects.create(name=f'csv-import-{index}', created_by=self.owner)
        Task.objects.create(name='csv-other', created_by=self.owner)

        names = self.export(self.owner.uri + 'tasks/?csv=true&q=csv-import', self.owner, 'name')

        self.assertEqual(len(names), 101)
        self.assertNotIn('csv-other', names)

    def test_map_project_list(self):
        MapProjectFactory(user=self.owner, organization=None, public_access=ACCESS_TYPE_NONE, name='csv-project')
        MapProjectFactory(user=self.owner, organization=None, public_access=ACCESS_TYPE_VIEW, name='csv-public-project')
        MapProjectFactory(
            user=self.outsider, organization=None, public_access=ACCESS_TYPE_VIEW, name='csv-other-project')

        for url in [self.owner.uri + 'map-projects/?csv=true&verbose=true', self.owner.uri + 'map-projects/?csv=true',
                    '/user/map-projects/?csv=true']:
            self.assertEqual(self.export(url, self.owner, 'name'), ['csv-project', 'csv-public-project'], url)
        for url in [self.owner.uri + 'map-projects/?csv=true&verbose=true', self.owner.uri + 'map-projects/?csv=true']:
            self.assertEqual(self.export(url, None, 'name'), ['csv-public-project'], url)
        self.assertNotIn('matches', self.export_columns(self.owner.uri + 'map-projects/?csv=true', self.owner))
        verbose_url = self.owner.uri + 'map-projects/?csv=true&verbose=true'
        self.assertIn('matches', self.export_columns(verbose_url, self.owner))

    @tag('es')
    def test_search_without_type(self):
        private_org = OrganizationFactory(public_access=ACCESS_TYPE_NONE, mnemonic=self.org.mnemonic + '-private')
        unmatched_org = OrganizationFactory(public_access=ACCESS_TYPE_VIEW, mnemonic='csv-unmatched')
        OrganizationDocument().update([self.org, private_org, unmatched_org], refresh=True)

        mnemonics = self.export(f'/orgs/?csv=true&q={self.org.mnemonic}', None, 'mnemonic')

        self.assertIn(self.org.mnemonic, mnemonics)
        self.assertNotIn(private_org.mnemonic, mnemonics)
        # the export is the search's, not the whole list
        self.assertNotIn(unmatched_org.mnemonic, mnemonics)

    def export_columns(self, url, user):
        response = self.request('get', url, user)
        with zipfile.ZipFile(io.BytesIO(self.exports[response.data['url'].removeprefix('https://exports.test/')])) \
                as zip_file, zip_file.open(zip_file.namelist()[0]) as csv_file:
            return csv.DictReader(io.TextIOWrapper(csv_file, 'utf-8-sig')).fieldnames

    def test_repo_concepts(self):
        source = UserSourceFactory(user=self.owner, public_access=ACCESS_TYPE_NONE)
        concept = ConceptFactory(parent=source, mnemonic='csv-concept')
        url = source.concepts_url + '?csv=true&limit=1000'

        self.assertEqual(self.export(url, self.owner, 'mnemonic'), [concept.mnemonic])
        self.assertEqual(self.request('get', url, self.outsider).status_code, 403)

    def test_auto_match_run_list_checks_the_project(self):
        project = MapProjectFactory(user=self.owner, organization=None, public_access=ACCESS_TYPE_NONE)
        run = AutomatchRunFactory(map_project=project)
        url = f'{self.owner.uri}map-projects/{project.id}/auto-match-runs/?csv=true'

        self.assertEqual(self.export(url, self.owner, 'id'), [str(run.id)])
        self.assertEqual(self.request('get', url, self.outsider).status_code, 403)
        self.assertEqual(self.request('get', url, None).status_code, 401)


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

    def test_member_cannot_edit_or_delete(self):
        self.assertEqual(self.edit(self.member).status_code, 403)
        self.assertEqual(self.delete(self.member).status_code, 403)
        self.assert_extras({'foo': 'bar'})

    def test_admin_can_edit_and_delete(self):
        self.assertEqual(self.edit(self.admin).status_code, 200)
        self.assert_extras({'foo': 'changed'})
        self.assertEqual(self.delete(self.admin).status_code, 204)
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
    Adding a config to an org needs staff or an admin of it, and to a repo staff or edit access. Changing a config
    needs staff or its creator.
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
        self.assertEqual(self.create(self.org, self.member).status_code, 403)
        self.assertFalse(self.org.client_configs.exists())
        self.assert_can_create(self.org, self.admin)
        self.assert_can_create(self.org, self.staff)

    def test_create_on_source(self):
        self.assert_cannot_create(self.source)
        self.assert_can_create(self.source, self.member)

    def test_create_on_collection(self):
        self.assert_cannot_create(self.collection)
        self.assert_can_create(self.collection, self.member)

    def test_edit(self):
        config_id = self.assert_can_create(self.org, self.admin)
        url = f'/client-configs/{config_id}/'

        for user in [self.outsider, self.owner, self.member]:
            self.assertEqual(self.request('put', url, user, {'name': 'Renamed'}).status_code, 403)
            self.assertEqual(self.request('patch', url, user, {'name': 'Renamed'}).status_code, 403)
        self.assertEqual(self.request('put', url, None, {'name': 'Renamed'}).status_code, 401)
        self.assertEqual(ClientConfig.objects.get(id=config_id).name, 'Home')

        self.assertEqual(self.request('put', url, self.admin, {'name': 'Renamed'}).status_code, 200)
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

    def create_released_repo(self, org, public_access):
        head = self.org_repo_factory(organization=org, public_access=public_access)  # pylint: disable=not-callable
        version = self.org_repo_factory(  # pylint: disable=not-callable
            mnemonic=head.mnemonic, organization=org, version='v1', released=True, public_access=public_access)
        return head, version

    def get_versions_url(self, *repos):
        """One versions list for all `repos`, its owner and repository segments separated by commas."""
        owners = ','.join(repo.organization.mnemonic for repo in repos)
        names = ','.join(repo.mnemonic for repo in repos)
        return f'/orgs/{owners}/{self.model.get_resource_url_kwarg()}s/{names}/versions/'

    def assert_versions_listed(self, url, user, versions):
        response = self.request('get', url, user)
        self.assertEqual(response.status_code, 200, url)
        self.assertEqual(sorted(item['version_url'] for item in response.data), sorted(v.uri for v in versions), url)
        response = self.request('head', url, user)
        self.assertEqual(response.status_code, 200, url)
        self.assertEqual(response['num_found'], str(len(versions)), url)

    def test_versions_list_of_several_repos_is_hidden_unless_each_is_viewable(self):
        private_repo, _ = self.create_released_repo(self.org, ACCESS_TYPE_NONE)
        other_org = OrganizationFactory()
        other_member = UserProfileFactory(organizations=[other_org])
        other_repo, _ = self.create_released_repo(other_org, ACCESS_TYPE_NONE)  # the newest versions listed
        public_repo, _ = self.create_released_repo(OrganizationFactory(), ACCESS_TYPE_VIEW)
        with_other_repo = self.get_versions_url(private_repo, other_repo)
        with_public_repo = self.get_versions_url(private_repo, public_repo)

        for url, user, status_code in [
                (with_other_repo, other_member, 403), (with_other_repo, None, 401),
                (with_public_repo, self.outsider, 403), (with_public_repo, other_member, 403),
                (with_public_repo, None, 401), (f'{with_public_repo}?verbose=true', None, 401)
        ]:
            for method in ('get', 'head'):
                self.assertEqual(self.request(method, url, user).status_code, status_code, (method, url, user))

    def test_versions_list_of_several_viewable_repos_lists_them_all(self):
        private_versions = self.create_released_repo(self.org, ACCESS_TYPE_NONE)
        other_org = OrganizationFactory()
        other_versions = self.create_released_repo(other_org, ACCESS_TYPE_NONE)
        public_versions = self.create_released_repo(OrganizationFactory(), ACCESS_TYPE_VIEW)
        public_versions2 = self.create_released_repo(OrganizationFactory(), ACCESS_TYPE_VIEW)
        member_of_both = UserProfileFactory(organizations=[self.org, other_org])

        self.assert_versions_listed(
            self.get_versions_url(private_versions[0], other_versions[0]), member_of_both,
            [*private_versions, *other_versions])
        self.assert_versions_listed(
            self.get_versions_url(private_versions[0], public_versions[0]), self.member,
            [*private_versions, *public_versions])
        self.assert_versions_listed(
            self.get_versions_url(public_versions[0], public_versions2[0]), None, [*public_versions, *public_versions2])

    def test_versions_list_count_answers_as_the_list(self):
        private_versions = self.create_released_repo(self.org, ACCESS_TYPE_NONE)
        public_versions = self.create_released_repo(self.org, ACCESS_TYPE_VIEW)
        url = private_versions[0].uri + 'versions/'

        self.assertEqual(self.request('head', url, self.outsider).status_code, 403)
        self.assertEqual(self.request('head', url).status_code, 401)
        self.assert_versions_listed(url, self.member, private_versions)
        self.assert_versions_listed(url, self.staff, private_versions)
        self.assert_versions_listed(public_versions[0].uri + 'versions/', None, public_versions)

    def test_versions_list_of_repo_without_head_answers_as_its_versions(self):
        head, version = self.create_released_repo(self.org, ACCESS_TYPE_VIEW)
        self.model.objects.filter(id=version.id).update(public_access=ACCESS_TYPE_NONE)
        newer_version = self.org_repo_factory(  # pylint: disable=not-callable
            mnemonic=head.mnemonic, organization=self.org, version='v2', public_access=ACCESS_TYPE_VIEW)
        self.model.objects.filter(id=head.id).update(is_active=False)
        url = head.uri + 'versions/'

        for method in ('get', 'head'):
            self.assertEqual(self.request(method, url, self.outsider).status_code, 403, method)
            self.assertEqual(self.request(method, url).status_code, 401, method)
        self.assert_versions_listed(url, self.member, [version, newer_version])

    def test_versions_list_check_covers_every_repo_named_whatever_the_filters(self):
        private_repo = self.org_repo_factory(  # pylint: disable=not-callable
            organization=self.org, public_access=ACCESS_TYPE_NONE)  # no release
        public_versions = self.create_released_repo(OrganizationFactory(), ACCESS_TYPE_VIEW)
        urls = [private_repo.uri + 'versions/', self.get_versions_url(private_repo, public_versions[0])]

        for url in urls:
            for query in ['?released=true', '?released=false', '?processing=true']:
                for method in ('get', 'head'):
                    self.assertEqual(self.request(method, url + query, self.outsider).status_code, 403, url + query)
                    self.assertEqual(self.request(method, url + query).status_code, 401, url + query)
        response = self.request('get', urls[1] + '?released=true', self.member)
        self.assertEqual([item['version_url'] for item in response.data], [public_versions[1].uri])

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

    def test_resolved_repo_versions_are_those_the_requester_can_view(self):
        private_source = UserSourceFactory(user=self.owner, public_access=ACCESS_TYPE_NONE)
        public_source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        for source in [private_source, public_source]:
            CollectionReference(
                expression=f'{source.uri}concepts/foo/', collection=self.collection, system=source.uri, code='foo'
            ).save()
        url = self.collection.uri + 'HEAD/references/?includeResolvedRepoVersions=true'

        def get_resolved_urls(user):
            response = self.request('get', url, user)
            self.assertEqual(response.status_code, 200)
            return sorted(
                version['version_url']
                for reference in response.data for version in reference['resolved_repo_versions'])

        for user in [None, self.outsider, self.member]:
            self.assertEqual(get_resolved_urls(user), [public_source.uri], user)
        for user in [self.owner, self.staff]:
            self.assertEqual(get_resolved_urls(user), sorted([private_source.uri, public_source.uri]), user)


class CollectionReferenceResourcesAccessMixin(AccessTestMixin):
    """
    A private collection's HEAD and released v1, each with a reference and an expansion holding two concepts and a
    mapping of a public source.
    """
    reference_list_queries = ('', '?q=refc', '?facetsOnly=true', '?sortAsc=id')

    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory(public_access=ACCESS_TYPE_VIEW)
        self.concepts = [ConceptFactory(parent=self.source, mnemonic=f'refc{i}') for i in (1, 2)]
        self.mapping = MappingFactory(parent=self.source, from_concept=self.concepts[0], to_concept=self.concepts[1])
        self.collection = OrganizationCollectionFactory(organization=self.org, public_access=ACCESS_TYPE_NONE)
        self.version = OrganizationCollectionFactory(
            organization=self.org, mnemonic=self.collection.mnemonic, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        self.reference_list_urls = []
        for collection_version in [self.collection, self.version]:
            reference = CollectionReference(expression=self.source.uri + 'concepts/', collection=collection_version)
            reference.save()
            reference.concepts.set(self.concepts)
            reference.mappings.set([self.mapping])
            expansion = ExpansionFactory(collection_version=collection_version)
            expansion.concepts.set(self.concepts)
            expansion.mappings.set([self.mapping])
            collection_version.expansion_uri = expansion.uri
            collection_version.save()
            self.reference_list_urls += [
                f'{collection_version.uri}references/{reference.id}/{resources}/'
                for resources in ['concepts', 'mappings']
            ]


class CollectionReferenceResourcesAccessTest(CollectionReferenceResourcesAccessMixin, OCLAPITestCase):
    """A reference's concept and mapping lists, searched, sorted, faceted or counted, answer as the plain list."""
    def test_reference_lists_are_hidden_from_non_members(self):
        for url in self.reference_list_urls:
            for query in self.reference_list_queries:
                for method in ('get', 'head'):
                    self.assertEqual(self.request(method, url + query, self.outsider).status_code, 403, url + query)
                    self.assertEqual(self.request(method, url + query).status_code, 401, url + query)

    def test_reference_lists_are_visible_to_members(self):
        for url in self.reference_list_urls:
            count = 2 if url.endswith('/concepts/') else 1
            for user in [self.member, self.staff]:
                response = self.request('get', url, user)
                self.assertEqual(response.status_code, 200, url)
                self.assertEqual(len(response.data), count, url)
                response = self.request('head', url, user)
                self.assertEqual(response.status_code, 200, url)
                self.assertEqual(response['num_found'], str(count), url)

    def test_missing_reference_lists_answer_404(self):
        for collection_version in [self.collection, self.version]:
            for resources in ['concepts', 'mappings']:
                url = f'{collection_version.uri}references/0/{resources}/'
                for query in self.reference_list_queries:
                    for method in ('get', 'head'):
                        self.assertEqual(self.request(method, url + query, self.member).status_code, 404, url + query)
                        self.assertEqual(self.request(method, url + query).status_code, 401, url + query)

    def test_reference_lists_with_an_invalid_id_answer_404(self):
        for resources, reference_id in itertools.product(['concepts', 'mappings'], ['abc', '9' * 30, '9' * 5000]):
            url = f'{self.version.uri}references/{reference_id}/{resources}/'
            for query in self.reference_list_queries:
                for method in ('get', 'head'):
                    self.assertEqual(self.request(method, url + query, self.member).status_code, 404, url + query)
                    self.assertEqual(self.request(method, url + query, self.outsider).status_code, 403, url + query)

    def test_reference_lists_with_another_spelling_of_an_id_answer_404(self):
        reference_id = str(self.version.references.get().id)
        full_width = ''.join(chr(ord(digit) + 0xFEE0) for digit in reference_id)
        spellings = [f'0_{reference_id}', f'+{reference_id}', full_width]
        for resources, spelling in itertools.product(['concepts', 'mappings'], spellings):
            url = f'{self.version.uri}references/{spelling}/{resources}/'
            for method in ('get', 'head'):
                self.assertEqual(self.request(method, url, self.member).status_code, 404, (method, url))
        self.assertEqual(
            self.request('get', f'{self.version.uri}references/{reference_id}/concepts/', self.member).status_code, 200)

    def test_version_reference_count_answers_as_the_list(self):
        url = self.version.uri + 'references/'

        for method in ('get', 'head'):
            self.assertEqual(self.request(method, url, self.outsider).status_code, 403, method)
            self.assertEqual(self.request(method, url).status_code, 401, method)
        for query in ['', '?q=concepts', '?q=nothing-matches']:
            response = self.request('get', url + query, self.member)
            head_response = self.request('head', url + query, self.member)
            self.assertEqual(head_response.status_code, 200, query)
            self.assertEqual(head_response['num_found'], response['num_found'], query)
            self.assertEqual(head_response['num_found'], str(len(response.data)), query)

    def test_missing_version_references_answer_404(self):
        url = self.collection.uri + 'v9/references/'

        for user in [None, self.outsider, self.member]:
            for method in ('get', 'head'):
                self.assertEqual(self.request(method, url, user).status_code, 404, (method, user))


@tag('es')
class CollectionReferenceResourcesSearchAccessTest(CollectionReferenceResourcesAccessMixin, OCLAPITestCase):
    """A reference's concept and mapping lists, searched, sorted or faceted, stay open to members."""
    def test_reference_list_searches_are_visible_to_members(self):
        ConceptDocument().update(Concept.objects.filter(parent=self.source), refresh=True)
        MappingDocument().update(Mapping.objects.filter(parent=self.source), refresh=True)

        for url in self.reference_list_urls:
            for query in self.reference_list_queries[1:]:
                self.assertEqual(self.request('get', url + query, self.member).status_code, 200, url + query)
            self.assertEqual(self.request('head', url + '?q=refc', self.member).status_code, 200, url)
            if url.endswith('/concepts/'):
                response = self.request('get', url + '?q=refc1', self.member)
                self.assertIn('refc1', [item['id'] for item in response.data], url)
                response = self.request('get', url + '?facetsOnly=true', self.member)
                self.assertIn('facets', response.data, url)


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


class SourceVersionReadAccessMixin(AccessTestMixin):
    """
    The org's public source has a private released v1 holding two concepts and a mapping between them; HEAD has
    since moved one of the concepts on to a newer revision. The org's private source has a released v1 too.
    """
    def setUp(self):
        super().setUp()
        self.name = f'zebra{uuid.uuid4().hex}'  # what searches look for: only this test's concepts have it
        self.source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        self.release = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        self.concept = ConceptFactory(parent=self.source, names=[ConceptNameFactory.build(name=self.name)])
        other_concept = ConceptFactory(parent=self.source, names=[ConceptNameFactory.build(name='Other')])
        self.mapping = MappingFactory(
            parent=self.source, from_concept=self.concept, to_concept=other_concept, mnemonic=f'{self.name}map')
        self.private_source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_NONE)
        self.private_release = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.private_source.mnemonic, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        self.private_concept = ConceptFactory(
            parent=self.private_source, public_access=ACCESS_TYPE_NONE,
            names=[ConceptNameFactory.build(name=self.name)])
        for resource, version in [(self.concept, self.release), (other_concept, self.release),
                                  (self.mapping, self.release), (self.private_concept, self.private_release)]:
            self.add_to_version(resource, version)
        self.released_revision = self.concept.get_latest_version()
        response = self.request('put', self.concept.uri, self.member, {
            'datatype': 'Numeric', 'update_comment': 'Moved on',
            'names': [{'locale': 'en', 'locale_preferred': True, 'name': self.name, 'name_type': 'Fully Specified'}]})
        self.assertEqual(response.status_code, 200, response.data)

    @staticmethod
    def add_to_version(resource, version):
        resource.sources.add(version)
        resource.get_latest_version().sources.add(version)

    def assert_answers(self, url, anonymous, outsider, member, method='get'):
        for user, status_code in [(None, anonymous), (self.outsider, outsider), (self.member, member)]:
            kwargs = {'HTTP_AUTHORIZATION': 'Token ' + user.get_token()} if user else {}
            response = getattr(self.client, method)(url, **kwargs)
            self.assertEqual(response.status_code, status_code, (method, url, user))

    def version_urls(self):
        """The release, by name and as `latest`."""
        return [self.release.uri, self.source.uri + 'latest/']


class SourceVersionReadAccessTest(SourceVersionReadAccessMixin, OCLAPITestCase):
    def read_suffixes(self):
        concept, mapping = f'concepts/{self.concept.mnemonic}/', f'mappings/{self.mapping.mnemonic}/'
        revision = f'{concept}{self.released_revision.version}/'
        return [
            concept, revision, f'{revision}extras/', f'{concept}versions/', f'{concept}mappings/',
            f'{concept}children/', f'{concept}parents/', f'{concept}summary/', f'{concept}names/',
            f'{concept}collection-versions/', f'{concept}$cascade/', mapping,
            f'{mapping}{self.mapping.get_latest_version().version}/', f'{mapping}versions/', f'{mapping}extras/',
        ]

    def test_reads_under_a_private_release_answer_as_its_lists(self):
        for version_url in self.version_urls():
            for suffix in ['concepts/', 'mappings/', *self.read_suffixes()]:
                self.assert_answers(version_url + suffix, 401, 403, 200)
                self.assert_answers(version_url + suffix, 401, 403, 200, method='head')
                self.assertEqual(self.request('get', version_url + suffix, self.staff).status_code, 200)

    def test_reads_under_a_public_head_stay_visible(self):
        for suffix in ['concepts/', 'mappings/', *self.read_suffixes()]:
            self.assert_answers(self.source.uri + suffix, 200, 200, 200)

        response = self.request('get', f'{self.concept.uri}versions/')
        self.assertEqual(
            sorted(version['version'] for version in response.data),
            sorted(version.version for version in self.concept.versions))

    def test_reads_under_a_private_source_are_hidden(self):
        for version_url in [self.private_source.uri, self.private_release.uri, self.private_source.uri + 'latest/']:
            for suffix in ['', 'versions/', 'mappings/']:
                self.assert_answers(f'{version_url}concepts/{self.private_concept.mnemonic}/{suffix}', 401, 403, 200)

    def test_a_missing_source_or_version_is_not_found(self):
        concept, mapping = self.concept.mnemonic, self.mapping.mnemonic
        missing_version, with_missing = self.source.uri + 'v9/', self.source.uri[:-1] + ',NoSuchSource/'
        urls = [
            missing_version + 'concepts/', missing_version + 'mappings/', f'{missing_version}concepts/{concept}/',
            f'{missing_version}mappings/{mapping}/versions/', self.private_source.uri + 'v9/concepts/',
            with_missing + 'concepts/', with_missing + 'v1/concepts/', f'{with_missing}v1/concepts/{concept}/',
            f'{self.org.uri}sources/,/concepts/', f'{self.org.uri}sources/NoSuchSource/concepts/',
            f'{self.org.uri}sources/NoSuchSource/latest/concepts/',
        ]

        for url in urls:
            self.assert_answers(url, 404, 404, 404)
            self.assert_answers(url, 404, 404, 404, method='head')
            self.assertEqual(self.request('get', url, self.staff).status_code, 404, url)

    def test_sources_separated_by_commas_in_the_url_answer_as_each(self):
        public = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        public_release = OrganizationSourceFactory(
            organization=self.org, mnemonic=public.mnemonic, version='v1', released=True,
            public_access=ACCESS_TYPE_VIEW)
        public_concept = ConceptFactory(parent=public)
        self.add_to_version(public_concept, public_release)
        concept, mapping = f'concepts/{self.concept.mnemonic}/', f'mappings/{self.mapping.mnemonic}/'

        for sources in [f'{self.source.mnemonic},', f'{self.source.mnemonic},{public.mnemonic}']:
            url = f'{self.org.uri}sources/{sources}/'
            for suffix in ['v1/concepts/', 'v1/mappings/', f'v1/{concept}', 'latest/concepts/']:
                self.assert_answers(url + suffix, 401, 403, 200)
                self.assert_answers(url + suffix, 401, 403, 200, method='head')
            self.assert_answers(url + 'concepts/', 200, 200, 200)
            # the reads find no latest release of a list of sources, as before
            self.assert_answers(f'{url}latest/{mapping}', 401, 403, 404)
        response = self.request('get', f'{self.org.uri}sources/{self.source.mnemonic},{public.mnemonic}/v1/concepts/',
                                self.member)
        self.assertTrue(
            {self.concept.mnemonic, public_concept.mnemonic} <= {item['id'] for item in response.data}, response.data)

        url = f'{self.org.uri}sources/{public.mnemonic},/v1/concepts/'
        self.assert_answers(url, 200, 200, 200)
        self.assertEqual({item['id'] for item in self.request('get', url).data}, {public_concept.mnemonic})

    def test_latest_of_a_source_without_a_release_answers_as_its_head(self):
        source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        for user in [None, self.outsider]:
            response = self.request('get', source.uri + 'latest/concepts/', user)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, [])

        private_source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_NONE)
        self.assert_answers(private_source.uri + 'latest/concepts/', 401, 403, 200)

    def test_user_owned_source_routes(self):
        source = UserSourceFactory(user=self.owner, public_access=ACCESS_TYPE_VIEW)
        release = UserSourceFactory(
            user=self.owner, mnemonic=source.mnemonic, version='v1', released=True, public_access=ACCESS_TYPE_NONE)
        concept = ConceptFactory(parent=source)
        self.add_to_version(concept, release)
        url = f'{release.uri}concepts/{concept.mnemonic}/'
        own_url = f'/user/sources/{source.mnemonic}/v1/concepts/{concept.mnemonic}/'

        self.assertEqual(self.request('get', url).status_code, 401)
        self.assertEqual(self.request('get', url, self.outsider).status_code, 403)
        self.assertEqual(self.request('get', url, self.owner).status_code, 200)
        self.assertEqual(self.request('get', own_url, self.owner).status_code, 200)
        self.assertEqual(self.request('get', own_url, self.outsider).status_code, 404)
        self.assertEqual(self.request('get', concept.uri, self.outsider).status_code, 200)

    def test_a_version_named_in_the_query_string_answers_as_in_the_url(self):
        concept, mapping = f'concepts/{self.concept.mnemonic}/', f'mappings/{self.mapping.mnemonic}/'
        paths = [
            concept, f'HEAD/{concept}', f'HEAD/{mapping}', f'{concept}names/', f'{concept}mappings/',
            f'{concept}versions/', f'{concept}summary/', f'{concept}{self.released_revision.version}/', 'concepts/',
            'HEAD/concepts/', 'mappings/']
        for query in ['?version=v1', '?version=latest']:
            for path in paths:
                self.assert_answers(self.source.uri + path + query, 401, 403, 200)
                self.assert_answers(self.source.uri + path + query, 401, 403, 200, method='head')

        # the query string names the source and owner too
        other = OrganizationSourceFactory(public_access=ACCESS_TYPE_VIEW)
        self.assert_answers(
            f'{other.uri}HEAD/{concept}?org={self.org.mnemonic}&source={self.source.mnemonic}&version=v1',
            401, 403, 200)
        user_source = UserSourceFactory(user=self.owner, mnemonic=f'{self.name}u', public_access=ACCESS_TYPE_VIEW)
        user_release = UserSourceFactory(
            user=self.owner, mnemonic=user_source.mnemonic, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        user_concept = ConceptFactory(parent=user_source)
        self.add_to_version(user_concept, user_release)
        url = (f'{other.uri}HEAD/concepts/{user_concept.mnemonic}/?org=&user={self.owner.username}'
               f'&source={user_source.mnemonic}&version=v1')
        self.assertEqual(self.request('get', url).status_code, 401)
        self.assertEqual(self.request('get', url, self.outsider).status_code, 403)
        self.assertEqual(self.request('get', url, self.owner).status_code, 200)

        # the named owner's source doesn't have the version
        renamed = f'{self.org.uri}sources/{self.name}r/HEAD/{concept}?version=v1'
        for url in [f'{self.source.uri}{concept}?version=v9', renamed]:
            if url == renamed:  # a renamed source's versions keep the old mnemonic
                Source.objects.filter(id=self.source.id).update(mnemonic=f'{self.name}r')
            self.assert_answers(url, 404, 404, 404)
            self.assertEqual(self.request('get', url, self.staff).status_code, 404, url)

    def test_a_version_named_without_an_owner_leaves_out_those_the_requester_cannot_view(self):
        concept, mapping = f'concepts/{self.concept.mnemonic}/', f'mappings/{self.mapping.mnemonic}/'
        other = OrganizationSourceFactory(public_access=ACCESS_TYPE_VIEW)
        query = f'?org=&source={self.source.mnemonic}&version=v1'
        urls = [f'{other.uri}{concept}{query}', f'{other.uri}{concept}{self.released_revision.version}/{query}',
                f'{other.uri}{mapping}{query}']  # lists go to the search, which reads no version from the query string

        # only the org's source has v1, which only its members can view
        for url in urls:
            self.assert_answers(url, 404, 404, 200)
            self.assert_answers(url, 404, 404, 200, method='head')
            self.assertEqual(self.request('get', url, self.staff).status_code, 200, url)

        # another owner's source of that name has a public v1: the reads answer from it alone
        twin = OrganizationSourceFactory(mnemonic=self.source.mnemonic, public_access=ACCESS_TYPE_VIEW)
        twin_release = OrganizationSourceFactory(
            organization=twin.organization, mnemonic=twin.mnemonic, version='v1', released=True,
            public_access=ACCESS_TYPE_VIEW)
        twin_concept = ConceptFactory(parent=twin, mnemonic=self.concept.mnemonic)
        self.add_to_version(twin_concept, twin_release)
        for user in [None, self.outsider]:
            response = self.request('get', urls[0], user)
            self.assertEqual(response.status_code, 200, user)
            self.assertEqual(response.data['owner'], twin.organization.mnemonic, user)
            self.assertEqual(self.request('get', urls[2], user).status_code, 404, user)  # only the org's has it
        self.assertEqual(self.request('get', urls[0], self.member).status_code, 200)

        # each source named answers so
        twins = f'{other.uri}{concept}?org=&source={self.source.mnemonic},{self.private_source.mnemonic}&version=v1'
        self.assert_answers(twins, 404, 404, 200)
        # without an owner, `latest` resolves to no release, so there's nothing to read
        self.assert_answers(f'{other.uri}{concept}?org=&source={self.source.mnemonic}&version=latest', 404, 404, 404)

        # a renamed source's versions keep their old name, so another source given the new name can't reach them
        Source.objects.filter(id=self.source.id).update(mnemonic=f'{self.name}r')
        own = UserSourceFactory(user=self.outsider, mnemonic=f'{self.name}r', public_access=ACCESS_TYPE_VIEW)
        UserSourceFactory(
            user=self.outsider, mnemonic=own.mnemonic, version='v1', released=True, public_access=ACCESS_TYPE_VIEW)
        for url in [f'{own.uri}v1/{concept}?user=', f'{own.uri}v1/{mapping}?user=']:
            for user in [None, self.outsider]:
                self.assertEqual(self.request('get', url, user).status_code, 404, (url, user))
        response = self.client.head(f'{own.uri[:-1]},/v1/concepts/?user=')  # a list, read without a parent
        self.assertEqual((response.status_code, response.headers['num_found']), (200, '0'))

    def test_latest_in_the_query_string_is_the_newest_release_the_reads_use(self):
        OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='latest', public_access=ACCESS_TYPE_NONE)
        public_release = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v2', released=True,
            public_access=ACCESS_TYPE_VIEW)
        self.add_to_version(self.concept, public_release)

        self.assert_answers(f'{self.source.uri}HEAD/concepts/{self.concept.mnemonic}/?version=latest', 200, 200, 200)

    def test_a_collection_route_keeps_its_own_version(self):
        collection = OrganizationCollectionFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        collection_version = OrganizationCollectionFactory(
            organization=self.org, mnemonic=collection.mnemonic, version='v1', public_access=ACCESS_TYPE_VIEW)
        expansion = ExpansionFactory(collection_version=collection_version)
        expansion.concepts.add(self.released_revision)
        collection_version.expansion_uri = expansion.uri
        collection_version.save()

        url = f'{collection_version.uri}concepts/{self.concept.mnemonic}/$cascade/'  # the one under a source's view
        self.assert_answers(url, 200, 200, 200)
        self.assert_answers(f'{url}?source={self.source.mnemonic}', 200, 200, 200)

    def test_head_or_a_public_version_named_in_the_query_string_stays_visible(self):
        concept = f'concepts/{self.concept.mnemonic}/'
        public_version = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v0', public_access=ACCESS_TYPE_VIEW)
        self.add_to_version(self.concept, public_version)

        for path in [concept, f'HEAD/{concept}', 'concepts/', 'mappings/']:
            for query in ['?version=HEAD', '?version=v0', '?version=']:
                self.assert_answers(self.source.uri + path + query, 200, 200, 200)

    def test_concept_feeds_answer_only_under_public_versions(self):
        feed = f'concepts/{self.concept.mnemonic}/atom/'
        public_version = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v0', public_access=ACCESS_TYPE_VIEW)
        self.add_to_version(self.concept, public_version)

        for url in [*(version_url + feed for version_url in self.version_urls()), self.source.uri + 'v9/' + feed,
                    self.source.uri[:-1] + ',/v1/' + feed]:
            self.assertEqual(self.request('get', url).status_code, 404, url)
        for url in [self.source.uri + feed, public_version.uri + feed]:
            self.assertEqual(self.request('get', url).status_code, 200, url)
        # /sources/<source>/ names no owner, so no source of anyone's
        for version in ['', 'v0/']:
            self.assertEqual(self.request('get', f'/sources/{self.source.mnemonic}/{version}{feed}').status_code, 404)

    def test_inactive_versions_leave_latest_release_searches_as_they_are(self):
        def scoped():
            return {scope[0] for scope in Source.get_latest_release_scopes_for(None)}

        self.assertIn(self.source.mnemonic, scoped())
        Source.objects.filter(id=self.release.id).update(is_active=False)
        self.assertNotIn(self.source.mnemonic, scoped())

        source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        OrganizationSourceFactory(
            organization=self.org, mnemonic=source.mnemonic, version='v1', released=True,
            public_access=ACCESS_TYPE_NONE)
        self.assertIn(source.mnemonic, scoped())
        Source.objects.filter(id=source.id).update(is_active=False)
        self.assertNotIn(source.mnemonic, scoped())

    def test_concept_feeds_under_the_current_user_route_need_a_signed_in_user(self):
        # feeds are read signed out, so /user/ names no source of anyone's
        for version in ['', 'v1/', 'latest/']:
            url = f'/user/sources/{self.source.mnemonic}/{version}concepts/{self.concept.mnemonic}/atom/'
            self.assertEqual(self.client.get(url).status_code, 404, url)


@tag('es')
class SourceVersionSearchAccessTest(SourceVersionReadAccessMixin, OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.patch_concept_es_mapping_for_ci()
        self.index()

    def index(self):
        ConceptDocument().update(Concept.objects.filter(parent__organization=self.org), refresh=True)
        MappingDocument().update(Mapping.objects.filter(parent__organization=self.org), refresh=True)

    def search(self, url, user=None, **headers):
        if user:
            headers['HTTP_AUTHORIZATION'] = 'Token ' + user.get_token()
        response = self.client.get(url, **headers)
        self.assertEqual(response.status_code, 200, url)
        return response.data

    def hits_from(self, source, url, user=None, **headers):
        return [hit['id'] for hit in self.search(url, user, **headers)
                if hit['source'] == source.mnemonic and hit['owner'] == source.parent.mnemonic]

    @staticmethod
    def facet_values(data, field='source'):
        return [str(value[0]).lower() for value in data['facets']['fields'].get(field, [])]

    def test_searched_version_histories_answer_as_their_version(self):
        histories = [
            (version_url, f'concepts/{self.concept.mnemonic}/versions/') for version_url in self.version_urls()] + [
            (version_url, f'mappings/{self.mapping.mnemonic}/versions/') for version_url in self.version_urls()] + [
            (version_url, f'concepts/{self.private_concept.mnemonic}/versions/') for version_url in [
                self.private_source.uri, self.private_release.uri, self.private_source.uri + 'latest/']]

        for version_url, suffix in histories:
            url = version_url + suffix
            for query in [f'?q={self.name}', '?facetsOnly=true', '?sortAsc=id']:
                self.assert_answers(url + query, 401, 403, 200)
            self.assert_answers(f'{url}?q={self.name}', 401, 403, 200, method='head')

    def test_searches_naming_a_version_in_the_query_string_answer_as_in_the_url(self):
        other = OrganizationSourceFactory(public_access=ACCESS_TYPE_VIEW)
        source = self.source.mnemonic
        named = f'org={self.org.mnemonic}&source={source}&version=v1'
        for url in [f'/concepts/?{named}', f'/mappings/?{named}', f'{other.uri}concepts/?{named}',
                    f'{self.source.uri}concepts/?q={self.name}&version=v1']:
            self.assert_answers(url, 401, 403, 200)
            self.assert_answers(url, 401, 403, 200, method='head')
        # no owner: only the org's source has v1, which only its members can view
        for url in [f'/concepts/?source={source}&version=v1', f'/mappings/?source={source}&version=v1']:
            self.assert_answers(url, 404, 404, 200)
            self.assert_answers(url, 404, 404, 200, method='head')

        for url in [f'/concepts/?source={source}', f'/concepts/?org={self.org.mnemonic}&source={source}',
                    f'/concepts/?source={source}&version=HEAD', f'{self.source.uri}concepts/?q={self.name}']:
            self.assert_answers(url, 200, 200, 200)
            self.assert_answers(url, 200, 200, 200, method='head')

    def test_a_source_facet_under_a_public_release_stays_a_search(self):
        public_release = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v0', released=True,
            public_access=ACCESS_TYPE_VIEW)
        self.add_to_version(self.concept, public_release)
        self.index()

        for facet in [f'{self.source.mnemonic[:3]}*', 'NoSuchSource']:
            self.assert_answers(f'{public_release.uri}concepts/?source={facet}', 200, 200, 200)
            self.assert_answers(f'{public_release.uri}concepts/?source={facet}', 200, 200, 200, method='head')

    def test_fuzzy_search_checks_the_source_versions_it_names(self):
        public_source = OrganizationSourceFactory(public_access=ACCESS_TYPE_VIEW)
        public_release = OrganizationSourceFactory(
            organization=public_source.organization, mnemonic=public_source.mnemonic, version='v1', released=True,
            public_access=ACCESS_TYPE_VIEW)
        concept = ConceptFactory(parent=public_source, names=[ConceptNameFactory.build(name=self.name)])
        self.add_to_version(concept, public_release)
        ConceptDocument().update(Concept.objects.filter(parent=public_source), refresh=True)
        url = f'{public_source.uri}concepts/?fuzzy=true&q={self.name}&source_version='

        for version in [self.release, self.private_release]:
            self.assert_answers(url + version.uri, 401, 403, 200)
            self.assert_answers(url + version.uri, 401, 403, 200, method='head')
        self.assert_answers(f'{url}{self.release.uri},{public_release.uri}', 401, 403, 200)
        self.assertEqual(self.hits_from(public_source, url + public_release.uri, self.outsider), [concept.mnemonic])

        # expressions that don't name a source version are skipped: a collection's version, or one that can't be read
        release_member = ConceptFactory(parent=self.source, names=[ConceptNameFactory.build(name=self.name + 'c')])
        self.add_to_version(release_member, self.release)
        self.index()
        OrganizationCollectionFactory(
            organization=self.org, mnemonic=self.source.mnemonic, public_access=ACCESS_TYPE_VIEW)
        collection_version = OrganizationCollectionFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v1', public_access=ACCESS_TYPE_VIEW)
        for target in [collection_version.uri, 'http://example.org/cs|v1|v2']:
            self.assertEqual(self.hits_from(self.source, f'{url}{target},{public_release.uri}', self.outsider), [])
            self.assertEqual(
                self.hits_from(public_source, f'{url}{target},{public_release.uri}', self.outsider), [concept.mnemonic])
            self.assert_answers(url + target, 200, 200, 200, method='head')
        response = self.client.head(
            url + public_release.uri, HTTP_AUTHORIZATION='Token ' + self.outsider.get_token())
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(int(response.headers['num_found']), 1)

    def test_latest_release_search_leaves_out_releases_the_requester_cannot_view(self):
        latest = {'HTTP_INCLUDESEARCHLATEST': 'true'}
        source = self.source.mnemonic.lower()
        searches = [(f'/concepts/?q={self.name}', self.concept.mnemonic),
                    (f'/mappings/?q={self.mapping.mnemonic}', self.mapping.mnemonic)]

        for url, mnemonic in searches:
            for user in [None, self.outsider]:
                self.assertEqual(self.hits_from(self.source, url, user, **latest), [], (url, user))
                self.assertNotIn(source, self.facet_values(self.search(url + '&facetsOnly=true', user, **latest)))
                self.assertIn(mnemonic, self.hits_from(self.source, url, user), (url, user))  # HEAD's, as before
            for user in [self.member, self.staff]:
                self.assertIn(mnemonic, self.hits_from(self.source, url, user, **latest), (url, user))
                self.assertIn(source, self.facet_values(self.search(url + '&facetsOnly=true', user, **latest)))

    def test_latest_release_search_reads_its_scopes_once(self):
        with patch.object(
                Source, 'get_latest_release_scopes_for', wraps=Source.get_latest_release_scopes_for) as scopes:
            self.search(f'/concepts/?q={self.name}', self.outsider, HTTP_INCLUDESEARCHLATEST='true',
                        HTTP_INCLUDEFACETS='true')
        self.assertEqual(scopes.call_count, 1)

    def test_latest_release_search_keeps_to_the_latest_release_while_the_index_catches_up(self):
        url = f'/concepts/?q={self.name}'
        other_concept = ConceptFactory(parent=self.source, names=[ConceptNameFactory.build(name=self.name + 'b')])
        self.add_to_version(other_concept, self.release)
        self.index()
        public_release = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v2', released=True,
            public_access=ACCESS_TYPE_VIEW)
        self.add_to_version(other_concept, public_release)

        # the index still marks v1's members as the latest release's, and doesn't know v2's yet
        for user in [None, self.outsider]:
            self.assertEqual(self.hits_from(self.source, url, user, HTTP_INCLUDESEARCHLATEST='true'), [], user)

        self.index()
        for user in [None, self.outsider, self.member]:
            self.assertEqual(
                set(self.hits_from(self.source, url, user, HTTP_INCLUDESEARCHLATEST='true')),
                {other_concept.mnemonic}, user)

    def test_latest_release_search_keeps_public_releases(self):
        source = OrganizationSourceFactory(public_access=ACCESS_TYPE_VIEW)
        release = OrganizationSourceFactory(
            organization=source.organization, mnemonic=source.mnemonic, version='v1', released=True,
            public_access=ACCESS_TYPE_VIEW)
        concept = ConceptFactory(parent=source, names=[ConceptNameFactory.build(name=self.name)])
        self.add_to_version(concept, release)
        ConceptDocument().update(Concept.objects.filter(parent=source), refresh=True)

        for user in [None, self.outsider]:
            hits = self.hits_from(source, f'/concepts/?q={self.name}', user, HTTP_INCLUDESEARCHLATEST='true')
            self.assertTrue(hits, user)
            self.assertEqual(set(hits), {concept.mnemonic}, user)


@tag('es')
class QueryCollectionAccessTest(AccessTestMixin, OCLAPITestCase):
    """
    The org's public source holds the concepts and mappings. Its private collection P holds `inP` in HEAD and in v1;
    its public collection Q holds `inQhead` in HEAD and `inQv1` in a private v1; its public collection R holds
    `inRv1` in a public v1. A collection named in the query string counts only through versions the requester can
    view.
    """
    def setUp(self):
        super().setUp()
        self.patch_concept_es_mapping_for_ci()
        self.tag = f'qc{uuid.uuid4().hex[:6]}'
        self.source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        nowhere = ConceptFactory(parent=self.source, mnemonic=f'nowhere{self.tag}')
        self.concepts, self.mappings = {}, {}
        for key in ['inP', 'inQhead', 'inQv1', 'inRv1']:
            self.concepts[key] = ConceptFactory(
                parent=self.source, mnemonic=f'{key}{self.tag}', names=[ConceptNameFactory.build(name=self.tag)])
            self.mappings[key] = MappingFactory(
                parent=self.source, from_concept=self.concepts[key], to_concept=nowhere, mnemonic=f'm{key}{self.tag}')
        self.p_head, self.p_v1 = self.collection('P', ACCESS_TYPE_NONE, ACCESS_TYPE_NONE, ['inP'], ['inP'])
        self.q_head, self.q_v1 = self.collection('Q', ACCESS_TYPE_VIEW, ACCESS_TYPE_NONE, ['inQhead'], ['inQv1'])
        self.r_head, self.r_v1 = self.collection('R', ACCESS_TYPE_VIEW, ACCESS_TYPE_VIEW, [], ['inRv1'])
        ConceptDocument().update(Concept.objects.filter(parent=self.source), refresh=True)
        MappingDocument().update(Mapping.objects.filter(parent=self.source), refresh=True)

    def collection(self, name, head_access, version_access, head_keys, version_keys):
        """A collection's HEAD holding head_keys' resources by reference, and its released v1 holding version_keys'."""
        head = OrganizationCollectionFactory(
            organization=self.org, mnemonic=f'{self.tag}{name}', public_access=head_access)
        head.expansion_uri = ExpansionFactory(collection_version=head).uri
        head.save()
        if head_keys:
            head.add_expressions({'expressions': [self.concepts[key].uri for key in head_keys] + [
                self.mappings[key].uri for key in head_keys]}, self.member)
        version = OrganizationCollectionFactory(
            organization=self.org, mnemonic=head.mnemonic, version='v1', released=True, public_access=version_access)
        expansion = ExpansionFactory(collection_version=version)
        version.expansion_uri = expansion.uri
        version.save()
        for key in version_keys:
            expansion.concepts.add(self.concepts[key], self.concepts[key].get_latest_version())
            expansion.mappings.add(self.mappings[key], self.mappings[key].get_latest_version())
        return head, version

    def assert_answers(self, url, anonymous, outsider, member):
        for user, status_code in [(None, anonymous), (self.outsider, outsider), (self.member, member)]:
            self.assertEqual(self.request('get', url, user).status_code, status_code, (url, user))

    def keys(self, rows, resource):
        """The keys of this test's rows, as setUp names them."""
        prefix = 'm' if resource == 'mappings' else ''
        return sorted({row[0].removeprefix(prefix).removesuffix(self.tag) for row in rows if (
            row[0].endswith(self.tag) and row[0].startswith(prefix) and 'nowhere' not in row[0])})

    def listed(self, url, user, resource):
        response = self.request('get', url, user)
        self.assertEqual(response.status_code, 200, (url, user))
        return self.keys([(row['id'],) for row in response.data], resource)

    def assert_lists(self, query, outsiders, members, resources=('concepts', 'mappings')):
        """Signed out and an outsider list `outsiders`; a member and staff list `members`."""
        for resource in resources:
            url = f'/{resource}/?{query}'
            for user, expected in [(None, outsiders), (self.outsider, outsiders), (self.member, members),
                                   (self.staff, members)]:
                self.assertEqual(self.listed(url, user, resource), sorted(expected), (url, user))

    def test_collection_facets_count_only_versions_the_requester_can_view(self):
        prefix, owner_url = self.tag, self.org.uri
        self.assert_lists(f'collection={prefix}P', [], ['inP'])
        self.assert_lists(f'collection={prefix}Q', ['inQhead'], ['inQhead', 'inQv1'])
        self.assert_lists(f'collection={prefix}R', ['inRv1'], ['inRv1'])
        self.assert_lists(f'collection={prefix}P,{prefix}R', ['inRv1'], ['inP', 'inRv1'])
        self.assert_lists(f'q={prefix}&collection={prefix}Q', ['inQhead'], ['inQhead', 'inQv1'], ['concepts'])
        self.assert_lists(f'collection={prefix}*', ['inQhead', 'inRv1'], ['inP', 'inQhead', 'inQv1', 'inRv1'])
        # `*` alone: rewritten for outsiders; a member's matches every index entry, so narrowed to this test's
        for user in [None, self.outsider]:
            self.assertEqual(self.listed('/concepts/?collection=*', user, 'concepts'), ['inQhead', 'inRv1'], user)
        self.assert_lists(f'collection=*&collection_url={owner_url}collections/{prefix}*',
                          ['inQhead', 'inRv1'], ['inP', 'inQhead', 'inQv1', 'inRv1'])
        self.assert_lists(f'collection={prefix}@*', [], [])  # a literal prefix: `@` isn't a wildcard
        self.assert_lists(f'collection_url={self.q_v1.uri}', [], ['inQv1'])
        self.assert_lists(f'collection_url={self.r_v1.uri}', ['inRv1'], ['inRv1'])
        self.assert_lists(f'collection_url={owner_url}collections/{prefix}*&collection={prefix}*',
                          ['inQhead', 'inRv1'], ['inP', 'inQhead', 'inQv1', 'inRv1'])
        self.assert_lists(f'collection_owner_url={owner_url}&collection={prefix}*',
                          ['inQhead', 'inRv1'], ['inP', 'inQhead', 'inQv1', 'inRv1'])
        source = f'source={self.source.mnemonic}'
        self.assert_lists(f'{source}&collection=!{prefix}P', ['inP', 'inQhead', 'inQv1', 'inRv1'],
                          ['inQhead', 'inQv1', 'inRv1'])
        # HEAD holds the concepts' first versions, so a search of latest versions finds inQhead in no collection
        self.assert_lists(f'{source}&collection=!{prefix}Q', ['inP', 'inQhead', 'inQv1', 'inRv1'],
                          ['inP', 'inQhead', 'inRv1'])

    @patch('core.common.mixins.write_csv_to_s3', Mock(return_value='https://exports.test/export.csv'))
    def test_a_collection_named_with_its_owner_answers_as_its_lists(self):
        org, prefix = self.org.mnemonic, self.tag
        for resource in ['concepts', 'mappings']:
            for query in [f'collection={prefix}P', f'collection={prefix}P&version=v1',
                          f'collection={prefix}Q&version=v1', f'collection={prefix}Q&version=latest',
                          f'collection={prefix}R,{prefix}P']:
                for url in [f'/{resource}/?org={org}&{query}', f'/{resource}/?org={org}&csv=true&{query}']:
                    self.assert_answers(url, 401, 403, 200)
            for query in [f'collection={prefix}Q', f'collection={prefix}R&version=v1', 'collection=NoSuchCollection',
                          f'collection=!{prefix}P', f'collection={prefix}P*']:
                self.assert_answers(f'/{resource}/?org={org}&{query}', 200, 200, 200)
            self.assert_answers(f'/{resource}/?org={org}&collection={prefix}Q&version=v9', 404, 404, 404)
            # quoted, as a search reads it
            self.assert_answers(f'/{resource}/?org={org}&collection=%22{prefix}P%22', 401, 403, 200)
            # an empty source, which leaves the source check out, leaves this one in
            self.assert_answers(f'/{resource}/?source=&org={org}&collection={prefix}P&version=v1', 401, 403, 200)
            # the owner the URL names; the version in a repository's URL is that repository's
            self.assert_answers(f'{self.source.uri}{resource}/?collection={prefix}P', 401, 403, 200)
            item = f'{resource}/{"m" if resource == "mappings" else ""}inP{prefix}/'
            self.assert_answers(f'{self.source.uri}{item}?collection={prefix}P', 401, 403, 200)
            self.assert_answers(f'{self.r_v1.uri}{resource}/?collection={prefix}R', 200, 200, 200)

    def test_csv_exports_count_only_versions_the_requester_can_view(self):
        exports = []

        def export(data, *_args, **_kwargs):
            exports.append([(row['mnemonic'],) for row in data])
            return 'https://exports.test/export.csv'

        prefix = self.tag
        cases = [(f'collection={prefix}P', [], ['inP']), (f'collection={prefix}Q', ['inQhead'], ['inQhead', 'inQv1']),
                 (f'collection={prefix}Q&version=v1', [], ['inQv1']), (f'collection={prefix}R', ['inRv1'], ['inRv1'])]
        with patch('core.common.mixins.write_csv_to_s3', side_effect=export):
            for resource, (query, outsiders, members) in itertools.product(['concepts', 'mappings'], cases):
                for user, expected in [(None, outsiders), (self.outsider, outsiders), (self.member, members),
                                       (self.staff, members)]:
                    exports.clear()
                    response = self.request('get', f'/{resource}/?csv=true&{query}', user)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(self.keys(exports[0], resource), sorted(expected), (resource, query, user))

    def test_csv_exports_match_the_version_and_its_access_in_one_expansion(self):
        both = ConceptFactory(parent=self.source, mnemonic=f'inQboth{self.tag}')
        self.q_head.expansion.concepts.add(both)
        self.q_v1.expansion.concepts.add(both)
        exports = []

        def export(data, *_args, **_kwargs):
            exports.append([(row['mnemonic'],) for row in data])
            return 'https://exports.test/export.csv'

        with patch('core.common.mixins.write_csv_to_s3', side_effect=export):
            for user, expected in [(None, []), (self.outsider, []), (self.member, ['inQboth', 'inQv1'])]:
                exports.clear()
                response = self.request('get', f'/concepts/?csv=true&collection={self.tag}Q&version=v1', user)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.keys(exports[0], 'concepts'), expected, user)

    def test_deleted_versions_match_nothing(self):
        """The index keeps a deleted version's members until they're indexed again."""
        p_v1_uri = self.p_v1.uri
        Collection.objects.filter(id__in=[self.p_v1.id, self.p_head.id]).delete()
        for query in [f'collection_url={p_v1_uri}', f'collection={self.tag}P', f'collection={self.tag}P*']:
            for resource, user in itertools.product(['concepts', 'mappings'], [None, self.outsider, self.member]):
                self.assertEqual(self.listed(f'/{resource}/?{query}', user, resource), [], (query, user))

    def test_inactive_versions_match_nothing(self):
        """A version keeps its access when its HEAD is made private; deactivating its owner leaves both inactive."""
        Collection.objects.filter(id=self.p_v1.id).update(public_access=ACCESS_TYPE_VIEW)
        self.assert_lists(f'collection={self.tag}P', [], ['inP'])
        Collection.objects.filter(id__in=[self.p_v1.id, self.p_head.id]).update(is_active=False)
        for query in [f'collection={self.tag}P', f'collection_url={self.p_v1.uri}']:
            for resource, user in itertools.product(['concepts', 'mappings'], [None, self.outsider, self.member]):
                self.assertEqual(self.listed(f'/{resource}/?{query}', user, resource), [], (query, user))

    @override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
    def test_collection_facets_on_a_cached_source_version_list_answer_each_requester(self):
        release = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v1', released=True,
            public_access=ACCESS_TYPE_VIEW)
        release.concepts.add(*[concept.get_latest_version() for concept in self.concepts.values()])
        ConceptDocument().update(Concept.objects.filter(parent=self.source), refresh=True)
        cache.clear()
        for query in [f'collection_url={self.p_v1.uri}', f'collectionUrl={self.p_v1.uri}']:
            url = f'{release.uri}concepts/?{query}'
            self.assertEqual(self.listed(url, self.member, 'concepts'), ['inP'], url)
            self.assertEqual(self.listed(url, self.outsider, 'concepts'), [], url)
            self.assertEqual(self.listed(url, None, 'concepts'), [], url)

    def test_a_prefix_matching_too_many_versions_answers_400(self):
        url = f'/concepts/?collection={self.tag}*'
        with patch.object(BaseAPIView, 'max_viewable_collection_urls', 2):
            self.assert_answers(url, 400, 400, 200)
            self.assertEqual(self.request('get', url, self.staff).status_code, 200)
            self.assert_answers(f'/concepts/?collection={self.tag}R*', 200, 200, 200)
            # a prefix longer than any owner URL matches no owner
            self.assert_answers(f'/concepts/?collection_owner_url={self.org.uri}collections/*', 200, 200, 200)


class CanViewAllContentTest(OCLTestCase):
    """A collection reference reads all of a repo's content only for those who can view it."""
    def setUp(self):
        super().setUp()
        self.staff = UserProfile.objects.get(username='ocladmin')
        self.org = OrganizationFactory()
        self.member = UserProfileFactory(organizations=[self.org])
        self.owner = UserProfileFactory()
        self.outsider = UserProfileFactory()

    def test_private_repos(self):
        user_source = UserSourceFactory(user=self.owner, public_access=ACCESS_TYPE_NONE)
        org_source = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_NONE)

        for source, viewers in [(user_source, [self.owner, self.staff]), (org_source, [self.member, self.staff])]:
            for user in [self.owner, self.member, self.outsider, self.staff]:
                self.assertEqual(source.can_view_all_content(user), user in viewers, (source.uri, user.username))
            self.assertFalse(source.can_view_all_content(None))  # no user: public content only

    def test_public_repo(self):
        source = UserSourceFactory(user=self.owner, public_access=ACCESS_TYPE_VIEW)

        self.assertTrue(source.can_view_all_content(self.outsider))

    def test_public_version_of_a_private_head(self):
        head = OrganizationSourceFactory(organization=self.org, public_access=ACCESS_TYPE_NONE)
        version = OrganizationSourceFactory(
            organization=self.org, mnemonic=head.mnemonic, version='v1', public_access=ACCESS_TYPE_VIEW)

        for user in [self.member, self.outsider, self.staff]:
            self.assertEqual(
                version.can_view_all_content(user), version.has_view_access(user), user.username)
        self.assertFalse(version.can_view_all_content(self.outsider))


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


class RetiredPublicEditAccessTest(AccessTestMixin, OCLAPITestCase):
    """
    public_access='Edit' is retired and stored as 'View', so a formerly public-Edit repo stays publicly readable, and
    only staff, the owner or a member can change it, add content to it or delete it.
    """
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory(organization=self.org, public_access=RETIRED_ACCESS_TYPE_EDIT)
        self.collection = UserCollectionFactory(user=self.owner, public_access=RETIRED_ACCESS_TYPE_EDIT)

    def test_edit_is_stored_as_view(self):
        for repo in [self.source, self.collection]:
            repo.refresh_from_db()
            self.assertEqual(repo.public_access, ACCESS_TYPE_VIEW)
            self.assertEqual(self.request('get', repo.uri).status_code, 200)

    def test_edit_in_payload_is_saved_as_view(self):
        response = self.request('put', self.source.uri, self.member, {'public_access': RETIRED_ACCESS_TYPE_EDIT})

        self.assertEqual(response.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.public_access, ACCESS_TYPE_VIEW)

        response = self.request(
            'post', self.owner.uri + 'sources/', self.owner,
            {'id': 'edit-src', 'name': 'edit-src', 'public_access': RETIRED_ACCESS_TYPE_EDIT}
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Source.objects.get(mnemonic='edit-src').public_access, ACCESS_TYPE_VIEW)

    def test_outsider_cannot_add_content(self):
        url = self.source.uri + 'concepts/'
        payload = SourceContentAccessTest.concept_payload

        self.assertEqual(self.request('post', url, self.outsider, payload).status_code, 403)
        self.assertEqual(self.request('post', url, self.member, payload).status_code, 201)

    def test_outsider_cannot_edit_or_delete(self):
        for repo in [self.source, self.collection]:
            self.assertEqual(self.request('put', repo.uri, self.outsider, {'name': 'Renamed'}).status_code, 403)
            self.assertEqual(self.request('delete', repo.uri, self.outsider).status_code, 403)
            repo.refresh_from_db()
            self.assertNotEqual(repo.name, 'Renamed')
            self.assertTrue(repo.is_active)

    @patch('core.common.models.delete_s3_objects')
    def test_owner_and_member_can_edit_and_delete(self, _delete_s3_objects_mock):
        for repo, user in [(self.source, self.member), (self.collection, self.owner)]:
            self.assertEqual(self.request('put', repo.uri, user, {'name': 'Renamed'}).status_code, 200)
            repo.refresh_from_db()
            self.assertEqual(repo.name, 'Renamed')
            self.assertEqual(self.request('delete', repo.uri, user).status_code, 204)

    def test_unmigrated_edit_row_grants_outsider_nothing(self):
        Source.objects.filter(id=self.source.id).update(public_access=RETIRED_ACCESS_TYPE_EDIT)

        self.assertEqual(self.request('put', self.source.uri, self.outsider, {'name': 'Renamed'}).status_code, 403)
        self.assertEqual(self.request('delete', self.source.uri, self.outsider).status_code, 403)
        self.assertTrue(Source.objects.get(id=self.source.id).is_active)
