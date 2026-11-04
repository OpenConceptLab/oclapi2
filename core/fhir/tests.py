from rest_framework.test import APIClient
from core.common.tests import OCLTestCase


class FhirStatementTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_public_can_view_capability_statement(self):
        response = self.client.get('/fhir/metadata')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resourceType'], "CapabilityStatement")

    def test_public_can_vew_terminology_statement(self):
        response = self.client.get('/fhir/metadata?mode=terminology')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resourceType'], "TerminologyCapabilities")

    def test_implementation_url_is_the_fhir_base_of_the_request_host(self):
        response = self.client.get(
            '/fhir/metadata?mode=terminology', HTTP_HOST='fhir.openconceptlab.org', HTTP_X_FORWARDED_PROTO='https')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['implementation']['url'], 'https://fhir.openconceptlab.org/fhir'
        )
