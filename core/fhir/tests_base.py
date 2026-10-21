import datetime
import json
import re
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.http import HttpResponse, JsonResponse
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codesystem import CodeSystem, CodeSystemConcept
from fhir.resources.R4B.operationoutcome import OperationOutcome
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


def child_names(element):
    return [etree.QName(child).localname for child in element]


def assert_in_model_order(test, element, model_class):
    """The element's children are elements of the R4B model, in the model's order (repeats kept together)."""
    names, sequence = child_names(element), model_class.elements_sequence()
    test.assertEqual([name for name in names if name not in sequence], [], 'elements the model does not define')
    test.assertEqual(names, sorted(names, key=sequence.index))


def released_code_system(**overrides):
    """What CodeSystemDetailSerializer gives for a released source version, in its key order."""
    return {
        'resourceType': 'CodeSystem', 'url': 'http://example.org/cs', 'title': 'Codes', 'status': 'active',
        'id': 'codes', 'language': 'en', 'count': 1, 'content': 'complete',
        'property': [{'code': 'conceptclass', 'uri': 'http://testserver/orgs/OCL/sources/Classes/concepts',
                      'description': 'Standard list of concept classes.', 'type': 'string'}],
        'meta': {'lastUpdated': '2026-10-14T10:00:00.123456Z'}, 'version': 'v1',
        'identifier': [{'system': 'http://testserver', 'value': '/orgs/X/CodeSystem/codes/', 'type': {
            'text': 'Accession ID', 'coding': [{'system': 'http://hl7.org/fhir/v2/0203', 'code': 'ACSN'}]}}],
        'name': 'codes', 'publisher': 'X', 'revisionDate': '2026-10-14T10:00:00Z', 'experimental': False,
        'caseSensitive': True, 'compositional': False, 'versionNeeded': False,
        'collectionReference': '/orgs/X/collections/c/', 'hierarchyMeaning': 'is-a',
        'concept': [{'code': 'c1', 'display': 'One', 'designation': [{'language': 'en', 'value': 'One'}],
                     'property': [{'code': 'conceptclass', 'valueString': 'Diagnosis'}]}],
        'text': {'status': 'generated', 'div': '<div xmlns="http://www.w3.org/1999/xhtml">Codes</div>'},
        'filters': [{'code': 'x'}],
        **overrides,
    }


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

    def test_explicit_exclusions_override_wildcards(self):
        """A broader range cannot make an explicitly excluded renderer acceptable."""
        cases = [
            ('application/fhir+json;q=0, */*;q=1', JSONRenderer),
            ('application/json+fhir;q=0, application/json;q=0, application/*;q=1', FhirXmlRenderer),
        ]
        for accept, expected in cases:
            with self.subTest(accept=accept):
                self.assertEqual(self.select(HTTP_ACCEPT=accept)[0], expected)

    def test_most_specific_range_sets_each_renderers_quality(self):
        """Specific ranges determine quality before renderers compete with one another."""
        cases = [
            'application/fhir+json;q=0.2, application/fhir+xml;q=0.5, application/json;q=0, */*;q=1',
            'application/fhir+xml;q=0.5, application/*;q=0, */*;q=1',
            '*/*;q=1, application/fhir+xml;q=1',
        ]
        for accept in cases:
            with self.subTest(accept=accept):
                self.assertEqual(self.select(HTTP_ACCEPT=accept)[0], FhirXmlRenderer)

    def test_type_wildcard_exclusion_overrides_universal_wildcard(self):
        """Reject the request when all available application renderers are excluded."""
        with self.assertRaises(NotAcceptable):
            self.select(HTTP_ACCEPT='application/*;q=0, */*;q=1')

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
    def assert_in_model_order(self, element, model_class):
        assert_in_model_order(self, element, model_class)

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

    def test_a_resource_its_model_refuses_on_a_pattern_is_still_written_by_the_model(self):
        code_system = {
            'resourceType': 'CodeSystem', 'concept': [
                {'code': 'a', 'display': 'A', 'designation': [{'language': 'en', 'value': 'A'}]}, {'code': 'b'}],
            'count': 2, 'caseSensitive': True, 'content': 'complete', 'status': 'active', 'url': 'http://x',
            'extension': [{'url': 'http://ext', 'valueString': 'x'}],
            'text': {'status': 'generated', 'div': '<div xmlns="http://www.w3.org/1999/xhtml"><p>Codes</p></div>'},
            'id': 'my_codes@v1',
        }

        root = etree.fromstring(base.to_xml(code_system))

        self.assertEqual(root.tag, f'{FHIR}CodeSystem')
        self.assert_in_model_order(root, CodeSystem)
        self.assertEqual(root.find(f'{FHIR}id').get('value'), 'my_codes@v1')
        self.assertEqual(root.find(f'{FHIR}caseSensitive').get('value'), 'true')
        self.assertEqual(root.find(f'{FHIR}count').get('value'), '2')
        self.assertEqual(root.find(f'{FHIR}extension').get('url'), 'http://ext')
        self.assertEqual(root.find(f'{FHIR}extension/{FHIR}valueString').get('value'), 'x')
        self.assertEqual(root.find(f'{FHIR}text/{{http://www.w3.org/1999/xhtml}}div/'
                                   f'{{http://www.w3.org/1999/xhtml}}p').text, 'Codes')
        concepts = root.findall(f'{FHIR}concept')
        self.assertEqual(len(concepts), 2)
        self.assert_in_model_order(concepts[0], CodeSystemConcept)
        self.assertEqual(concepts[0].find(f'{FHIR}designation/{FHIR}language').get('value'), 'en')

    def test_contained_resources_and_narratives_that_are_not_xhtml(self):
        bundle = {'resourceType': 'Bundle', 'type': 'searchset', 'total': 1, 'unknownElement': 'x',
                  'entry': [{'resource': {'resourceType': 'CodeSystem', 'id': 'a_b', 'status': 'active',
                                          'text': {'status': 'generated', 'div': 'plain <text'}}},
                            {'resource': {'resourceType': 'ValueSet', 'id': 'v_1', 'status': 'active',
                                          'text': {'status': 'generated', 'div': '<p>Not a div</p>'}}},
                            {'resource': {'resourceType': 'NoSuchResource', 'id': 'x'}},
                            {'resource': 'not a resource'}]}

        root = etree.fromstring(base.to_xml(bundle))

        self.assertEqual(child_names(root), ['type', 'total', 'entry', 'entry'])  # the other two had nothing R4
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


