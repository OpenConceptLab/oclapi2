"""
ConceptMap $translate (SVCM ITI-101, FHIR R4's $translate), version-neutral: what a translation finds, with OCL map
types as R5-shaped relationships. core.fhir.adapters.r4 writes the answer as an R4 Parameters resource.

- The ConceptMap: the one `url` (and `conceptMapVersion`) names; else the code system the code comes from, `system`
  and `version` resolved as a CodeSystem, whose own mappings are its map (in reverse, `targetsystem`, the system of
  the codes sought). Resolution, versions and access are core.fhir.resolve's: a released version, the newest the caller
  is shown unless one is named; public only at /fhir/; at an owner base, private ones to those who may view them.
- The matches: that version's live (not retired) mappings from the code (in reverse, to it) whose from side (in
  reverse, to side) is shown as the code's system; with `targetsystem`, whose other side is shown as that system. Never
  another repository's mappings, HEAD's or another release's.
- A side of a mapping is shown as its source's canonical URL, when the caller is shown that source and it has one; else
  as the URL the mapping stores (its source's OCL URL when it stores none), an OCL URL as a ConceptMap path
  (/orgs/{org}/ConceptMap/{id}/). So a source the caller isn't shown is named as a source OCL doesn't hold would be,
  and a system only such sources have answers as an unknown one. ConceptMapGroupField names groups the same way.
- Map types (design D27): case, spaces, hyphens, underscores and surrounding whitespace don't matter, so SAME-AS,
  SAME AS and Same As are one. The relationships are core.concept_maps.constants'; result is true when some match isn't
  not-related-to. With no match, the answer says why (message).
- `source`, the value set the code was chosen from, is a precondition: a code outside it has no translation, and one
  that resolves to no value set (or code system) the caller is shown is not found. `target` narrows nothing: OCL's
  ConceptMaps declare no target scope, so it selects no map (as FHIRsmith reads OCL's maps); `targetsystem` narrows.
  `conceptMap` (an inline map) and `dependency` aren't supported.
- Cost: a fixed number of queries whatever the number of mappings (resolution, then the mappings, with whether the
  caller is shown their sources); at most MATCH_LIMIT matches.
"""
import re
from collections import namedtuple
from functools import reduce
from operator import or_
from urllib.parse import quote, unquote

from django.core.exceptions import ValidationError
from django.db.models import Case, Exists, F, OuterRef, Q, When

from core.collections.models import Expansion
from core.common.serializers import IdentifierSerializer
from core.concept_maps.constants import MAP_TYPE_RELATIONSHIPS, NOT_RELATED_TO, RELATED_TO, RESOURCE_TYPE
from core.fhir import outcomes
from core.fhir.resolve import UnresolvedError, namespace_of, resolve, viewable_rows
from core.sources.models import Source

MATCH_LIMIT = 1000  # the most matches an answer lists
UNSUPPORTED_PARAMETERS = ('conceptMap', 'dependency')
OCL_PATH = re.compile(r'/(orgs|users)/([^/]+)/(?:sources|collections|CodeSystem|ConceptMap|ValueSet)/([^/]+)/?')
SIDES = ('from', 'to')

Coding = namedtuple('Coding', 'system code version')
Request = namedtuple('Request', 'coding url concept_map_version source targetsystem reverse')


def map_type_key(map_type):
    """A map type as MAP_TYPE_RELATIONSHIPS spells it: upper case, a run of spaces, hyphens and underscores a hyphen."""
    return re.sub(r'[\s_-]+', '-', (map_type or '').strip().upper()).strip('-')


def relationship(map_type):
    """The relationship an OCL map type is (design D27): MAP_TYPE_RELATIONSHIPS', else related-to."""
    return MAP_TYPE_RELATIONSHIPS.get(map_type_key(map_type), RELATED_TO)


def side_system(canonical, url, uri=None):
    """
    How a side of a mapping is shown (as a ConceptMap group's source or target, or a translation's system): `canonical`,
    its source's canonical URL when the caller is shown one that has it; else `url`, the URL the mapping stores, or
    `uri`, its source's OCL URL when it stores none (pass it only for a source the caller is shown); an OCL URL as a
    ConceptMap path.
    """
    if canonical:
        return canonical
    url = url or uri
    try:
        return IdentifierSerializer.convert_ocl_uri_to_fhir_url(url, RESOURCE_TYPE)
    except ValidationError:  # an OCL URL too short to name a repository
        return url


def fhir_code(code):
    """A code as FHIR carries it: OCL stores one with reserved characters percent-encoded (ICD-11's ND53.Y&XK70)."""
    return unquote(code) if code and '%' in code else code


def stored_codes(code):
    """The forms a code may be stored in: as it is, and percent-encoded as OCL encodes reserved characters."""
    return list(dict.fromkeys([code, quote(code, safe=''), quote(code)]))


