"""
What $lookup and $validate-code share (core.fhir.ops.lookup, core.fhir.ops.validate_code): reading the code they are
asked about, finding it in a code system version, and the concept's display and names.

- Input: a code with its system, version and display as their own parameters, or as a Coding (`coding`, or a
  codeableConcept holding one coding). Text inputs may come as any FHIR string type (valueString, valueCode,
  valueUri); anything else is a 400 naming the parameter.
- A code is found among the version's own concepts, retired ones included, as FHIR spells it or percent-encoded, as
  OCL stores a code with reserved characters. One query for the concept (with its repository), one for its names.
- Display: with a language, the concept's preferred name in that language when it has one; else its own display (OCL's
  display_name: the preferred name in its code system's default locale).
"""
from collections import namedtuple
from urllib.parse import quote, unquote

from django.db.models import F, Q

from core.common.constants import HEAD
from core.common.utils import to_parent_uri
from core.concepts.models import Concept
from core.fhir import outcomes
from core.fhir.resolve import REPO_PATHS, UnresolvedError, namespace_of, resolve, to_ocl_path
from core.fhir.search import read_version

# A code, as an operation is given it; `parameter` is the Coding parameter it came in, None for separate parameters
Coding = namedtuple('Coding', 'system code version display parameter')
PATHS = {None: '', 'coding': 'Coding.', 'codeableConcept': 'CodeableConcept.coding[0].'}


def text(parameters, name):
    """A text input (a string, code or uri), or None when it's absent or empty; any other value is a 400 naming it."""
    value = parameters.get(name)
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise outcomes.InvalidError(f"'{name}' must be text.", expression=name)
    return value


def coding_of(value, name):
    """A Coding parameter as a Coding: a dict, or in a GET, a system|code token. Its parts must be text."""
    if isinstance(value, str):
        system, _, code = value.rpartition('|')
        value = {'system': system, 'code': code}
    parts = {key: value.get(key) for key in ('system', 'code', 'version', 'display')} if isinstance(value, dict) else {}
    if not parts or not all(part is None or isinstance(part, str) for part in parts.values()):
        raise outcomes.InvalidError(f"'{name}' must be a Coding.", expression=name)
    return Coding(*(part or None for part in parts.values()), parameter=name)


def coded_input(parameters, system, version):
    """
    The code an operation is asked about: `coding`, a codeableConcept with one coding, or else code and display with
    the system and version from the parameters named `system` and `version`.
    """
    if 'coding' in parameters:
        return coding_of(parameters.get('coding'), 'coding')
    if 'codeableConcept' in parameters:
        concept = parameters.get('codeableConcept')
        codings = concept.get('coding') if isinstance(concept, dict) else None
        if not isinstance(codings, list) or len(codings) != 1:
            raise outcomes.NotSupportedError(
                'A codeableConcept is supported with one coding: send each coding on its own.',
                expression='codeableConcept')
        return coding_of(codings[0], 'codeableConcept')
    return Coding(text(parameters, system), text(parameters, 'code'), text(parameters, version),
                  text(parameters, 'display'), None)


def path(coding, part):
    """The FHIRPath of a part of the code in an issue: the parameter itself, or the part of the Coding it came in."""
    return PATHS[coding.parameter] + part


def require(coding, part, message):
    """The part of the code an operation needs, or a 400 naming the input it's missing from (`part`, or the Coding)."""
    value = getattr(coding, part)
    if not value:
        raise outcomes.InvalidError(message, expression=coding.parameter or part)
    return value


def stored_codes(code):
    """The forms a code may be stored in: as it is, and percent-encoded as OCL encodes reserved characters."""
    return list(dict.fromkeys([code, quote(code, safe=''), quote(code)]))


def code_q(field, code):
    """Q for rows whose `field` is the code in a form OCL stores it in: an equality when there's only one."""
    forms = stored_codes(code)
    return Q(**{field: forms[0]}) if len(forms) == 1 else Q(**{f'{field}__in': forms})


def fhir_code(code):
    """A code as FHIR spells it: OCL stores one with reserved characters percent-encoded (ICD-11's ND53.Y&XK70)."""
    return unquote(code) if code and '%' in code else code