class XmlElementOrderTest(SimpleTestCase):
    """FHIR XML has a fixed element order: the R4B model's, whatever the serializer's key order."""

    def assert_in_model_order(self, element, model_class):
        assert_in_model_order(self, element, model_class)

    def test_a_released_code_system_comes_out_in_fhir_element_order(self):
        xml = base.to_xml(released_code_system())

        root = etree.fromstring(xml)
        self.assert_in_model_order(root, CodeSystem)
        self.assertEqual(child_names(root)[:4], ['id', 'meta', 'language', 'text'])
        for name in ('revisionDate', 'collectionReference', 'filters'):
            self.assertNotIn(name, child_names(root))
        concept = root.find(f'{FHIR}concept')
        self.assert_in_model_order(concept, CodeSystemConcept)
        self.assertEqual(base.from_xml(xml)['concept'][0]['display'], 'One')  # valid R4B XML

    def test_a_search_bundle_holding_one_comes_out_in_fhir_element_order(self):
        code_system = released_code_system(id='my_codes')  # an owner-base id the model refuses
        bundle = {'resourceType': 'Bundle', 'meta': {'lastUpdated': '2026-10-14T10:00:00Z'}, 'type': 'searchset',
                  'total': 1, 'entry': [{'resource': code_system}],
                  'link': [{'relation': 'self', 'url': 'http://testserver/fhir/CodeSystem/?page=1'}]}

        root = etree.fromstring(base.to_xml(bundle))

        self.assertEqual(child_names(root), ['meta', 'type', 'total', 'link', 'entry'])
        resource = root.find(f'{FHIR}entry/{FHIR}resource/{FHIR}CodeSystem')
        self.assert_in_model_order(resource, CodeSystem)
        self.assertEqual(child_names(resource)[:4], ['id', 'meta', 'language', 'text'])
        self.assertEqual(resource.find(f'{FHIR}id').get('value'), 'my_codes')

    def test_bundle_children_keep_the_model_order_with_an_id(self):
        root = etree.fromstring(base.to_xml({
            'resourceType': 'Bundle', 'entry': [{'resource': {'resourceType': 'CodeSystem', 'id': 'codes@1',
                                                              'status': 'active', 'content': 'complete'}}],
            'link': [{'relation': 'self', 'url': 'http://x'}], 'total': 1, 'type': 'searchset', 'id': 'b1',
            'meta': {'lastUpdated': '2026-10-14T10:00:00Z'}}))

        self.assertEqual(child_names(root), ['id', 'meta', 'type', 'total', 'link', 'entry'])

    def test_keys_r4_does_not_define_are_dropped_at_every_depth(self):
        code_system = {'resourceType': 'CodeSystem', 'id': 'a', 'status': 'active', 'content': 'complete',
                       'contact': [{'name': 'Jo', 'my key': 'x', 'telecom': [{'value': 'a@b.c', 'oops': 1}]}],
                       'concept': [{'code': 'a', 'concept_class': 'Misc'}]}

        root = etree.fromstring(base.to_xml(code_system))

        self.assertEqual(child_names(root.find(f'{FHIR}contact')), ['name', 'telecom'])
        self.assertEqual(child_names(root.find(f'{FHIR}contact/{FHIR}telecom')), ['value'])
        self.assertEqual(child_names(root.find(f'{FHIR}concept')), ['code'])

    def test_each_dropped_key_is_logged_once(self):
        base.LOGGED_DROPPED_ELEMENTS.clear()
        with self.assertLogs('oclapi', level='WARNING') as logs:
            base.to_xml(released_code_system())
            base.to_xml(released_code_system())

        dropped = [line for line in logs.output if 'does not define' in line]
        self.assertEqual(len(dropped), 3, dropped)
        self.assertTrue(any('CodeSystem.revisionDate' in line for line in dropped), dropped)

    def test_values_of_the_wrong_shape_are_dropped(self):
        outcome = {'resourceType': 'OperationOutcome', 'issue': [
            {'severity': 'error', 'details': 'Failed to represent "/x" as CodeSystem', 'expression': 'code'}],
            'text': ['not', 'a', 'narrative'], 'id': {'not': 'an id'}}

        root = etree.fromstring(base.to_xml(outcome))

        self.assertEqual(child_names(root), ['issue'])
        self.assertEqual(child_names(root.find(f'{FHIR}issue')), ['severity', 'expression'])

    def test_a_resource_the_model_cannot_write_falls_back_to_the_element_writer(self):
        bundle = {'resourceType': 'Bundle', 'type': 'searchset', 'entry': [{'resource': {
            'resourceType': 'CodeSystem', 'id': 'a', 'status': 'active', 'content': 'complete', 'experimental': False,
            'text': {'status': 'generated', 'div': '<div xmlns="http://www.w3.org/1999/xhtml"><p>A</p></div>'},
            'extension': [{'url': 'http://ext', 'valueString': 'x'}], 'concept': [{'id': 'c1', 'code': 'x'}]}}]}
        with patch.object(Bundle, 'model_dump_xml', side_effect=ValueError('boom')), \
                self.assertLogs('oclapi', level='ERROR'):
            root = etree.fromstring(base.to_xml(bundle))

        code_system = root.find(f'{FHIR}entry/{FHIR}resource/{FHIR}CodeSystem')
        self.assertEqual(code_system.find(f'{FHIR}id').get('value'), 'a')
        self.assertEqual(code_system.find(f'{FHIR}experimental').get('value'), 'false')
        self.assertEqual(code_system.find(f'{FHIR}extension').get('url'), 'http://ext')
        self.assertEqual(code_system.find(f'{FHIR}concept').get('id'), 'c1')
        self.assertEqual(code_system.find(f'{FHIR}concept/{FHIR}code').get('value'), 'x')
        self.assertEqual(code_system.find(f'{FHIR}text/{{http://www.w3.org/1999/xhtml}}div/'
                                          f'{{http://www.w3.org/1999/xhtml}}p').text, 'A')


