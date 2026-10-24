"""Curated Swagger 2.0 descriptions of the FHIR terminology reads and operations."""
from copy import deepcopy
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator

from core.fhir.base import FHIR_JSON, FHIR_XML
from core.fhir.search import PARAMETERS, STRING_FIELDS, STRING_LOOKUPS

BASES = ('/fhir', '/orgs/{org}', '/users/{user}')
TAGS = ('CodeSystem', 'ValueSet', 'ConceptMap', 'Capabilities')
VERSION_SELECTION = (
    'The default is the latest released version visible at this base; HEAD is used only when explicitly named. '
    'An instance id at /fhir names a released version; at an owner base it is the repository mnemonic.')
SEARCH_DESCRIPTIONS = {
    '_id': 'Resource id: an opaque release id at /fhir, or the repository mnemonic at an owner base.',
    '_lastUpdated': 'FHIR date or dateTime, optionally prefixed by eq, ne, gt, lt, ge, le, sa or eb.',
    'url': 'Canonical URL of the resource.',
    'version': 'Business version token. Searches include released versions only, never HEAD.',
    'status': 'Publication status token: active or retired.',
    'identifier': 'Identifier token: value, system|value, |value or system|.',
    'system': 'Canonical URL of the code system (an alias of url).',
    'code': 'A code defined by the code system; code or system|code.',
    'language': 'Language token for the code system (urn:ietf:bcp:47).',
    'reference': 'Canonical code system URL referenced by the value set\'s stored default expansion.',
    'source-system': 'Canonical URL of a source system in a ConceptMap group.',
    'source-uri': 'Alias of source-system.',
    'target-system': 'Canonical URL of a target system in a ConceptMap group.',
    'target-uri': 'Alias of target-system.',
}


def on_fhir_host(request):
    """Use the same host boundary for the schema and the FHIR middleware."""
    return bool(request and settings.FHIR_SUBDOMAIN and
                request.get_host().startswith(settings.FHIR_SUBDOMAIN + '.'))


def fhir_swagger_url(request, api_url):
    """The FHIR sibling of the API host, keeping the request's scheme and port."""
    url = urlsplit(request.build_absolute_uri('/') if request else api_url)
    host = settings.FHIR_SUBDOMAIN + '.' + (url.netloc.partition('.')[2] or url.netloc)
    return urlunsplit((url.scheme, host, '/swagger/', '', ''))


def query(name, description, type_=openapi.TYPE_STRING, **kwargs):
    """A typed query parameter; complex FHIR values are described in the POST body instead."""
    return openapi.Parameter(name, openapi.IN_QUERY, type=type_, description=description, **kwargs)


NOT_WITH_CODING = ' Not read when coding or codeableConcept is given.'
FORMAT = query('_format', 'Response format; overrides Accept.', enum=['json', 'xml'])
CODE = query('code', 'Code to look up, validate or translate; alternatively supply coding.' + NOT_WITH_CODING)
SYSTEM = query('system', 'Canonical URL or OCL path of the code system the code is from.' + NOT_WITH_CODING)
VERSION = query('version', 'Business version of the code system; defaults to the latest released version.')
LOOKUP_SYSTEM = query('system', 'Canonical URL or OCL path of the code system the code is from. At an instance it '
                                'must name that code system. With coding at the type level, the coding\'s system '
                                'is used instead.')
URL = query('url', 'Canonical URL or OCL path of the target resource; may include |version.')
VALUE_SET_VERSION = query('valueSetVersion', 'Business version of the value set.')
LANGUAGE = query('displayLanguage', 'Preferred language for the returned display, when a name in it exists.')
DISPLAY = query('display', 'Display to validate against the concept\'s names, ignoring case.' + NOT_WITH_CODING)


def coding(replaces, applies=''):
    """The coding input of one operation: a system|code token here, a full Coding in a POSTed body."""
    return query('coding', f'A system|code token. It replaces {replaces}.{applies} A GET token carries no version '
                           'or display: send those inside a POSTed valueCoding.')


