import json

from django.test import SimpleTestCase
from django.http import HttpResponse, JsonResponse
from lxml import etree
from rest_framework.exceptions import NotAcceptable, ParseError, UnsupportedMediaType
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from core.fhir import base, outcomes
from core.fhir.base import FhirContentNegotiation, FhirJsonParser, FhirJsonRenderer, FhirXmlParser, \
    FhirXmlRenderer, FhirParameters, FhirVersioning, FhirViewMixin

FHIR = '{http://hl7.org/fhir}'
BROWSER_ACCEPT = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'


def renderers():
    return [klass() for klass in FhirViewMixin.renderer_classes]


def parsers():
    return [klass() for klass in FhirViewMixin.parser_classes]


class NegotiationTest(SimpleTestCase):
    factory = APIRequestFactory()

    def select(self, path='/fhir/CodeSystem', **headers):
        renderer, media_type = FhirContentNegotiation().select_renderer(Request(self.factory.get(path, **headers)),
                                                                        renderers())
        return type(renderer), media_type

    def test_fhir_json_is_the_default(self):
        self.assertEqual(self.select(), (FhirJsonRenderer, 'application/fhir+json'))
        self.assertEqual(self.select(HTTP_ACCEPT='*/*'), (FhirJsonRenderer, 'application/fhir+json'))

    def test_accept_header(self):
        self.assertEqual(self.select(HTTP_ACCEPT='application/fhir+xml')[0], FhirXmlRenderer)
        self.assertEqual(self.select(HTTP_ACCEPT='application/xml')[0], FhirXmlRenderer)
        self.assertEqual(self.select(HTTP_ACCEPT='text/xml')[0], FhirXmlRenderer)
        self.assertEqual(self.select(HTTP_ACCEPT='application/json+fhir')[0], FhirJsonRenderer)
        self.assertEqual(self.select(HTTP_ACCEPT='application/json')[0], JSONRenderer)

    def test_quality_values_are_honoured(self):
        accept = 'application/fhir+json;q=0.5, application/xml+fhir;q=0.9'

        self.assertEqual(self.select(HTTP_ACCEPT=accept)[0], FhirXmlRenderer)

    def test_a_malformed_quality_value_ranks_last(self):
        self.assertEqual(self.select(HTTP_ACCEPT='application/fhir+xml;q=high, application/fhir+json')[0],
                         FhirJsonRenderer)

    def test_a_browser_gets_json_rather_than_the_xml_it_also_lists(self):
        self.assertEqual(self.select(HTTP_ACCEPT=BROWSER_ACCEPT), (JSONRenderer, 'application/json'))

    def test_format_parameter_overrides_accept(self):
        cases = [
            ('_format=xml', FhirXmlRenderer), ('_format=json', FhirJsonRenderer),
            ('_format=application/fhir%2Bxml', FhirXmlRenderer),
            ('_format=application/fhir+xml', FhirXmlRenderer),  # an unencoded + arrives as a space
            ('_format=application/xml', FhirXmlRenderer), ('_format=text/xml', FhirXmlRenderer),
            ('_format=application/json', JSONRenderer), ('format=xml', FhirXmlRenderer),
        ]
        for query, renderer in cases:
            self.assertEqual(
                self.select(f'/fhir/CodeSystem?{query}', HTTP_ACCEPT='application/fhir+json')[0], renderer, query)

    def test_the_fhir_version_parameter_is_kept_on_the_accepted_media_type(self):
        self.assertEqual(
            self.select(HTTP_ACCEPT='application/fhir+json; fhirVersion=4.0'),
            (FhirJsonRenderer, 'application/fhir+json; fhirVersion=4.0'))
        self.assertEqual(
            self.select('/fhir/CodeSystem?_format=application/fhir%2Bxml;fhirVersion=4.0'),
            (FhirXmlRenderer, 'application/fhir+xml; fhirVersion=4.0'))

    def test_unsupported_formats_are_not_acceptable(self):
        for path, accept in (('/fhir/CodeSystem?_format=turtle', '*/*'), ('/fhir/CodeSystem', 'text/csv'),
                             ('/fhir/CodeSystem', 'application/fhir+json;q=0')):
            with self.assertRaises(NotAcceptable, msg=(path, accept)):
                self.select(path, HTTP_ACCEPT=accept)

    def test_parser_selection_understands_fhir_media_types(self):
        cases = [
            ('application/fhir+json', FhirJsonParser), ('application/fhir+json; charset=utf-8', FhirJsonParser),
            ('application/json+fhir', FhirJsonParser), ('application/json', JSONParser),
            ('application/fhir+xml', FhirXmlParser), ('application/xml', FhirXmlParser),
            ('text/xml; charset=utf-8', FhirXmlParser), ('application/fhir+json; fhirVersion=4.0', FhirJsonParser),
        ]
        for content_type, parser in cases:
            request = Request(self.factory.post('/fhir/CodeSystem/$lookup', '{}', content_type=content_type))
            self.assertIsInstance(FhirContentNegotiation().select_parser(request, parsers()), parser, content_type)

        request = Request(self.factory.post('/fhir/CodeSystem/$lookup', 'x', content_type='text/plain'))
        self.assertIsNone(FhirContentNegotiation().select_parser(request, parsers()))


