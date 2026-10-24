"""
ConceptMap $translate (SVCM ITI-101, FHIR R4's $translate), version-neutral: what a translation finds, with OCL map
types as R5-shaped relationships. core.fhir.adapters.r4 writes the answer as an R4 Parameters resource.

- The ConceptMap: the one `url` (and `conceptMapVersion`) names; else the code system the code comes from, `system`
  resolved as a CodeSystem (in reverse, `targetsystem`, the system of the codes sought), whose own mappings are its
  map, at the release `conceptMapVersion` names, else (forward) the code's `version`. Resolution, versions and access
  are core.fhir.resolve's: a released version, the newest the caller is shown unless one is named; public only at
  /fhir/; at an owner base, private ones to those who may view them.
- The matches: that version's live (neither retired nor deleted) mappings from the code (in reverse, to it) whose from
  side (in reverse, to side) is shown as the code's system; with `targetsystem`, whose other side is shown as that
  system. Never another repository's mappings, HEAD's or another release's.
- A side of a mapping is shown as its source's canonical URL, when the caller is shown that source and it has one; else
  as the URL the mapping stores (its source's OCL URL when it stores none), an OCL URL as a ConceptMap path
  (/orgs/{org}/ConceptMap/{id}/). So a source the caller isn't shown is named as a source OCL doesn't hold would be,
  and a system only such sources have answers as an unknown one. ConceptMapGroupField names groups the same way.
  A system given as an OCL path of a source (/orgs/{org}/CodeSystem/{id}/, or the sources or ConceptMap form) names
  that repository if the caller is shown it, whether it has a canonical URL or not; the answer names it as above. A
  ValueSet or collections path names a value set, not a source.
- Map types (design D27): case, spaces, hyphens, underscores and surrounding whitespace don't matter, so SAME-AS,
  SAME AS and Same As are one. The relationships are core.concept_maps.constants'; result is true when some match isn't
  not-related-to. A match's relationship reads from the code named to the match, so in reverse, where the code named
  is the mapping's target, narrower and broader swap. With no match, the answer says why (message).
- The code's scope, `source` (the value set the code was chosen from; in reverse, which reverses the meaning of
  source and target, `target`), is a precondition: a code outside it has no translation, and one that resolves to no
  value set (or code system) the caller is shown is not found. The other scope narrows nothing: OCL's ConceptMaps
  declare no scope, so it selects no map (as FHIRsmith reads OCL's maps); `targetsystem` narrows.
- The code's `version` (or its coding's) keeps the mappings of that version of its system, and those of none
  (from_source_version; in reverse, to_source_version).
  `conceptMap` (an inline map) and `dependency` aren't supported.
- Cost: a fixed number of queries whatever the number of mappings (resolution, the sources the systems name, the ids
  of the release's mappings of the code, then those mappings, with whether the caller is shown their sources); at
  most FHIR_TRANSLATE_MATCH_LIMIT matches. Its statements are bounded as a search's are (too long is too costly, a 422).
"""
import re
from collections import namedtuple
from functools import cached_property, reduce
from operator import or_

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Case, Exists, F, OuterRef, Q, When

from core.collections.models import Expansion
from core.concepts.models import Concept
from core.common.fhir_helpers import repository_prefixes
from core.common.serializers import IdentifierSerializer
from core.concept_maps.constants import (
    MAP_TYPE_RELATIONSHIPS, NOT_RELATED_TO, RELATED_TO, RESOURCE_TYPE, REVERSED_RELATIONSHIPS)
from core.fhir import outcomes
from core.fhir.constants import TRANSLATION_TOO_COSTLY
from core.fhir.ops.concepts import fhir_code, stored_codes
from core.fhir.parameters import Coding
from core.fhir.resolve import UnresolvedError
from core.fhir.search import within_search_timeout
from core.mappings.models import Mapping
from core.sources.models import Source

UNSUPPORTED_PARAMETERS = ('conceptMap', 'dependency')
IMPLICIT_VALUE_SET = '?fhir_vs'  # {code system}?fhir_vs: every code of the code system

Request = namedtuple('Request', 'coding url concept_map_version scope scope_name targetsystem reverse')