# FHIR's instant: a full date and time with seconds and a zone
FHIR_INSTANT = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$')
UPDATED = datetime.datetime(2026, 10, 14, 12, 34, 56, 123456, tzinfo=datetime.timezone.utc)


def serialized_value_set(id_):
    """What ValueSetDetailSerializer gives: meta.lastUpdated is the model's raw datetime."""
    return {'resourceType': 'ValueSet', 'id': id_, 'version': 'v1', 'url': f'http://example.org/vs/{id_}',
            'status': 'active', 'meta': {'lastUpdated': UPDATED}, 'date': UPDATED}


def json_of(data):
    """What the JSON response carries for the same data."""
    return json.loads(FhirJsonRenderer().render(data))


class XmlValuesTest(SimpleTestCase):
    """XML carries the values the JSON response carries, on both the model and the unvalidated path."""

    def last_updated(self, element):
        return element.find(f'{FHIR}meta/{FHIR}lastUpdated').get('value')

    def test_a_refused_value_set_writes_its_datetimes_as_the_json_does(self):
        data = serialized_value_set('my_vs')  # an owner-base id the model refuses

        root = etree.fromstring(base.to_xml(data))

        expected = json_of(data)['meta']['lastUpdated']
        self.assertEqual(self.last_updated(root), expected)
        self.assertRegex(self.last_updated(root), FHIR_INSTANT)
        self.assertEqual(root.find(f'{FHIR}date').get('value'), json_of(data)['date'])

    def test_every_entry_of_a_bundle_holding_a_refused_one(self):
        bundle = {'resourceType': 'Bundle', 'type': 'searchset', 'total': 2,
                  'entry': [{'resource': serialized_value_set('good')}, {'resource': serialized_value_set('bad_id')}]}

        root = etree.fromstring(base.to_xml(bundle))

        expected = json_of(bundle)['entry'][0]['resource']['meta']['lastUpdated']
        for resource in root.iterfind(f'{FHIR}entry/{FHIR}resource/{FHIR}ValueSet'):
            self.assertEqual(self.last_updated(resource), expected)
            self.assertRegex(self.last_updated(resource), FHIR_INSTANT)

    def test_a_valid_resource_writes_the_same_instant(self):
        data = serialized_value_set('good')

        written = self.last_updated(etree.fromstring(base.to_xml(data)))

        self.assertRegex(written, FHIR_INSTANT)
        self.assertEqual(datetime.datetime.fromisoformat(written),
                         datetime.datetime.fromisoformat(json_of(data)['meta']['lastUpdated']))

    def test_other_python_values_are_written_as_json_writes_them(self):
        code = uuid.UUID('12345678-1234-5678-1234-567812345678')
        parameters = {'resourceType': 'Parameters', 'id': 'p_1', 'parameter': [
            {'name': 'amount', 'valueDecimal': Decimal('1.50')}, {'name': 'when', 'valueDate': UPDATED.date()},
            {'name': 'code', 'valueString': code}]}

        values = [element.get('value') for element in etree.fromstring(base.to_xml(parameters)).iter()
                  if element.get('value') and etree.QName(element).localname.startswith('value')]

        self.assertEqual(values, ['1.5', '2026-10-14', str(code)])


