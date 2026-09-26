from django.contrib.auth.models import Group
from mock.mock import patch, Mock
from rest_framework.exceptions import ValidationError

from core.collections.models import Collection
from core.common.tests import OCLAPITestCase
from core.importers.importer import ResourceImporter
from core.importers.models import CREATED, UPDATED, PERMISSION_DENIED
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.constants import PREVIEW_GRANDFATHERED_GROUP
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


def accession_id(value):
    return [{
        'value': value,
        'type': {'coding': [{'code': 'ACSN', 'system': 'http://hl7.org/fhir/v2/0203'}]}
    }]


def fhir_resource(resource_type, mnemonic, url, identifier=None):
    resource = {
        'resourceType': resource_type,
        'id': mnemonic,
        'url': url,
        'name': mnemonic,
        'title': mnemonic,
        'status': 'active',
        'version': '1.0',
    }
    if resource_type == 'CodeSystem':
        resource['content'] = 'complete'
    if identifier:
        resource['identifier'] = accession_id(identifier)
    return resource


@patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
@patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
class FHIRCreateOwnerTest(OCLAPITestCase):
    """New FHIR repos go under the owner in the URL, whatever the payload's accession ID says."""

    def setUp(self):
        super().setUp()
        self.own_org = OrganizationFactory(mnemonic='OwnOrg')
        self.other_org = OrganizationFactory(mnemonic='OtherOrg')
        self.member = UserProfileFactory(username='fhirmember')
        self.own_org.members.add(self.member)
        self.token = self.member.get_token()

    def post(self, url, data):
        return self.client.post(url, data, HTTP_AUTHORIZATION='Token ' + self.token, format='json')

    def test_accession_id_for_another_owner_is_refused(self):
        for resource_type in ['CodeSystem', 'ValueSet', 'ConceptMap']:
            response = self.post(
                f'/orgs/{self.own_org.mnemonic}/{resource_type}/',
                fhir_resource(
                    resource_type, f'{resource_type}Squat', f'http://example.com/{resource_type}Squat',
                    f'/orgs/{self.other_org.mnemonic}/{resource_type}/{resource_type}Squat/')
            )

            self.assertEqual(response.status_code, 400, resource_type)
            self.assertFalse(Source.objects.filter(organization=self.other_org).exists())
            self.assertFalse(Collection.objects.filter(organization=self.other_org).exists())

    def test_accession_id_for_a_user_is_refused_under_an_org(self):
        response = self.post(
            f'/orgs/{self.own_org.mnemonic}/CodeSystem/',
            fhir_resource(
                'CodeSystem', 'UserSquat', 'http://example.com/UserSquat',
                f'/users/{self.own_org.mnemonic}/CodeSystem/UserSquat/')
        )

        self.assertEqual(response.status_code, 400)

    def test_accession_id_for_the_url_owner_is_accepted(self):
        for resource_type in ['CodeSystem', 'ValueSet', 'ConceptMap']:
            response = self.post(
                f'/orgs/{self.own_org.mnemonic}/{resource_type}/',
                fhir_resource(
                    resource_type, f'{resource_type}Own', f'http://example.com/{resource_type}Own',
                    f'/orgs/{self.own_org.mnemonic}/{resource_type}/{resource_type}Own/')
            )

            self.assertEqual(response.status_code, 201, resource_type)
            self.assertEqual(response.data['id'], f'{resource_type}Own')

    def test_without_accession_id_uses_the_url_owner(self):
        response = self.post(
            f'/orgs/{self.own_org.mnemonic}/CodeSystem/',
            fhir_resource('CodeSystem', 'NoAcsn', 'http://example.com/NoAcsn')
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Source.objects.filter(organization=self.own_org, mnemonic='NoAcsn').exists())

    def test_outsider_cannot_create_under_org(self):
        response = self.post(
            f'/orgs/{self.other_org.mnemonic}/CodeSystem/',
            fhir_resource('CodeSystem', 'Outsider', 'http://example.com/Outsider')
        )

        self.assertEqual(response.status_code, 403)


