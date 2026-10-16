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
- TerminologyCapabilities lists the code systems a CodeSystem search at the base shows the caller
  (core.fhir.resolve's visible_versions: released versions only, never HEAD; at /fhir/ the public ones of public
  repositories; at an owner base the owner's, private ones to those who may view them), by their canonical URL as a
  search shows it (the version's own, else its repository's), each with its versions, newest first, and the default:
  the newest live release of its repository, the one the resolver serves without a version, where the resolver serves
  it (under the repository's own canonical URL). A canonical URL two repositories share has no default, since the
  resolver serves one of them. A version without a canonical URL isn't listed. One query, bounded as a search's is
  (SEARCH_TIMEOUT_MS; past it the list is too costly, a 422).
- The routed operations' contracts fill TerminologyCapabilities' blocks: $translate finds the map from the code's
  system (core.fhir.ops.translate), so it needs none; $expand pages, with offset and count.
"""
import re
from functools import lru_cache

from django.urls import URLResolver, get_resolver
from django.utils import timezone

from core import __version__
from core.fhir import outcomes
from core.fhir.base import (
    RESOURCE_TYPES, FhirOperationView, FhirVersioning, FhirViewMixin, fhir_base_url)
from core.fhir.resolve import visible_versions
from core.fhir.search import (
    PARAMETERS, STRING_FIELDS, FhirReadMixin, FhirSearchMixin, with_repository_canonical, within_search_timeout)

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


def listed_versions(kwargs, user):
    """
    The CodeSystem versions a search at the base shows the caller, newest first, as (canonical URL as a search shows
    it, the repository's canonical URL, version, retired, repository): one query.
    """
    rows = with_repository_canonical(visible_versions('CodeSystem', kwargs, user)).order_by('-created_at', '-id')
    return [(own or repository_url, repository_url, version, retired, tuple(repository))
            for own, repository_url, version, retired, *repository in rows.values_list(
                'canonical_url', 'repository_canonical_url', 'version', 'retired', 'mnemonic', 'organization_id',
                'user_id')]


def code_systems(kwargs, user):
    """
    [{url, versions, default}] for each canonical URL the base shows the caller, by URL (see the module's doc). The
    default is the newest live release of the one repository listed under the URL, marked when the resolver serves it
    there: the URL is the repository's own (its HEAD's, which the resolver matches), and the release is listed under it.
    """
    defaults, by_url = {}, {}
    for url, repository_url, version, retired, repository in listed_versions(kwargs, user):
        if not retired:
            defaults.setdefault(repository, (repository_url, version))  # the newest live release
        if url:
            codes, repositories = by_url.setdefault(url, ([], set()))
            codes.append(version)
            repositories.add(repository)
    listed = []
    for url in sorted(by_url):
        codes, repositories = by_url[url]
        only = next(iter(repositories)) if len(repositories) == 1 else None
        served_at, default = defaults.get(only, (None, None))
        listed.append({'url': url, 'versions': unique(codes),
                       'default': default if served_at == url and default in codes else None})
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
