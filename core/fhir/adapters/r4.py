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
