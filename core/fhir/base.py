"""
The FHIR view layer: what every FHIR view shares, whatever its operation.

- Media types: FHIR JSON and XML in and out, with the aliases FHIR clients use, and `_format`.
- The `fhirVersion` MIME parameter: R4 (4.0) is served and any other version is refused.
- XML through fhir.resources' R4B models, which are wire-identical to R4 for the terminology resources.
- One reader for operation parameters, from a GET query string or a POSTed Parameters resource.
- The FHIR base URL of a request, from its own host.
- Errors as OperationOutcome (core.fhir.outcomes), in views and in the middleware.
"""
import json
import logging

from django.http import HttpResponse
from fhir.resources.R4B import get_fhir_model_class
from lxml import etree
from rest_framework.exceptions import NotAcceptable, ParseError, UnsupportedMediaType
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.parsers import BaseParser, FormParser, JSONParser, MultiPartParser
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.utils.mediatypes import _MediaType, media_type_matches
from rest_framework.versioning import AcceptHeaderVersioning

from core.fhir import outcomes

logger = logging.getLogger('oclapi')

FHIR_VERSION = '4.0'  # the fhirVersion MIME parameter, and what $versions answers
FHIR_RELEASE = '4.0.1'  # CapabilityStatement.fhirVersion
ACCEPTED_FHIR_VERSIONS = (FHIR_VERSION, FHIR_RELEASE)

FHIR_JSON = 'application/fhir+json'
FHIR_XML = 'application/fhir+xml'
# Other names for the two formats: the short _format values, the DSTU2 types, and the generic XML types R4 says
# a server SHOULD accept. Plain application/json stays itself, so OCL's own clients keep getting it.
MEDIA_TYPE_ALIASES = {
    'json': FHIR_JSON,
    'application/json+fhir': FHIR_JSON,
    'xml': FHIR_XML,
    'application/xml+fhir': FHIR_XML,
    'application/xml': FHIR_XML,
    'text/xml': FHIR_XML,
}
FORMAT_PARAMETERS = ('_format', 'format')
# Protocol parameters: they choose the response format, and are never operation or search input
CONTROL_PARAMETERS = frozenset({'_format', 'format', '_pretty'})
RESOURCE_TYPES = ('CodeSystem', 'ValueSet', 'ConceptMap')

FHIR_NS = 'http://hl7.org/fhir'
XHTML_NS = 'http://www.w3.org/1999/xhtml'
EXTENSION_ELEMENTS = ('extension', 'modifierExtension')
EMPTY_VALUES = (None, '', {}, [])


def xml_parser():
    """A parser that never fetches or expands entities: a request body is untrusted."""
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, remove_comments=True)


def is_fhir_path(path):
    """Whether a path is under a FHIR base: the global /fhir/, or a FHIR resource type at an owner base."""
    segments = path.strip('/').split('/')
    return segments[0] == 'fhir' or (len(segments) >= 3 and segments[2] in RESOURCE_TYPES)


def fhir_base_url(request, kwargs=None):
    """
    The FHIR base a request came in on, from its own scheme and host (the forwarded ones behind the load balancer),
    so links work on every host OCL serves FHIR from: /fhir, or the owner base /orgs/{org} or /users/{user}.
    """
    kwargs = kwargs or {}
    if kwargs.get('org'):
        path = f"/orgs/{kwargs['org']}"
    elif kwargs.get('user'):
        path = f"/users/{kwargs['user']}"
    else:
        path = '/fhir'
    return request.build_absolute_uri(path)


def normalize_media_type(media_range):
    """A media range from Accept, Content-Type or _format, with FHIR's other names replaced by the formal type."""
    main, separator, params = media_range.partition(';')
    main = main.strip().lower().replace(' ', '+')  # an unencoded + in _format arrives as a space
    return MEDIA_TYPE_ALIASES.get(main, main) + separator + params


def accepted_media_ranges(request):
    """The media ranges a request accepts: its _format, else its Accept header."""
    for name in FORMAT_PARAMETERS:
        value = request.GET.get(name)
        if value:
            return [normalize_media_type(value)]
    ranges = [item.strip() for item in request.META.get('HTTP_ACCEPT', '').split(',') if item.strip()]
    if any(item.startswith('text/html') for item in ranges) and not any('fhir' in item for item in ranges):
        return ['application/json']  # a browser: JSON as before, not the application/xml it lists after text/html
    return [normalize_media_type(item) for item in ranges] or ['*/*']


def quality(media_range):
    try:
        return float(_MediaType(media_range).params.get('q', 1))
    except ValueError:
        return 0.0


def media_type_with_params(media_type, media_range):
    """The renderer's media type, carrying the parameters the client sent other than q (such as fhirVersion)."""
    params = [param.strip() for param in media_range.split(';')[1:]]
    params = [param for param in params if param and not param.lower().startswith('q=')]
    return '; '.join([media_type, *params])


