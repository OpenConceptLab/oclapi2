from rest_framework.test import APIClient
from core.common.tests import OCLTestCase


class FhirStatementTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_public_can_view_capability_statement(self):
        response = self.client.get('/fhir/metadata')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['resourceType']), "CapabilityStatement")

    def test_public_can_vew_terminology_statement(self):
        response = self.client.get('/fhir/metadata?mode=terminology')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['resourceType']), "TerminologyCapabilities")
