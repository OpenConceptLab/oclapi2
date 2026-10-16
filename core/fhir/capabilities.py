"""
What the FHIR metadata says, version-neutral, built from the code: core.fhir.adapters.r4 writes it as R4's
CapabilityStatement, TerminologyCapabilities and $versions Parameters.

- The routes: what a base offers is what the URL configuration routes under it (/fhir/, /orgs/{org}/ or
  /users/{user}/). A resource type's search route (a FhirSearchMixin view) is its search-type interaction, its
  instance route (a FhirReadMixin view) its read, and each $operation route to a FhirOperationView, at the type or
  instance level, one of its operations; a $operation route directly under the base is a system operation. Routing a
  view lists it; nothing else does.
- The search parameters are the search's own (core.fhir.search's PARAMETERS and STRING_FIELDS).
- The FHIR versions are those the fhirVersion MIME parameter negotiates (core.fhir.base): R4, 4.0.
- The software is the OCL API and its version; the base URL is the request's.
- TerminologyCapabilities lists the code systems the operations serve the caller at the base, as resolve() serves
  them were the repositories the caller isn't shown missing (core.fhir.resolve's served_heads and served_versions),
  so a hidden repository changes nothing in it. Each is listed by the identifier the resolver finds it by: its
  canonical URL (its HEAD's; the repository the resolver picks for it, with the URL registry when the toggle is on),
  else its OCL path as a CodeSystem; with the released versions served by name, newest first, and the default, the
  one served without a version. A fixed number of queries, bounded as a search's are (SEARCH_TIMEOUT_MS; past it the
  list is too costly, a 422).
- The routed operations' contracts fill TerminologyCapabilities' blocks: $translate finds the map from the code's
  system (core.fhir.ops.translate), so it needs none; $expand pages, with offset and count.
"""
import re
from functools import lru_cache

from django.urls import URLResolver, get_resolver
from django.utils import timezone

from core import __version__
from core.common.utils import decode_string
from core.fhir import outcomes
from core.fhir.base import (
    RESOURCE_TYPES, FhirOperationView, FhirVersioning, FhirViewMixin, fhir_base_url)
from core.fhir.resolve import served_heads, served_versions
from core.fhir.search import PARAMETERS, STRING_FIELDS, FhirReadMixin, FhirSearchMixin, within_search_timeout

SOFTWARE = {'name': 'OCL API', 'version': __version__}
STARTED = timezone.now().replace(microsecond=0).isoformat()  # what the statements describe was deployed then
FORMATS = [renderer.media_type for renderer in FhirViewMixin.renderer_classes if 'fhir' in renderer.media_type]
OWNER_BASES = {'orgs': 'org', 'users': 'user'}  # the owner bases' first segment, and the route kwarg naming the owner
INTERACTIONS = {'read': FhirReadMixin, 'search-type': FhirSearchMixin}  # in R4's order, by the view serving each
CONVERTER = re.compile(r'<(?:\w+:)?(\w+)>')  # a route's <converter:name> segment
# What TerminologyCapabilities says of an operation the base routes, by its block: its contract
OPERATION_BLOCKS = {
    ('ConceptMap', 'translate'): ('translation', {'needsMap': False}),
    ('ValueSet', 'expand'): ('expansion', {'paging': True}),
}


def routes(resolver, prefix=''):
    """(route, view class) for every route of a URL configuration, its includes followed."""
    for pattern in resolver.url_patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from routes(pattern, route)
        else:
            yield route, getattr(pattern.callback, 'view_class', None)


def base_of(route):
    """(the base, the route's segments under it) of a route under a FHIR base: None for /fhir/, else the owner kwarg
    ('org' or 'user'). (False, None) for any other route."""
    segments = route.strip('/').split('/')
    if segments[0] == 'fhir':
        return None, segments[1:]
    converter = CONVERTER.fullmatch(segments[1]) if len(segments) > 1 else None
    if segments[0] in OWNER_BASES and converter and converter[1] == OWNER_BASES[segments[0]]:
        return converter[1], segments[2:]
    return False, None


def is_view(view, kind):
    return isinstance(view, type) and issubclass(view, kind)


