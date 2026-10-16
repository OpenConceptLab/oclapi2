"""
CodeSystem $lookup (SVCM ITI-98, FHIR R4's $lookup), version-neutral: what OCL holds about a code of the code system
version the request names. core.fhir.adapters.r4 writes the answer as R4 Parameters.

- The code system: `system` and `version` (or a `coding`'s), resolved by core.fhir.resolve at the base's namespace: a
  released version, the newest the caller is shown unless one is named (HEAD only when named); public only at /fhir/;
  at an owner base, private ones to those who may view them. CodeSystem/{id}/$lookup names it as a read does. One the
  caller isn't shown answers exactly as one that doesn't exist.
- The code: that version's concept, retired ones too, which are flagged inactive (core.fhir.ops.concepts).
- The answer: name, version (the one resolved), display, code, system, abstract (OCL has no abstract concepts), each
  of the concept's names as a designation, and the inactive property. `displayLanguage` picks the display among the
  concept's names in that language; a concept without names is displayed by its code.
- Errors, as FHIRsmith answers them: no code, or no system at the type level, is a 400; a code system that doesn't
  resolve is a 422 not-found, a code it doesn't hold a 404 (tx-issue-type invalid-code).
- Cost: a fixed number of queries whatever the size of the code system: resolution, the concept, its names.
"""
from rest_framework import status

from core.fhir import outcomes
from core.fhir.ops.concepts import (
    coded_input, require, find_concept, display_of, designations, fhir_code, is_system_of, path, system_of, text)
from core.fhir.resolve import UnresolvedError, resolve
from core.fhir.search import read_version


def code_system_version(coding, kwargs, user, instance_id=None):
    """The code system version a $lookup or $validate-code names: the instance, else the coding's system and version."""
    if instance_id:
        return read_version('CodeSystem', instance_id, coding.version, kwargs, user)
    return resolve('CodeSystem', coding.system, coding.version, kwargs, user)


def lookup(parameters, kwargs, user, instance_id=None):
    """
    $lookup's answer for its FhirParameters at the base `kwargs` names (CodeSystem/{instance_id}/$lookup at the instance
    level), for `user`: {name, version, display, code, system, abstract, inactive, designations}.
    """
    coding = coded_input(parameters, 'system', 'version')
    code = require(coding, 'code', 'code', '$lookup needs the code to look up.')
    if not instance_id:
        require(coding, 'system', 'system', '$lookup needs the system the code is from.')
    try:
        version = code_system_version(coding, kwargs, user, instance_id)
    except UnresolvedError as ex:
        raise outcomes.NotFoundError(
            str(ex.detail), tx_issue_type='not-found', status_code=status.HTTP_422_UNPROCESSABLE_ENTITY) from ex
    if instance_id and coding.system and not is_system_of(coding.system, version):
        raise outcomes.InvalidError(
            f"'{coding.system}' is not the code system {instance_id} this $lookup is on.",
            expression=path(coding, 'system'))
    system = system_of(version, coding.system)
    concept = find_concept(version, code)
    if not concept:
        raise outcomes.NotFoundError(
            f"Unable to find code '{code}' in {system} version {version.version}", tx_issue_type='invalid-code')
    return {
        'name': version.name, 'version': version.version,
        'display': display_of(concept, text(parameters, 'displayLanguage')) or fhir_code(concept.mnemonic),
        'code': fhir_code(concept.mnemonic), 'system': system, 'abstract': False, 'inactive': bool(concept.retired),
        'designations': designations(concept),
    }
