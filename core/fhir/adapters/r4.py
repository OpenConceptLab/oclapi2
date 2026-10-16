"""
The FHIR R4 wire form of the version-neutral (R5-shaped) answers core.fhir.ops build: a ConceptMap relationship as R4's
equivalence, and $translate's answer as an R4 Parameters resource.
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


def equivalence(relationship_code):
    """A relationship's R4 equivalence."""
    return EQUIVALENCES[relationship_code]


def translate_parameters(answer):
    """$translate's answer (core.fhir.ops.translate) as R4 Parameters: result, message, and each match's parts."""
    parameter = [{'name': 'result', 'valueBoolean': answer['result']}]
    if answer.get('message'):
        parameter.append({'name': 'message', 'valueString': answer['message']})
    for match in answer['matches']:
        parameter.append({'name': 'match', 'part': [
            {'name': 'equivalence', 'valueCode': equivalence(match['relationship'])},
            {'name': 'concept', 'valueCoding': match['concept']},
            {'name': 'source', 'valueUri': match['source']},
        ]})
    return {'resourceType': 'Parameters', 'parameter': parameter}