def system_of(version, named=None):
    """A code system version's canonical URL (else its repository's), or else the URL it was named by."""
    return version.canonical_url or getattr(version, 'repository_canonical_url', None) or named


def instance_version(resource_type, instance_id, version, kwargs, user):
    """
    The repository version an instance-level operation is on: as a read finds it (a release: at the global base the
    opaque id's, at an owner base the mnemonic's `version`, else the newest shown); or, at an owner base with HEAD
    named, HEAD through core.fhir.resolve, with its visibility rule (design D6). A global id names a release, so a
    HEAD named there is left to the caller's version check. Not found is a read's 404.
    """
    namespace = namespace_of(kwargs)
    if namespace is None or version != HEAD:
        return read_version(resource_type, instance_id, version, kwargs, user)
    try:
        return resolve(resource_type, f'{namespace}{REPO_PATHS[resource_type]}/{instance_id}/', HEAD, kwargs, user)
    except UnresolvedError as ex:
        raise outcomes.NotFoundError(f'{resource_type}/{instance_id} not found') from ex


def is_system_of(system, version, named=None):
    """
    Whether a coding's system (or a value set url) names a repository version: its canonical URL (or its
    repository's), the URL it was resolved by (`named`), or an OCL path of its repository or of the version.
    """
    url = system.partition('|')[0]
    canonicals = {version.canonical_url, getattr(version, 'repository_canonical_url', None),
                  (named or '').partition('|')[0]}
    if url in canonicals - {None, ''}:
        return True
    repository = to_parent_uri(to_ocl_path(url))
    repository = repository if repository.endswith('/') else f'{repository}/'
    return repository in {version.uri, '/'.join(version.uri.split('/')[:5]) + '/'}


def mismatch(parameters, coding, version, system_name, instance_id):
    """
    (expression, message) of an input naming another code system or version than the one resolved, else None. At the
    instance level, the id names it: the system parameter (`system_name`, url or system), the coding's system and any
    version named (the version parameter, the coding's, a url|version) must all be its. At the type level, a Coding's
    system and version must be those of the code system it is validated in, which url and version may name.
    """
    if instance_id:
        systems = [(text(parameters, system_name), system_name), (coding.system, path(coding, 'system'))]
        versions = [(text(parameters, 'version'), 'version'), (coding.version, path(coding, 'version'))]
        named = None
    elif coding.parameter:
        systems, versions = [(coding.system, path(coding, 'system'))], [(coding.version, path(coding, 'version'))]
        named = text(parameters, system_name) or coding.system
    else:
        return None
    for system, expression in systems:
        if system and not is_system_of(system, version, named):
            return expression, f"'{system}' is not the code system '{system_of(version, named)}' it is about"
        versions.append((system and system.partition('|')[2], expression))
    for named_version, expression in versions:
        if named_version and named_version != version.version:
            return expression, f"Version '{named_version}' is not the version '{version.version}' it is about"
    return None


def find_concept(version, code):
    """
    The code system version's concept with the code, retired or not (a deleted one, is_active false, is gone), with its
    repository and names; else None.
    """
    concepts = version.concepts_set.filter(id=F('versioned_object_id')) if version.is_head else \
        Concept.objects.filter(sources=version)
    return concepts.filter(code_q('mnemonic', code), is_active=True).select_related('parent').prefetch_related(
        'names').order_by('id').first()


def display_of(concept, language=None):
    """
    The concept's display: with a language, its preferred name in that language, newest first, when it has one; else
    its own display. None for a concept without names.
    """
    if language:
        names = sorted((name for name in concept.names.all() if name.locale == language and not name.retired),
                       key=lambda name: (name.locale_preferred, name.created_at), reverse=True)
        if names:
            return names[0].name
    return concept.display_name


def has_name(concept, display):
    """
    Whether a display is right for the concept: one of its names, in any language, ignoring case (FHIRsmith's default
    check). A concept without names has nothing to check a display against.
    """
    names = [name.name.casefold() for name in concept.names.all()]
    return not names or display.casefold() in names


def designations(concept):
    """Each of the concept's names as a designation: its language, its OCL name type as `use`, and its text."""
    return [{'language': name.locale, 'use': {'code': name.type} if name.type else None, 'value': name.name}
            for name in concept.names.all()]