class FhirContentNegotiation(DefaultContentNegotiation):
    """
    Content negotiation for FHIR views: `_format` (or `format`) overrides Accept, FHIR's other media type names are
    understood, and quality values are honoured. DRF's own `?format=` renderer filter is not used.
    """

    def select_parser(self, request, parsers):
        content_type = normalize_media_type(request.content_type or '')
        return next((parser for parser in parsers if media_type_matches(parser.media_type, content_type)), None)

    def select_renderer(self, request, renderers, format_suffix=None):
        ranges = sorted(accepted_media_ranges(request), key=lambda item: (-quality(item), -_MediaType(item).precedence))
        for media_range in ranges:
            if quality(media_range) <= 0:
                continue
            for renderer in renderers:
                if media_type_matches(renderer.media_type, media_range):
                    return renderer, media_type_with_params(renderer.media_type, media_range)
        raise NotAcceptable(available_renderers=renderers)


class FhirVersioning(AcceptHeaderVersioning):
    """
    The fhirVersion MIME parameter, on Accept (or _format) and on Content-Type. R4 is served: 4.0 (or 4.0.1, or no
    parameter) is accepted, and any other version is refused, 406 on Accept and 415 on Content-Type. Django
    lowercases parameter names, so the parameter is read without regard to case.
    """
    version_param = 'fhirVersion'
    default_version = FHIR_VERSION
    allowed_versions = ACCEPTED_FHIR_VERSIONS
    invalid_version_message = f'This server speaks FHIR R4: fhirVersion={FHIR_VERSION}.'

    def version_of(self, media_type):
        return _MediaType(media_type or '').params.get(self.version_param.lower())

    def determine_version(self, request, *args, **kwargs):
        content_version = self.version_of(request.content_type)
        if content_version is not None and content_version not in self.allowed_versions:
            raise UnsupportedMediaType(request.content_type, detail=self.invalid_version_message)
        accept_version = self.version_of(request.accepted_media_type)
        if accept_version is not None and accept_version not in self.allowed_versions:
            raise NotAcceptable(self.invalid_version_message)
        return self.default_version


def prune(value):
    """FHIR has no empty values: drop None, '', {} and [] at every depth."""
    if isinstance(value, dict):
        items = ((key, prune(item)) for key, item in value.items())
        return {key: item for key, item in items if item not in EMPTY_VALUES}
    if isinstance(value, (list, tuple)):
        return [item for item in (prune(item) for item in value) if item not in EMPTY_VALUES]
    return value


def from_xml(content):
    """FHIR XML -> FHIR JSON, through the R4B model of the root element. Raises ParseError."""
    try:
        root = etree.fromstring(content, parser=xml_parser())
        name = etree.QName(root)
        if name.namespace != FHIR_NS:
            raise ValueError(f'the root element {root.tag} is not a FHIR resource')
        resource = get_fhir_model_class(name.localname).model_validate_xml(content, xmlparser=xml_parser())
    except Exception as ex:
        message = (str(ex).splitlines() or [type(ex).__name__])[0]
        raise ParseError(f'The body is not a valid FHIR XML resource: {message}') from ex
    return json.loads(resource.model_dump_json())


def to_xml(data):
    """
    FHIR JSON -> FHIR XML. A resource its R4B model accepts is written by the model, in FHIR's element order. One it
    refuses (an OCL id with '_' or '@', an element R4 doesn't have) is still answered, written element by element.
    """
    if data is None:
        return b''
    if not isinstance(data, dict) or not data.get('resourceType'):
        data = outcomes.operation_outcome(outcomes.issue('not-supported', 'This response has no FHIR XML form.'))
    data = prune(data)
    try:
        return get_fhir_model_class(data['resourceType']).model_validate(data).model_dump_xml()
    except Exception as ex:
        logger.info('FHIR XML: %s written without its model (%s)', data['resourceType'], type(ex).__name__)
        root = etree.Element(f"{{{FHIR_NS}}}{data['resourceType']}", nsmap={None: FHIR_NS})
        write_xml_children(root, data, resource=True)
        return etree.tostring(root, xml_declaration=True, encoding='utf-8')


def write_xml_children(element, obj, resource=False, extension=False):
    for key, value in obj.items():
        if key == 'resourceType' or key.startswith('_'):
            continue
        if not resource and (key == 'id' or (extension and key == 'url')) and not isinstance(value, (dict, list)):
            element.set(key, str(value))  # an element's id and an extension's url are XML attributes
        elif key == 'div' and isinstance(value, str):
            element.append(xhtml_div(value))
        else:
            for item in value if isinstance(value, list) else [value]:
                write_xml_element(element, key, item)


def write_xml_element(parent, name, value):
    child = etree.SubElement(parent, f'{{{FHIR_NS}}}{name}')
    if isinstance(value, dict) and value.get('resourceType'):
        inner = etree.SubElement(child, f"{{{FHIR_NS}}}{value['resourceType']}")  # a resource in a container
        write_xml_children(inner, value, resource=True)
    elif isinstance(value, dict):
        write_xml_children(child, value, extension=name in EXTENSION_ELEMENTS)
    else:
        child.set('value', ('true' if value else 'false') if isinstance(value, bool) else str(value))