def map_type_key(map_type):
    """A map type as MAP_TYPE_RELATIONSHIPS spells it: upper case, a run of spaces, hyphens and underscores a hyphen."""
    return re.sub(r'[\s_-]+', '-', (map_type or '').strip().upper()).strip('-')


def relationship(map_type, reverse=False):
    """
    The relationship an OCL map type is (design D27): MAP_TYPE_RELATIONSHIPS', else related-to. reverse reads it from
    the mapping's target to its source, as a reverse translation answers it, relative to the code named (its target).
    """
    found = MAP_TYPE_RELATIONSHIPS.get(map_type_key(map_type), RELATED_TO)
    return REVERSED_RELATIONSHIPS.get(found, found) if reverse else found


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


def named_sources(systems, resolver):
    """
    For each system, the ids of the Source rows the base shows the caller that it names, in one query: those with it as
    their canonical URL; for an OCL path, the repository's rows, whether it has a canonical URL or not (the path is an
    alias: side_system still shows one that has as its canonical URL). A literal list, so the planner reads the mappings
    by their own indexes.
    """
    paths = {system: (repository_prefixes(system, ('sources',)) or [None])[0] for system in systems}
    rows = resolver.viewable_rows(Source).filter(reduce(or_, (
        Q(uri__startswith=path) if path else Q(canonical_url=system) for system, path in paths.items())))
    named = {system: [] for system in systems}
    for source_id, canonical, uri in rows.values_list('id', 'canonical_url', 'uri'):
        for system, path in paths.items():
            if uri.startswith(path) if path else canonical == system:
                named[system].append(source_id)
    return named


def shown_as(side, system, source_ids, resolver):
    """
    Q for mappings whose `side` ('from' or 'to') `system` names: their source is one of `source_ids` (named_sources');
    or it isn't one the caller is shown by a canonical URL, and the URL they store names the system, as side_system
    then shows it. The subquery runs only on the mappings whose URL does.
    """
    source, url = f'{side}_source', f'{side}_source_url'
    unnamed = ~Exists(resolver.viewable_rows(Source).filter(
        id=OuterRef(f'{source}_id'), canonical_url__gt=''))
    paths = repository_prefixes(system, ('sources', 'collections', 'ConceptMap'))
    stored = reduce(or_, (Q(**{f'{url}__startswith': path}) for path in paths)) if paths else Q(**{url: system})
    return Q(**{f'{source}_id__in': source_ids}) | (stored & unnamed)


def scope_system(scope):
    """The code system a scope names, if it is one: the scope itself, or the code system of an implicit value set."""
    return scope.removesuffix(IMPLICIT_VALUE_SET) if scope else scope


def holds(value_set, code, sources):
    """
    Whether a value set's default expansion holds the code as an active concept of one of the `sources`: from the
    code's concepts, each looked up in the expansion, rather than from the expansion's members.
    """
    return bool(value_set.expansion_uri and sources) and Expansion.concepts.through.objects.filter(
        expansion__uri=value_set.expansion_uri, concept_id__in=Concept.objects.filter(
            mnemonic__in=stored_codes(code), parent_id__in=sources, is_active=True).values('id')).exists()


def of_version(side, version):
    """Q for mappings whose `side` is that version of its system, or names no version; None names no constraint."""
    if not version:
        return Q()
    field = f'{side}_source_version'
    return Q(**{field: version}) | Q(**{f'{field}__isnull': True}) | Q(**{field: ''})


def with_side(queryset, side, resolver):
    """
    Each mapping annotated with whether the base shows the caller its `side`'s source ({side}_shown), and the canonical
    URL that side is then shown by ({side}_canonical, None for none): subqueries run only on the mappings listed.
    """
    queryset = queryset.annotate(**{f'{side}_shown': Exists(resolver.viewable_rows(Source).filter(
        id=OuterRef(f'{side}_source_id')))})
    return queryset.annotate(**{f'{side}_canonical': Case(When(
        Q(**{f'{side}_shown': True, f'{side}_source__canonical_url__gt': ''}),
        then=F(f'{side}_source__canonical_url')), default=None)})


