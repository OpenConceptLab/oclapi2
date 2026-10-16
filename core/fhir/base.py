"""
The FHIR view layer: what every FHIR view shares, whatever its operation.

- Media types: FHIR JSON and XML in and out, with the aliases FHIR clients use, and `_format`.
- The `fhirVersion` MIME parameter: R4 (4.0) is served and any other version is refused.
- XML through fhir.resources' R4B models, which are wire-identical to R4 for the terminology resources, so
  elements always come out in FHIR's order.
- One reader for operation parameters, from a GET query string or a POSTed Parameters resource.
- The FHIR base URL of a request, from its own host.
- Errors as OperationOutcome (core.fhir.outcomes), in views and in the middleware.
"""
import gzip
import json
import logging
import re
import types
import typing
import zlib
from functools import lru_cache

from django.core.exceptions import BadRequest, SuspiciousOperation
from django.http import HttpResponse
from fhir.resources.R4B import get_fhir_model_class
from fhir_core.types import FhirBase
from lxml import etree
from pydantic import ValidationError
from rest_framework.exceptions import NotAcceptable, ParseError, UnsupportedMediaType
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.parsers import BaseParser, FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.utils.encoders import JSONEncoder
from rest_framework.utils.mediatypes import _MediaType
from rest_framework.versioning import AcceptHeaderVersioning
from rest_framework.views import APIView

from core.common.throttling import ThrottleUtil
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
OWNER_BASE_PATHS = ('metadata', '$versions')  # what an owner base answers besides its resource types

FHIR_NS = 'http://hl7.org/fhir'
XHTML_NS = 'http://www.w3.org/1999/xhtml'
EXTENSION_ELEMENTS = ('extension', 'modifierExtension')
EMPTY_VALUES = (None, '', {}, [])
# What XML 1.0 can't hold: C0 controls other than tab, LF and CR, lone surrogates, U+FFFE and U+FFFF
XML_FORBIDDEN_CHARACTERS = re.compile('[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')
REPLACEMENT_CHARACTER = '\ufffd'
XML_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]*$')
LOGGED_DROPPED_ELEMENTS = set()  # what the XML has left out so far: each kind is logged once per process


def xml_parser():
    """A parser that never fetches or expands entities: a request body is untrusted."""
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, remove_comments=True)


def is_fhir_path(path):
    """
    Whether a path is under a FHIR base: the global /fhir/, or at an owner base a FHIR resource type, its metadata or
    $versions.
    """
    segments = path.strip('/').split('/')
    return segments[0] == 'fhir' or (len(segments) >= 3 and segments[2] in RESOURCE_TYPES) or (
        len(segments) == 3 and segments[0] in ('orgs', 'users') and segments[2] in OWNER_BASE_PATHS)


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


def parse_media_type(media_type):
    """Treat undecodable MIME parameters as a bad request, including unknown RFC 2231 encodings."""
    try:
        return _MediaType(media_type)
    except (LookupError, ValueError) as exc:
        raise BadRequest('The media type parameters could not be parsed.') from exc


def accepted_media_ranges(request):
    """The media ranges a request accepts: its _format, else its Accept header."""
    ranges = [item.strip() for item in request.META.get('HTTP_ACCEPT', '').split(',') if item.strip()]
    for media_range in ranges:
        parse_media_type(media_range)  # validate before _format and browser preference shortcuts
    for name in FORMAT_PARAMETERS:
        value = request.GET.get(name)
        if value:
            return [normalize_media_type(value)]
    if any(item.startswith('text/html') for item in ranges) and not any('fhir' in item for item in ranges):
        return ['application/json']  # a browser: JSON as before, not the application/xml it lists after text/html
    return [normalize_media_type(item) for item in ranges] or ['*/*']


def quality(media_range):
    try:
        return float(parse_media_type(media_range).params.get('q', 1))
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
        content_type = parse_media_type(normalize_media_type(request.content_type or ''))
        return next((parser for parser in parsers if parse_media_type(parser.media_type).match(content_type)), None)

    def select_renderer(self, request, renderers, format_suffix=None):
        """Each renderer takes its quality from its most specific match, including q=0 exclusions."""
        # Reject a malformed Content-Type before choosing XML, so every parsing failure can use safe JSON.
        parse_media_type(request.META.get('CONTENT_TYPE', request.META.get('HTTP_CONTENT_TYPE', '')))
        ranges = sorted(enumerate(accepted_media_ranges(request)),
                        key=lambda item: (-parse_media_type(item[1]).precedence, -quality(item[1])))
        candidates = []
        for renderer in renderers:
            for position, media_range in ranges:
                parsed_range = parse_media_type(media_range)
                if parse_media_type(renderer.media_type).match(parsed_range):
                    preference = (quality(media_range), parsed_range.precedence, -position)
                    if preference[0] > 0:
                        candidates.append((preference, renderer, media_range))
                    break  # a less specific range cannot override this renderer's quality
        if candidates:
            _, renderer, media_range = max(candidates, key=lambda candidate: candidate[0])
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
        return parse_media_type(media_type or '').params.get(self.version_param.lower())

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


