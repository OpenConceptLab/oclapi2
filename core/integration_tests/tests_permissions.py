from django.core.files.uploadedfile import SimpleUploadedFile
from mock import patch

from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLAPITestCase
from core.map_projects.tests.factories import AutomatchRunFactory, MapProjectFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory

# Users, orgs and each resource type are numbered by separate sequences, so their ids overlap. These explicit ids
# sit far above anything the test sequences hand out, which lets an org share an id with a user or a resource.
OWNER_ID = 900001
RESOURCE_ID = 900002


class OrgMembershipAccessMixin:
    """
    Org membership grants access to the org itself and to what the org owns, and to nothing else.

    Owners, members of the owning org and staff can view and edit a private resource. Members of an org that only
    shares its id with the owning user, or with the resource itself, are refused.
    """
    edit_method = 'put'
    edit_field = 'name'
    edit_value = 'Updated'

    def create_user_owned(self, user):
        raise NotImplementedError

    def create_org_owned(self, organization, resource_id):
        raise NotImplementedError

    @staticmethod
    def get_url(resource):
        return resource.uri

    def setUp(self):
        super().setUp()
        self.staff = UserProfile.objects.get(username='ocladmin')
        self.owner = UserProfileFactory(id=OWNER_ID)
        self.owning_org = OrganizationFactory()
        self.owning_org_member = UserProfileFactory(organizations=[self.owning_org])
        self.user_owned = self.create_user_owned(self.owner)
        self.org_owned = self.create_org_owned(self.owning_org, RESOURCE_ID)
        self.member_of_org_sharing_owner_id = UserProfileFactory(organizations=[OrganizationFactory(id=OWNER_ID)])
        self.member_of_org_sharing_resource_id = UserProfileFactory(
            organizations=[OrganizationFactory(id=RESOURCE_ID)])

    def view(self, resource, user):
        return self.client.get(self.get_url(resource), HTTP_AUTHORIZATION='Token ' + user.get_token())

    def edit(self, resource, user):
        return getattr(self.client, self.edit_method)(
            self.get_url(resource),
            {self.edit_field: self.edit_value},
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

    def assert_can_view_and_edit(self, resource, user):
        self.assertEqual(self.view(resource, user).status_code, 200)
        self.assertEqual(self.edit(resource, user).status_code, 200)
        resource.refresh_from_db()
        self.assertEqual(getattr(resource, self.edit_field), self.edit_value)

    def assert_cannot_view_or_edit(self, resource, user):
        self.assertEqual(self.view(resource, user).status_code, 403)
        self.assertEqual(self.edit(resource, user).status_code, 403)
        resource.refresh_from_db()
        self.assertNotEqual(getattr(resource, self.edit_field), self.edit_value)

    def test_owner_can_view_and_edit(self):
        self.assert_can_view_and_edit(self.user_owned, self.owner)

    def test_owning_org_member_can_view_and_edit(self):
        self.assert_can_view_and_edit(self.org_owned, self.owning_org_member)

    def test_staff_can_view_and_edit(self):
        self.assert_can_view_and_edit(self.user_owned, self.staff)
        self.assert_can_view_and_edit(self.org_owned, self.staff)

    def test_member_of_org_sharing_owner_id_is_refused(self):
        self.assert_cannot_view_or_edit(self.user_owned, self.member_of_org_sharing_owner_id)

    def test_member_of_org_sharing_resource_id_is_refused(self):
        self.assert_cannot_view_or_edit(self.org_owned, self.member_of_org_sharing_resource_id)


class SourceAccessTest(OrgMembershipAccessMixin, OCLAPITestCase):
    def create_user_owned(self, user):
        return UserSourceFactory(user=user, public_access=ACCESS_TYPE_NONE)

    def create_org_owned(self, organization, resource_id):
        return OrganizationSourceFactory(id=resource_id, organization=organization, public_access=ACCESS_TYPE_NONE)


class SourceVersionAccessTest(OrgMembershipAccessMixin, OCLAPITestCase):
    edit_field = 'external_id'
    edit_value = 'EXT-UPDATED'

    def create_user_owned(self, user):
        head = UserSourceFactory(user=user, public_access=ACCESS_TYPE_NONE)
        return UserSourceFactory(mnemonic=head.mnemonic, user=user, version='v1', public_access=ACCESS_TYPE_NONE)

    def create_org_owned(self, organization, resource_id):
        head = OrganizationSourceFactory(organization=organization, public_access=ACCESS_TYPE_NONE)
        return OrganizationSourceFactory(
            id=resource_id, mnemonic=head.mnemonic, organization=organization, version='v1',
            public_access=ACCESS_TYPE_NONE
        )


class CollectionAccessTest(OrgMembershipAccessMixin, OCLAPITestCase):
    def create_user_owned(self, user):
        return UserCollectionFactory(user=user, public_access=ACCESS_TYPE_NONE)

    def create_org_owned(self, organization, resource_id):
        return OrganizationCollectionFactory(
            id=resource_id, organization=organization, public_access=ACCESS_TYPE_NONE)


class CollectionVersionAccessTest(OrgMembershipAccessMixin, OCLAPITestCase):
    edit_field = 'external_id'
    edit_value = 'EXT-UPDATED'

    def create_user_owned(self, user):
        head = UserCollectionFactory(user=user, public_access=ACCESS_TYPE_NONE)
        return UserCollectionFactory(
            mnemonic=head.mnemonic, user=user, version='v1', public_access=ACCESS_TYPE_NONE)

    def create_org_owned(self, organization, resource_id):
        head = OrganizationCollectionFactory(organization=organization, public_access=ACCESS_TYPE_NONE)
        return OrganizationCollectionFactory(
            id=resource_id, mnemonic=head.mnemonic, organization=organization, version='v1',
            public_access=ACCESS_TYPE_NONE
        )


class MapProjectAccessTest(OrgMembershipAccessMixin, OCLAPITestCase):
    @staticmethod
    def get_url(resource):
        return f'{resource.parent.uri}map-projects/{resource.id}/'

    def edit(self, resource, user):
        # Same multipart PUT as the Mapper, which re-sends the input file on every save.
        with patch('core.services.storages.cloud.aws.S3.upload'):
            return self.client.put(
                self.get_url(resource),
                {self.edit_field: self.edit_value, 'file': SimpleUploadedFile('input.csv', b'content', 'text/csv')},
                HTTP_AUTHORIZATION='Token ' + user.get_token()
            )

    def create_user_owned(self, user):
        return MapProjectFactory(organization=None, user=user, public_access=ACCESS_TYPE_NONE)

    def create_org_owned(self, organization, resource_id):
        return MapProjectFactory(id=resource_id, organization=organization, public_access=ACCESS_TYPE_NONE)


class AutomatchRunAccessTest(OrgMembershipAccessMixin, OCLAPITestCase):
    """Runs are authorized against their map project, so the owner and the shared id are the project's."""
    edit_method = 'patch'
    edit_field = 'completed_rows'
    edit_value = 7

    @staticmethod
    def get_url(resource):
        return f'/auto-match-runs/{resource.id}/'

    def create_user_owned(self, user):
        project = MapProjectFactory(organization=None, user=user, public_access=ACCESS_TYPE_NONE)
        return AutomatchRunFactory(map_project=project, started_by=user)

    def create_org_owned(self, organization, resource_id):
        project = MapProjectFactory(id=resource_id, organization=organization, public_access=ACCESS_TYPE_NONE)
        return AutomatchRunFactory(map_project=project)
