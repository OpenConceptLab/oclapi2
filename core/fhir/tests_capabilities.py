"""
FHIR metadata at the global base /fhir/ and at owner bases: the CapabilityStatement built from the routes and views
that exist (fhirVersion 4.0.1, each resource's interactions, search parameters and operations, $versions, SVCM's
Terminology Repository, the software), $versions, and TerminologyCapabilities listing the code systems the base shows
the caller, with the versions it serves and the default one, in a fixed number of queries, with the URL registry
toggle off and on. OpenConceptLab/ocl_online#451.
"""
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, include, path, resolve as resolve_path
from fhir.resources.R4B.capabilitystatement import CapabilityStatement
from fhir.resources.R4B.parameters import Parameters
from fhir.resources.R4B.terminologycapabilities import TerminologyCapabilities
from mock import patch
from rest_framework.test import APIClient

from core import __version__
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLTestCase, OCLAPITransactionTestCase
from core.fhir import base, search
from core.fhir.base import FhirOperationView
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM
from core.fhir.resolve import resolve
from core.fhir.tests_ops_lookup import OpsCorpusMixin, CIEL, ICD10, REST, SHARED, NEWEST_PRIVATE, HIV
from core.fhir.tests_search import source
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.url_registry.tests_resolution import set_registry_toggle
from core.users.tests.factories import UserProfileFactory

SVCM_REPOSITORY = 'https://profiles.ihe.net/ITI/SVCM/CapabilityStatement/IHE.SVCM.TerminologyRepository|1.5.1'
OPERATION_DEFINITION = 'http://hl7.org/fhir/OperationDefinition/'
CLOSED = 'http://moh.example/fhir/CodeSystem/closed'
DRAFT = 'http://example.org/fhir/CodeSystem/draft'
# every R4 operation on the three resources and at the base, routed or not
R4_OPERATIONS = {
    'CodeSystem': ('lookup', 'validate-code', 'subsumes', 'find-matches'),
    'ValueSet': ('expand', 'validate-code'),
    'ConceptMap': ('translate', 'closure'),
}
R4_SYSTEM_OPERATIONS = ('versions', 'closure', 'convert', 'meta')
BASES = ('/fhir', '/orgs/CIEL', '/users/jdoe')


def is_operation_view(url):
    """Whether Django routes a path to a FHIR operation view."""
    try:
        view = resolve_path(url).func.view_class
    except (Resolver404, AttributeError):
        return False
    return issubclass(view, FhirOperationView)


def routed_operations(fhir_base='/fhir'):
    """(resource type, operation) for each R4 operation Django routes to an operation view, at the type or instance
    level."""
    return {(resource_type, name) for resource_type, names in R4_OPERATIONS.items() for name in names
            if is_operation_view(f'{fhir_base}/{resource_type}/${name}') or
            is_operation_view(f'{fhir_base}/{resource_type}/some-id/${name}')}


def client_for(user=None):
    client = APIClient()
    if user:
        client.force_authenticate(user)
    return client


def listed_operations(statement):
    return {(resource['type'], operation['name']) for resource in statement['rest'][0]['resource']
            for operation in resource.get('operation', [])}


class CapabilityStatementTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        OrganizationFactory(mnemonic='CIEL')
        UserProfileFactory(username='jdoe')

    def statement(self, fhir_base='/fhir', **params):
        response = APIClient().get(f'{fhir_base}/metadata', params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['resourceType'], 'CapabilityStatement')
        return response.data

    def test_fhir_version_is_r4s(self):
        statement = self.statement()
        self.assertEqual(statement['fhirVersion'], base.FHIR_RELEASE)
        self.assertEqual(statement['fhirVersion'], '4.0.1')
        self.assertEqual((statement['kind'], statement['status']), ('instance', 'active'))

    def test_the_operations_are_the_routed_ones(self):
        for fhir_base in BASES:
            statement = self.statement(fhir_base)
            self.assertEqual(listed_operations(statement), routed_operations(fhir_base), fhir_base)
            for resource in statement['rest'][0]['resource']:
                for operation in resource.get('operation', []):
                    self.assertEqual(
                        operation['definition'], f"{OPERATION_DEFINITION}{resource['type']}-{operation['name']}")
            self.assertEqual(statement['rest'][0]['operation'], [
                {'name': 'versions', 'definition': f'{OPERATION_DEFINITION}CapabilityStatement-versions'}])
        self.assertTrue({('CodeSystem', 'lookup'), ('CodeSystem', 'validate-code'), ('ValueSet', 'validate-code'),
                         ('ConceptMap', 'translate')} <= listed_operations(self.statement()))
        self.assertNotIn(('CodeSystem', 'subsumes'), listed_operations(self.statement()))

    def test_system_operations_are_the_routed_ones(self):
        routed = [name for name in R4_SYSTEM_OPERATIONS if is_operation_view(f'/fhir/${name}')]
        self.assertEqual(routed, ['versions'])
        self.assertEqual([operation['name'] for operation in self.statement()['rest'][0]['operation']], routed)

    def test_interactions_and_search_parameters_are_the_searchs(self):
        resources = {resource['type']: resource for resource in self.statement()['rest'][0]['resource']}
        self.assertEqual(list(resources), ['CodeSystem', 'ValueSet', 'ConceptMap'])
        for resource_type, resource in resources.items():
            self.assertEqual([item['code'] for item in resource['interaction']], ['read', 'search-type'])
            names = [item['name'] for item in resource['searchParam']]
            self.assertEqual(names, [*search.PARAMETERS[resource_type], *search.STRING_FIELDS])
            for item in resource['searchParam']:
                self.assertTrue(item['definition'].startswith('http://hl7.org/fhir/SearchParameter/'), item)
        code_system = {item['name']: item for item in resources['CodeSystem']['searchParam']}
        self.assertEqual(code_system['url'], {
            'name': 'url', 'definition': 'http://hl7.org/fhir/SearchParameter/conformance-url', 'type': 'uri'})
        self.assertEqual(code_system['_lastUpdated']['type'], 'date')
        self.assertEqual(code_system['title']['type'], 'string')
        self.assertEqual(code_system['code']['definition'], 'http://hl7.org/fhir/SearchParameter/CodeSystem-code')
        concept_map = {item['name']: item['type'] for item in resources['ConceptMap']['searchParam']}
        self.assertEqual((concept_map['source-system'], concept_map['source-uri']), ('uri', 'reference'))

    def test_it_instantiates_svcms_terminology_repository_and_names_the_software(self):
        statement = self.statement()
        self.assertEqual(statement['instantiates'], [SVCM_REPOSITORY])
        self.assertEqual(statement['software'], {'name': 'OCL API', 'version': __version__})
        self.assertEqual(statement['format'], ['application/fhir+json', 'application/fhir+xml'])
        self.assertIn('date', statement)

    def test_the_implementation_url_is_the_requests_base(self):
        response = APIClient().get(
            '/orgs/CIEL/metadata', HTTP_HOST='fhir.example.org', HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(response.data['implementation']['url'], 'https://fhir.example.org/orgs/CIEL')
        self.assertEqual(self.statement('/users/jdoe')['implementation']['url'], 'http://testserver/users/jdoe')
        self.assertEqual(self.statement()['implementation']['url'], 'http://testserver/fhir')

    def test_every_base_with_and_without_a_trailing_slash(self):
        expected = self.statement()
        for fhir_base in ('/fhir', *BASES[1:], '/orgs/no-such-org', '/users/no-such-user'):
            for suffix in ('metadata', 'metadata/'):
                response = APIClient().get(f'{fhir_base}/{suffix}')
                self.assertEqual(response.status_code, 200, (fhir_base, suffix))
                answer = response.data
                self.assertEqual(answer['implementation']['url'], f'http://testserver{fhir_base}')
                self.assertEqual({**answer, 'implementation': None}, {**expected, 'implementation': None}, fhir_base)
        self.assertEqual(APIClient().get('/fhir/').data, expected)

    def test_it_parses_as_r4_json_and_xml(self):
        for fhir_base in BASES:
            for media_type in ('application/fhir+json', 'application/fhir+xml'):
                response = APIClient().get(f'{fhir_base}/metadata', HTTP_ACCEPT=media_type)
                self.assertEqual(response['Content-Type'].split(';')[0], media_type)
                statement = (CapabilityStatement.model_validate_xml(response.content) if media_type.endswith('xml')
                             else CapabilityStatement.model_validate_json(response.content))
                self.assertEqual(statement.fhirVersion, '4.0.1')
                self.assertEqual(len(statement.rest[0].resource), 3)

    def test_get_only(self):
        for fhir_base in BASES:
            response = APIClient().post(f'{fhir_base}/metadata', {}, format='json')
            self.assertEqual(response.status_code, 405, fhir_base)
            self.assertEqual(response.data['resourceType'], 'OperationOutcome')

    def test_another_fhir_version_is_refused(self):
        for fhir_base in BASES:
            for suffix in ('metadata', 'metadata?mode=terminology', '$versions'):
                response = APIClient().get(
                    f'{fhir_base}/{suffix}', HTTP_ACCEPT='application/fhir+json; fhirVersion=3.0')
                self.assertEqual(response.status_code, 406, (fhir_base, suffix))
                issue = response.data['issue'][0]
                self.assertEqual(issue['code'], 'not-supported')
                self.assertEqual(issue['details']['coding'][0]['system'], TX_ISSUE_TYPE_SYSTEM)
                response = APIClient().get(
                    f'{fhir_base}/{suffix}', HTTP_ACCEPT='application/fhir+json; fhirVersion=4.0')
                self.assertEqual(response.status_code, 200, (fhir_base, suffix))

    def test_reads_nothing_from_the_database(self):
        self.statement()
        with self.assertNumQueries(0):
            self.statement()
        with self.assertNumQueries(0):
            self.statement('/orgs/CIEL')


class RoutedOperationView(FhirOperationView):
    """An operation the statement has not heard of, routed for the test below."""
    operation = staticmethod(lambda *args: {'resourceType': 'Parameters'})
    adapter = staticmethod(lambda answer: answer)


# A URL configuration with CodeSystem/$subsumes and ValueSet/$expand routed, and no ConceptMap at all
urlpatterns = [
    path('fhir/', include('core.fhir.urls')),
    path('fhir/CodeSystem/$subsumes', RoutedOperationView.as_view()),
    path('fhir/CodeSystem/', include('core.code_systems.urls')),
    path('fhir/ValueSet/$expand', RoutedOperationView.as_view()),
    path('fhir/ValueSet/', include('core.value_sets.urls')),
]


@override_settings(ROOT_URLCONF=__name__)
class TheRoutesMakeTheStatementTest(OCLTestCase):
    """Routing an operation lists it, and a resource type with no routes isn't listed: nothing else changes."""

    def test_an_added_route_is_listed_and_a_removed_one_is_not(self):
        statement = APIClient().get('/fhir/metadata').data
        resources = {resource['type']: resource for resource in statement['rest'][0]['resource']}
        self.assertEqual(list(resources), ['CodeSystem', 'ValueSet'])
        self.assertEqual([item['name'] for item in resources['CodeSystem']['operation']],
                         ['subsumes', 'lookup', 'validate-code'])
        self.assertIn('expand', [item['name'] for item in resources['ValueSet']['operation']])
        self.assertEqual(listed_operations(statement), routed_operations())

        capabilities = APIClient().get('/fhir/metadata', {'mode': 'terminology'}).data
        self.assertEqual(capabilities['expansion'], {'paging': True})
        self.assertNotIn('translation', capabilities)


class VersionsTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        OrganizationFactory(mnemonic='CIEL')
        UserProfileFactory(username='jdoe')

    def test_versions_at_every_base_with_and_without_a_trailing_slash(self):
        expected = {'resourceType': 'Parameters', 'parameter': [
            {'name': 'version', 'valueCode': '4.0'}, {'name': 'default', 'valueCode': '4.0'}]}
        for fhir_base in (*BASES, '/orgs/no-such-org'):
            for suffix in ('$versions', '$versions/'):
                response = APIClient().get(f'{fhir_base}/{suffix}')
                self.assertEqual((response.status_code, response.data), (200, expected), (fhir_base, suffix))

    def test_the_versions_are_the_negotiated_ones(self):
        response = APIClient().get('/fhir/$versions')
        self.assertEqual([item['valueCode'] for item in response.data['parameter']],
                         [base.FHIR_VERSION, base.FhirVersioning.default_version])

    def test_it_parses_as_r4_json_and_xml(self):
        for media_type in ('application/fhir+json', 'application/fhir+xml'):
            response = APIClient().get('/orgs/CIEL/$versions', HTTP_ACCEPT=media_type)
            parameters = (Parameters.model_validate_xml(response.content) if media_type.endswith('xml')
                          else Parameters.model_validate_json(response.content))
            self.assertEqual([(item.name, item.valueCode) for item in parameters.parameter],
                             [('version', '4.0'), ('default', '4.0')])

    def test_get_only(self):
        for fhir_base in BASES:
            response = APIClient().post(f'{fhir_base}/$versions', {}, format='json')
            self.assertEqual(response.status_code, 405, fhir_base)
            self.assertEqual(response.data['resourceType'], 'OperationOutcome')


class OwnerBaseFhirPathTest(SimpleTestCase):
    """An owner base's metadata and $versions are FHIR paths, so the FHIR host serves them and errors are outcomes."""

    def test_owner_metadata_and_versions_are_fhir_paths(self):
        for path_ in ('/orgs/CIEL/metadata', '/orgs/CIEL/metadata/', '/users/jo/$versions', '/users/jo/$versions/'):
            self.assertTrue(base.is_fhir_path(path_), path_)
        for path_ in ('/orgs/CIEL/', '/orgs/CIEL/metadata/x/', '/orgs/metadata/', '/sources/x/metadata',
                      '/users/jo/extras/'):
            self.assertFalse(base.is_fhir_path(path_), path_)


class TerminologyCapabilitiesCases(OpsCorpusMixin):
    """
    The code systems a base shows the caller, as its CodeSystem search lists them, with the versions it serves, newest
    first, and the one it serves without a version marked isDefault. On top of the operations' corpus: HL7's retired
    release beside its live one, a member of HL7, MOH's public release of a repository MOH made private, sehatapp's
    copy of ICD-10 (a canonical shared by two public repositories), and Acme's code system without a canonical URL and
    one with nothing released.
    """

    def setUp(self):
        super().setUp()
        self.rest_retired = source(self.hl7, 'restful-interaction', '3.0.1', released=True, retired=True,
                                   canonical_url=REST)
        self.hl7_member = UserProfileFactory(username='hl7-member')
        self.hl7.members.add(self.hl7_member)
        self.closed = source(self.moh, 'closed', canonical_url=CLOSED, public_access=ACCESS_TYPE_NONE)
        self.closed_v1 = source(self.moh, 'closed', 'v1', released=True, canonical_url=CLOSED)
        self.sehatapp = OrganizationFactory(mnemonic='sehatapp')
        source(self.sehatapp, 'ICD-10', canonical_url=ICD10)
        source(self.sehatapp, 'ICD-10', 'v1.0', released=True, canonical_url=ICD10)
        source(self.acme, 'nameless')
        source(self.acme, 'nameless', 'v1', released=True)
        source(self.acme, 'draft', canonical_url=DRAFT)
        source(self.acme, 'draft', 'v1', canonical_url=DRAFT)
        self.users = (None, self.outsider, self.moh_member, self.hl7_member, self.jdoe, self.admin)

    def capabilities(self, fhir_base='/fhir', user=None, **params):
        response = client_for(user).get(f'{fhir_base}/metadata', {'mode': 'terminology', **params})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['resourceType'], 'TerminologyCapabilities')
        return response

    def code_systems(self, fhir_base='/fhir', user=None):
        """{uri: [version, with a * on the default]} of a base's TerminologyCapabilities for a caller."""
        return {item['uri']: [version['code'] + ('*' if version.get('isDefault') else '')
                              for version in item['version']]
                for item in self.capabilities(fhir_base, user).data.get('codeSystem', [])}

    def test_the_global_base_lists_the_public_released_code_systems_to_everyone(self):
        expected = {
            CIEL: ['v2*', 'v1'],
            ICD10: ['v1.0', 'ICD102019'],  # two repositories: no default
            NEWEST_PRIVATE: ['v1*'],
            REST: ['3.0.1', '4.0.1*'],  # the retired release is served when named, never by default
            SHARED: ['v1*'],
        }
        for user in self.users:
            self.assertEqual(self.code_systems(user=user), expected, user)
        self.assertEqual(list(self.code_systems()), sorted(expected))

    def test_an_owner_base_lists_the_owners_to_those_who_may_view_them(self):
        public_hl7 = {NEWEST_PRIVATE: ['v1*'], REST: ['3.0.1', '4.0.1*'], SHARED: ['v1*']}
        for user in self.users:
            may_view = user in (self.hl7_member, self.admin)
            self.assertEqual(self.code_systems('/orgs/HL7', user), {
                **public_hl7, NEWEST_PRIVATE: ['v2*', 'v1'] if may_view else ['v1*']}, user)
            self.assertEqual(self.code_systems('/orgs/MOH', user), {HIV: ['v1*'], CLOSED: ['v1*']} if user in (
                self.moh_member, self.admin) else {}, user)
            self.assertEqual(self.code_systems('/users/jdoe', user), {SHARED: ['v1*']} if user in (
                self.jdoe, self.admin) else {}, user)
            self.assertEqual(self.code_systems('/orgs/no-such-org', user), {}, user)
            self.assertEqual(self.code_systems('/orgs/Acme', user), {}, user)
        self.assertEqual(self.code_systems('/orgs/CIEL'), {CIEL: ['v2*', 'v1']})
        self.assertEqual(self.code_systems('/orgs/WHO'), {ICD10: ['ICD102019*']})
        self.assertEqual(self.code_systems('/orgs/sehatapp'), {ICD10: ['v1.0*']})

    def test_what_it_lists_agrees_with_resolve(self):
        """Every version listed for a canonical one repository holds is served by name, and isDefault is the one
        served without a version, at every base, to every caller."""
        cases = [({}, '/fhir'), ({'user': 'jdoe'}, '/users/jdoe')] + [
            ({'org': org}, f'/orgs/{org}') for org in ('HL7', 'MOH', 'CIEL', 'WHO', 'sehatapp')]
        for kwargs, fhir_base in cases:
            for user in self.users:
                for uri, versions in self.code_systems(fhir_base, user).items():
                    if uri == ICD10 and fhir_base == '/fhir':
                        continue  # shared by two repositories: the resolver serves one of them
                    for version in versions:
                        self.assertEqual(resolve('CodeSystem', uri, version.rstrip('*'), kwargs, user).version,
                                         version.rstrip('*'), (fhir_base, user, uri))
                    default = [version.rstrip('*') for version in versions if version.endswith('*')]
                    self.assertEqual([resolve('CodeSystem', uri, None, kwargs, user).version], default,
                                     (fhir_base, user, uri))

    def test_a_hidden_repository_answers_as_one_that_does_not_exist(self):
        """MOH's private repositories, to those who may not view them, and at /fhir/ to everyone: the same bytes and
        the same number of queries as when they don't exist."""
        hidden = [('/orgs/MOH', None), ('/orgs/MOH', self.outsider), ('/orgs/MOH', self.hl7_member),
                  *[('/fhir', user) for user in self.users]]

        def answers():
            found = {}
            for fhir_base, user in hidden:
                with CaptureQueriesContext(connection) as queries:
                    content = self.capabilities(fhir_base, user).content
                found[(fhir_base, user)] = (content, len(queries))
            return found

        with_them = answers()
        self.assertNotIn(HIV.encode(), b''.join(content for content, _ in with_them.values()))
        self.assertNotIn(CLOSED.encode(), b''.join(content for content, _ in with_them.values()))
        Source.objects.filter(mnemonic__in=['hiv', 'closed'], organization=self.moh).delete()
        self.assertEqual(answers(), with_them)

    def test_a_fixed_number_of_queries(self):
        """One query for the list, whatever its size; at an owner base, the owner and the caller's rights first."""
        def count(expected, fhir_base='/fhir', user=None):
            with self.assertNumQueries(expected):
                return len(self.capabilities(fhir_base, user).data.get('codeSystem', []))

        self.assertEqual(count(1), 5)
        self.assertEqual(count(2, '/orgs/HL7'), 3)
        self.assertEqual(count(3, '/orgs/HL7', self.hl7_member), 3)
        for number in range(30):
            url = f'http://example.org/fhir/CodeSystem/many-{number}'
            source(self.hl7, f'many-{number}', canonical_url=url)
            for version in ('v1', 'v2'):
                source(self.hl7, f'many-{number}', version, released=True, canonical_url=url)
        self.assertEqual(count(1), 35)
        self.assertEqual(count(2, '/orgs/HL7'), 33)
        self.assertEqual(count(3, '/orgs/HL7', self.hl7_member), 33)

    def test_the_blocks_are_the_routed_operations(self):
        capabilities = self.capabilities().data
        routed = routed_operations()
        self.assertEqual(capabilities.get('translation'),
                         {'needsMap': False} if ('ConceptMap', 'translate') in routed else None)
        self.assertEqual(capabilities.get('expansion'),
                         {'paging': True} if ('ValueSet', 'expand') in routed else None)
        self.assertNotIn('validateCode', capabilities)

    def test_it_describes_the_service_as_the_statement_does(self):
        capabilities = self.capabilities('/orgs/HL7').data
        statement = client_for().get('/orgs/HL7/metadata').data
        for key in ('status', 'date', 'kind', 'software', 'implementation'):
            self.assertEqual(capabilities[key], statement[key], key)

    def test_it_parses_as_r4_json_and_xml(self):
        for fhir_base, user in (('/fhir', None), ('/orgs/HL7', self.hl7_member), ('/orgs/no-such-org', None)):
            for media_type in ('application/fhir+json', 'application/fhir+xml'):
                response = client_for(user).get(
                    f'{fhir_base}/metadata', {'mode': 'terminology'}, HTTP_ACCEPT=media_type)
                self.assertEqual(response['Content-Type'].split(';')[0], media_type)
                capabilities = (
                    TerminologyCapabilities.model_validate_xml(response.content) if media_type.endswith('xml')
                    else TerminologyCapabilities.model_validate_json(response.content))
                self.assertEqual(capabilities.kind, 'instance')
        listed = TerminologyCapabilities.model_validate_xml(client_for().get(
            '/fhir/metadata', {'mode': 'terminology'}, HTTP_ACCEPT='application/fhir+xml').content)
        self.assertEqual([(item.uri, [(version.code, version.isDefault) for version in item.version])
                          for item in listed.codeSystem if item.uri == CIEL], [(CIEL, [('v2', True), ('v1', None)])])