class XmlSingleElementTest(SimpleTestCase):
    """A list for an element R4 allows once keeps its first item."""

    def concept_map(self, identifiers):
        return {'resourceType': 'ConceptMap', 'id': 'cm', 'status': 'active', 'identifier': identifiers}

    def test_a_concept_map_keeps_its_identifier(self):
        first = {'system': 'http://testserver', 'value': '/orgs/X/ConceptMap/cm/'}
        for identifiers in ([first], [first, {'value': 'another'}]):
            root = etree.fromstring(base.to_xml(self.concept_map(identifiers)))

            self.assertEqual(len(root.findall(f'{FHIR}identifier')), 1, identifiers)
            self.assertEqual(root.find(f'{FHIR}identifier/{FHIR}value').get('value'), '/orgs/X/ConceptMap/cm/')

    def test_the_items_left_out_are_logged_once(self):
        base.LOGGED_DROPPED_ELEMENTS.clear()
        identifiers = [{'value': 'a'}, {'value': 'b'}, {'value': 'c'}]
        with self.assertLogs('oclapi', level='WARNING') as logs:
            base.to_xml(self.concept_map(identifiers))
            base.to_xml(self.concept_map(identifiers))

        self.assertEqual(len([line for line in logs.output if 'ConceptMap.identifier' in line]), 1, logs.output)