def json_values(data):
    """The data as the JSON response carries it: DRF's encoder writes datetimes, dates, decimals and UUIDs."""
    return json.loads(json.dumps(data, cls=JSONEncoder))


def xml_safe(value):
    """(value, how many characters were replaced): every character XML 1.0 forbids becomes U+FFFD."""
    if isinstance(value, str):
        return XML_FORBIDDEN_CHARACTERS.subn(REPLACEMENT_CHARACTER, value)
    if isinstance(value, dict):
        items = [(xml_safe(key), xml_safe(item)) for key, item in value.items()]
        return {key: item for (key, _), (item, _) in items}, sum(left + right for (_, left), (_, right) in items)
    if isinstance(value, (list, tuple)):
        items = [xml_safe(item) for item in value]
        return [item for item, _ in items], sum(count for _, count in items)
    return value, 0


def element_type(annotation):
    """(the R4B model of a field's values, or None for a primitive; whether the field repeats)."""
    repeats = False
    while True:
        origin = typing.get_origin(annotation)
        if origin in (typing.Union, types.UnionType):
            annotation = next(arg for arg in typing.get_args(annotation) if arg is not type(None))
        elif origin is list:
            repeats, annotation = True, typing.get_args(annotation)[0]
        elif origin is typing.Annotated:
            annotation = typing.get_args(annotation)[0]
        else:
            break
    is_model = isinstance(annotation, type) and issubclass(annotation, FhirBase)
    return (annotation.get_model_klass() if is_model else None), repeats


@lru_cache(maxsize=None)
def model_elements(model_class):
    """An R4B model's elements by their JSON name: (field name, the model of its values or None, repeats)."""
    return {field.alias or name: (name, *element_type(field.annotation))
            for name, field in model_class.model_fields.items() if name != 'fhir_comments'}


def resource_model(data):
    """The R4B model of a FHIR JSON resource, or None when it isn't one."""
    if not isinstance(data, dict) or not isinstance(data.get('resourceType'), str):
        return None
    try:
        return get_fhir_model_class(data['resourceType'])
    except ValueError:
        return None


def log_dropped(model_class, key, reason):
    if (model_class.__name__, key, reason) not in LOGGED_DROPPED_ELEMENTS:
        LOGGED_DROPPED_ELEMENTS.add((model_class.__name__, key, reason))
        logger.warning('FHIR XML: %s.%s %s', model_class.__name__, key, reason)


def xhtml_markup(text):
    """A narrative div as XHTML markup: the text itself when it is an XHTML div, else the text inside one."""
    try:
        if etree.fromstring(text, parser=xml_parser()).tag == f'{{{XHTML_NS}}}div':
            return text
    except etree.XMLSyntaxError:
        pass
    return etree.tostring(xhtml_div(text), encoding='unicode')


def conform_value(value_model, value):
    """One value of an element, conformed, or None when it doesn't have the element's shape."""
    if value_model is None:
        return None if isinstance(value, (dict, list)) else value
    if not isinstance(value, dict):
        return None
    model_class = resource_model(value) if 'resourceType' in value else value_model  # a resource names its type
    return conform(model_class, value) if model_class else None


def conform(model_class, data):
    """
    FHIR JSON reduced to what the R4 model defines, at every depth. A key R4 doesn't define (OCL's revisionDate) and
    a value of the wrong shape are left out, each kind logged once: that is the content's problem, not the wire
    format's. The JSON response itself is not changed.
    """
    elements, result = model_elements(model_class), {}
    for key, value in data.items():
        if key == 'resourceType':
            result[key] = value
            continue
        if key not in elements:
            log_dropped(model_class, key, 'is an element R4 does not define, so the XML leaves it out')
            continue
        _, value_model, repeats = elements[key]
        if isinstance(value, list) and not repeats:  # OCL's ConceptMap.identifier is a list; R4 allows one
            if len(value) > 1:
                log_dropped(model_class, key, 'repeats, but not in R4, so the XML keeps only the first')
            value = value[:1]
        items = value if isinstance(value, list) else [value]
        kept = [item for item in (conform_value(value_model, item) for item in items) if item is not None]
        if len(kept) < len(items):
            log_dropped(model_class, key, 'has values of a shape R4 does not allow, so the XML leaves them out')
        if key == 'div' and kept:
            kept = [xhtml_markup(kept[0])]
        if kept:
            result[key] = kept if repeats else kept[0]
    return result


