import re

# the OCL repository kind a FHIR resource type names
REPOSITORY_KINDS = {'CodeSystem': 'sources', 'ConceptMap': 'sources', 'ValueSet': 'collections'}
REPOSITORY_PATH = re.compile(r'/(orgs|users)/([^/]+)/([^/]+)/([^/]+)/?')


def delete_empty_fields(obj):
    if isinstance(obj, dict):
        for field in list(obj.keys()):
            if obj[field] is None or obj[field] == {} or obj[field] == []:
                del obj[field]
            elif isinstance(obj[field], (dict, list)):
                delete_empty_fields(obj[field])
    if isinstance(obj, list):
        for item in obj:
            delete_empty_fields(item)


def to_ocl_path(url, kind=None):
    """
    An OCL FHIR path (/orgs/{org}/CodeSystem/{id}/...) as the OCL path of the repository it names, by its own
    resource type (so a reference to the other type finds nothing) unless `kind` names one; anything else as it is.
    """
    parts = url.split('/')
    if len(parts) > 3 and not parts[0] and parts[1] in ('orgs', 'users') and parts[3] in REPOSITORY_KINDS:
        parts[3] = kind or REPOSITORY_KINDS[parts[3]]
    return '/'.join(parts)


def to_fhir_path(uri, resource_type):
    """
    An OCL path of a repository, or of anything in it (/orgs/{org}/{sources|collections}/{id}/...), as the repository's
    FHIR path, /orgs/{org}/{resource_type}/{id}/: only the kind's segment is renamed. None when it names no repository.
    """
    parts = uri.strip('/').split('/')
    if len(parts) < 4:
        return None
    if parts[2] in ('sources', 'collections'):
        parts[2] = resource_type
    return '/' + '/'.join(parts[:4]) + '/'


def repository_prefixes(path, kinds, names=('sources', 'CodeSystem', 'ConceptMap'), trailing_slash=False):
    """
    The OCL path prefixes (/orgs/{org}/{kind}/{id}/, one per kind) of the repository a path names as one of `names`
    (by default a source's: its own path, or as FHIR names it), with no version; None for any other value.
    `trailing_slash` requires the slash, as a ConceptMap group writes it.
    """
    match = REPOSITORY_PATH.fullmatch(path or '')
    if not match or match[3] not in names or (trailing_slash and not path.endswith('/')):
        return None
    return [f'/{match[1]}/{match[2]}/{kind}/{match[4]}/' for kind in kinds]
