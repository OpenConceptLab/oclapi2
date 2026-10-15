"""
The FHIR protocol through the real views (OpenConceptLab/ocl_online#451): FHIR JSON and XML in and out, _format,
the fhirVersion parameter, trailing slashes on operations, and errors as OperationOutcome, never a 5xx.
"""
import json
from unittest.mock import patch, Mock

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codesystem import CodeSystem
from django.utils import timezone
from lxml import etree
from rest_framework.test import APIClient

from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.fhir import base
from core.fhir.outcomes import TX_ISSUE_TYPE_SYSTEM
from core.fhir.tests_base import assert_in_model_order, child_names
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.tests.factories import UserProfileFactory

FHIR = '{http://hl7.org/fhir}'
SYSTEM = 'http://example.org/fhir/CodeSystem/proto'
VALUE_SET = 'http://example.org/fhir/ValueSet/proto'
RELEASED = 'http://example.org/fhir/CodeSystem/released'


def parameters(**values):
    """A Parameters resource from name=(valueType, value) pairs."""
    return {'resourceType': 'Parameters',
            'parameter': [{'name': name, f'value{kind}': value} for name, (kind, value) in values.items()]}


def parameters_xml(**values):
    """The same Parameters resource as FHIR XML."""
    items = ''.join(f'<parameter><name value="{name}"/><value{kind} value="{value}"/></parameter>'
                    for name, (kind, value) in values.items())
    return f'<Parameters xmlns="http://hl7.org/fhir">{items}</Parameters>'