class VersioningTest(SimpleTestCase):
    factory = APIRequestFactory()

    def version(self, accepted_media_type, content_type=None):
        if content_type:
            request = Request(self.factory.post('/fhir/CodeSystem/$lookup', '{}', content_type=content_type))
        else:
            request = Request(self.factory.get('/fhir/CodeSystem'))
        request.accepted_media_type = accepted_media_type
        return FhirVersioning().determine_version(request)

    def test_r4_is_served(self):
        self.assertEqual(self.version('application/fhir+json'), '4.0')
        self.assertEqual(self.version('application/fhir+json; fhirVersion=4.0'), '4.0')
        self.assertEqual(self.version('application/fhir+xml; fhirVersion=4.0.1'), '4.0')
        self.assertEqual(self.version('application/fhir+json', 'application/fhir+json; fhirVersion=4.0'), '4.0')

    def test_another_version_is_refused(self):
        with self.assertRaises(NotAcceptable):
            self.version('application/fhir+json; fhirVersion=4.3')
        with self.assertRaises(NotAcceptable):
            self.version('application/fhir+json; fhirVersion=5.0')
        with self.assertRaises(UnsupportedMediaType):
            self.version('application/fhir+json', 'application/fhir+json; fhirVersion=5.0')


class XmlTest(SimpleTestCase):
    def test_a_valid_resource_round_trips_through_its_model(self):
        parameters = {'resourceType': 'Parameters', 'parameter': [
            {'name': 'result', 'valueBoolean': True}, {'name': 'display', 'valueString': 'Malaria'},
            {'name': 'match', 'part': [
                {'name': 'concept', 'valueCoding': {'system': 'http://a', 'code': '1', 'display': 'One'}}]},
            {'name': 'issues', 'resource': {'resourceType': 'OperationOutcome', 'issue': [
                {'severity': 'error', 'code': 'invalid', 'details': {'text': 'Bad'}}]}}]}

        xml = base.to_xml(parameters)

        self.assertTrue(xml.startswith(b'<?xml'))
        self.assertEqual(base.from_xml(xml), parameters)

    def test_xml_output_drops_empty_values(self):
        xml = base.to_xml({'resourceType': 'CodeSystem', 'id': 'a', 'status': 'active', 'content': 'complete',
                           'title': '', 'description': None, 'identifier': [], 'meta': {}})

        root = etree.fromstring(xml)
        self.assertEqual([child.tag for child in root], [f'{FHIR}id', f'{FHIR}status', f'{FHIR}content'])

    def test_a_resource_its_model_refuses_is_written_element_by_element(self):
        code_system = {
            'resourceType': 'CodeSystem', 'id': 'my_codes@v1', 'url': 'http://x', 'status': 'active',
            'content': 'complete', 'caseSensitive': True, 'count': 2,
            'text': {'status': 'generated', 'div': '<div xmlns="http://www.w3.org/1999/xhtml"><p>Codes</p></div>'},
            'extension': [{'url': 'http://ext', 'valueString': 'x'}],
            'concept': [{'code': 'a', 'display': 'A', 'designation': [{'language': 'en', 'value': 'A'}]},
                        {'id': 'c2', 'code': 'b'}],
        }

        root = etree.fromstring(base.to_xml(code_system))

        self.assertEqual(root.tag, f'{FHIR}CodeSystem')
        self.assertEqual(root.find(f'{FHIR}id').get('value'), 'my_codes@v1')
        self.assertEqual(root.find(f'{FHIR}caseSensitive').get('value'), 'true')
        self.assertEqual(root.find(f'{FHIR}count').get('value'), '2')
        self.assertEqual(root.find(f'{FHIR}extension').get('url'), 'http://ext')
        self.assertEqual(root.find(f'{FHIR}extension/{FHIR}valueString').get('value'), 'x')
        self.assertEqual(root.find(f'{FHIR}text/{{http://www.w3.org/1999/xhtml}}div/'
                                   f'{{http://www.w3.org/1999/xhtml}}p').text, 'Codes')
        concepts = root.findall(f'{FHIR}concept')
        self.assertEqual(len(concepts), 2)
        self.assertEqual(concepts[0].find(f'{FHIR}designation/{FHIR}language').get('value'), 'en')
        self.assertEqual(concepts[1].get('id'), 'c2')

    def test_a_contained_resource_and_a_narrative_that_is_not_xhtml_in_the_element_writer(self):
        bundle = {'resourceType': 'Bundle', 'type': 'searchset', 'total': 1, 'unknownElement': 'x',
                  'entry': [{'resource': {'resourceType': 'CodeSystem', 'id': 'a_b', 'status': 'active',
                                          'text': {'status': 'generated', 'div': 'plain <text'}}},
                            {'resource': {'resourceType': 'ValueSet', 'id': 'v_1', 'status': 'active',
                                          'text': {'status': 'generated', 'div': '<p>Not a div</p>'}}}]}

        root = etree.fromstring(base.to_xml(bundle))

        resource = root.find(f'{FHIR}entry/{FHIR}resource/{FHIR}CodeSystem')
        self.assertEqual(resource.find(f'{FHIR}id').get('value'), 'a_b')
        self.assertEqual(resource.find(f'{FHIR}text/{{http://www.w3.org/1999/xhtml}}div').text, 'plain <text')
        value_set = root.find(f'{FHIR}entry/{FHIR}resource/{FHIR}ValueSet')
        self.assertEqual(value_set.find(f'{FHIR}text/{{http://www.w3.org/1999/xhtml}}div').text, '<p>Not a div</p>')

    def test_data_that_is_not_a_resource_is_answered_with_an_outcome(self):
        root = etree.fromstring(base.to_xml({'versions': ['4.0']}))

        self.assertEqual(root.tag, f'{FHIR}OperationOutcome')
        self.assertEqual(base.to_xml(None), b'')

    def test_invalid_xml_bodies_are_parse_errors(self):
        for body in (b'not xml', b'<Parameters/>', b'<Foo xmlns="http://hl7.org/fhir"/>',
                     b'<Parameters xmlns="http://hl7.org/fhir"><parameter><nm value="x"/></parameter></Parameters>'):
            with self.assertRaises(ParseError, msg=body):
                base.from_xml(body)

    def test_entities_are_not_resolved(self):
        body = (b'<?xml version="1.0"?><!DOCTYPE p [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
                b'<Parameters xmlns="http://hl7.org/fhir"><parameter><name value="code"/>'
                b'<valueString value="&e;"/></parameter></Parameters>')
        try:
            parsed = base.from_xml(body)
        except ParseError:
            return
        self.assertNotIn('root:', json.dumps(parsed))

    def test_the_xml_parser_and_renderer(self):
        body = b'<Parameters xmlns="http://hl7.org/fhir"><parameter><name value="code"/>' \
               b'<valueCode value="1"/></parameter></Parameters>'
        stream = type('Stream', (), {'read': lambda self: body})()

        parsed = FhirXmlParser().parse(stream)

        self.assertEqual(parsed, {'resourceType': 'Parameters', 'parameter': [{'name': 'code', 'valueCode': '1'}]})
        self.assertEqual(etree.fromstring(FhirXmlRenderer().render(parsed)).tag, f'{FHIR}Parameters')


