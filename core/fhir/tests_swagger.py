"""The terminology schema describes the routed reads and keeps the other API operations unchanged."""
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve
from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator
from rest_framework.request import Request
from rest_framework.test import APIClient, APITestCase
from whitenoise.middleware import WhiteNoiseMiddleware

from core.common.swagger import OCLSwaggerEndpointEnumerator, OCLSwaggerSchemaGenerator
from core.fhir.base import FhirViewMixin, is_fhir_path
from core.fhir.search import PARAMETERS, STRING_FIELDS, STRING_LOOKUPS
from core.fhir.swagger import FhirSwaggerSchemaGenerator
from core.middlewares.middlewares import FhirMiddleware
from core.urls import api_info

BASES = ('/fhir', '/orgs/{org}', '/users/{user}')
OPERATIONS = {
    'CodeSystem': ('/$lookup', '/{id}/$lookup', '/$validate-code', '/{id}/$validate-code'),
    'ValueSet': ('/$validate-code', '/{id}/$validate-code', '/$expand', '/{id}/$expand'),
    'ConceptMap': ('/$translate',),
}
MIDDLEWARE = ['core.middlewares.middlewares.FhirMiddleware']
SERVICE_MIDDLEWARE = tuple(settings.MIDDLEWARE)


def expected_operations():
    """An independent pin of the documented methods and paths at all three bases."""
    expected = {}
    for base in BASES:
        for resource, operations in OPERATIONS.items():
            for suffix in ('', '/{id}'):
                expected[('get', f'{base}/{resource}{suffix}')] = resource
            for suffix in operations:
                for method in ('get', 'post'):
                    expected[(method, f'{base}/{resource}{suffix}')] = resource
        for suffix in ('/metadata', '/$versions'):
            expected[('get', base + suffix)] = 'Capabilities'
    return expected


def operations_in(schema):
    """Index actual schema operations without counting path-level parameters."""
    return {(method, path): operation for path, item in schema['paths'].items()
            for method, operation in item.items() if method in openapi.PathItem.OPERATION_NAMES}