class FhirProtocolTest(OCLTestCase):
    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory(mnemonic='ProtoOrg')
        self.source = OrganizationSourceFactory(
            organization=self.org, mnemonic='proto', canonical_url=SYSTEM, full_name='Protocol codes')
        self.concept = ConceptFactory(parent=self.source, mnemonic='c1', names=1, names__name='Malaria')
        self.other = ConceptFactory(parent=self.source, mnemonic='c2')
        MappingFactory(parent=self.source, from_concept=self.concept, to_concept=self.other)
        self.source_v1 = OrganizationSourceFactory.build(
            version='v1', mnemonic=self.source.mnemonic, organization=self.org, canonical_url=SYSTEM)
        Source.persist_new_version(self.source_v1, self.source.created_by)
        self.collection = OrganizationCollectionFactory(
            organization=self.org, mnemonic='protovs', canonical_url=VALUE_SET, version='HEAD')
        self.collection_v1 = OrganizationCollectionFactory(
            organization=self.org, mnemonic='protovs', canonical_url=VALUE_SET, version='v1', is_latest_version=True)
        expansion = ExpansionFactory(mnemonic='e1', collection_version=self.collection_v1, parameters={})
        self.collection_v1.expansion_uri = expansion.uri
        self.collection_v1.save()
        self.user = UserProfileFactory(username='protouser')
        self.client = APIClient()

    def assert_fhir_answer(self, response, msg=None):
        """Never a 5xx; an error is an OperationOutcome whose every issue has a tx-issue-type coding."""
        self.assertLess(response.status_code, 500, msg)
        if response.status_code >= 400:
            outcome = self.body(response)
            self.assertEqual(outcome['resourceType'], 'OperationOutcome', msg)
            for issue in outcome['issue']:
                self.assertIn(TX_ISSUE_TYPE_SYSTEM, [coding['system'] for coding in issue['details']['coding']], msg)

    @staticmethod
    def body(response):
        if 'xml' in response['Content-Type']:
            return base.from_xml(response.content)
        return json.loads(response.content)

    def post(self, path, body, content_type='application/fhir+json', **headers):
        data = body if isinstance(body, str) else json.dumps(body)
        return self.client.post(path, data, content_type=content_type, **headers)

    # FHIR JSON in

    def test_fhir_json_posts_are_read(self):
        validate = parameters(url=('Uri', SYSTEM), code=('Code', 'c1'), version=('String', 'v1'))
        for content_type in ('application/fhir+json', 'application/fhir+json; charset=utf-8',
                             'application/json+fhir', 'application/fhir+json; fhirVersion=4.0'):
            response = self.post('/fhir/CodeSystem/$validate-code', validate, content_type)

            self.assertEqual(response.status_code, 200, content_type)
            self.assertEqual(response['Content-Type'], 'application/fhir+json')
            self.assertEqual(response.data['parameter'][0], {'name': 'result', 'valueBoolean': True})

        response = self.post('/fhir/ValueSet/$validate-code', parameters(
            url=('Uri', VALUE_SET), system=('Uri', SYSTEM), code=('Code', 'c1')))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resourceType'], 'Parameters')

    def test_operations_answer_posts_without_a_5xx(self):
        cases = [
            ('/fhir/CodeSystem/$lookup', parameters(system=('Uri', SYSTEM), code=('Code', 'c1'))),
            ('/fhir/ConceptMap/$translate',
             parameters(url=('Uri', SYSTEM), system=('Uri', SYSTEM), code=('Code', 'c1'))),
            ('/fhir/CodeSystem/$validate-code', {'resourceType': 'Parameters', 'parameter': [{'name': 'code'}]}),
            ('/fhir/CodeSystem/$validate-code', '{not json'),
        ]
        for path, body in cases:
            self.assert_fhir_answer(self.post(path, body), path)

    # XML in and out

    def test_format_parameter_on_search(self):
        response = self.client.get(f'/fhir/CodeSystem/?url={SYSTEM}&_format=json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resourceType'], 'Bundle')
        self.assertEqual(len(response.data['entry']), 1)

        response = self.client.get(f'/fhir/CodeSystem/?url={SYSTEM}&_format=xml')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/fhir+xml; charset=utf-8')
        root = etree.fromstring(response.content)
        self.assertEqual(root.tag, f'{FHIR}Bundle')
        self.assertEqual(root.find(f'{FHIR}entry/{FHIR}resource/{FHIR}CodeSystem/{FHIR}url').get('value'), SYSTEM)

    def test_xml_out_on_operations_and_metadata(self):
        lookup = f'/fhir/CodeSystem/$lookup?system={SYSTEM}&code=c1'
        for path, headers in ((lookup + '&_format=xml', {}), (lookup, {'HTTP_ACCEPT': 'application/fhir+xml'}),
                              (lookup, {'HTTP_ACCEPT': 'application/xml'})):
            response = self.client.get(path, **headers)

            self.assertEqual(response.status_code, 200, (path, headers))
            self.assertEqual(response['Content-Type'], 'application/fhir+xml; charset=utf-8')
            parameters_ = base.from_xml(response.content)
            self.assertEqual(parameters_['resourceType'], 'Parameters')
            self.assertIn({'name': 'display', 'valueString': 'Malaria'}, parameters_['parameter'])

        response = self.client.get(f'/fhir/CodeSystem/$validate-code?url={SYSTEM}&code=c1&_format=xml')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(base.from_xml(response.content)['parameter'][0], {'name': 'result', 'valueBoolean': True})

        for search in ('ValueSet', 'ConceptMap'):
            response = self.client.get(f'/fhir/{search}/?url={VALUE_SET}&_format=xml')
            self.assertEqual(response.status_code, 200, search)
            self.assertEqual(etree.fromstring(response.content).tag, f'{FHIR}Bundle', search)

        response = self.client.get(f'/orgs/{self.org.mnemonic}/ValueSet/protovs/$expand?_format=xml')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(etree.fromstring(response.content).tag, f'{FHIR}ValueSet')

        response = self.client.get('/fhir/metadata?_format=xml')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(etree.fromstring(response.content).tag, f'{FHIR}CapabilityStatement')

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def released_source(self):
        """A released source version with an owner-base id FHIR doesn't allow and a display with a control character."""
        source = OrganizationSourceFactory(organization=self.org, mnemonic='rel_codes', canonical_url=RELEASED)
        ConceptFactory(parent=source, mnemonic='r1', names=1, names__name='Mal\x0baria')
        Source.persist_new_version(OrganizationSourceFactory.build(
            version='v1', mnemonic='rel_codes', organization=self.org, canonical_url=RELEASED, released=True),
            source.created_by)
        # a release through the API sets the revision date (ConceptContainerModel.clean)
        Source.objects.filter(mnemonic='rel_codes', version='v1').update(revision_date=timezone.now())

    def test_xml_of_released_code_systems_is_in_fhir_element_order_and_json_is_unchanged(self):
        self.released_source()

        response = self.client.get(f'/fhir/CodeSystem/?url={RELEASED}')
        self.assertIn('revisionDate', response.data['entry'][0]['resource'])

        response = self.client.get(f'/fhir/CodeSystem/?url={RELEASED}&_format=xml')
        self.assertEqual(response.status_code, 200)
        root = etree.fromstring(response.content)
        assert_in_model_order(self, root, Bundle)
        self.assertLess(child_names(root).index('link'), child_names(root).index('entry'))
        resource = root.find(f'{FHIR}entry/{FHIR}resource/{FHIR}CodeSystem')
        assert_in_model_order(self, resource, CodeSystem)
        self.assertEqual(resource.find(f'{FHIR}id').get('value'), 'rel_codes')

        response = self.client.get(f'/orgs/{self.org.mnemonic}/CodeSystem/rel_codes/?_format=xml')
        self.assertEqual(response.status_code, 200)
        root = etree.fromstring(response.content)
        assert_in_model_order(self, root, CodeSystem)
        self.assertEqual(child_names(root)[:2], ['id', 'meta'])

    def test_a_control_character_never_stops_an_xml_response(self):
        self.released_source()
        lookup = f'/fhir/CodeSystem/$lookup?system={RELEASED}&code=r1'

        self.assertIn({'name': 'display', 'valueString': 'Mal\x0baria'}, self.client.get(lookup).data['parameter'])

        response = self.client.get(lookup + '&_format=xml')
        self.assertEqual(response.status_code, 200)
        self.assertIn({'name': 'display', 'valueString': 'Mal\ufffdaria'}, base.from_xml(response.content)['parameter'])

    def test_xml_in_and_out(self):
        body = parameters_xml(url=('Uri', SYSTEM), code=('Code', 'c1'), version=('String', 'v1'))

        response = self.post(
            '/fhir/CodeSystem/$validate-code', body, 'application/fhir+xml', HTTP_ACCEPT='application/fhir+xml')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(base.from_xml(response.content)['parameter'][0], {'name': 'result', 'valueBoolean': True})

    def test_an_invalid_xml_body_is_a_400_outcome_in_xml(self):
        response = self.post('/fhir/CodeSystem/$validate-code', '<Parameters><oops', 'application/fhir+xml',
                             HTTP_ACCEPT='application/fhir+xml')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Content-Type'], 'application/fhir+xml; charset=utf-8')
        self.assertEqual(self.body(response)['issue'][0]['code'], 'invalid')

    def test_unsupported_format_is_a_406_outcome(self):
        response = self.client.get(f'/fhir/CodeSystem/$lookup?system={SYSTEM}&code=c1&_format=turtle')

        self.assertEqual(response.status_code, 406)
        self.assertEqual(response['Content-Type'], 'application/fhir+json')
        self.assertEqual(self.body(response)['issue'][0]['code'], 'not-supported')
        self.assert_fhir_answer(response)

    def test_unsupported_content_type_is_a_415_outcome(self):
        response = self.post('/fhir/CodeSystem/$validate-code', 'code=c1', 'text/plain')

        self.assertEqual(response.status_code, 415)
        self.assert_fhir_answer(response)

    # fhirVersion

    def test_fhir_version_parameter(self):
        lookup = f'/fhir/CodeSystem/$lookup?system={SYSTEM}&code=c1'

        response = self.client.get(lookup, HTTP_ACCEPT='application/fhir+json; fhirVersion=4.0')
        self.assertEqual(response.status_code, 200)

        response = self.client.get(lookup, HTTP_ACCEPT='application/fhir+json; fhirVersion=5.0')
        self.assertEqual(response.status_code, 406)
        self.assertEqual(self.body(response)['issue'][0]['code'], 'not-supported')

        response = self.post('/fhir/CodeSystem/$validate-code', parameters(code=('Code', 'c1')),
                             'application/fhir+json; fhirVersion=4.3')
        self.assertEqual(response.status_code, 415)
        self.assert_fhir_answer(response)

    # errors

    def test_errors_are_outcomes_with_a_tx_issue_type(self):
        cases = [
            ('/fhir/CodeSystem/$lookup?system=http://example.org/unknown&code=1', 400),
            ('/fhir/CodeSystem/?unsupported_parameter=1', 400),
            ('/fhir/CodeSystem/no-such-code-system/', 404),
            ('/fhir/Patient/', 404),
            ('/fhir/ValueSet/$nothing/', 404),
            (f'/orgs/{self.org.mnemonic}/CodeSystem/no-such-code-system/', 404),
            (f'/orgs/{self.org.mnemonic}/ValueSet/no-such-value-set/$expand/', 404),
        ]
        for path, status in cases:
            response = self.client.get(path)

            self.assertEqual(response.status_code, status, path)
            self.assert_fhir_answer(response, path)

    def test_an_error_returned_rather_than_raised_is_an_outcome(self):
        response = self.client.get(f'/orgs/{self.org.mnemonic}/ValueSet/protovs/$expand?count=5')

        self.assertEqual(response.status_code, 404)
        self.assert_fhir_answer(response)

    def test_a_crash_outside_the_view_handler_is_a_4xx_outcome(self):
        client = APIClient(raise_request_exception=False)
        with self.assertLogs('django.request', level='ERROR'):
            response = client.delete('/fhir/CodeSystem/$validate-code')  # the view returns None: a 500 in Django

        self.assertEqual(response.status_code, 422)
        self.assert_fhir_answer(response)

    # trailing slashes (OpenConceptLab/ocl_online#444)

    def test_operations_answer_the_same_with_or_without_a_trailing_slash(self):
        operations = [
            ('CodeSystem/$lookup', f'system={SYSTEM}&code=c1'),
            ('CodeSystem/$validate-code', f'url={SYSTEM}&code=c1'),
            ('ValueSet/$validate-code', f'url={VALUE_SET}&system={SYSTEM}&code=c1'),
            ('ValueSet/protovs/$validate-code', f'system={SYSTEM}&code=c1'),
            ('ValueSet/protovs/$expand', 'count=5'),
            ('ConceptMap/$translate', f'system={SYSTEM}&code=c1'),
        ]
        for fhir_base in ('/fhir/', f'/orgs/{self.org.mnemonic}/', f'/users/{self.user.username}/'):
            for operation, query in operations:
                path = fhir_base + operation
                for without, with_slash in ((path, path + '/'), (f'{path}?{query}', f'{path}/?{query}')):
                    answer, answer_with_slash = self.client.get(without), self.client.get(with_slash)

                    self.assertEqual(answer.status_code, answer_with_slash.status_code, (without, with_slash))
                    self.assert_fhir_answer(answer, without)

        self.assertEqual(self.client.get(f'/orgs/{self.org.mnemonic}/ValueSet/protovs/$expand/').status_code, 200)

    def test_metadata_with_or_without_a_trailing_slash(self):
        for path in ('/fhir/metadata', '/fhir/metadata/', '/fhir/'):
            response = self.client.get(path)

            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.data['resourceType'], 'CapabilityStatement', path)

    # base URL

    def test_the_fhir_base_url_comes_from_the_request_host(self):
        response = self.client.get(
            '/fhir/metadata?mode=terminology', HTTP_HOST='fhir.example.org', HTTP_X_FORWARDED_PROTO='https')

        self.assertEqual(response.data['implementation']['url'], 'https://fhir.example.org/fhir')
