"""FHIR content negotiation, versioning, parsers, renderers and middleware responses."""
import gzip
import json
import zlib

from django.core.exceptions import BadRequest, SuspiciousOperation
from django.http import HttpResponse
from rest_framework.exceptions import NotAcceptable, UnsupportedMediaType
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.parsers import BaseParser, FormParser, JSONParser, MultiPartParser
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.utils.mediatypes import _MediaType
from rest_framework.versioning import AcceptHeaderVersioning

from core.fhir import outcomes
from core.fhir.constants import (
    ACCEPTED_FHIR_VERSIONS, FHIR_JSON, FHIR_VERSION, FHIR_XML, FORMAT_PARAMETERS, MEDIA_TYPE_ALIASES)
from core.fhir.xml import from_xml, to_xml


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


PARSER_CLASSES = (FhirJsonParser, JSONParser, FhirXmlParser, FormParser, MultiPartParser)
RENDERER_CLASSES = (FhirJsonRenderer, JSONRenderer, FhirXmlRenderer)


def outcome_response(request, outcome, status_code, headers=None):
    """A middleware outcome in the requested format, or safe JSON 400 for unparseable queries or MIME parameters."""
    renderer = FhirJsonRenderer()
    try:
        renderer, _ = FhirContentNegotiation().select_renderer(
            request, [klass() for klass in RENDERER_CLASSES])
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