def shown_as(side, system):
    """
    Q for mappings whose `side` ('from' or 'to') is shown as `system` (side_system), on a queryset annotated by
    with_sides. An OCL path names the repositories without a canonical URL shown by it.
    """
    path = OCL_PATH.fullmatch(system)
    if not path:
        return Q(**{f'{side}_canonical': system}) | Q(
            **{f'{side}_canonical__isnull': True, f'{side}_source_url': system})
    owner, mnemonic = f'/{path[1]}/{path[2]}/', path[3]
    stored = reduce(or_, (Q(**{f'{side}_source_url__startswith': f'{owner}{kind}/{mnemonic}/'})
                          for kind in ('sources', 'collections', 'ConceptMap')))
    own = Q(**{f'{side}_source_url__isnull': True, f'{side}_shown': True,
               f'{side}_source__uri__startswith': f'{owner}sources/{mnemonic}/'})
    return Q(**{f'{side}_canonical__isnull': True}) & (stored | own)


def with_sides(queryset, namespace, user):
    """
    Each mapping annotated, for each side, with whether the base shows the caller its source ({side}_shown) and the
    canonical URL it is then shown by ({side}_canonical, None for none), from correlated subqueries the planner runs
    only on the mappings found.
    """
    shown = viewable_rows(Source, namespace, user)
    annotations = {}
    for side in SIDES:
        annotations[f'{side}_shown'] = Exists(shown.filter(id=OuterRef(f'{side}_source_id')))
    queryset = queryset.annotate(**annotations)
    return queryset.annotate(**{f'{side}_canonical': Case(When(
        Q(**{f'{side}_shown': True, f'{side}_source__canonical_url__gt': ''}),
        then=F(f'{side}_source__canonical_url')), default=None) for side in SIDES})


def text(parameters, name, required=False):
    """A parameter that is text (a string, uri or code), or None; anything else is a 400 naming it."""
    value = parameters.get(name)
    if value is None or value == '':
        if required:
            raise outcomes.InvalidError(f"$translate needs '{name}' with the code it translates.", expression=name)
        return None
    if not isinstance(value, str):
        raise outcomes.InvalidError(f"'{name}' must be text.", expression=name)
    return value


def boolean(parameters, name):
    value = parameters.get(name)
    if value in (None, ''):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    raise outcomes.InvalidError(f"'{name}' must be true or false.", expression=name)


def coding_of(value, name):
    """A Coding parameter (in a GET, a system|code token), as a Coding; one without a system and a code is a 400."""
    if isinstance(value, str):
        system, _, code = value.partition('|')
        value = {'system': system, 'code': code}
    if not isinstance(value, dict) or not all(isinstance(value.get(key), str) and value[key]
                                              for key in ('system', 'code')):
        raise outcomes.InvalidError(f"'{name}' must be a Coding with a system and a code.", expression=name)
    return Coding(value['system'], value['code'], value.get('version'))


def read_request(parameters):
    """The request in FhirParameters: the code (code and system, coding, or a codeableConcept's first coding), etc."""
    for name in UNSUPPORTED_PARAMETERS:
        if name in parameters:
            raise outcomes.NotSupportedError(f"$translate doesn't support '{name}'.", expression=name)
    if 'coding' in parameters:
        coding = coding_of(parameters.get('coding'), 'coding')
    elif 'codeableConcept' in parameters:
        concept = parameters.get('codeableConcept')
        codings = (concept.get('coding') if isinstance(concept, dict) else None) or []
        if not isinstance(codings, list) or not codings:
            raise outcomes.InvalidError("'codeableConcept' must hold a coding.", expression='codeableConcept')
        coding = coding_of(codings[0], 'codeableConcept')
    elif 'code' in parameters:
        code = text(parameters, 'code', required=True)
        system, _, piped = text(parameters, 'system', required=True).partition('|')
        coding = Coding(system, code, text(parameters, 'version') or piped or None)
    else:
        raise outcomes.InvalidError('$translate needs the code it translates: code and system, coding, or '
                                    'codeableConcept.', expression='code')
    return Request(coding, text(parameters, 'url'), text(parameters, 'conceptMapVersion'), text(parameters, 'source'),
                   text(parameters, 'targetsystem'), boolean(parameters, 'reverse'))


