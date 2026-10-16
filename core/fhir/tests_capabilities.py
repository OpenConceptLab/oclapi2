"""
FHIR metadata at the global base /fhir/ and at owner bases: the CapabilityStatement built from the routes and views
that exist (fhirVersion 4.0.1, each resource's interactions, search parameters and operations, $versions, SVCM's
Terminology Repository, the software), $versions, and TerminologyCapabilities listing the code systems the base shows
the caller, with the versions it serves and the default one, in a fixed number of queries, with the URL registry
toggle off and on. OpenConceptLab/ocl_online#451.
"""
from django.db import connection, transaction
from django.db.models import Q
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, include, path, resolve as resolve_path
from fhir.resources.R4B.capabilitystatement import CapabilityStatement
from fhir.resources.R4B.parameters import Parameters
from fhir.resources.R4B.terminologycapabilities import TerminologyCapabilities
from mock import patch
from rest_framework.test import APIClient

from core import __version__
from core.common.constants import ACCESS_TYPE_NONE, HEAD
from core.common.serializers import IdentifierSerializer
from core.common.tests import OCLTestCase, OCLAPITransactionTestCase
from core.fhir import base, search
from core.fhir.base import FhirOperationView
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM
from core.fhir.resolve import UnresolvedError, namespace_of, resolve, viewable_rows
from core.fhir.tests_ops_lookup import OpsCorpusMixin, CIEL, ICD10, REST, SHARED, NEWEST_PRIVATE, HIV
from core.fhir.tests_search import source
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.url_registry.factories import OrganizationURLRegistryFactory
from core.url_registry.models import URLRegistry
from core.url_registry.tests_resolution import set_registry_toggle
from core.users.tests.factories import UserProfileFactory

SVCM_REPOSITORY = 'https://profiles.ihe.net/ITI/SVCM/CapabilityStatement/IHE.SVCM.TerminologyRepository|1.5.1'
OPERATION_DEFINITION = 'http://hl7.org/fhir/OperationDefinition/'
CLOSED = 'http://moh.example/fhir/CodeSystem/closed'
DRAFT = 'http://example.org/fhir/CodeSystem/draft'
UNREGISTERED = 'http://example.org/fhir/CodeSystem/unregistered'
REDIRECTED = 'http://example.org/fhir/CodeSystem/redirected'
COLLIDE = 'http://example.org/fhir/CodeSystem/collide'
OLD, NEW = 'http://example.org/fhir/CodeSystem/renamed', 'http://example.org/fhir/CodeSystem/renamed-new'
RETIRED_HEAD = 'http://example.org/fhir/CodeSystem/retired-head'
NAMELESS = '/orgs/Acme/CodeSystem/nameless/'  # a code system without a canonical URL, by its OCL path
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


class Rollback(Exception):
    """Ends a savepoint, undoing what ran in it."""


