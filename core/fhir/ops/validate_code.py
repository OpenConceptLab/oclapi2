"""
CodeSystem and ValueSet $validate-code (SVCM ITI-99, FHIR R4's $validate-code), version-neutral: whether a code is
valid, with what OCL knows about it. core.fhir.adapters.r4 writes the answer as R4 Parameters.

- Resolution and access are core.fhir.resolve's (a released version, the newest the caller is shown unless one is
  named; public only at /fhir/; at an owner base, private ones to those who may view them); CodeSystem/{id} and
  ValueSet/{id} name theirs as a read does. A code system, value set or content the caller isn't shown answers exactly
  as one that doesn't exist.
- CodeSystem: the code in the version `url` (and `version`) names: result, and the code, system, version used and
  display; `display` must be one of the concept's names (ignoring case); a retired concept is valid but flagged
  inactive, with a warning. The code system is the instance, else url (and version), else a coding's system; a
  coding of another system is result false (invalid-data). A code system that doesn't resolve answers result false
  with a not-found issue and x-caused-by-unknown-system, as FHIRsmith does, with or without a code. Then a code is
  required (400), and a url.
- ValueSet: membership is the resolved version's default expansion: the code, from a concept whose code system is
  `system` (and `systemVersion`), in a code system version the caller is shown, released or not. The version
  answered is a release the caller is shown that holds the concept: the expansion's explicit version, else the one
  it evaluated, else the newest; none without one (never HEAD or an unreleased version). Not a member: result false
  (not-in-vs), with what the code system itself says of the code (unknown system, unknown code, or its display). A
  value set that doesn't resolve is a 422 not-found OperationOutcome. Without url (and not at the instance level),
  the code is checked in its code system alone, as OCL has always answered.
- Issues carry FHIRsmith's message ids (operationoutcome-message-id) and expressions, so clients and the parity
  suite compare them by kind; `message` joins their texts.
- Cost: a fixed number of queries whatever the size of the code system or value set.
"""
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Value, When
from rest_framework import status

from core.collections.models import Expansion
from core.common.constants import HEAD
from core.concepts.models import Concept
from core.fhir import outcomes
from core.fhir.ops.concepts import (
    coded_input, path, require, text, find_concept, display_of, has_name, fhir_code, code_q, is_system_of, mismatch,
    system_of)
from core.fhir.ops.lookup import code_system_version
from core.fhir.resolve import UnresolvedError, namespace_of, resolve, to_ocl_path, viewable_rows
from core.fhir.search import read_version
from core.sources.models import Source

UNKNOWN_CODE_SYSTEM = 'x-caused-by-unknown-system'  # how CodeSystem $validate-code names an unknown system
UNKNOWN_VALUE_SET_SYSTEM = 'x-unknown-system'  # and ValueSet $validate-code


def issue(coding, part, kind, message_id, message):  # pylint: disable=too-many-arguments
    """An issue about one part of the code (code, system or display), of a kind in KINDS."""
    severity, code, tx_issue_type = KINDS[kind]
    return outcomes.issue(code, message, severity=severity, expression=path(coding, part),
                          tx_issue_type=tx_issue_type, message_id=message_id)


# kind: (severity, issue type, tx-issue-type), as FHIRsmith reports each
KINDS = {
    'unknown-system': ('error', 'not-found', 'not-found'),
    'unknown-code': ('error', 'code-invalid', 'invalid-code'),
    'wrong-display': ('error', 'invalid', 'invalid-display'),
    'inactive': ('warning', 'business-rule', 'code-comment'),
    'not-in-value-set': ('error', 'code-invalid', 'not-in-vs'),
    'other-system': ('error', 'invalid', 'invalid-data'),
}


def answer(coding, issues=(), **found):
    """
    The neutral answer: result (no error issue), the code and system asked about, what was found (version, display,
    inactive, the unknown system), the issues and the message their texts make.
    """
    issues = list(issues)
    return {
        'result': not any(item['severity'] in ('error', 'fatal') for item in issues),
        'message': '; '.join(item['details']['text'] for item in issues) or None,
        'code': fhir_code(coding.code), 'system': coding.system, **found, 'issues': issues,
    }


def unknown_system(coding, parameter):
    """
    (the issue, the unknown system parameter) for a code system that doesn't resolve, or that the caller isn't shown:
    the same for both, as they depend only on what was asked (the system, and the version named or url|version).
    """
    system, _, piped = coding.system.partition('|')
    version = coding.version or piped or None
    if version:
        message_id = 'UNKNOWN_CODESYSTEM_VERSION'
        message = f"A definition for CodeSystem '{system}' version '{version}' could not be found, " \
                  "so the code cannot be validated"
    else:
        message_id = 'UNKNOWN_CODESYSTEM'
        message = f"A definition for CodeSystem '{system}' could not be found, so the code cannot be validated"
    named = f'{system}|{version}' if version else system
    return issue(coding, 'system', 'unknown-system', message_id, message), (parameter, named)