class FhirParametersTest(SimpleTestCase):
    factory = APIRequestFactory()

    def read(self, django_request):
        request = Request(django_request, parsers=parsers(), negotiator=FhirContentNegotiation())
        return FhirParameters.from_request(request)

    def test_query_parameters(self):
        params = self.read(self.factory.get(
            '/fhir/CodeSystem/$lookup?system=http://a&code=1&property=x&property=y&_format=xml&format=json&_pretty=1'))

        self.assertEqual(params.get('system'), 'http://a')
        self.assertEqual(params.getlist('property'), ['x', 'y'])
        self.assertEqual(params.names(), ['system', 'code', 'property'])
        self.assertNotIn('_format', params)
        self.assertIsNone(params.get('version'))
        self.assertEqual(params.get('version', 'v1'), 'v1')

    def test_a_posted_parameters_resource(self):
        body = {'resourceType': 'Parameters', 'parameter': [
            {'name': 'url', 'valueUri': 'http://a'}, {'name': 'activeOnly', 'valueBoolean': False},
            {'name': 'count', 'valueInteger': 10},
            {'name': 'coding', 'valueCoding': {'system': 'http://a', 'code': '1'}},
            {'name': 'valueSet', 'resource': {'resourceType': 'ValueSet', 'status': 'active'}},
            {'name': 'dependency', 'part': [{'name': 'element', 'valueUri': 'http://e'}]},
            {'name': 'empty'}]}

        params = self.read(self.factory.post(
            '/fhir/ValueSet/$expand?_format=xml&offset=5', json.dumps(body), content_type='application/fhir+json'))

        self.assertEqual(params.get('url'), 'http://a')
        self.assertIs(params.get('activeOnly'), False)
        self.assertEqual(params.get('count'), 10)
        self.assertEqual(params.get('coding'), {'system': 'http://a', 'code': '1'})
        self.assertEqual(params.get('valueSet')['resourceType'], 'ValueSet')
        self.assertEqual(params.get('dependency').get('element'), 'http://e')
        self.assertIsNone(params.get('empty'))
        self.assertEqual(params.get('offset'), '5')
        self.assertNotIn('_format', params)

    def test_a_posted_xml_parameters_resource(self):
        body = '<Parameters xmlns="http://hl7.org/fhir"><parameter><name value="code"/><valueCode value="1"/>' \
               '</parameter></Parameters>'

        params = self.read(self.factory.post('/fhir/CodeSystem/$lookup', body, content_type='application/fhir+xml'))

        self.assertEqual(params.get('code'), '1')

    def test_a_form_post(self):
        params = self.read(self.factory.post('/fhir/CodeSystem/$lookup', {'code': '1', 'system': 'http://a'}))

        self.assertEqual(params.get('code'), '1')
        self.assertEqual(params.get('system'), 'http://a')

    def test_a_post_without_a_body_reads_the_query(self):
        params = self.read(self.factory.post('/fhir/CodeSystem/$lookup?code=1'))

        self.assertEqual(params.get('code'), '1')

    def test_a_posted_resource_that_is_not_parameters_is_invalid(self):
        with self.assertRaises(outcomes.InvalidError):
            self.read(self.factory.post(
                '/fhir/CodeSystem/$lookup', json.dumps({'resourceType': 'CodeSystem'}),
                content_type='application/fhir+json'))