class BulkImportOwnerAccessTest(OCLAPITestCase):
    """The requester must own, or be a member of, the owner they import into."""

    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory(mnemonic='ImportOrg')
        self.member = UserProfileFactory(username='importmember')
        self.org.members.add(self.member)
        self.outsider = UserProfileFactory(username='importoutsider')
        self.admin = UserProfile.objects.get(username='ocladmin')
        # FHIR/NPM (import_type) imports need users.bulk_import_advanced (ocl_online#230); these are existing users
        grandfathered = Group.objects.get(name=PREVIEW_GRANDFATHERED_GROUP)
        self.member.groups.add(grandfathered)
        self.outsider.groups.add(grandfathered)

    def post(self, user, data):
        return self.client.post(
            '/importers/bulk-import/', data, HTTP_AUTHORIZATION='Token ' + user.get_token(), format='json')

    @patch('core.importers.views.bulk_import_new')
    def test_outsider_is_refused(self, bulk_import_mock):
        for data in [
                {'import_type': 'npm', 'owner_type': 'orgs', 'owner': 'ImportOrg', 'file_url': 'http://x/y.tgz'},
                {'import_type': 'npm', 'owner_type': 'users', 'owner': 'importmember', 'file_url': 'http://x/y.tgz'},
        ]:
            response = self.post(self.outsider, data)

            self.assertEqual(response.status_code, 403)
        bulk_import_mock.apply_async.assert_not_called()

    @patch('core.importers.views.bulk_import_new')
    def test_unknown_owner_is_404(self, bulk_import_mock):
        response = self.post(
            self.member, {'import_type': 'npm', 'owner_type': 'orgs', 'owner': 'NoSuchOrg', 'file_url': 'http://x'})

        self.assertEqual(response.status_code, 404)
        bulk_import_mock.apply_async.assert_not_called()

    @patch('core.importers.views.get_export_service', Mock())
    @patch('core.importers.limits.requests.get')  # limited users' package URLs are downloaded in the request
    @patch('core.importers.views.bulk_import_new')
    def test_owner_member_and_staff_are_allowed(self, bulk_import_mock, get_mock):
        get_mock.return_value = Mock(ok=True, headers={}, iter_content=Mock(return_value=[b'package']))
        bulk_import_mock.apply_async.return_value = Mock(id='task-id', state='PENDING')

        for user, data in [
                (self.member, {'import_type': 'npm', 'owner_type': 'orgs', 'owner': 'ImportOrg'}),
                (self.member, {'import_type': 'npm'}),
                (self.admin, {'import_type': 'npm', 'owner_type': 'orgs', 'owner': 'ImportOrg'}),
        ]:
            response = self.post(user, {**data, 'file_url': 'http://x/y.tgz'})

            self.assertEqual(response.status_code, 202)
            args = bulk_import_mock.apply_async.call_args[0][0]
            self.assertEqual(args[2:4], (data.get('owner_type', 'user'), data.get('owner', user.username)))


@patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
@patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
class FHIRImporterOwnerAccessTest(OCLAPITestCase):
    """FHIR resources in a bulk import need edit access to the repo or its owner."""

    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory(mnemonic='ImporterOrg')
        self.member = UserProfileFactory(username='importermember')
        self.org.members.add(self.member)
        self.outsider = UserProfileFactory(username='importeroutsider')
        self.source = OrganizationSourceFactory(
            organization=self.org, mnemonic='Existing', canonical_url='http://example.com/Existing',
            public_access='View')

    def test_outsider_cannot_update_or_create(self):
        for url in ['http://example.com/Existing', '/orgs/ImporterOrg/CodeSystem/Existing/']:
            result = ResourceImporter.import_code_system(
                'ImporterOrg', 'orgs', fhir_resource('CodeSystem', 'Existing', url), 'CodeSystem', url,
                self.outsider.username)

            self.assertEqual(result, PERMISSION_DENIED)

        for importer in [ResourceImporter.import_code_system, ResourceImporter.import_concept_map,
                         ResourceImporter.import_value_set]:
            result = importer(
                'ImporterOrg', 'orgs', fhir_resource('CodeSystem', 'NewOne', 'http://example.com/NewOne'),
                'CodeSystem', 'http://example.com/NewOne', self.outsider.username)

            self.assertEqual(result, PERMISSION_DENIED)
        self.assertFalse(Source.objects.filter(mnemonic='NewOne').exists())
        self.assertFalse(Collection.objects.filter(mnemonic='NewOne').exists())

    def test_member_can_update_and_create(self):
        resource = fhir_resource('CodeSystem', 'Existing', 'http://example.com/Existing')
        resource['version'] = '2.0'
        self.assertEqual(
            ResourceImporter.import_code_system(
                'ImporterOrg', 'orgs', resource, 'CodeSystem', 'http://example.com/Existing', self.member.username),
            UPDATED
        )

        self.assertEqual(
            ResourceImporter.import_code_system(
                'ImporterOrg', 'orgs', fhir_resource('CodeSystem', 'Created', 'http://example.com/Created'),
                'CodeSystem', 'http://example.com/Created', self.member.username),
            CREATED
        )
        self.assertTrue(Source.objects.filter(organization=self.org, mnemonic='Created').exists())

    def test_accession_id_for_another_owner_is_refused(self):
        other_org = OrganizationFactory(mnemonic='ImporterOther')
        resource = fhir_resource(
            'CodeSystem', 'Elsewhere', 'http://example.com/Elsewhere', '/orgs/ImporterOther/CodeSystem/Elsewhere/')

        with self.assertRaises(ValidationError):
            ResourceImporter.import_code_system(
                'ImporterOrg', 'orgs', resource, 'CodeSystem', 'http://example.com/Elsewhere', self.member.username)
        self.assertFalse(Source.objects.filter(organization=other_org).exists())