def unknown_code(coding, system, version):
    return issue(coding, 'code', 'unknown-code', 'Unknown_Code_in_Version',
                 f"Unknown code '{fhir_code(coding.code)}' in the CodeSystem '{system}' version '{version}'")


def checked(coding, concept, display):
    """The issues of a code its code system holds: a display that isn't one of its names, and a retired concept."""
    issues = []
    if coding.display and not has_name(concept, coding.display):
        issues.append(issue(
            coding, 'display', 'wrong-display', 'Display_Name_for__should_be_one_of__instead_of',
            f"Wrong Display Name '{coding.display}' for {coding.system}#{fhir_code(coding.code)}: it should be one of "
            f"the concept's names, such as '{display}'"))
    if concept.retired:
        issues.append(issue(coding, 'code', 'inactive', 'INACTIVE_CONCEPT_FOUND',
                            f"The concept '{fhir_code(coding.code)}' has a status of inactive and its use should be "
                            "reviewed"))
    return issues


def in_code_system(coding, version, language):
    """The answer for a code in a code system version it resolved to."""
    system = system_of(version, coding.system)
    coding = coding._replace(system=system)
    concept = find_concept(version, coding.code)
    if not concept:
        return answer(coding, [unknown_code(coding, system, version.version)], version=version.version)
    display = display_of(concept, language)
    return answer(coding, checked(coding, concept, display), version=version.version, display=display,
                  inactive=concept.retired or None)


def validate_code_system(parameters, kwargs, user, instance_id=None):
    """
    CodeSystem $validate-code's answer for its FhirParameters at the base `kwargs` names (CodeSystem/{instance_id} at
    the instance level), for `user`. A url that doesn't resolve answers before the code is required, as it always has.
    """
    coding = coded_input(parameters, 'url', 'version')
    target = coding  # the code system validated against: the instance, else url (and version), else the coding's
    if coding.parameter:
        target = coding._replace(system=text(parameters, 'url') or coding.system,
                                 version=text(parameters, 'version') or coding.version)
    version = None
    if instance_id or target.system:
        try:
            version = code_system_version(target, kwargs, user, instance_id)
        except UnresolvedError:
            problem, unknown = unknown_system(target, UNKNOWN_CODE_SYSTEM)
            return answer(target, [problem], version=target.version, unknown=unknown)
    require(coding, 'code', 'code', 'A code to validate is required.')
    if version is None:
        raise outcomes.InvalidError('url is required with a code: the code system to validate it in.',
                                    expression=coding.parameter or 'url')
    conflict = mismatch(parameters, coding, version, 'url', instance_id)
    if conflict:
        severity, code, tx_issue_type = KINDS['other-system']
        return answer(coding, [outcomes.issue(
            code, f'{conflict[1]}: the code is not validated', severity=severity, expression=conflict[0],
            tx_issue_type=tx_issue_type)])
    return in_code_system(coding, version, text(parameters, 'displayLanguage'))


def system_q(field, system):
    """Q for concepts (through `field`) whose repository is the system: by canonical URL, or by an OCL path."""
    reference = to_ocl_path(system)
    if reference.startswith('/'):
        return Q(**{f'{field}__uri': reference if reference.endswith('/') else f'{reference}/'})
    return Q(**{f'{field}__canonical_url': system})