class FhirSwaggerTest(SimpleTestCase):
    """Schema generation is independent of resource data and serializer introspection."""

    def setUp(self):
        self.schema = FhirSwaggerSchemaGenerator(api_info).get_schema(public=True)

    def test_exact_operations_and_routed_methods(self):
        actual, expected = operations_in(self.schema), expected_operations()
        self.assertEqual(set(actual), set(expected))
        self.assertEqual(len(actual), 78)
        identifiers = []
        for (method, path), operation in actual.items():
            self.assertFalse(path.endswith('/'), path)
            self.assertEqual(operation['tags'], [expected[(method, path)]])
            self.assertTrue(operation['summary'])
            self.assertNotIn('\n', operation['summary'])
            self.assertTrue(operation['description'])
            self.assertNotIn('security', operation)  # the schema's: a token shows private content at owner bases
            identifiers.append(operation['operationId'])
            concrete = path.replace('{org}', 'Acme').replace('{user}', 'jo').replace('{id}', '123')
            view_class = resolve(concrete).func.view_class
            self.assertTrue(issubclass(view_class, FhirViewMixin), path)
            self.assertIn(method, view_class.http_method_names, path)
            self.assertTrue(callable(getattr(view_class, method, None)), path)
            parameters = operation['parameters']
            self.assertEqual({p['name'] for p in parameters if p['in'] == 'path'}, set(re.findall(r'{(\w+)}', path)))
            self.assertEqual(len({(p['in'], p['name']) for p in parameters}), len(parameters))
            self.assertIn('_format', [p['name'] for p in parameters])
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_search_parameters_match_the_search_parser(self):
        for base in BASES:
            for resource in OPERATIONS:
                parameters = self.schema['paths'][f'{base}/{resource}']['get']['parameters']
                query = {p['name']: p for p in parameters if p['in'] == 'query'}
                names = {f'{name}:{modifier}' if modifier else name
                         for name in STRING_FIELDS for modifier in STRING_LOOKUPS}
                self.assertEqual(set(query), set(PARAMETERS[resource]) | names | {'_count', 'page', '_format'})
                self.assertEqual(query['_count']['type'], 'integer')
                self.assertEqual(query['_count']['maximum'], 100)
                self.assertEqual(query['_format']['enum'], ['json', 'xml'])

    def test_operation_parameters_and_bodies(self):
        expected = {
            'CodeSystem/$lookup': {'system', 'version', 'code', 'coding', 'displayLanguage'},
            'CodeSystem/$validate-code': {'url', 'version', 'code', 'coding', 'display', 'displayLanguage'},
            'ValueSet/$validate-code': {
                'url', 'valueSetVersion', 'system', 'systemVersion', 'code', 'coding', 'display', 'displayLanguage'},
            'ValueSet/$expand': {
                'url', 'valueSetVersion', 'filter', 'displayLanguage', 'includeDesignations', 'activeOnly',
                'offset', 'count', 'includeDefinition', 'excludePostCoordinated', 'excludeNested', 'excludeNotForUI'},
            'ConceptMap/$translate': {
                'url', 'conceptMapVersion', 'code', 'system', 'version', 'coding', 'source', 'target',
                'targetsystem', 'reverse'},
        }
        for path, names in expected.items():
            for base in BASES:
                for method in ('get', 'post'):
                    operation = self.schema['paths'][f'{base}/{path}'][method]
                    parameters = operation['parameters']
                    query = {p['name']: p for p in parameters if p['in'] == 'query'}
                    self.assertEqual(set(query), names | {'_format'})
                    self.assertTrue(all('type' in parameter for parameter in query.values()))
                    bodies = [p for p in parameters if p['in'] == 'body']
                    self.assertEqual(len(bodies), int(method == 'post'))
                    if bodies:
                        self.assertEqual(operation['consumes'], ['application/fhir+json'])
                        self.assertEqual(bodies[0]['schema']['$ref'], '#/definitions/FhirParameters')
        expand = self.schema['paths']['/fhir/ValueSet/$expand']['get']['parameters']
        count = next(p for p in expand if p['name'] == 'count')
        self.assertEqual((count['type'], count['maximum']), ('integer', 1000))

    def test_coding_says_which_inputs_it_replaces(self):
        # a coding replaces these inputs (core.fhir.ops: coded_input, translate.read_request), and the coding's
        # description names them; those it leaves unread say so ($lookup's system still names an instance: mismatch)
        replaced = {
            'CodeSystem/$lookup': ({'code', 'system'}, {'code'}),
            'CodeSystem/$validate-code': ({'code', 'display'}, {'code', 'display'}),
            'ValueSet/$validate-code': ({'code', 'system', 'systemVersion', 'display'},
                                        {'code', 'system', 'systemVersion', 'display'}),
            'ConceptMap/$translate': ({'code', 'system', 'version'}, {'code', 'system', 'version'}),
        }
        for path, (names, unread) in replaced.items():
            query = {p['name']: p for p in self.schema['paths'][f'/fhir/{path}']['get']['parameters']}
            for name in names:
                self.assertIn(name, query['coding']['description'], (path, name))
                self.assertEqual('Not read when coding' in query[name]['description'], name in unread, (path, name))
        lookup = {p['name']: p for p in self.schema['paths']['/fhir/CodeSystem/$lookup']['get']['parameters']}
        self.assertIn('At an instance it must name that code system', lookup['system']['description'])

    def test_tags_order_responses_and_security_definitions(self):
        for tag in (*OPERATIONS, 'Capabilities'):
            paths = [path for (_, path), operation in operations_in(self.schema).items() if operation['tags'] == [tag]]
            order = [next(index for index, base in enumerate(BASES) if path.startswith(base + '/')) for path in paths]
            self.assertEqual(order, sorted(order))
        self.assertEqual(set(self.schema['securityDefinitions']), {'Basic', 'Token'})
        self.assertEqual({name for requirement in self.schema['security'] for name in requirement}, {'Basic', 'Token'})
        for (_, path), operation in operations_in(self.schema).items():
            success = operation['responses']['200']
            self.assertTrue(success['schema'])
            for code, response in operation['responses'].items():
                if code != '200':
                    self.assertEqual(response['schema']['properties']['resourceType']['enum'], ['OperationOutcome'])
            if path.endswith('/metadata'):
                self.assertIn('TerminologyCapabilities', success['description'])