OPERATION_PARAMETERS = {
    ('CodeSystem', '$lookup'): [
        LOOKUP_SYSTEM, VERSION, CODE,
        coding('code and system', ' version still selects the code system version; at an instance, a separate '
                                  'system must still name it.'),
        LANGUAGE],
    ('CodeSystem', '$validate-code'): [
        URL, VERSION, CODE,
        coding('code and display', ' url and version still name the code system validated against.'),
        DISPLAY, LANGUAGE],
    ('ValueSet', '$validate-code'): [
        URL, VALUE_SET_VERSION, SYSTEM,
        query('systemVersion', 'Business version of the input code system.' + NOT_WITH_CODING),
        CODE, coding('code, system, systemVersion and display'), DISPLAY, LANGUAGE],
    ('ValueSet', '$expand'): [
        URL, VALUE_SET_VERSION,
        query('filter', 'Each word must prefix a word in the code or one of its names, ignoring case.'), LANGUAGE,
        query('includeDesignations', 'Include every name as a designation.', openapi.TYPE_BOOLEAN),
        query('activeOnly', 'Exclude retired concepts.', openapi.TYPE_BOOLEAN),
        query('offset', 'Number of matching members to skip.', openapi.TYPE_INTEGER, minimum=0, default=0),
        query('count', f'Page size, capped at {settings.FHIR_EXPAND_MAX_COUNT:,}; zero returns only the total.',
              openapi.TYPE_INTEGER, minimum=0, maximum=settings.FHIR_EXPAND_MAX_COUNT,
              default=settings.FHIR_EXPAND_MAX_COUNT),
        query('includeDefinition', 'Only false is supported; definitions are not included.', openapi.TYPE_BOOLEAN,
              enum=[False]),
        query('excludePostCoordinated', 'Only false is supported; stored expansions do not record this distinction.',
              openapi.TYPE_BOOLEAN, enum=[False]),
        query('excludeNested', 'Either boolean is accepted; stored expansions are flat.', openapi.TYPE_BOOLEAN),
        query('excludeNotForUI', 'Either boolean is accepted; stored expansions have no abstract codes.',
              openapi.TYPE_BOOLEAN)],
    ('ConceptMap', '$translate'): [
        URL, query('conceptMapVersion', 'Business version of the map; without url, selects the source\'s map version.'),
        CODE, SYSTEM,
        query('version', 'Input code system version; omitted means no mapping-side version filter. '
                        'Without url in forward translation, also selects the map version unless conceptMapVersion '
                        'is given.' + NOT_WITH_CODING),
        coding('code, system and version'),
        query('source', 'Input value set scope for forward translation; ignored when reverse=true.'),
        query('target', 'Input value set scope for reverse translation; ignored in forward translation.'),
        query('targetsystem', 'Restrict matches to this code system; in reverse, the system of the codes sought.'),
        query('reverse', 'Translate from the target side of the mappings.', openapi.TYPE_BOOLEAN, default=False)],
}
OPERATION_DESCRIPTIONS = {
    ('CodeSystem', '$lookup'): (
        'Look up a code', 'Returns the code\'s display, names and inactive flag, including retired concepts. '
        'A code and, at type level, its system are required (or supply coding).', (400, 404, 422)),
    ('CodeSystem', '$validate-code'): (
        'Validate a code in a code system',
        'Checks the code and optional display in url (or coding.system). A code and a target system are required. '
        'An unknown code or system returns Parameters with result=false; retired codes are valid with a warning.',
        (400,)),
    ('ValueSet', '$validate-code'): (
        'Validate a code in a value set',
        'Checks membership in the stored default expansion and an optional display. A code and its system are '
        'required (or supply coding). Without url at type level, checks the code system alone. '
        'A failed validation returns Parameters with result=false.', (400, 422)),
    ('ValueSet', '$expand'): (
        'Read a stored value set expansion',
        'Returns a page of the stored default expansion; never computes or saves an expansion. '
        'At type level url is required. count is capped at 1,000. Missing, unfinished or incomplete stored '
        'expansions return 422. Inline value sets, contexts, dates, selected designations and code system version '
        'overrides are not supported.', (400, 422)),
    ('ConceptMap', '$translate'): (
        'Translate a code',
        'Returns live mappings from the named ConceptMap. Without url, uses the input system\'s own mappings '
        'in forward translation, or targetsystem\'s mappings in reverse; without targetsystem, reverse has no match. '
        'A code and system are required (or supply coding). Supports reverse translation and a target system filter. '
        'No match returns Parameters with result=false. A POSTed codeableConcept uses its first coding.',
        (400, 404, 422)),
}


