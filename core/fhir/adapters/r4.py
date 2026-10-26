"""
The FHIR R4 wire form of the version-neutral (R5-shaped) answers core.fhir.ops build, as R4 Parameters resources.

- $lookup: R4's outputs in R4's order (name, version, display, designation, property), with code, system and abstract
  after display, as FHIRsmith and the terminology ecosystem IG return them. inactive is a property.
- $validate-code: result first, then message, display, code, system, version, inactive, the unknown system, and the
  issues as an OperationOutcome.
- $translate: result, message, and each match's equivalence (a ConceptMap relationship as R4's equivalence), concept
  and source.
"""
from core.concept_maps.constants import (
    EQUIVALENT, NOT_RELATED_TO, RELATED_TO, SOURCE_IS_BROADER, SOURCE_IS_NARROWER)
from core.fhir.constants import FHIR_RELEASE
from core.fhir.xml import prune

# R5 relationship -> R4 equivalence, as HL7's R4/R5 ConceptMap conversion maps them: the target is wider than a source
# that is narrower than it; not-related-to is disjoint
EQUIVALENCES = {
    EQUIVALENT: 'equivalent',
    RELATED_TO: 'relatedto',
    SOURCE_IS_NARROWER: 'wider',
    SOURCE_IS_BROADER: 'narrower',
    NOT_RELATED_TO: 'disjoint',
}


def parameter(name, value_type, value):
    return {'name': name, value_type: value}


def parameters(items):
    """A Parameters resource of (name, value type, value) items, leaving out those without a value."""
    return {'resourceType': 'Parameters', 'parameter': [
        item if isinstance(item, dict) else parameter(*item) for item in items
        if isinstance(item, dict) or item[2] is not None]}


def part_parameter(name, parts):
    return {'name': name, 'part': [parameter(*part) for part in parts if part[2] is not None]}


# The metadata (core.fhir.capabilities) in R4: the service, SVCM 1.5.1's Terminology Repository, which the
# CapabilityStatement instantiates, and the definitions of the operations and search parameters it lists
SERVICE_NAME, SERVICE_TITLE = 'OCLTerminologyService', 'Open Concept Lab FHIR terminology service'
PUBLISHER = 'Open Concept Lab'
SVCM_TERMINOLOGY_REPOSITORY = (
    'https://profiles.ihe.net/ITI/SVCM/CapabilityStatement/IHE.SVCM.TerminologyRepository|1.5.1')
OPERATION_DEFINITION = 'http://hl7.org/fhir/OperationDefinition/'
SYSTEM_OPERATION_DEFINITIONS = {'versions': 'CapabilityStatement-versions'}
SEARCH_PARAMETER = 'http://hl7.org/fhir/SearchParameter/'
SEARCH_PARAMETER_TYPES = {
    '_id': 'token', '_lastUpdated': 'date', 'url': 'uri', 'version': 'token', 'status': 'token', 'identifier': 'token',
    'name': 'string', 'title': 'string', 'description': 'string', 'publisher': 'string', 'system': 'uri',
    'code': 'token', 'language': 'token', 'reference': 'uri', 'source-system': 'uri', 'source-uri': 'reference',
    'target-system': 'uri', 'target-uri': 'reference'}
# R4 defines these once for every resource, or for every conformance resource; the others per resource type
SHARED_SEARCH_PARAMETERS = {
    '_id': 'Resource-id', '_lastUpdated': 'Resource-lastUpdated',
    **{name: f'conformance-{name}' for name in (
        'url', 'version', 'status', 'identifier', 'name', 'title', 'description', 'publisher')}}
# The search parameters OCL reads otherwise than R4 defines them, and how (core.fhir.search's mapped)
SEARCH_PARAMETER_DOCUMENTATION = {
    ('ConceptMap', f'{side}-uri'): (
        f'Read as `{side}-system`: OCL keeps no {side} value set on a ConceptMap, so this matches the system of a '
        f"group's {side}, not a value set.") for side in ('source', 'target')}


def search_parameter(resource_type, name):
    """A search parameter as a CapabilityStatement lists it: its name, R4 definition and type, and how OCL reads it
    when that isn't as R4 defines it."""
    definition = SHARED_SEARCH_PARAMETERS.get(name, f'{resource_type}-{name}')
    documentation = SEARCH_PARAMETER_DOCUMENTATION.get((resource_type, name))
    return {'name': name, 'definition': SEARCH_PARAMETER + definition, 'type': SEARCH_PARAMETER_TYPES[name],
            **({'documentation': documentation} if documentation else {})}


def operation(name, resource_type=None):
    """An operation as a CapabilityStatement lists it: its name and R4 OperationDefinition."""
    definition = f'{resource_type}-{name}' if resource_type else SYSTEM_OPERATION_DEFINITIONS[name]
    return {'name': name, 'definition': OPERATION_DEFINITION + definition}


def described(resource_type, metadata):
    """What a CapabilityStatement and a TerminologyCapabilities both say of the service, in R4's element order."""
    return {
        'resourceType': resource_type, 'name': SERVICE_NAME, 'title': SERVICE_TITLE, 'status': 'active',
        'date': metadata['date'], 'publisher': PUBLISHER, 'kind': 'instance', 'software': metadata['software'],
        'implementation': {'description': SERVICE_TITLE, 'url': metadata['url']},
    }