class XmlCharactersTest(SimpleTestCase):
    """Characters XML 1.0 forbids never stop a response: each becomes U+FFFD, the replacement character."""

    def test_a_control_character_in_a_value_becomes_the_replacement_character(self):
        parameters = {'resourceType': 'Parameters', 'parameter': [
            {'name': 'display', 'valueString': 'Mal\x0baria\x00'},
            {'name': 'name', 'valueString': 'Tab\tand\nnew line'}]}

        with self.assertLogs('oclapi', level='WARNING') as logs:
            xml = base.to_xml(parameters)

        values = [element.get('value') for element in etree.fromstring(xml).iter(f'{FHIR}valueString')]
        self.assertEqual(values, ['Mal\ufffdaria\ufffd', 'Tab\tand\nnew line'])
        self.assertIn('2', logs.output[0])

    def test_lone_surrogates_and_non_characters_in_text_and_narrative(self):
        code_system = {'resourceType': 'CodeSystem', 'id': 'a', 'status': 'active', 'content': 'complete',
                       'title': 'A\ud800B\uffffC', 'text': {'status': 'generated', 'div': 'x\x1fy'}}

        with self.assertLogs('oclapi', level='WARNING'):
            root = etree.fromstring(base.to_xml(code_system))

        self.assertEqual(root.find(f'{FHIR}title').get('value'), 'A\ufffdB\ufffdC')
        self.assertEqual(root.find(f'{FHIR}text/{{http://www.w3.org/1999/xhtml}}div').text, 'x\ufffdy')

    def test_the_element_writer_is_safe_too(self):
        with patch.object(CodeSystem, 'model_dump_xml', side_effect=ValueError('boom')), \
                self.assertLogs('oclapi', level='WARNING'):
            root = etree.fromstring(base.to_xml({'resourceType': 'CodeSystem', 'id': 'a', 'status': 'active',
                                                 'content': 'complete', 'title': 'bad\x0b'}))

        self.assertEqual(root.find(f'{FHIR}title').get('value'), 'bad\ufffd')


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

    def test_a_parameter_with_more_than_one_value_is_invalid_whatever_their_order(self):
        """R4 gives a parameter one value[x], resource or part: two are refused, never read as whichever came first. A
        primitive's _value[x] is its own value[x], and another's is a second one."""
        absent = {'extension': [{'url': 'http://hl7.org/fhir/StructureDefinition/data-absent-reason',
                                 'valueCode': 'unknown'}]}
        body = {'resourceType': 'Parameters', 'parameter': [
            {'name': 'code', 'valueCode': 'c1', '_valueCode': {'id': 'c'}}, {'name': 'system', '_valueUri': absent}]}
        params = self.read(self.factory.post(
            '/fhir/CodeSystem/$lookup', json.dumps(body), content_type='application/fhir+json'))
        self.assertEqual((params.get('code'), params.get('system')), ('c1', None))
        for given in ({'valueString': 'c2', 'valueCode': 'c1'}, {'valueCode': 'c1', 'valueString': 'c2'},
                      {'valueCode': 'c1', '_valueString': absent}, {'_valueString': absent, 'valueCode': 'c1'},
                      {'valueUri': 'http://a', 'part': [{'name': 'x', 'valueCode': 'y'}]},
                      {'valueString': 'c1', 'resource': {'resourceType': 'ValueSet'}}):
            for nested in (False, True):
                parameter = {'name': 'code', **given}
                body = {'resourceType': 'Parameters', 'parameter': [
                    {'name': 'property', 'part': [parameter]} if nested else parameter]}
                with self.subTest((given, nested)):
                    with self.assertRaises(outcomes.InvalidError) as raised:
                        self.read(self.factory.post(
                            '/fhir/CodeSystem/$lookup', json.dumps(body), content_type='application/fhir+json'))
                    self.assertEqual(raised.exception.expression, 'code')


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

    @override_settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=1000)
    def test_unparseable_query_uses_a_400_json_outcome(self):
        """Error negotiation must not repeat a failed query parse or retain the original error status."""
        query = '&'.join(['_format=xml'] + ['x='] * 1001)
        response = base.outcome_response(
            self.factory.get(f'/fhir/missing?{query}', HTTP_ACCEPT='application/fhir+xml'),
            self.outcome, 404, {'Retry-After': '1'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Content-Type'], 'application/fhir+json')
        self.assertEqual(response['Retry-After'], '1')
        outcome = OperationOutcome.model_validate_json(response.content)
        self.assertEqual(outcome.issue[0].code, 'invalid')
        self.assertEqual(outcome.issue[0].details.coding[0].system, outcomes.TX_ISSUE_TYPE_SYSTEM)
        self.assertEqual(outcome.issue[0].details.coding[0].code, 'invalid-data')

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


class ReturnedOutcomeView(FhirViewMixin, APIView):
    """Return the legacy serializer error shape with a configurable HTTP status."""
    permission_classes = (AllowAny,)
    authentication_classes = ()
    status_code = 200

    def get(self, _request):
        """Leave normalization to the production view mixin."""
        return Response({'resourceType': 'OperationOutcome', 'issue': [
            {'severity': 'error', 'details': 'Failed to serialize the resource.'}]}, status=self.status_code)


class FhirViewMixinTest(SimpleTestCase):
    factory = APIRequestFactory()

    def test_returned_outcomes_are_normalized_at_every_status(self):
        """Successful and error statuses both retain the diagnostic in valid JSON and XML outcomes."""
        for status, expected in ((200, 200), (201, 201), (400, 400), (503, 422)):
            for media_type in ('application/fhir+json', 'application/fhir+xml'):
                with self.subTest(status=status, media_type=media_type):
                    response = ReturnedOutcomeView.as_view(status_code=status)(
                        self.factory.get('/fhir/x', HTTP_ACCEPT=media_type))
                    response.render()

                    self.assertEqual(response.status_code, expected)
                    self.assertEqual(response['Content-Type'].split(';')[0], media_type)
                    outcome = (OperationOutcome.model_validate_xml(response.content) if media_type.endswith('xml')
                               else OperationOutcome.model_validate_json(response.content))
                    issue = outcome.issue[0]
                    self.assertEqual(issue.code, 'processing')
                    self.assertEqual(issue.details.text, 'Failed to serialize the resource.')
                    self.assertEqual(issue.details.coding[0].system, outcomes.TX_ISSUE_TYPE_SYSTEM)
                    self.assertEqual(issue.details.coding[0].code, 'invalid-data')

    @override_settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=1000)
    def test_query_parsing_failure_in_a_view_is_a_400_json_outcome(self):
        """A query rejected during initial negotiation is a client error, even when XML was requested."""
        query = '&'.join(['x='] * 1001)
        response = EchoView.as_view()(self.factory.get(f'/fhir/x?{query}', HTTP_ACCEPT='application/fhir+xml'))
        response.render()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Content-Type'], 'application/fhir+json')
        outcome = OperationOutcome.model_validate_json(response.content)
        self.assertEqual(outcome.issue[0].code, 'invalid')
        self.assertEqual(outcome.issue[0].details.coding[0].system, outcomes.TX_ISSUE_TYPE_SYSTEM)
        self.assertEqual(outcome.issue[0].details.coding[0].code, 'invalid-data')

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