def member(value_set, coding, namespace, user):
    """
    (the concept, the version of its code system) of the code in the value set version's default expansion, or None:
    a concept with the code (not deleted), of the system (in systemVersion, if named), in a code system version the
    caller is shown (not deleted), released or not: the expansion defines the value set. The version answered is a
    release the caller is shown that holds the concept: the one the expansion pins (explicit), else the one it
    evaluated, else the newest; None without one, never HEAD or an unreleased version.
    The code's concepts are read first, by their code, then looked for in the expansion by its (expansion, concept)
    index, so the cost doesn't grow with the size of the value set.
    """
    if not value_set.expansion_uri:
        return None
    concept_ids = list(Concept.objects.filter(
        code_q('mnemonic', coding.code), system_q('parent', coding.system), is_active=True).values_list(
        'id', flat=True))
    if concept_ids:
        concept_ids = list(Expansion.concepts.through.objects.filter(
            expansion__uri=value_set.expansion_uri, concept_id__in=concept_ids).values_list('concept_id', flat=True))
    if not concept_ids:
        return None
    rows = Concept.sources.through.objects.filter(
        Exists(viewable_rows(Source, namespace, user).filter(id=OuterRef('source_id'))), concept_id__in=concept_ids,
        source__is_active=True)
    if coding.version:
        rows = rows.filter(source__version=coding.version)
    def pinned(versions):
        return Exists(versions.through.objects.filter(
            expansion__uri=value_set.expansion_uri, source_id=OuterRef('source_id')))
    release = Q(source__released=True, source__retired=False) & ~Q(source__version=HEAD)
    row = rows.annotate(
        release=Case(When(release, then=Value(1)), default=Value(0), output_field=IntegerField()),
        explicit=pinned(Expansion.explicit_source_versions), evaluated=pinned(Expansion.evaluated_source_versions),
    ).order_by('-release', '-explicit', '-evaluated', '-source__created_at', 'concept_id').values_list(
        'concept_id', 'source__version', 'release').first()
    if not row:
        return None
    concept = Concept.objects.filter(id=row[0]).select_related('parent').prefetch_related('names').first()
    return concept, row[1] if row[2] else None


def value_set_version(parameters, kwargs, user, instance_id):
    """
    The value set version the request names; a url that doesn't resolve is a 422 not-found. At the instance level the id
    names it, and a url or valueSetVersion naming another one is a 400.
    """
    version, url = text(parameters, 'valueSetVersion'), text(parameters, 'url')
    if instance_id:
        value_set = read_version('ValueSet', instance_id, version, kwargs, user)
        if url and not is_system_of(url, value_set):
            raise outcomes.InvalidError(f"'{url}' is not the value set {instance_id} this $validate-code is on.",
                                        expression='url')
        for named, expression in ((version, 'valueSetVersion'), ((url or '').partition('|')[2], 'url')):
            if named and named != value_set.version:
                raise outcomes.InvalidError(
                    f"Version '{named}' is not the version '{value_set.version}' of the value set {instance_id}.",
                    expression=expression)
        return value_set
    try:
        return resolve('ValueSet', url, version, kwargs, user)
    except UnresolvedError as ex:
        raise outcomes.NotFoundError(
            f"A definition for the value Set '{url}' could not be found", tx_issue_type='not-found',
            message_id='Unable_to_resolve_value_Set_', status_code=status.HTTP_422_UNPROCESSABLE_ENTITY) from ex


def validate_value_set(parameters, kwargs, user, instance_id=None):
    """
    ValueSet $validate-code's answer for its FhirParameters at the base `kwargs` names (ValueSet/{instance_id} at the
    instance level), for `user`. A code and its system are required first.
    """
    coding = coded_input(parameters, 'system', 'systemVersion')
    require(coding, 'code', 'code', 'A code to validate is required.')
    require(coding, 'system', 'system', 'system is required with a code: the code system the code is from.')
    language = text(parameters, 'displayLanguage')
    if not instance_id and not text(parameters, 'url'):
        try:
            return in_code_system(coding, resolve('CodeSystem', coding.system, coding.version, kwargs, user), language)
        except UnresolvedError:
            problem, unknown = unknown_system(coding, UNKNOWN_CODE_SYSTEM)
            return answer(coding, [problem], version=coding.version, unknown=unknown)

    value_set = value_set_version(parameters, kwargs, user, instance_id)
    found = member(value_set, coding, namespace_of(kwargs), user)
    if found:
        concept, version = found
        display = display_of(concept, language)
        return answer(coding, checked(coding, concept, display), version=version, display=display,
                      inactive=concept.retired or None)
    return not_a_member(coding, value_set, kwargs, user, language)


def not_a_member(coding, value_set, kwargs, user, language):  # pylint: disable=too-many-arguments
    """A code outside the value set: not-in-vs, with what its code system says of it."""
    name = f"{system_of(value_set, value_set.uri)}|{value_set.version}"
    outside = issue(coding, 'code', 'not-in-value-set', 'None_of_the_provided_codes_are_in_the_value_set_one',
                    f"The provided code '{coding.system}#{fhir_code(coding.code)}' was not found in the value set "
                    f"'{name}'")
    try:
        version = resolve('CodeSystem', coding.system, coding.version, kwargs, user)
    except UnresolvedError:
        problem, unknown = unknown_system(coding, UNKNOWN_VALUE_SET_SYSTEM)
        return answer(coding, [problem, outside], unknown=unknown)
    system = system_of(version, coding.system)
    concept = find_concept(version, coding.code)
    if not concept:
        return answer(coding, [unknown_code(coding, system, version.version), outside])
    return answer(coding, [outside], version=version.version, display=display_of(concept, language))