@override_settings(FHIR_SUBDOMAIN='fhir', MIDDLEWARE=MIDDLEWARE, ALLOWED_HOSTS=['*'])
class FhirSwaggerHostTest(SimpleTestCase):
    """The FHIR host serves its own documentation without opening other API paths."""

    def test_schema_urls_use_the_request_host_and_forwarded_scheme(self):
        for path in ('/swagger/?format=openapi', '/swagger.json', '/swagger.yaml'):
            response = APIClient().get(path, HTTP_HOST='fhir.example.org:8443', HTTP_X_FORWARDED_PROTO='https')
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(set(operations_in(response.data)), set(expected_operations()))
            self.assertEqual(response.data['host'], 'fhir.example.org:8443')
            self.assertEqual(response.data['schemes'], ['https'])
            self.assertEqual(response.data['basePath'], '/')
            self.assertIn('FHIR terminology', response.data['info']['title'])

    def test_only_the_documentation_paths_open_on_the_fhir_host(self):
        response = APIClient().get('/swagger/', HTTP_HOST='fhir.example.org', HTTP_ACCEPT='text/html')
        self.assertEqual(response.status_code, 200)
        middleware = FhirMiddleware(lambda request: HttpResponse('allowed'))
        for path in ('/swagger/', '/swagger.json', '/swagger.yaml', '/version/'):
            request = RequestFactory().get(path, HTTP_HOST='fhir.example.org')
            self.assertEqual(middleware(request).content, b'allowed', path)
        for path in ('/orgs/', '/redoc/', '/swagger', '/swagger/other', '/swagger.json/extra', '/swagger.yaml/'):
            response = APIClient().get(path, HTTP_HOST='fhir.example.org')
            self.assertEqual(response.status_code, 404, path)
            self.assertEqual(response.json()['resourceType'], 'OperationOutcome', path)

    def test_the_ui_files_are_served_ahead_of_the_host_check(self):
        """Static files are served before FhirMiddleware, so the Swagger UI's files load on the FHIR host."""
        static = 'whitenoise.middleware.WhiteNoiseMiddleware'
        if static in SERVICE_MIDDLEWARE:
            self.assertLess(SERVICE_MIDDLEWARE.index(static),
                            SERVICE_MIDDLEWARE.index('core.middlewares.middlewares.FhirMiddleware'))
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'drf-yasg' / 'style.css'
            target.parent.mkdir(parents=True)
            target.write_text('body {}')
            with self.settings(STATIC_ROOT=directory, STATIC_URL='/static/', WHITENOISE_AUTOREFRESH=True,
                               WHITENOISE_USE_FINDERS=False):
                middleware = WhiteNoiseMiddleware(FhirMiddleware(
                    lambda request: HttpResponse('not static', status=404)))
                response = middleware(RequestFactory().get('/static/drf-yasg/style.css', HTTP_HOST='fhir.example.org'))
                self.assertEqual(response.status_code, 200)
                response.close()


class OriginalSchemaGenerator(OpenAPISchemaGenerator):
    """The API generator before FHIR curation, for a comparison of non-FHIR operations."""
    endpoint_enumerator_class = OCLSwaggerEndpointEnumerator


@override_settings(MIDDLEWARE=MIDDLEWARE, ALLOWED_HOSTS=['*'])
class ApiSwaggerTest(APITestCase):
    """Compare the complete API schema using real resolver endpoints and the original inspector."""

    def schema(self):
        response = self.client.get('/swagger/?format=openapi', HTTP_HOST='api.example.org',
                                   HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(response.status_code, 200)
        return response.data

    @override_settings(FHIR_SUBDOMAIN='fhir')
    def test_separate_host_omits_fhir_and_links_to_its_swagger(self):
        schema = self.schema()
        self.assertFalse(any(is_fhir_path(path) for path in schema['paths']))
        self.assertFalse(any('CodeSystem' in path or 'ValueSet' in path or 'ConceptMap' in path
                             for path in schema['paths']))
        self.assertIn('https://fhir.example.org/swagger/', schema['info']['description'])
        self.assertNotIn('fhir.example.org', api_info.description)

    @override_settings(FHIR_SUBDOMAIN=None)
    def test_same_host_includes_only_curated_fhir(self):
        schema = self.schema()
        fhir = {key: operation for key, operation in operations_in(schema).items() if is_fhir_path(key[1])}
        curated = operations_in(FhirSwaggerSchemaGenerator(api_info).get_schema(public=True))
        self.assertEqual(fhir, curated)
        self.assertFalse(any('CodeSystem$' in path or 'ValueSet$' in path or 'ConceptMap$' in path
                             for path in schema['paths']))

    def test_non_fhir_paths_and_operations_are_unchanged(self):
        request = Request(RequestFactory().get('/swagger/', HTTP_HOST='api.example.org'))
        original = OriginalSchemaGenerator(api_info)
        endpoints = {path: endpoint for path, endpoint in original.get_endpoints(request).items()
                     if not issubclass(endpoint[0], FhirViewMixin) and not is_fhir_path(path)}
        original_schema = original.get_schema(request, public=True)
        expected = {path: item for path, item in original_schema.paths.items() if path in endpoints}
        self.assertTrue(expected)
        for subdomain in ('fhir', None):
            with self.settings(FHIR_SUBDOMAIN=subdomain):
                schema = OCLSwaggerSchemaGenerator(api_info).get_schema(request, public=True)
            actual = {path: item for path, item in schema.paths.items() if not is_fhir_path(path)}
            self.assertEqual(actual, dict(expected))