def xhtml_div(text):
    try:
        div = etree.fromstring(text, parser=xml_parser())
        if div.tag == f'{{{XHTML_NS}}}div':
            return div
    except etree.XMLSyntaxError:
        pass
    div = etree.Element(f'{{{XHTML_NS}}}div', nsmap={None: XHTML_NS})
    div.text = text
    return div


class FhirJsonParser(JSONParser):
    media_type = FHIR_JSON


class FhirXmlParser(BaseParser):
    media_type = FHIR_XML

    def parse(self, stream, media_type=None, parser_context=None):
        return from_xml(stream.read())


class FhirJsonRenderer(JSONRenderer):
    media_type = FHIR_JSON
    format = 'json'


class FhirXmlRenderer(BaseRenderer):
    media_type = FHIR_XML
    format = 'xml'
    charset = 'utf-8'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return to_xml(data)


def parameter_value(parameter):
    """A Parameters.parameter's value: its value[x], resource, or parts (as FhirParameters)."""
    for key, value in parameter.items():
        if key.startswith('value'):
            return value
    if 'resource' in parameter:
        return parameter['resource']
    if 'part' in parameter:
        return FhirParameters.from_resource({'parameter': parameter['part']})
    return None


class FhirParameters:
    """
    An operation's input, read the same way from a GET query string and from a POSTed Parameters resource (FHIR JSON,
    FHIR XML or a form). Names can repeat. Values keep their FHIR JSON form: a string, boolean or number, a dict for
    a Coding, CodeableConcept or resource, and FhirParameters for parts. On a POST, the body's parameters come first,
    then the query string's. Protocol parameters such as _format are left out.
    """

    def __init__(self, items=()):
        self.items = list(items)

    @classmethod
    def from_resource(cls, resource):
        return cls((item.get('name'), parameter_value(item)) for item in resource.get('parameter') or [])

    @classmethod
    def from_request(cls, request):
        items = cls.body_items(request.data) if request.method in ('POST', 'PUT') else []
        return cls(items + cls.query_items(request.query_params))

    @staticmethod
    def query_items(query):
        return [(name, value) for name, values in query.lists() if name not in CONTROL_PARAMETERS for value in values]

    @classmethod
    def body_items(cls, data):
        if not data:
            return []
        if hasattr(data, 'lists'):  # a form
            return cls.query_items(data)
        if isinstance(data, dict) and data.get('resourceType') == 'Parameters':
            return cls.from_resource(data).items
        raise outcomes.InvalidError('The request body must be a Parameters resource.')

    def get(self, name, default=None):
        return next((value for key, value in self.items if key == name), default)

    def getlist(self, name):
        return [value for key, value in self.items if key == name]

    def names(self):
        return list(dict.fromkeys(key for key, _ in self.items))

    def __contains__(self, name):
        return any(key == name for key, _ in self.items)


def outcome_response(request, outcome, status_code, headers=None):
    """An OperationOutcome as a plain Django response, for the middleware, in the format the request asks for."""
    renderer = FhirJsonRenderer()
    try:
        renderer, _ = FhirContentNegotiation().select_renderer(
            request, [klass() for klass in FhirViewMixin.renderer_classes])
    except NotAcceptable:
        pass
    content_type = f'{renderer.media_type}; charset={renderer.charset}' if renderer.charset else renderer.media_type
    response = HttpResponse(
        renderer.render(outcome), status=outcomes.response_status(status_code), content_type=content_type)
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def is_outcome_response(response):
    """Whether a response is already FHIR: an error from a FHIR view, rather than Django's or another view's."""
    content_type = response.get('Content-Type', '')
    if 'fhir' in content_type:
        return True
    if 'json' not in content_type:
        return False
    try:
        return outcomes.is_outcome(json.loads(response.content))
    except ValueError:
        return False


class FhirViewMixin:
    """
    The first base of every FHIR view: FHIR JSON and XML in and out, `_format`, the fhirVersion parameter, and every
    error as an OperationOutcome with a tx-issue-type, never a 5xx.
    """
    parser_classes = (FhirJsonParser, JSONParser, FhirXmlParser, FormParser, MultiPartParser)
    renderer_classes = (FhirJsonRenderer, JSONRenderer, FhirXmlRenderer)
    content_negotiation_class = FhirContentNegotiation
    versioning_class = FhirVersioning

    def get_exception_handler(self):
        return outcomes.exception_handler

    def finalize_response(self, request, response, *args, **kwargs):
        """An error a view returns rather than raises (an empty 404, a {'detail': ...}) goes out as an outcome."""
        if isinstance(response, Response) and response.status_code >= 400:
            if not outcomes.is_outcome(response.data):
                response.data = outcomes.outcome_for_status(response.status_code, response.data)
            response.status_code = outcomes.response_status(response.status_code)
        return super().finalize_response(request, response, *args, **kwargs)

    def get_fhir_parameters(self):
        return FhirParameters.from_request(self.request)

    def get_fhir_base_url(self):
        return fhir_base_url(self.request, self.kwargs)
