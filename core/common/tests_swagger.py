from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile


class SwaggerSchemaTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.admin_token = UserProfile.objects.get(username='ocladmin').get_token()

    def get_schema(self):
        response = self.client.get('/swagger.json', HTTP_AUTHORIZATION=f"Token {self.admin_token}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_list_endpoints_are_arrays(self):
        paths = self.get_schema()['paths']

        for path in (
                '/orgs/{org}/sources/', '/orgs/{org}/collections/', '/orgs/{org}/sources/{source}/concepts/',
                '/orgs/{org}/sources/{source}/versions/'
        ):
            schema = paths[path]['get']['responses']['200']['schema']
            self.assertEqual(schema['type'], 'array', path)
            self.assertIn('$ref', schema['items'], path)

    def test_list_endpoints_document_paging_parameters(self):
        parameters = self.get_schema()['paths']['/orgs/{org}/sources/']['get']['parameters']

        names = [parameter['name'] for parameter in parameters]
        self.assertIn('page', names)
        self.assertIn('limit', names)

    def test_source_detail_logo_url_is_nullable(self):
        logo_url = self.get_schema()['definitions']['SourceDetail']['properties']['logo_url']

        self.assertTrue(logo_url.get('x-nullable'))

    def test_checksums_are_objects(self):
        definitions = self.get_schema()['definitions']

        for name in ('ConceptDetail', 'SourceDetail', 'SourceVersionDetail'):
            self.assertEqual(definitions[name]['properties']['checksums']['type'], 'object', name)

    def test_source_version_external_exports_is_array(self):
        definitions = self.get_schema()['definitions']

        self.assertEqual(definitions['SourceVersionDetail']['properties']['external_exports']['type'], 'array')

    def test_concept_detail_required_fields_are_returned(self):
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source)
        required = self.get_schema()['definitions']['ConceptDetail']['required']

        response = self.client.get(concept.versioned_object.uri, HTTP_AUTHORIZATION=f"Token {self.admin_token}")

        self.assertEqual(response.status_code, 200)
        for field in required:
            self.assertIn(field, response.json())
        self.assertIsInstance(response.json()['checksums'], dict)

    def test_source_detail_without_logo_returns_null_logo_url(self):
        source = OrganizationSourceFactory()

        response = self.client.get(source.uri, HTTP_AUTHORIZATION=f"Token {self.admin_token}")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['logo_url'])

    def test_api_root_lists_real_schema_url(self):
        response = self.client.get('/', HTTP_AUTHORIZATION=f"Token {self.admin_token}")

        routes = response.json()['routes']
        self.assertTrue(routes['schema-json'].endswith('/swagger.json'))
        self.assertNotIn('^', routes['schema-json'])
        self.assertTrue(routes['schema-swagger-ui'].endswith('/swagger/'))