def offering(segments, view):
    """
    What a route under a base offers, from its segments under the base and its view: ('$', name) for a system
    operation, (resource type, 'interaction', code) or (resource type, 'operation', name), else None.
    """
    resource_type, *rest = segments
    if not rest and resource_type.startswith('$') and is_view(view, FhirOperationView):
        return '$', resource_type[1:]
    if resource_type not in RESOURCE_TYPES:
        return None
    instance = bool(rest) and bool(CONVERTER.fullmatch(rest[0]))
    rest = rest[1:] if instance else rest
    interaction = 'read' if instance else 'search-type'
    if not rest and is_view(view, INTERACTIONS[interaction]):
        return resource_type, 'interaction', interaction
    if len(rest) == 1 and rest[0].startswith('$') and is_view(view, FhirOperationView):
        return resource_type, 'operation', rest[0][1:]
    return None


@lru_cache(maxsize=None)
def routed(resolver):
    """
    {base: (resources, system operations)} for a URL configuration: each base's resource types in route order, each
    with its interactions (in R4's order) and its operations (in route order), and the base's own operations.
    """
    found = {}
    for route, view in routes(resolver):
        fhir_base, segments = base_of(route)
        offer = offering(segments, view) if fhir_base is not False and segments and segments[0] else None
        if offer:
            resources, system = found.setdefault(fhir_base, ({}, []))
            if offer[0] == '$':
                system.append(offer[1])
            else:
                resources.setdefault(offer[0], {'interaction': [], 'operation': []})[offer[1]].append(offer[2])
    return {fhir_base: (
        [{'type': resource_type, 'interactions': [code for code in INTERACTIONS if code in resource['interaction']],
          'operations': unique(resource['operation'])} for resource_type, resource in resources.items()],
        unique(system)) for fhir_base, (resources, system) in found.items()}


def unique(items):
    return list(dict.fromkeys(items))


def base_kind(kwargs):
    """The base a route's kwargs name: None for /fhir/, else the owner kwarg."""
    return next((kind for kind in OWNER_BASES.values() if (kwargs or {}).get(kind)), None)


def offered(kwargs):
    """(resources, system operations) the base `kwargs` names routes."""
    return routed(get_resolver()).get(base_kind(kwargs), ([], []))


def described(request, kwargs):
    """What both statements say of the service: the base URL, when it was deployed, and the software."""
    return {'url': fhir_base_url(request, kwargs), 'date': STARTED, 'software': SOFTWARE}


def capability_statement(request, kwargs):
    """The CapabilityStatement of the base `kwargs` names, as the routes under it make it."""
    resources, system = offered(kwargs)
    return {
        **described(request, kwargs),
        'formats': FORMATS,
        'resources': [{
            **resource,
            'search_parameters': [*PARAMETERS[resource['type']], *STRING_FIELDS]
            if 'search-type' in resource['interactions'] else []} for resource in resources],
        'operations': system,
    }


def versions(parameters, kwargs, user, instance=None):  # pylint: disable=unused-argument
    """
    $versions: the FHIR versions the fhirVersion MIME parameter accepts, as major.minor (4.0 and 4.0.1 are R4, 4.0),
    and the one served when a request names none.
    """
    return {'versions': unique('.'.join(version.split('.')[:2]) for version in FhirVersioning.allowed_versions),
            'default': FhirVersioning.default_version}


def code_systems(kwargs, user):
    """
    [{url, versions, default}] for each code system the base serves the caller (core.fhir.resolve's served_heads: the
    repository resolve() picks for each identifier, were the repositories the caller isn't shown missing), by its
    identifier: the released versions it serves by name, newest first, and the one it serves without a version (the
    newest live one the caller is shown, as resolve() picks it). A repository serving no live release isn't listed.
    """
    heads = served_heads(kwargs, user)
    served = served_versions(kwargs, user, heads.values())
    listed = []
    for identifier in sorted(heads):
        head = heads[identifier]
        rows = served[(head.mnemonic, head.organization_id, head.user_id)]
        default = next((version for version, retired in rows if not retired), None)
        if default is None:
            continue
        codes = [version for version, _ in rows if decode_string(version) == version]  # as the resolver reads one
        listed.append({'url': identifier, 'versions': codes, 'default': default if default in codes else None})
    return listed


def list_too_costly():
    return outcomes.TooCostlyError('The list of code systems would take too long to answer.')


def terminology_capabilities(request, kwargs):
    """The TerminologyCapabilities of the base `kwargs` names, for the request's user."""
    resources, _ = offered(kwargs)
    operations = {(resource['type'], name) for resource in resources for name in resource['operations']}
    return {
        **described(request, kwargs),
        'code_systems': within_search_timeout(lambda: code_systems(kwargs, request.user), list_too_costly),
        **dict(block for operation, block in OPERATION_BLOCKS.items() if operation in operations),
    }