def resource_schema(*resource_types):
    """Identify the FHIR response type without duplicating the complete FHIR resource definitions."""
    return openapi.Schema(type=openapi.TYPE_OBJECT, required=['resourceType'], properties={
        'resourceType': openapi.Schema(type=openapi.TYPE_STRING, enum=list(resource_types)),
    })


def parameters_schema(components):
    """One shared Parameters body, including the structured alternatives to query-string coding."""
    definitions = components.with_scope(openapi.SCHEMA_DEFINITIONS)
    if not definitions.has('FhirParameters'):
        schema = resource_schema('Parameters')
        schema.properties['parameter'] = openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(
            type=openapi.TYPE_OBJECT, required=['name'], properties={
                'name': openapi.Schema(type=openapi.TYPE_STRING),
                'valueString': openapi.Schema(type=openapi.TYPE_STRING),
                'valueCode': openapi.Schema(type=openapi.TYPE_STRING),
                'valueUri': openapi.Schema(type=openapi.TYPE_STRING),
                'valueBoolean': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                'valueInteger': openapi.Schema(type=openapi.TYPE_INTEGER),
                'valueCoding': openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                    key: openapi.Schema(type=openapi.TYPE_STRING) for key in ('system', 'code', 'version', 'display')}),
                'valueCodeableConcept': openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                    'coding': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT))}),
            }))
        definitions.set('FhirParameters', schema)
    return openapi.SchemaRef(definitions, 'FhirParameters')


def operation(summary, description, parameters, response_type, errors=(400, 422)):
    """A read with FHIR response media types and OperationOutcome errors; a token shows private content."""
    responses = {'200': openapi.Response(response_type, schema=resource_schema(response_type))}
    errors_by_status = {
        400: 'Invalid or unsupported input.', 404: 'Resource or code not found or not visible at this base.',
        422: 'The request cannot be processed or exceeds the operation\'s processing bound.',
    }
    responses.update({str(code): openapi.Response(errors_by_status[code], schema=resource_schema('OperationOutcome'))
                      for code in errors})
    return openapi.Operation(
        operation_id='', summary=summary, description=description, parameters=[*parameters, FORMAT],
        responses=openapi.Responses(responses), produces=[FHIR_JSON, FHIR_XML])


def search_operation(resource_type):
    """Take the supported names and modifiers from the search parser itself."""
    parameters = [query(name, SEARCH_DESCRIPTIONS[name]) for name in PARAMETERS[resource_type]]
    modifiers = {'': 'Starts with, ignoring case and accents.', 'exact': 'Exact, including case and accents.',
                 'contains': 'Contains, ignoring case and accents.'}
    parameters.extend(query(f'{name}:{modifier}' if modifier else name, f'{name}: {modifiers[modifier]}')
                      for name in STRING_FIELDS for modifier in STRING_LOOKUPS)
    parameters.extend([
        query('_count', f'Page size, capped at {settings.FHIR_SEARCH_MAX_COUNT:,}; zero returns only the total.',
              openapi.TYPE_INTEGER, minimum=0, maximum=settings.FHIR_SEARCH_MAX_COUNT,
              default=settings.FHIR_SEARCH_MAX_COUNT),
        query('page', 'Page number, starting at 1.', openapi.TYPE_INTEGER, minimum=1, default=1),
    ])
    return operation(f'Search {resource_type} resources',
                     'Returns a searchset Bundle of visible released versions, never HEAD. Entries are summaries '
                     'tagged SUBSETTED. Commas separate alternatives; repeated parameters must all match. '
                     'Unsupported parameters or modifiers return 400.', parameters, 'Bundle')


def resource_operations(resource_type, base):
    """The type's search, read and supported terminology operations, once per canonical suffix."""
    read_parameters = [] if base == '/fhir' else [query('version', 'Released business version; never HEAD.')]
    read_description = (
        'The opaque id selects one released version.' if base == '/fhir' else
        'The id is the repository mnemonic. Returns the latest visible release unless version is named.')
    paths = {
        f'/{resource_type}': {'get': search_operation(resource_type)},
        f'/{resource_type}/{{id}}': {'get': operation(
            f'Read a {resource_type}', read_description + ' HEAD is never returned. A cut read is tagged SUBSETTED.',
            read_parameters, resource_type, (400, 404, 422))},
    }
    for (resource, name), (summary, description, errors) in OPERATION_DESCRIPTIONS.items():
        if resource != resource_type:
            continue
        for instance in (False, True) if resource != 'ConceptMap' else (False,):
            suffix = f'/{resource}/' + ('{id}/' if instance else '') + name
            operation_errors = tuple(sorted(set(errors) | ({404} if instance else set())))
            documented = operation(summary, description + ' ' + VERSION_SELECTION,
                                   OPERATION_PARAMETERS[(resource, name)],
                                   'ValueSet' if name == '$expand' else 'Parameters', operation_errors)
            paths[suffix] = {'get': documented, 'post': deepcopy(documented)}
    return paths