def given_coding(parameters, name):
    """A Coding the code comes in (coding, or a codeableConcept's one coding); without a system and a code, a 400."""
    coding = parameters.coding(name) if name == 'coding' else parameters.codeable_concept_coding(name)
    if not coding.system or not coding.code:
        raise outcomes.InvalidError(f"'{name}' must be a Coding with a system and a code.", expression=name)
    return coding


def read_request(parameters):
    """The request in FhirParameters: the code (code and system, coding, or a codeableConcept's coding), etc."""
    for name in UNSUPPORTED_PARAMETERS:
        if name in parameters:
            raise outcomes.NotSupportedError(f"$translate doesn't support '{name}'.", expression=name)
    if 'coding' in parameters or 'codeableConcept' in parameters:
        coding = given_coding(parameters, 'coding' if 'coding' in parameters else 'codeableConcept')
    elif 'code' in parameters:
        code = parameters.required_text('code', "$translate needs 'code' with the code it translates.")
        system, _, piped = parameters.required_text(
            'system', "$translate needs 'system' with the code it translates.").partition('|')
        coding = Coding(system, code, parameters.text('version') or piped or None, None, None)
    else:
        raise outcomes.InvalidError('$translate needs the code it translates: code and system, coding, or '
                                    'codeableConcept.', expression='code')
    reverse = bool(parameters.boolean('reverse'))
    scope_name = 'target' if reverse else 'source'  # R4: reverse reverses the meaning of source and target
    return Request(coding, parameters.text('url'), parameters.text('conceptMapVersion'), parameters.text(scope_name),
                   scope_name, parameters.text('targetsystem'), reverse)