class TerminologyCapabilitiesCases(OpsCorpusMixin):  # pylint: disable=too-many-instance-attributes
    """
    The code systems the operations serve the caller at a base, each under the identifier they resolve it by (its
    canonical URL, or its OCL path when it has none), with the released versions served by name, newest first, and the
    one served without a version marked isDefault; as the resolver serves them were the repositories the caller isn't
    shown not there. On top of the operations' corpus: HL7's retired release beside its live one, a member of HL7, MOH's
    public release of a repository MOH made private, sehatapp's copy of ICD-10 (a canonical two public repositories
    share), and these, mostly Acme's: a code system without a canonical URL; one with nothing released; one the
    registry doesn't hold; one whose newest release has a canonical URL of its own; one retired (its HEAD); a canonical
    Acme holds and HL7 holds too, which Acme's registry sends to HL7; and two of HL7's sharing a canonical, the older
    private.
    """

    def setUp(self):  # pylint: disable=too-many-statements
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
        source(self.acme, 'unregistered', canonical_url=UNREGISTERED)
        source(self.acme, 'unregistered', 'v1', released=True, canonical_url=UNREGISTERED)
        source(self.acme, 'renamed', canonical_url=OLD)
        source(self.acme, 'renamed', 'v1', released=True, canonical_url=None)
        source(self.acme, 'renamed', 'v2', released=True, canonical_url=NEW)
        source(self.acme, 'retired-head', canonical_url=RETIRED_HEAD, retired=True)
        source(self.acme, 'retired-head', 'v1', released=True, canonical_url=RETIRED_HEAD)
        source(self.acme, 'local', canonical_url=REDIRECTED)
        source(self.acme, 'local', 'v1', released=True, canonical_url=REDIRECTED)
        source(self.hl7, 'redirected', canonical_url=REDIRECTED)
        source(self.hl7, 'redirected', 'r1', released=True, canonical_url=REDIRECTED)
        OrganizationURLRegistryFactory(organization=self.acme, url=REDIRECTED, namespace=self.hl7.uri)
        source(self.hl7, 'collide-private', canonical_url=COLLIDE, public_access=ACCESS_TYPE_NONE)
        source(self.hl7, 'collide-private', 'p1', released=True, canonical_url=COLLIDE,
               public_access=ACCESS_TYPE_NONE)
        source(self.hl7, 'collide-public', canonical_url=COLLIDE)
        source(self.hl7, 'collide-public', 'v1', released=True, canonical_url=COLLIDE)
        self.users = (None, self.outsider, self.moh_member, self.hl7_member, self.jdoe, self.admin)

    def capabilities(self, fhir_base='/fhir', user=None, **params):
        response = client_for(user).get(f'{fhir_base}/metadata', {'mode': 'terminology', **params})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['resourceType'], 'TerminologyCapabilities')
        return response

    def code_systems(self, fhir_base='/fhir', user=None):
        """{uri: [version, with a * on the default]} of a base's TerminologyCapabilities for a caller."""
        return {item['uri']: [version['code'] + ('*' if version.get('isDefault') else '')
                              for version in item.get('version', [])]
                for item in self.capabilities(fhir_base, user).data.get('codeSystem', [])}

    @staticmethod
    def served(kwargs, user):
        """
        {identifier: (released versions served by name, sorted; the version served without one)} by resolve() itself,
        over every canonical URL OCL holds or registers, and the OCL path of every repository without one (at an owner
        base, the owner's), each with every released version code OCL holds.
        """
        namespace = namespace_of(kwargs)
        urls = set(Source.objects.exclude(canonical_url=None).exclude(canonical_url='').values_list(
            'canonical_url', flat=True)) | set(URLRegistry.objects.values_list('url', flat=True))
        heads = Source.objects.filter(version=HEAD).filter(Q(canonical_url=None) | Q(canonical_url=''))
        if namespace:
            heads = heads.filter(Q(organization__uri=namespace) | Q(user__uri=namespace))
        paths = {IdentifierSerializer.convert_ocl_uri_to_fhir_url(uri, 'CodeSystem')
                 for uri in heads.values_list('uri', flat=True)}
        codes = set(Source.objects.filter(released=True).exclude(version=HEAD).values_list('version', flat=True))
        found = {}
        for url in urls | paths:
            try:
                default = resolve('CodeSystem', url, None, kwargs, user).version
            except UnresolvedError:
                continue
            versions = []
            for code in codes:
                try:
                    instance = resolve('CodeSystem', url, code, kwargs, user)
                except UnresolvedError:
                    continue
                if instance.released and not instance.is_head:
                    versions.append(code)
            found[url] = (sorted(versions), default)
        return found

    def served_were_hidden_missing(self, kwargs, user):
        """served(), with the repositories the base doesn't show the caller (their HEAD) deleted, then restored."""
        hidden = Source.objects.filter(version=HEAD).exclude(
            id__in=viewable_rows(Source, namespace_of(kwargs), user).filter(version=HEAD).values('id'))
        found = None
        try:
            with transaction.atomic():
                for mnemonic, organization_id, user_id in hidden.values_list('mnemonic', 'organization_id', 'user_id'):
                    Source.objects.filter(mnemonic=mnemonic, organization_id=organization_id, user_id=user_id).delete()
                found = self.served(kwargs, user)
                raise Rollback
        except Rollback:
            pass
        return found

    def test_it_lists_what_the_resolver_serves_were_hidden_repositories_missing(self):
        """
        At every base, to every caller: exactly the identifiers the operations resolve, with the released versions they
        serve by name and the one they serve without a version, as they serve them were the repositories the caller
        isn't shown missing. With them there, every version listed is served, but where a hidden repository of the
        base's owner holds the canonical too: the resolver tries it and stops there (open decision A.3).
        """
        cases = [({}, '/fhir'), ({'user': 'jdoe'}, '/users/jdoe')] + [
            ({'org': org}, f'/orgs/{org}') for org in ('HL7', 'MOH', 'CIEL', 'WHO', 'sehatapp', 'Acme')]
        for kwargs, fhir_base in cases:
            for user in self.users:
                listed = self.code_systems(fhir_base, user)
                self.assertEqual(
                    {uri: (sorted(code.rstrip('*') for code in codes),
                           next((code.rstrip('*') for code in codes if code.endswith('*')), None))
                     for uri, codes in listed.items()},
                    self.served_were_hidden_missing(kwargs, user), (fhir_base, user))
                unserved = set()
                for uri, codes in listed.items():
                    for code in [None, *(code.rstrip('*') for code in codes)]:
                        try:
                            resolve('CodeSystem', uri, code, kwargs, user)
                        except UnresolvedError:
                            unserved.add(uri)
                self.assertEqual(unserved, self.hidden_collisions(fhir_base, user), (fhir_base, user))

    def hidden_collisions(self, fhir_base, user):
        """The canonicals a hidden repository of the base's owner holds that the listing serves from another one."""
        if fhir_base == '/orgs/HL7' and user not in (self.hl7_member, self.admin):
            return {COLLIDE}
        if fhir_base == '/users/jdoe' and self.registry and user not in (self.jdoe, self.admin):
            return {SHARED}  # jdoe's own copy is private; the global registry's is HL7's
        return set()

    def test_the_global_base(self):
        expected = {
            CIEL: ['v2*', 'v1'], ICD10: ['ICD102019*'], NEWEST_PRIVATE: ['v1*'], REST: ['3.0.1', '4.0.1*'],
            SHARED: ['v1*'], NAMELESS: ['v1*']}
        if not self.registry:
            expected.update({UNREGISTERED: ['v1*'], REDIRECTED: ['v1*'], COLLIDE: ['v1*'], OLD: ['v2*', 'v1']})
        for user in self.users:
            self.assertEqual(self.code_systems(user=user), expected, user)
        self.assertEqual(list(self.code_systems()), sorted(expected))

    def test_with_the_registry_on_only_what_the_registry_resolves(self):
        """F1.1: at /fhir/ a canonical the global registry doesn't hold isn't served; an owner's entry sends its
        canonical where the resolver sends it."""
        self.assertEqual(UNREGISTERED in self.code_systems(), not self.registry)
        acme = self.code_systems('/orgs/Acme')
        self.assertEqual(acme[UNREGISTERED], ['v1*'])
        self.assertEqual(acme[REDIRECTED], ['r1*'] if self.registry else ['v1*'])
        self.assertEqual(resolve('CodeSystem', REDIRECTED, None, {'org': 'Acme'}).version,
                         'r1' if self.registry else 'v1')
        self.assertEqual(CIEL in acme, self.registry)  # the global registry's, where the owner holds none

    def test_a_shared_canonical_is_the_repository_the_resolver_picks(self):
        """F1.2: WHO's ICD-10 (the older), not sehatapp's, with either toggle."""
        self.assertEqual(self.code_systems()[ICD10], ['ICD102019*'])
        with self.assertRaises(UnresolvedError):
            resolve('CodeSystem', ICD10, 'v1.0')
        self.assertEqual(self.code_systems('/orgs/sehatapp'), {
            ICD10: ['v1.0*'], **(self.globals('sehatapp') if self.registry else {})})

    def globals(self, owner):
        """What the global registry serves at an owner base that holds none of its canonicals itself."""
        found = {CIEL: ['v2*', 'v1'], ICD10: ['ICD102019*'], NEWEST_PRIVATE: ['v1*'], REST: ['3.0.1', '4.0.1*'],
                 SHARED: ['v1*']}
        return {url: codes for url, codes in found.items() if not (owner == 'sehatapp' and url == ICD10)}

    def test_a_code_system_without_a_canonical_url_is_listed_by_its_ocl_path(self):
        """F1.3: as the operations resolve it, and as $expand names its members' system."""
        self.assertEqual(NAMELESS, IdentifierSerializer.convert_ocl_uri_to_fhir_url('/orgs/Acme/sources/nameless/',
                                                                                    'CodeSystem'))
        for fhir_base, kwargs in (('/fhir', {}), ('/orgs/Acme', {'org': 'Acme'})):
            self.assertEqual(self.code_systems(fhir_base)[NAMELESS], ['v1*'], fhir_base)
            self.assertEqual(resolve('CodeSystem', NAMELESS, None, kwargs).version, 'v1')
        self.assertNotIn(NAMELESS, self.code_systems('/orgs/HL7'))  # another owner's

    def test_a_hidden_repository_of_the_owner_is_listed_as_missing(self):
        """
        F1.4: HL7's private collide-private is older than collide-public. To those who may view it, the resolver serves
        it, and so does the listing. To others, the listing answers byte for byte, in as many queries, as if it
        weren't there: collide-public's, which the resolver at HL7's base doesn't serve them (it tries the hidden one
        and stops: open decision A.3).
        """
        for user in (self.hl7_member, self.admin):
            self.assertEqual(self.code_systems('/orgs/HL7', user)[COLLIDE], ['p1*'], user)
        others = (None, self.outsider, self.moh_member, self.jdoe)
        before = {}
        for user in others:
            with CaptureQueriesContext(connection) as queries:
                before[user] = (self.capabilities('/orgs/HL7', user).content, len(queries))
            self.assertEqual(self.code_systems('/orgs/HL7', user)[COLLIDE], ['v1*'], user)
            with self.assertRaises(UnresolvedError):
                resolve('CodeSystem', COLLIDE, None, {'org': 'HL7'}, user)
        Source.objects.filter(mnemonic='collide-private', organization=self.hl7).delete()
        for user in others:
            with CaptureQueriesContext(connection) as queries:
                content = self.capabilities('/orgs/HL7', user).content
            self.assertEqual((content, len(queries)), before[user], user)
            self.assertEqual(resolve('CodeSystem', COLLIDE, None, {'org': 'HL7'}, user).version, 'v1')

    def test_a_release_is_listed_under_the_url_the_resolver_serves_it_at(self):
        """F1.5: the repository's canonical URL (its HEAD's), not a release's own (open decision DI19)."""
        acme = self.code_systems('/orgs/Acme')
        self.assertEqual(acme[OLD], ['v2*', 'v1'])
        self.assertNotIn(NEW, acme)
        self.assertEqual(resolve('CodeSystem', OLD, 'v2', {'org': 'Acme'}).version, 'v2')
        with self.assertRaises(UnresolvedError):
            resolve('CodeSystem', NEW, None, {'org': 'Acme'})

    def test_a_retired_repository_is_not_listed(self):
        """F1.6: the resolver serves no repository whose HEAD is retired, by canonical URL or path."""
        for fhir_base, kwargs in (('/fhir', {}), ('/orgs/Acme', {'org': 'Acme'})):
            listed = self.code_systems(fhir_base)
            self.assertNotIn(RETIRED_HEAD, listed)
            self.assertNotIn('/orgs/Acme/CodeSystem/retired-head/', listed)
            for url in (RETIRED_HEAD, '/orgs/Acme/CodeSystem/retired-head/'):
                with self.assertRaises(UnresolvedError):
                    resolve('CodeSystem', url, None, kwargs)

    def test_an_owner_base_shows_the_owners_private_code_systems_to_those_who_may_view_them(self):
        for user in self.users:
            hl7 = self.code_systems('/orgs/HL7', user)
            self.assertEqual(hl7[NEWEST_PRIVATE], ['v2*', 'v1'] if user in (self.hl7_member, self.admin) else ['v1*'])
            moh = self.code_systems('/orgs/MOH', user)
            self.assertEqual({url: moh.get(url) for url in (HIV, CLOSED)}, {HIV: ['v1*'], CLOSED: ['v1*']} if user in (
                self.moh_member, self.admin) else {HIV: None, CLOSED: None}, user)
            self.assertEqual(self.code_systems('/orgs/no-such-org', user), {}, user)
        self.assertEqual(self.code_systems('/users/jdoe', self.jdoe)[SHARED], ['v1*'])
        self.assertEqual(self.code_systems('/orgs/CIEL')[CIEL], ['v2*', 'v1'])
        self.assertEqual(self.code_systems('/orgs/WHO')[ICD10], ['ICD102019*'])

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
        """
        The toggle, the HEADs, the versions (with the registry on, its entries too); at an owner base, the owner first:
        whatever the number of code systems.
        """
        registry = 1 if self.registry else 0

        def count(expected, fhir_base='/fhir', user=None):
            with self.assertNumQueries(expected + registry):
                return len(self.capabilities(fhir_base, user).data.get('codeSystem', []))

        found = [count(3), count(4, '/orgs/HL7'), count(4, '/orgs/HL7', self.hl7_member)]
        for number in range(30):
            url = f'http://example.org/fhir/CodeSystem/many-{number}'
            source(self.hl7, f'many-{number}', canonical_url=url)
            for version in ('v1', 'v2'):
                source(self.hl7, f'many-{number}', version, released=True, canonical_url=url)
        self.assertEqual([count(3), count(4, '/orgs/HL7'), count(4, '/orgs/HL7', self.hl7_member)], [
            found[0] + (0 if self.registry else 30), found[1] + 30, found[2] + 30])

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
                          for item in listed.codeSystem if item.uri in (CIEL, NAMELESS)],
                         [(NAMELESS, [('v1', True)]), (CIEL, [('v2', True), ('v1', None)])])


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
        set_registry_toggle(False)
        org = OrganizationFactory(mnemonic='Acme')
        source(org, 'codes', canonical_url=DRAFT)
        source(org, 'codes', 'v1', released=True, canonical_url=DRAFT)

        def slow(*args, **kwargs):  # pylint: disable=unused-argument
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_sleep(1)')
            return []

        with patch('core.fhir.search.SEARCH_TIMEOUT_MS', 50), patch('core.fhir.capabilities.served_versions', slow):
            response = APIClient().get('/fhir/metadata', {'mode': 'terminology'})
        self.assertEqual(response.status_code, 422, response.data)
        issue = response.data['issue'][0]
        self.assertEqual(issue['code'], 'too-costly')
        self.assertIn({'system': TX_ISSUE_TYPE_SYSTEM, 'code': 'too-costly'}, issue['details']['coding'])
        self.assertEqual([item['uri'] for item in APIClient().get(
            '/fhir/metadata', {'mode': 'terminology'}).data['codeSystem']], [DRAFT])