def capability_operations():
    """The metadata variants share a route; $versions only supports GET."""
    metadata = operation('Read service capabilities',
                         'Returns CapabilityStatement, or TerminologyCapabilities for mode=terminology.',
                         [query('mode', 'Use terminology to list the code systems and versions visible at this base.',
                                enum=['terminology'])], 'CapabilityStatement')
    metadata.responses['200'] = openapi.Response(
        'CapabilityStatement; TerminologyCapabilities when mode=terminology.',
        schema=resource_schema('CapabilityStatement', 'TerminologyCapabilities'))
    return {
        '/metadata': {'get': metadata},
        '/$versions': {'get': operation('List supported FHIR versions',
                                       'Returns the supported FHIR versions and the default (4.0 for R4).',
                                       [], 'Parameters', (400,))},
    }


def path_parameters(path):
    """Document the FHIR spelling of the route variables, independent of Django's lookup kwarg names."""
    descriptions = {
        'org': 'Organization identifier.', 'user': 'User identifier.',
        'id': 'Opaque release id at /fhir; repository mnemonic at an owner base.',
    }
    return [openapi.Parameter(name, openapi.IN_PATH, type=openapi.TYPE_STRING, required=True, description=description)
            for name, description in descriptions.items() if '{' + name + '}' in path]


def fhir_paths(components):
    """One source of paths for the dedicated FHIR schema and the combined API schema."""
    paths = {}
    body = parameters_schema(components)
    for tag in TAGS:
        for base in BASES:
            templates = capability_operations() if tag == 'Capabilities' else resource_operations(tag, base)
            for suffix, methods in templates.items():
                path = base + suffix
                for method, documented in methods.items():
                    documented.operation_id = 'fhir_' + '_'.join(
                        path.strip('/').replace('{', '').replace('}', '').replace('$', '').replace('-', '_').split('/')
                    ) + '_' + method
                    documented.tags = [tag]
                    documented.parameters = deepcopy(documented.parameters) + path_parameters(path)
                    if method == 'post':
                        documented.consumes = [FHIR_JSON]
                        documented.parameters.append(openapi.Parameter(
                            'parameters', openapi.IN_BODY, schema=body,
                            description='FHIR Parameters: use the query input names with one value[x] per parameter. '
                                        'coding may use valueCoding; codeableConcept may use valueCodeableConcept '
                                        '(one coding for lookup/validation). Body values take precedence over query '
                                        'values. _format stays in the query string.'))
                paths[path] = openapi.PathItem(**methods)
    return openapi.Paths(paths)


class FhirSwaggerSchemaGenerator(OpenAPISchemaGenerator):
    """Generate the curated service schema without inspecting inherited resource write methods."""

    def get_schema(self, request=None, public=False):  # pylint: disable=unused-argument
        components = self.reference_resolver_class(openapi.SCHEMA_DEFINITIONS, force_init=True)
        paths = fhir_paths(components)
        api_url = settings.API_BASE_URL.rstrip('/') + '/swagger/'
        info = openapi.Info(
            title='OCL FHIR terminology service (R4)', default_version='4.0.1',
            description='FHIR R4 terminology in JSON and XML. Serves the SVCM transactions Query Value Set (ITI-95), '
                        'Query Code System (ITI-96), Expand Value Set (ITI-97), Lookup Code (ITI-98), '
                        'Validate Code (ITI-99), Query Concept Map (ITI-100) and Translate Code (ITI-101). '
                        'Bases: /fhir, /orgs/{org} and /users/{user}. The global base shows public resources; '
                        'owner bases show resources visible to the caller. '
                        f'[OCL API Swagger]({api_url}) describes the rest of OCL.')
        security_definitions = self.get_security_definitions()
        return openapi.Swagger(
            info=info, paths=paths, security_definitions=security_definitions,
            security=self.get_security_requirements(security_definitions),
            _url=request.build_absolute_uri('/') if request else self.url, _prefix='', **dict(components))