class Translation:
    """One $translate request for the resolver's base and caller."""

    def __init__(self, parameters, resolver):
        self.request = read_request(parameters)
        self.resolver = resolver

    def answer(self):
        """
        {'result', 'message', 'matches'}, each match {'relationship', 'concept': {system, code}, 'source'}. In
        FHIRsmith's order: the ConceptMap url names, then the code's scope, then the ConceptMap of the code's system.
        """
        request, coding = self.request, self.request.coding
        concept_map, named = self.named_concept_map() if request.url else (None, None)
        if request.scope and not self.in_scope():
            return self.no_match(f"'{coding.system}#{coding.code}' is not in the value set '{request.scope}', so no "
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
        message = f'Only the first {settings.FHIR_TRANSLATE_MATCH_LIMIT} matches are listed.' if more else None
        return {'result': any(match['relationship'] != NOT_RELATED_TO for match in matches), 'message': message,
                'matches': matches}

    @staticmethod
    def no_match(message):
        return {'result': False, 'message': message, 'matches': []}

    def no_concept_map(self):
        names = [self.request.coding.system, self.request.targetsystem or '(unspecified)']
        source, target = names[::-1] if self.request.reverse else names
        return f"No ConceptMap is available to translate from '{source}' to '{target}'"

    @cached_property
    def named_sources(self):
        """named_sources of the code's system, targetsystem and the code system its scope names, looked up once."""
        request, system = self.request, self.request.coding.system
        # in this order, whatever their URLs, so the statement's shape is too
        systems = dict.fromkeys([system, request.targetsystem or system, scope_system(request.scope) or system])
        return named_sources(systems, self.resolver)

    def resolve(self, resource_type, url, version, expression):
        return self.resolver.resolve(resource_type, url, version, expression)

    def named_concept_map(self):
        """(the ConceptMap version url and conceptMapVersion name, how a match's source names it); else a 404."""
        instance = self.resolve('ConceptMap', self.request.url, self.request.concept_map_version, 'url')
        return instance, self.map_name(instance, self.request.url)

    def code_system_map(self):
        """
        Without url: (the version of the code system the codes come from, how a match's source names it), whose own
        mappings are its map: the release conceptMapVersion names, else (forward) the code's version, else the newest
        the caller is shown; (None, None) when there is none.
        """
        request = self.request
        if request.reverse:
            url, version = request.targetsystem, request.concept_map_version
        else:  # conceptMapVersion names the map's release, else the code's version
            url, version = request.coding.system, request.concept_map_version or request.coding.version
        try:  # resolve() raises before any query for no url (reverse without targetsystem)
            instance = self.resolve('CodeSystem', url, version, 'system')
        except UnresolvedError:
            return None, None
        return instance, self.map_name(instance, url)

    @staticmethod
    def map_name(instance, url):
        """A ConceptMap version as a match's source names it: canonical URL (else the URL it was named by)|version."""
        return f"{instance.canonical_url or url.partition('|')[0]}|{instance.version}"

    def in_scope(self):
        """
        Whether the code is in its scope (`source`; in reverse, `target`): its own code system, or that system's
        implicit value set, named any way named_sources reads (its canonical URL or a source's OCL path, of a repository
        the caller is shown); a value set (a ValueSet or collections path is one) it is a member of (its default
        expansion), as a concept of a source the caller is shown as the code's system, as the matches are; not another
        code system. Neither one the caller is shown, nor ?fhir_vs of no system, is a 404 naming the parameter, as
        FHIRsmith answers a scope it can't find.
        """
        scope, name, coding = self.request.scope, self.request.scope_name, self.request.coding
        system = scope_system(scope)
        if not system:  # ?fhir_vs of no system
            raise self.scope_not_found(scope, name)
        if system == coding.system:
            return True
        named = self.named_sources
        if set(named[coding.system]) & set(named[system]):  # one repository the caller is shown, whatever names it
            return True
        if system == scope:  # not an implicit value set: a ValueSet, if one has that URL
            try:
                value_set = self.resolve('ValueSet', scope, None, name)
            except UnresolvedError:
                pass
            else:
                return holds(value_set, coding.code, named[coding.system])
        try:
            self.resolve('CodeSystem', system, None, name)
        except UnresolvedError as ex:
            raise self.scope_not_found(scope, name) from ex
        return False

    @staticmethod
    def scope_not_found(scope, name):
        return outcomes.NotFoundError(
            f"The value set '{scope}' could not be found, so the scope of the translation cannot be determined",
            expression=name, tx_issue_type='not-found')

    def match_ids(self, concept_map, near, far):
        """
        The ids of the release's live mappings of the code whose sides are shown as the systems, sorted: in no order and
        with no limit, so the planner reads the code's mappings by the code (and the source lists) and checks each
        against the release; ordered and limited, it would walk the release in id order.
        """
        request = self.request
        sides = {near: request.coding.system, **({far: request.targetsystem} if request.targetsystem else {})}
        mappings = concept_map.get_active_mappings().filter(  # neither retired nor deleted
            of_version(near, request.coding.version), **{f'{near}_concept_code__in': stored_codes(request.coding.code)})
        for side, system in sides.items():
            mappings = mappings.filter(shown_as(side, system, self.named_sources[system], self.resolver))
        return sorted(mappings.values_list('id', flat=True))

    def matches(self, concept_map, named):
        """
        (the matches, whether there were more than FHIR_TRANSLATE_MATCH_LIMIT), in mapping order, each match once: the
        sources the systems name, the mappings' ids, then the first limit + 1 of those mappings.
        """
        limit = settings.FHIR_TRANSLATE_MATCH_LIMIT
        near, far = ('to', 'from') if self.request.reverse else ('from', 'to')
        ids = self.match_ids(concept_map, near, far)
        rows = with_side(Mapping.objects.filter(id__in=ids[:limit + 1]), far, self.resolver).order_by(
            'id').values('map_type', f'{far}_concept_code', f'{far}_source_url', f'{far}_source__uri', f'{far}_shown',
                         f'{far}_canonical')
        matches = {}
        for row in rows:
            system = side_system(row[f'{far}_canonical'], row[f'{far}_source_url'],
                                 row[f'{far}_source__uri'] if row[f'{far}_shown'] else None)
            match = (system, fhir_code(row[f'{far}_concept_code']), relationship(row['map_type'], self.request.reverse))
            matches.setdefault(match, {
                'relationship': match[2], 'concept': {'system': match[0], 'code': match[1]}, 'source': named})
        return list(matches.values())[:limit], len(ids) > limit


def translate(parameters, resolver, _instance_id=None):
    """Translate for the resolver's caller within the search timeout; there is no instance level."""
    return within_search_timeout(lambda: Translation(parameters, resolver).answer(), TRANSLATION_TOO_COSTLY)
