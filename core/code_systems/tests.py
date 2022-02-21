from rest_framework.test import APIClient

from core.common.tests import OCLTestCase
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.tests.factories import UserProfileFactory


class SourceTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory()
        self.org.save()

        self.org_source = OrganizationSourceFactory.build(organization=self.org)
        self.org_source.canonical_url = '/some/url'
        self.org_source.save()

        self.user = UserProfileFactory()
        self.user.save()
        self.user_token = self.user.get_token()
        self.user_source = UserSourceFactory(user=self.user, public_access='None')
        self.user_source.canonical_url = '/some/url'
        self.user_source.save()

        self.client = APIClient()

    def test_public_can_view(self):
        response = self.client.get('/fhir/CodeSystem/?url=/some/url')

        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(response.data['entry'][0]['identifier'][0]['value'], '/orgs/' + self.org.mnemonic
                         + '/CodeSystem/' + self.org_source.mnemonic + '/')

    def test_private_can_view(self):
        response = self.client.get('/fhir/CodeSystem/?url=/some/url', HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(len(response.data['entry']), 2)
        self.assertEqual(response.data['entry'][0]['identifier'][0]['value'], '/users/' + self.user.mnemonic
                         + '/CodeSystem/' + self.user_source.mnemonic + '/')
        self.assertEqual(response.data['entry'][1]['identifier'][0]['value'], '/orgs/' + self.org.mnemonic
                         + '/CodeSystem/' + self.org_source.mnemonic + '/')

    def test_public_can_list(self):
        response = self.client.get('/fhir/CodeSystem/')

        self.assertEqual(len(response.data['entry']), 1)
        self.assertEqual(response.data['entry'][0]['identifier'][0]['value'], '/orgs/' + self.org.mnemonic
                         + '/CodeSystem/' + self.org_source.mnemonic + '/')

    def test_private_can_list(self):
        response = self.client.get('/fhir/CodeSystem/', HTTP_AUTHORIZATION='Token ' + self.user_token)

        self.assertEqual(len(response.data['entry']), 2)
        self.assertEqual(response.data['entry'][0]['identifier'][0]['value'], '/users/' + self.user.mnemonic
                         + '/CodeSystem/' + self.user_source.mnemonic + '/')
        self.assertEqual(response.data['entry'][1]['identifier'][0]['value'], '/orgs/' + self.org.mnemonic
                         + '/CodeSystem/' + self.org_source.mnemonic + '/')