class BaseUrlTest(SimpleTestCase):
    factory = APIRequestFactory()

    def test_the_base_comes_from_the_request_host(self):
        request = self.factory.get(
            '/orgs/CIEL/CodeSystem/', HTTP_HOST='fhir.openconceptlab.org', HTTP_X_FORWARDED_PROTO='https')

        self.assertEqual(base.fhir_base_url(request), 'https://fhir.openconceptlab.org/fhir')
        self.assertEqual(base.fhir_base_url(request, {'org': 'CIEL'}), 'https://fhir.openconceptlab.org/orgs/CIEL')
        self.assertEqual(base.fhir_base_url(request, {'user': 'jo'}), 'https://fhir.openconceptlab.org/users/jo')

    def test_fhir_paths(self):
        for path in ('/fhir', '/fhir/', '/fhir/metadata', '/fhir/CodeSystem/$lookup/', '/orgs/CIEL/CodeSystem',
                     '/orgs/CIEL/ValueSet/x/$expand/', '/users/jo/ConceptMap/$translate'):
            self.assertTrue(base.is_fhir_path(path), path)
        for path in ('/', '/orgs/CIEL/', '/orgs/CIEL/sources/', '/fhirx/', '/version/'):
            self.assertFalse(base.is_fhir_path(path), path)