def capability_statement(statement):
    """The CapabilityStatement (core.fhir.capabilities.capability_statement) in R4: fhirVersion 4.0.1, SVCM."""
    resource = described('CapabilityStatement', statement)
    software, implementation = resource.pop('software'), resource.pop('implementation')
    rest = {'mode': 'server', 'resource': [{
        'type': item['type'],
        'interaction': [{'code': code} for code in item['interactions']],
        'searchParam': [search_parameter(item['type'], name) for name in item['search_parameters']],
        'operation': [operation(name, item['type']) for name in item['operations']],
    } for item in statement['resources']], 'operation': [operation(name) for name in statement['operations']]}
    return {
        **resource, 'instantiates': [SVCM_TERMINOLOGY_REPOSITORY], 'software': software,
        'implementation': implementation, 'fhirVersion': FHIR_RELEASE, 'format': statement['formats'],
        'rest': [prune(rest)],
    }


def terminology_capabilities(capabilities):
    """
    The TerminologyCapabilities (core.fhir.capabilities.terminology_capabilities) in R4: each code system's versions,
    the default one marked isDefault, then the blocks of the operations routed.
    """
    return prune({
        **described('TerminologyCapabilities', capabilities),
        'codeSystem': [{'uri': item['url'], 'version': [
            {'code': version, **({'isDefault': True} if version == item['default'] else {})}
            for version in item['versions']]} for item in capabilities['code_systems']],
        **{block: capabilities[block] for block in ('expansion', 'translation') if block in capabilities},
    })


def versions_parameters(answer):
    """$versions' answer (core.fhir.capabilities.versions) as R4 Parameters: each version, then the default."""
    return parameters([*(('version', 'valueCode', version) for version in answer['versions']),
                       ('default', 'valueCode', answer['default'])])


def lookup_parameters(answer):
    """$lookup's answer (core.fhir.ops.lookup) as R4 Parameters."""
    return parameters([
        ('name', 'valueString', answer['name']),
        ('version', 'valueString', answer['version']),
        ('display', 'valueString', answer['display']),
        ('code', 'valueCode', answer['code']),
        ('system', 'valueUri', answer['system']),
        ('abstract', 'valueBoolean', answer['abstract']),
        *(part_parameter('designation', [
            ('language', 'valueCode', designation['language']),
            ('use', 'valueCoding', designation['use']),
            ('value', 'valueString', designation['value'])]) for designation in answer['designations']),
        part_parameter('property', [('code', 'valueCode', 'inactive'), ('value', 'valueBoolean', answer['inactive'])]),
    ])


def validate_code_parameters(answer):
    """$validate-code's answer (core.fhir.ops.validate_code) as R4 Parameters."""
    unknown_name, unknown_system = answer.get('unknown') or (None, None)
    issues = answer['issues']
    return parameters([
        ('result', 'valueBoolean', answer['result']),
        ('message', 'valueString', answer['message']),
        ('display', 'valueString', answer.get('display')),
        ('code', 'valueCode', answer['code']),
        ('system', 'valueUri', answer['system']),
        ('version', 'valueString', answer.get('version')),
        ('inactive', 'valueBoolean', answer.get('inactive')),
        (unknown_name, 'valueCanonical', unknown_system),
        ('issues', 'resource', {'resourceType': 'OperationOutcome', 'issue': issues} if issues else None),
    ])


def equivalence(relationship_code):
    """A relationship's R4 equivalence."""
    return EQUIVALENCES[relationship_code]


def concept_map_groups(groups):
    """ConceptMap groups (core.fhir.ops.translate.concept_map_groups) in R4, each relationship as an equivalence."""
    return [{**group, 'element': [{**element, 'target': [
        {'code': target['code'], 'equivalence': equivalence(target['relationship'])} for target in element['target']]}
        for element in group['element']]} for group in groups]


def translate_parameters(answer):
    """$translate's answer (core.fhir.ops.translate) as R4 Parameters: result, message, and each match's parts."""
    items = [{'name': 'result', 'valueBoolean': answer['result']}]
    if answer.get('message'):
        items.append({'name': 'message', 'valueString': answer['message']})
    for match in answer['matches']:
        items.append({'name': 'match', 'part': [
            {'name': 'equivalence', 'valueCode': equivalence(match['relationship'])},
            {'name': 'concept', 'valueCoding': match['concept']},
            {'name': 'source', 'valueUri': match['source']},
        ]})
    return {'resourceType': 'Parameters', 'parameter': items}


def expansion_value_set(resource, answer):
    """
    $expand's answer (core.fhir.ops.expand) as an R4 ValueSet: `resource`, the value set version as a read shows it
    without its compose, with the expansion: the stored expansion's identifier and timestamp, total, offset, the inputs
    echoed and each used-codesystem, then the page of members.
    """
    def present(items):
        return {key: value for key, value in items if value not in (None, [])}

    expansion = {
        'identifier': answer['identifier'],
        'timestamp': answer['timestamp'],
        'total': answer['total'],
        'offset': answer['offset'],
        'parameter': [parameter(*item) for item in answer['parameters']] + [
            parameter('used-codesystem', 'valueUri', system) for system in answer['used']],
        'contains': [present((
            ('system', member['system']), ('version', member['version']), ('code', member['code']),
            ('display', member['display']), ('inactive', member['inactive']),
            ('designation', [present(designation.items()) for designation in member['designations'] or []]),
        )) for member in answer['contains']],
    }
    return {**resource, 'expansion': present(expansion.items())}