class Translation:
    """One $translate request at the base `kwargs` names, for `user`."""

    def __init__(self, parameters, kwargs, user):
        self.request = read_request(parameters)
        self.kwargs, self.user, self.namespace = kwargs, user, namespace_of(kwargs)

    def answer(self):
        """
        {'result', 'message', 'matches'}, each match {'relationship', 'concept': {system, code}, 'source'}. In
        FHIRsmith's order: the ConceptMap url names, then the source scope, then the ConceptMap of the code's system.
        """
        request, coding = self.request, self.request.coding
        concept_map, named = self.named_concept_map() if request.url else (None, None)
        if request.source and not self.in_source_scope():
            return self.no_match(f"'{coding.system}#{coding.code}' is not in the value set '{request.source}', so no "
                                 "ConceptMap can translate it")
        if not request.url:
            concept_map, named = self.code_system_map()
        if concept_map is None:
            return self.no_match(self.no_concept_map())
        matches, more = self.matches(concept_map, named)
        if not matches:
            into = f" {'from' if request.reverse else 'to'} '{request.targetsystem}'" if request.targetsystem else ''
            return self.no_match(f"ConceptMap '{named}' has no mapping {'to' if request.reverse else 'from'} "
                                 f"'{coding.system}#{coding.code}'{into}")
        message = f'Only the first {MATCH_LIMIT} matches are listed.' if more else None
        return {'result': any(match['relationship'] != NOT_RELATED_TO for match in matches), 'message': message,
                'matches': matches}

    @staticmethod
    def no_match(message):
        return {'result': False, 'message': message, 'matches': []}

    def no_concept_map(self):
        request = self.request
        if request.reverse:
            names = request.targetsystem or '(unspecified)', request.coding.system
        else:
            names = request.coding.system, request.targetsystem or '(unspecified)'
        return f"No ConceptMap is available to translate from '{names[0]}' to '{names[1]}'"

    def resolve(self, resource_type, url, version, expression):
        return resolve(resource_type, url, version, self.kwargs, self.user, expression)

    def named_concept_map(self):
        """(the ConceptMap version url and conceptMapVersion name, how a match's source names it); else a 404."""
        instance = self.resolve('ConceptMap', self.request.url, self.request.concept_map_version, 'url')
        return instance, self.map_name(instance, self.request.url)

    def code_system_map(self):
        """
        Without url: (the version of the code system the codes come from, how a match's source names it), whose own
        mappings are its map; (None, None) when there is none the caller is shown.
        """
        request = self.request
        if request.reverse:
            url, version = request.targetsystem, None
        else:
            url, version = request.coding.system, request.coding.version
        if not url:
            return None, None
        try:
            instance = self.resolve('CodeSystem', url, version, 'system')
        except UnresolvedError:
            return None, None
        return instance, self.map_name(instance, url)

    @staticmethod
    def map_name(instance, url):
        """A ConceptMap version as a match's source names it: canonical URL (else the URL it was named by)|version."""
        return f"{instance.canonical_url or url.partition('|')[0]}|{instance.version}"

    def in_source_scope(self):
        """
        Whether the code is in the `source` scope: its own code system (or that system's implicit value set); a value
        set it is a member of (its default expansion); not another code system. Neither one the caller is shown is a
        404, as FHIRsmith answers a scope it can't find.
        """
        source, coding = self.request.source, self.request.coding
        if source in (coding.system, f'{coding.system}?fhir_vs'):
            return True
        try:
            value_set = self.resolve('ValueSet', source, None, 'source')
        except UnresolvedError:
            try:
                self.resolve('CodeSystem', source, None, 'source')
            except UnresolvedError as ex:
                raise outcomes.NotFoundError(
                    f"The value set '{source}' could not be found, so the scope of the translation cannot be "
                    "determined", expression='source', tx_issue_type='not-found') from ex
            return False
        return bool(value_set.expansion_uri) and Expansion.concepts.through.objects.filter(
            expansion__uri=value_set.expansion_uri, concept__mnemonic__in=stored_codes(coding.code),
            concept__parent__canonical_url=coding.system).exists()

    def matches(self, concept_map, named):
        """(the matches, whether there were more than MATCH_LIMIT): one query, in mapping order, each match once."""
        request = self.request
        near, far = ('to', 'from') if request.reverse else ('from', 'to')
        codes = stored_codes(request.coding.code)
        mappings = with_sides(concept_map.get_mappings_queryset().filter(
            retired=False, **{f'{near}_concept_code__in': codes}), self.namespace, self.user)
        mappings = mappings.filter(shown_as(near, request.coding.system))
        if request.targetsystem:
            mappings = mappings.filter(shown_as(far, request.targetsystem))
        rows = mappings.order_by('id').values(
            'map_type', f'{far}_concept_code', f'{far}_source_url', f'{far}_source__uri', f'{far}_shown',
            f'{far}_canonical')[:MATCH_LIMIT + 1]
        matches = {}
        for row in rows:
            system = side_system(row[f'{far}_canonical'], row[f'{far}_source_url'],
                                 row[f'{far}_source__uri'] if row[f'{far}_shown'] else None)
            match = (system, fhir_code(row[f'{far}_concept_code']), relationship(row['map_type']))
            matches.setdefault(match, {
                'relationship': match[2], 'concept': {'system': match[0], 'code': match[1]}, 'source': named})
        return list(matches.values())[:MATCH_LIMIT], len(rows) > MATCH_LIMIT


def translate(parameters, kwargs, user):
    """A $translate's answer (Translation.answer) for its FhirParameters, at the base `kwargs` names, for `user`."""
    return Translation(parameters, kwargs, user).answer()