class OutcomeResponseTest(SimpleTestCase):
    factory = APIRequestFactory()
    outcome = outcomes.operation_outcome(outcomes.issue('not-found', 'Nothing here'))

    def test_json_by_default(self):
        response = base.outcome_response(self.factory.get('/fhir/Patient'), self.outcome, 404)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['Content-Type'], 'application/fhir+json')
        self.assertEqual(json.loads(response.content), self.outcome)

    def test_xml_when_asked_and_never_a_5xx(self):
        response = base.outcome_response(
            self.factory.get('/fhir/Patient?_format=xml'), self.outcome, 500, {'Retry-After': '1'})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response['Content-Type'], 'application/fhir+xml; charset=utf-8')
        self.assertEqual(response['Retry-After'], '1')
        self.assertEqual(etree.fromstring(response.content).tag, f'{FHIR}OperationOutcome')

    def test_an_unsupported_format_still_gets_json(self):
        response = base.outcome_response(self.factory.get('/fhir/Patient?_format=turtle'), self.outcome, 404)

        self.assertEqual(response['Content-Type'], 'application/fhir+json')

    def test_is_outcome_response(self):
        self.assertTrue(base.is_outcome_response(base.outcome_response(
            self.factory.get('/fhir/x'), self.outcome, 404)))
        self.assertTrue(base.is_outcome_response(JsonResponse(self.outcome, status=404)))
        self.assertFalse(base.is_outcome_response(JsonResponse({'detail': 'Not found.'}, status=404)))
        self.assertFalse(base.is_outcome_response(HttpResponse(b'<h1>Not Found</h1>', status=404)))
        self.assertFalse(base.is_outcome_response(HttpResponse(b'{oops', content_type='application/json')))


class EchoView(FhirViewMixin, APIView):
    """A FHIR view that answers with its parameters, or with the status it is asked for."""
    permission_classes = (AllowAny,)
    authentication_classes = ()

    def get(self, request):
        status = int(request.query_params.get('status', 200))
        if status != 200:
            return Response({'detail': 'As asked.'} if status == 409 else None, status=status)
        return self.post(request)

    def post(self, _request):
        params = self.get_fhir_parameters()
        return Response({'resourceType': 'Parameters', 'parameter': [
            {'name': name, 'valueString': str(params.get(name))} for name in params.names()] + [
            {'name': 'base', 'valueUri': self.get_fhir_base_url()}]})


class FhirViewMixinTest(SimpleTestCase):
    factory = APIRequestFactory()

    def test_a_view_reads_its_parameters_the_same_way_from_get_and_post(self):
        get = EchoView.as_view()(self.factory.get('/fhir/x?code=1'))
        post = EchoView.as_view()(self.factory.post(
            '/fhir/x', json.dumps({'resourceType': 'Parameters', 'parameter': [{'name': 'code', 'valueCode': '1'}]}),
            content_type='application/fhir+json'))

        self.assertEqual(get.data, post.data)
        self.assertEqual(get.data['parameter'][0], {'name': 'code', 'valueString': '1'})
        self.assertEqual(get.data['parameter'][1], {'name': 'base', 'valueUri': 'http://testserver/fhir'})

    def test_errors_a_view_returns_go_out_as_outcomes_and_never_as_a_5xx(self):
        for status, expected, code in ((404, 404, 'not-found'), (409, 409, 'invalid'), (503, 422, 'exception')):
            response = EchoView.as_view()(self.factory.get(f'/fhir/x?status={status}'))

            self.assertEqual(response.status_code, expected, status)
            self.assertEqual(response.data['resourceType'], 'OperationOutcome', status)
            self.assertEqual(response.data['issue'][0]['code'], code, status)
