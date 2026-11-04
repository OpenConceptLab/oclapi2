"""FHIR route namespaces and request base URLs."""
from core.fhir.constants import OWNER_BASE_PATHS, RESOURCE_TYPES


def is_fhir_path(path):
    """
    Whether a path is under a FHIR base: the global /fhir/, or at an owner base (/orgs/{org}/, /users/{user}/) a FHIR
    resource type, its metadata or $versions.
    """
    segments = path.strip('/').split('/')
    return segments[0] == 'fhir' or (len(segments) >= 3 and segments[0] in ('orgs', 'users') and (
        segments[2] in RESOURCE_TYPES or (len(segments) == 3 and segments[2] in OWNER_BASE_PATHS)))


def fhir_base_url(request, kwargs=None):
    """
    The FHIR base a request came in on, from its own scheme and host (the forwarded ones behind the load balancer),
    so links work on every host OCL serves FHIR from: /fhir, or the owner base /orgs/{org} or /users/{user}.
    """
    return request.build_absolute_uri((namespace_of(kwargs) or '/fhir/')[:-1])


def namespace_of(kwargs):
    """The namespace of the FHIR base a route's kwargs name: the owner's URI, or None for the global base."""
    kwargs = kwargs or {}
    if kwargs.get('org'):
        return f"/orgs/{kwargs['org']}/"
    if kwargs.get('user'):
        return f"/users/{kwargs['user']}/"
    return None