def construct(model_class, data):
    """
    A model instance from conformed FHIR JSON, nested models included, built without validation, so the model
    still writes its elements in FHIR's order. For what the model refuses only on a pattern, such as an OCL id
    with '_' or '@', or a code with a trailing space.
    """
    elements, values = model_elements(model_class), {}
    for key, value in data.items():
        if key == 'resourceType':
            continue
        name, value_model, repeats = elements[key]
        items = [construct_value(value_model, item) for item in (value if repeats else [value])]
        values[name] = items if repeats else items[0]
    return model_class.model_construct(**values)


def construct_value(value_model, value):
    if value_model is None:
        return value if isinstance(value, bool) else str(value)  # the writer takes text, and booleans as they are
    return construct(resource_model(value) or value_model, value)


def to_xml(data):
    """
    FHIR JSON -> FHIR XML, always through the R4B model, so in FHIR's element order. It starts from the values the
    JSON response carries. Keys R4 doesn't define are left out; a resource the model refuses on a pattern is built
    without validation; characters XML 1.0 forbids become U+FFFD. Data that isn't a FHIR resource is answered with
    an OperationOutcome.
    """
    if data is None:
        return b''
    data = json_values(data)
    model_class = resource_model(data)
    if model_class is None:
        data = outcomes.operation_outcome(outcomes.issue('not-supported', 'This response has no FHIR XML form.'))
        model_class = resource_model(data)
    data, replaced = xml_safe(data)
    if replaced:
        logger.warning('FHIR XML: %s character(s) XML cannot hold became U+FFFD in a %s', replaced,
                       data['resourceType'])
    data = prune(conform(model_class, prune(data)))
    try:
        instance = model_class.model_validate(data)
    except ValidationError:
        instance = construct(model_class, data)
    try:
        return instance.model_dump_xml()
    except Exception:
        logger.exception('FHIR XML: the %s model could not write it; written element by element', model_class.__name__)
        root = etree.Element(f"{{{FHIR_NS}}}{data['resourceType']}", nsmap={None: FHIR_NS})
        write_xml_children(root, data, resource=True)
        return etree.tostring(root, xml_declaration=True, encoding='utf-8')


def write_xml_children(element, obj, resource=False, extension=False):
    """The last resort writer: element by element, in the data's own order."""
    for key, value in obj.items():
        if key == 'resourceType' or not XML_NAME.match(key):
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
    """A middleware outcome in the requested format, or safe JSON 400 for unparseable queries or MIME parameters."""
    renderer = FhirJsonRenderer()
    try:
        renderer, _ = FhirContentNegotiation().select_renderer(
            request, [klass() for klass in FhirViewMixin.renderer_classes])
    except NotAcceptable:
        pass
    except (BadRequest, SuspiciousOperation) as exc:
        status_code, outcome, _ = outcomes.outcome_for_exception(exc)
    content_type = f'{renderer.media_type}; charset={renderer.charset}' if renderer.charset else renderer.media_type
    response = HttpResponse(
        renderer.render(outcome), status=outcomes.response_status(status_code), content_type=content_type)
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def response_json(response):
    """A response's JSON body, gunzipped if need be, or None."""
    if 'json' not in response.get('Content-Type', '') or getattr(response, 'streaming', False):
        return None
    content = response.content
    try:
        if response.get('Content-Encoding') == 'gzip':
            content = gzip.decompress(content)
        return json.loads(content)
    except (OSError, EOFError, zlib.error, ValueError):
        return None


def is_outcome_response(response):
    """Whether a response is already FHIR: an error from a FHIR view, rather than Django's or another view's."""
    return 'fhir' in response.get('Content-Type', '') or outcomes.is_outcome(response_json(response))


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
        """Normalize returned outcomes at every status and depth, and wrap other error responses as outcomes."""
        if isinstance(response, Response):
            if response.status_code >= 400:
                if not outcomes.is_outcome(response.data):
                    response.data = outcomes.outcome_for_status(response.status_code, response.data)
                response.status_code = outcomes.response_status(response.status_code)
            response.data = outcomes.normalize_outcomes(response.data)
        return super().finalize_response(request, response, *args, **kwargs)

    def get_fhir_parameters(self):
        return FhirParameters.from_request(self.request)

    def get_fhir_base_url(self):
        return fhir_base_url(self.request, self.kwargs)


class FhirOperationView(FhirViewMixin, APIView):
    """
    A FHIR operation that only reads, GET and POST alike, at the global base and at owner bases: `operation`'s answer
    for the request's FhirParameters, base and user (and the resource the route kwarg `instance` names, at the instance
    level), written by `adapter`. Throttled as OCL's other views are.
    """
    permission_classes = (AllowAny,)  # what an operation shows the caller is core.fhir.resolve's rule
    operation = adapter = None
    instance = None

    def get_throttles(self):
        return ThrottleUtil.get_throttles_by_user_plan(self.request.user)

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        answer = self.operation(  # pylint: disable=not-callable
            self.get_fhir_parameters(), self.kwargs, request.user, kwargs.get(self.instance))
        return Response(self.adapter(answer))  # pylint: disable=not-callable

    post = get