class TerminologyCapabilitiesWithoutRegistryTest(TerminologyCapabilitiesCases, OCLTestCase):
    registry = False


class TerminologyCapabilitiesWithRegistryTest(TerminologyCapabilitiesCases, OCLTestCase):
    registry = True

    def tearDown(self):
        set_registry_toggle(False)
        super().tearDown()


class TerminologyCapabilitiesTimeoutTest(OCLAPITransactionTestCase):
    """The list's query is bounded as a search's is: one the database cancels is too costly (422), never a 5xx."""

    def test_a_cancelled_statement_is_too_costly(self):
        org = OrganizationFactory(mnemonic='Acme')
        source(org, 'codes', canonical_url=DRAFT)
        source(org, 'codes', 'v1', released=True, canonical_url=DRAFT)

        def slow(*args, **kwargs):  # pylint: disable=unused-argument
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_sleep(1)')
            return []

        with patch('core.fhir.search.SEARCH_TIMEOUT_MS', 50), patch('core.fhir.capabilities.listed_versions', slow):
            response = APIClient().get('/fhir/metadata', {'mode': 'terminology'})
        self.assertEqual(response.status_code, 422, response.data)
        issue = response.data['issue'][0]
        self.assertEqual(issue['code'], 'too-costly')
        self.assertIn({'system': TX_ISSUE_TYPE_SYSTEM, 'code': 'too-costly'}, issue['details']['coding'])
        self.assertEqual([item['uri'] for item in APIClient().get(
            '/fhir/metadata', {'mode': 'terminology'}).data['codeSystem']], [DRAFT])
