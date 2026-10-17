"""
FHIR search and read of CodeSystem, ValueSet and ConceptMap: SVCM's Query transactions (ITI-95, ITI-96, ITI-100).

- What a base shows is core.fhir.resolve's rule: released versions only, never HEAD; at the global base /fhir/ the
  public ones of public repositories (HEAD), whoever asks; at an owner base the owner's, private ones to those who may
  view them. Search uses its queryset form (visible_versions), a read checks is_visible and has_release on the version
  it finds.
- Ids: at the global base every released version has an opaque, stable id, its database key; an owner base keeps the
  repository's mnemonic (so its versions share one), and a read there serves the latest release the requester is
  shown, or the `version` named.
- Search parameters: SVCM's, with :exact and :contains on the string ones (no modifier is starts-with; both it and
  :contains ignore case and accents, :exact neither).
  A comma separates alternatives, a repeated parameter must hold every time. Anything else is a 400 not-supported.
- Paging: _count (at most MAX_COUNT, which is also the default) and page; Bundle.total is the full count, and every
  entry has a fullUrl and a search mode. The search runs once, for the ids of every match; the page's rows are then
  read by id.
- A search's queries are bounded together by SEARCH_TIMEOUT_MS: one the database cancels makes the search too costly
  (422). A parameter takes at most MAX_ALTERNATIVES values and a search MAX_SEARCH_ALTERNATIVES (else a 400
  too-costly), and the canonical URLs each parameter names are looked up in one query. ConceptMap's source and target
  systems also read their candidate versions from the mappings index (core.fhir.mapping_index), in one request per
  parameter within the same time.
- A search entry is the resource's summary, marked SUBSETTED: no concept, compose or group, which only a read returns.
  A CodeSystem is `complete` (with every concept, retired ones flagged) up to CONCEPT_LIMIT concepts, else
  `not-present`.
- A version released before its repository had a canonical URL has the repository's (HEAD's), in its `url` and for
  the url parameter.
"""
import json
import math
import re
import time
from datetime import datetime, timedelta
from functools import reduce
from operator import or_

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import connection, transaction, OperationalError
from django.db.models import BooleanField, Case, F, Exists, Func, OuterRef, Q, Subquery, TextField, Value, When
from django.db.models.functions import Coalesce
from django.db.models.lookups import Exact, IContains, In, IStartsWith
from django.utils import timezone
from psycopg2 import errorcodes
from rest_framework.fields import CharField
from rest_framework.response import Response

from core.bundles.serializers import FHIRBundleSerializer
from core.collections.models import Expansion
from core.common.constants import HEAD
from core.common.db_functions import ImmutableUnaccent
from core.common.mixins import CustomPaginator
from core.common.utils import to_int
from core.concepts.models import Concept
from core.fhir import mapping_index, outcomes
from core.fhir.base import CONTROL_PARAMETERS, fhir_base_url
from core.fhir.resolve import (
    PUBLIC_ACCESS, REPO_TYPES, namespace_of, owner_access, visible_versions, viewable_rows, is_public, is_shown,
    repository_rows, with_repository_access)
from core.mappings.models import Mapping
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile

MAX_COUNT = 100  # the most entries a search page holds, and how many it holds when _count isn't given
SEARCH_TIMEOUT_MS = 5000  # the database time a search may take; past it the search is too costly
CONCEPT_LIMIT = 1000  # a CodeSystem is `complete` up to this many concepts, else `not-present` (design D9)
SUBSETTED = {'system': 'http://terminology.hl7.org/CodeSystem/v3-ObservationValue', 'code': 'SUBSETTED'}
PAGING_PARAMETERS = frozenset({'_count', 'page'})
OPAQUE_ID = re.compile(r'[1-9][0-9]{0,17}')  # a database key, as str() writes it
NOTHING = Q(pk__in=[])
EVERYTHING = Q(pk__isnull=False)  # unlike Q(), it holds as an alternative too (Q() | x is x)

# string parameters: the field each searches, and the lookup of each modifier (:exact is the field's own equality)
STRING_FIELDS = {'name': 'name', 'title': 'full_name', 'description': 'description', 'publisher': 'publisher'}
# the title a CodeSystem or ValueSet shows (TitleField): the full name, else the name when there is none (null, empty or
# only whitespace)
SHOWN_TITLE = Case(When(Q(full_name__isnull=True) | Q(full_name__regex=r'^\s*$'), then=F('name')),
                   default=F('full_name'), output_field=TextField())
STRING_LOOKUPS = {'': IStartsWith, 'exact': None, 'contains': IContains}
# the other parameters, by resource type: the Search method each one's values go to
COMMON_PARAMETERS = {
    '_id': 'by_id', '_lastUpdated': 'by_last_updated', 'url': 'by_url', 'version': 'by_version',
    'status': 'by_status', 'identifier': 'by_identifier'}
PARAMETERS = {
    'CodeSystem': {**COMMON_PARAMETERS, 'system': 'by_url', 'code': 'by_code', 'language': 'by_language'},
    'ValueSet': {**COMMON_PARAMETERS, 'reference': 'by_reference'},
    'ConceptMap': {
        **COMMON_PARAMETERS, 'source-system': 'by_source', 'source-uri': 'by_source', 'target-system': 'by_target',
        'target-uri': 'by_target'},
}
# A stored OCL accession identifier, as IdentifierSerializer.find_ocl_identifier finds one in the serialized list: a
# value that isn't empty, and a type coding that is ACSN of v2-0203. IdentifierTypeCodingSerializer shows a missing code
# as ACSN and a missing system as v2-0203 (a null one as nothing).
STORED_ACCESSION = (
    'lax $[*] ? (@.value.type() == "string" && @.value != "" && exists(@.type.coding[*] ? ('
    '(@.code == "ACSN" || !exists(@.code)) && (@.system == "http://hl7.org/fhir/v2/0203" || !exists(@.system)))))')
# token parameters, by the system their codes belong to: None for none defined (|v is then v), CODE_SYSTEM for the
# CodeSystem itself (code=<its url>|<code>). identifier is a token too, with its own rules (by_identifier).
CODE_SYSTEM = 'the code system searched'
TOKEN_SYSTEMS = {
    '_id': None, 'version': None, 'status': 'http://hl7.org/fhir/publication-status', 'language': 'urn:ietf:bcp:47',
    'code': CODE_SYSTEM}
# what system| (any code of the system) means for a parameter with a system of its own
ANY_CODE = {'status': EVERYTHING, 'language': Q(default_locale__isnull=False) & ~Q(default_locale='')}
STATUSES = {'active': Q(released=True, retired=False), 'retired': Q(retired=True)}  # search shows no draft
# the parameters whose values are canonical URLs, looked up before the search (Search.look_up), by what they're among
LOOKUPS = {
    'url': 'repositories', 'system': 'repositories', 'code': 'repositories', 'reference': 'referenced',
    'source-system': 'sources', 'source-uri': 'sources', 'target-system': 'sources', 'target-uri': 'sources'}
# the side of a mapping each ConceptMap system parameter reads, by its Search method
SIDES = {'by_source': 'from', 'by_target': 'to'}
MAX_ALTERNATIVES = 20  # the values a search parameter takes: its alternatives and repeats together
MAX_SEARCH_ALTERNATIVES = 50  # the values a search takes, every parameter's together

# a FHIR date or dateTime search value: prefix, year, month, day, hour, minute, second, fraction, zone
DATE = re.compile(
    r'(eq|ne|gt|lt|ge|le|sa|eb)?(\d{4})(?:-(\d{2})(?:-(\d{2})'
    r'(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?(Z|[+-]\d{2}:\d{2})?)?)?)?')


def split(value, separator):
    """A search value split on the separators that aren't escaped; escapes are kept for unescape()."""
    parts, start, index = [], 0, 0
    while index < len(value):
        if value[index] == '\\':
            index += 2
            continue
        if value[index] == separator:
            parts.append(value[start:index])
            start = index + 1
        index += 1
    return parts + [value[start:]]


def token(value):
    """(system, code) of a token search value, unescaped: system None without a '|', '' for an explicit none."""
    system, *code = split(value, '|')
    return (None, unescape(system)) if not code else (unescape(system), unescape('|'.join(code)))


def unescape(value):
    """FHIR's search escapes: a backslash makes the next character (, | $ or \\) literal."""
    return re.sub(r'\\(.)', r'\1', value)


def date_range(value):
    """
    (prefix, start, end): the instants [start, end) a date search value covers at its precision, UTC by default. end is
    None when it would fall past year 9999, which datetime can't hold: the range runs on unbounded.
    """
    match = DATE.fullmatch(value)
    if not match:
        raise outcomes.InvalidError(f"'{value}' is not a supported FHIR date search value.", expression='_lastUpdated')
    prefix, year, month, day, hour, minute, second, fraction, zone = match.groups()
    try:
        start = datetime(
            int(year), int(month or 1), int(day or 1), int(hour or 0), int(minute or 0), int(second or 0),
            int((fraction or '0')[:6].ljust(6, '0')), tzinfo=datetime.strptime(zone or 'Z', '%z').tzinfo)
    except ValueError as ex:
        raise outcomes.InvalidError(f"'{value}' is not a valid date.", expression='_lastUpdated') from ex
    unit = 'seconds' if second else 'minutes' if minute else 'days' if day else 'months' if month else 'years'
    try:
        end = start + (
            timedelta(microseconds=10 ** max(0, 6 - len(fraction))) if fraction else relativedelta(**{unit: 1}))
    except (ValueError, OverflowError):
        end = None
    return prefix or 'eq', start, end


def canonical_q(url, repositories):
    """
    Rows whose canonical URL is `url` as CanonicalUrlField shows it: their own, or for a row with none (a version
    released before its repository had one), their repository's (HEAD's). `repositories` are the (mnemonic,
    organization_id, user_id) of the repositories whose HEAD has it, looked up first (repositories_with), so the
    planner sees plain conditions rather than a correlated subquery it can't estimate.
    """
    if not repositories:
        return Q(canonical_url=url)
    return Q(canonical_url=url) | ((Q(canonical_url__isnull=True) | Q(canonical_url='')) & reduce(or_, (
        Q(mnemonic=mnemonic, organization_id=organization_id, user_id=user_id)
        for mnemonic, organization_id, user_id in repositories)))


def repositories_with(rows, urls):
    """
    For each of `urls`, the (mnemonic, organization_id, user_id) of the repositories among `rows` whose HEAD has that
    canonical URL: one query for them all.
    """
    found = {url: [] for url in urls}
    for url, *repository in rows.filter(version=HEAD, canonical_url__in=urls).values_list(
            'canonical_url', 'mnemonic', 'organization_id', 'user_id'):
        found[url].append(tuple(repository))
    return found


def sources_with(rows, urls, versions):
    """
    ({url: [(id, (owner type, owner, mnemonic))]}, {(owner type, owner, mnemonic): {version names}}): for each of
    `urls` the Source rows among `rows` with that canonical URL, and the repositories and versions of the rows
    `versions` lists, in one query for them all.
    """
    fields = ('canonical_url', 'id', 'mnemonic', 'organization__mnemonic', 'user__username', 'version')
    named = rows.filter(canonical_url__in=urls).annotate(listed=Value(False)).values_list(*fields, 'listed')
    found, shown = {url: [] for url in urls}, {}
    for url, source_id, mnemonic, org, username, version, listed in named.union(
            versions.annotate(listed=Value(True)).values_list(*fields, 'listed'), all=True):
        repository = ('Organization', org, mnemonic) if org else ('User', username, mnemonic)
        if listed:
            shown.setdefault(repository, set()).add(version)
        else:
            found[url].append((source_id, repository))
    return found, shown


def with_repository_canonical(queryset):
    """Each row annotated with its repository's (HEAD's) canonical URL, for a version that has none of its own."""
    org_heads, user_heads = repository_rows(queryset.model, version=HEAD)
    return queryset.annotate(repository_canonical_url=Coalesce(
        Subquery(org_heads.values('canonical_url')[:1]), Subquery(user_heads.values('canonical_url')[:1])))


def code_system_concepts(source):
    """
    (count, concepts) for a CodeSystem read, cached on the version: its concepts, retired ones included, when it has
    at most CONCEPT_LIMIT, else (its number of concepts, None). The number is counted on the version's concept links
    alone (an index-only count, cheap even for LOINC); within the limit it's the concepts listed.
    """
    if not hasattr(source, 'fhir_concepts'):
        count, concepts = Concept.sources.through.objects.filter(source_id=source.id).count(), None
        if count <= CONCEPT_LIMIT:
            concepts = list(source.concepts.filter(is_active=True).select_related('parent').prefetch_related(
                'names', 'descriptions').order_by('id'))
            count = len(concepts)
        source.fhir_concepts = count, concepts
    return source.fhir_concepts


def concepts_over_limit(source):
    """Whether a CodeSystem has more than CONCEPT_LIMIT concepts: a search annotates it, a read counts them."""
    over = getattr(source, 'concepts_over_limit', None)
    return code_system_concepts(source)[1] is None if over is None else over


def with_concepts_over_limit(queryset):
    """Each Source row annotated with concepts_over_limit, reading at most CONCEPT_LIMIT + 1 of its concept links."""
    links = Concept.sources.through.objects.filter(source_id=OuterRef('id'))
    return queryset.annotate(concepts_over_limit=Exists(links[CONCEPT_LIMIT:CONCEPT_LIMIT + 1]))


def defines_concept(**filters):
    """
    A CodeSystem version with a concept (matching `filters`), as its read lists them: linked to the version and not
    deleted. A retired concept counts; the read shows it flagged inactive.
    """
    return Exists(Concept.sources.through.objects.filter(source_id=OuterRef('id'), concept__is_active=True, **filters))


class JsonPathExists(Func):  # pylint: disable=abstract-method
    """jsonb_path_exists(field, path, vars, silent): whether the path finds anything, an error being no match."""
    function = 'jsonb_path_exists'
    output_field = BooleanField()

    def __init__(self, field, path, variables):
        super().__init__(field, Value(path), Value(json.dumps(variables)), Value(True))


class JsonbTypeof(Func):  # pylint: disable=abstract-method
    function = 'jsonb_typeof'
    output_field = TextField()


def held_identifier(system, value):
    """
    Rows holding an identifier shown as system|value, both checked on the same identifier: `system` None is any
    system, '' none; `value` None is any value. IdentifierSerializer shows an identifier stored without a system key
    under API_BASE_URL, and one with a null or empty system without one. Only a list of identifiers is shown; lax mode
    reads a missing key as nothing (strict mode would make exists() unknown).
    """
    conditions, variables = [], {}
    if value is not None:
        conditions.append('@.value == $value')
        variables['value'] = value
    if system == '':
        conditions.append('exists(@.system) && (@.system == null || @.system == "")')
    elif system is not None:
        no_key = ' || !exists(@.system)' if system == settings.API_BASE_URL else ''
        conditions.append(f'(@.system == $system{no_key})')
        variables['system'] = system
    return Exact(JsonbTypeof('identifier'), 'array') & JsonPathExists(
        'identifier', f'lax $[*] ? ({" && ".join(conditions)})', variables)


def indexed_repository(owner_type, owner, mnemonic):
    """
    The rows of a repository as the mappings index names it (owner and mnemonic in lowercase), twins included. The
    owner is an uncorrelated IN, so the version rows need no join.
    """
    if owner_type == 'Organization':
        return Q(mnemonic__iexact=mnemonic, organization_id__in=Organization.objects.filter(
            mnemonic__iexact=owner).values('id'))
    return Q(mnemonic__iexact=mnemonic, user_id__in=UserProfile.objects.filter(username__iexact=owner).values('id'))


def mapped(side, value, sources, holding, namespace, user):  # pylint: disable=too-many-arguments
    """
    A ConceptMap version holding a live mapping whose group source (side 'from') or target ('to') shows `value`, as
    ConceptMapGroupField builds groups: retired and deleted mappings aren't read; a group's system is the from or to
    source's canonical URL when the caller is shown that source, else the URL the mapping stores (an OCL URL as a
    ConceptMap path, /orgs/{org}/ConceptMap/{id}/). OCL keeps no source or target value set on a ConceptMap, so
    source-uri and target-uri are read as the systems.

    `sources` are the ids of the sources with that canonical the base shows the caller (viewable_rows), listed first
    (sources_with), and `holding` the repositories and versions the mappings index lists as holding mappings from or
    to them (mapping_index.holding). Only those versions, and their repositories' releases created within
    mapping_index.LAG_WINDOW (which the index may not list yet), are candidates: the database decides each by its own
    mappings, so a heavily mapped source costs no more than another. A mapping whose source isn't one the caller is
    shown by a canonical URL is matched by the URL it stores: a source the caller isn't shown answers exactly as one
    OCL doesn't hold. Alone, that is one EXISTS, which the planner runs as a semi-join; beside the candidates it's an
    uncorrelated IN, which it runs once.
    """
    field = f'mapping__{side}_source'
    unnamed = ~Exists(viewable_rows(Source, namespace, user).filter(id=OuterRef(f'{field}_id'), canonical_url__gt=''))
    stored = Q(**{f'{field}_url': value}) & unnamed
    path = re.fullmatch(r'/(orgs|users)/([^/]+)/ConceptMap/([^/]+)/', value)
    if path:
        stored |= reduce(or_, (Q(**{f'{field}_url__startswith': f'/{path[1]}/{path[2]}/{kind}/{path[3]}/'})
                               for kind in ('sources', 'collections', 'ConceptMap'))) & unnamed
    links = Mapping.sources.through.objects.filter(mapping__retired=False, mapping__is_active=True)
    if not holding:
        return Exists(links.filter(stored, source_id=OuterRef('id')))
    recent = Q(created_at__gte=timezone.now() - mapping_index.LAG_WINDOW)
    candidates = reduce(or_, (indexed_repository(*repository) & (Q(version__in=sorted(versions)) | recent)
                              for repository, versions in sorted(holding.items())))
    # each candidate is read through its own links, each link's mapping a scalar subquery the planner can't turn into
    # a join: it can't start from all the source's mappings instead, which say nothing of where the version's are
    live_side = Mapping.objects.filter(id=OuterRef('mapping_id'), retired=False, is_active=True).values(
        f'{side}_source_id')[:1]
    named = Exists(Mapping.sources.through.objects.filter(In(Subquery(live_side), sources), source_id=OuterRef('id')))
    return Q(id__in=links.filter(stored).values('source_id')) | (candidates & named)


class Search:
    """The criteria of one search's parameters, on the versions of a resource type at a base, for a caller."""

    def __init__(self, resource_type, kwargs, user=None):
        self.resource_type = resource_type
        self.model = REPO_TYPES[resource_type]
        self.parameters = PARAMETERS[resource_type]
        self.namespace, self.user = namespace_of(kwargs), user
        self.access = owner_access(kwargs, user)
        self.found = {}  # (lookup, canonical URL): what look_up found for it
        self.deadline = time.monotonic() + SEARCH_TIMEOUT_MS / 1000  # for what isn't a query: the mappings index

    def viewable_sources(self):
        """
        The Source rows the base shows the caller, whoever owns them: what source-system, target-system and reference
        match. At an owner base that's another owner's private source too, if the caller may view it themselves.
        """
        return viewable_rows(Source, self.namespace, self.user)

    def criteria(self, query):
        """
        One Q for a query string: every parameter holds (a repeated one each time), any of a value's comma-separated
        alternatives does. An empty value isn't a criterion; the format and paging parameters aren't criteria.
        """
        criteria = Q()
        for name, modifier, repeats in self.parameters_of(query):
            self.look_up(name, [item for alternatives in repeats for item in alternatives])
            for alternatives in repeats:
                if alternatives:
                    criteria &= reduce(or_, [self.criterion(name, modifier, item) for item in alternatives])
        return criteria

    def parameters_of(self, query):
        """
        (name, modifier, each repeat's alternatives) for every search parameter of a query string, each supported and
        within MAX_ALTERNATIVES values (alternatives and repeats together) and the search within
        MAX_SEARCH_ALTERNATIVES, else a 400 too-costly naming the parameter. Checked before any query runs.
        """
        parameters, given, total = [], {}, 0
        for key, values in query.lists():
            if key in CONTROL_PARAMETERS or key in PAGING_PARAMETERS:
                continue
            name, _, modifier = key.partition(':')
            self.check(key, name, modifier)
            repeats = [[item for item in split(value, ',') if item] for value in values]
            count = sum(map(len, repeats))
            given[name], total = given.get(name, 0) + count, total + count
            if given[name] > MAX_ALTERNATIVES or total > MAX_SEARCH_ALTERNATIVES:
                raise outcomes.TooCostlyError(
                    f'A search takes at most {MAX_ALTERNATIVES} values of a parameter, its alternatives and repeats '
                    f'together, and {MAX_SEARCH_ALTERNATIVES} in all.', expression=key, status_code=400)
            parameters.append((name, modifier, repeats))
        return parameters

    def check(self, key, name, modifier):
        modifiers = STRING_LOOKUPS if name in STRING_FIELDS else ('',) if name in self.parameters else ()
        if modifier not in modifiers:
            raise outcomes.NotSupportedError(f"The search parameter '{key}' is not supported.", expression=key)

    def look_up(self, name, alternatives):
        """
        The canonical URLs a parameter's alternatives (still escaped) name, looked up together in one query, for its
        criteria: the repositories with them (url, system, code's system), or among the sources the base shows the
        caller, the repositories (reference) or the sources (source-system, source-uri, target-system, target-uri).
        The same query lists the ConceptMap versions the base shows the caller (visible_versions). For the sources
        found, one request to the mappings index lists the repositories and versions holding mappings from (source-) or
        to (target-) them, among those versions; none is sent for a URL no source the caller is shown has, or where the
        base shows no ConceptMap.
        """
        lookup = LOOKUPS.get(name)
        urls = {token(item)[0] if name == 'code' else unescape(item) for item in alternatives} - {None, ''}
        if not lookup or not urls:
            return
        if lookup == 'repositories':
            found = repositories_with(self.model.objects, urls)
        elif lookup == 'referenced':
            found = repositories_with(self.viewable_sources(), urls)
        else:
            found, shown = sources_with(
                self.viewable_sources(), urls, visible_versions(self.resource_type, access=self.access))
            side = SIDES[self.parameters[name]]
            holding = mapping_index.holding(
                side, {url: {repository for _, repository in rows} for url, rows in found.items()}, shown,
                self.deadline, search_too_costly)
            self.found.update({(side, url): holding.get(url, {}) for url in found})
            found = {url: [source_id for source_id, _ in rows] for url, rows in found.items()}
        self.found.update({(lookup, url): value for url, value in found.items()})

    def criterion(self, name, modifier, value):
        """The Q for one alternative (still escaped) of a parameter's value."""
        if name == 'title' and self.resource_type != 'ConceptMap':
            return self.by_string(SHOWN_TITLE, modifier, unescape(value))
        if name in STRING_FIELDS:
            return self.by_string(STRING_FIELDS[name], modifier, unescape(value))
        if name == 'identifier':
            return self.by_identifier(value)
        if name in TOKEN_SYSTEMS:
            return self.by_token(name, *token(value))
        return getattr(self, self.parameters[name])(unescape(value))

    def by_token(self, name, system, code):
        """
        A token parameter: code (any system), system|code, system| (any code of that system) or |code (explicitly no
        system), against the system its codes belong to (TOKEN_SYSTEMS). A parameter with no system takes |code as
        code; any other system is no match.
        """
        handler, implicit = getattr(self, self.parameters[name]), TOKEN_SYSTEMS[name]
        if system is None or (system == '' and implicit is None):
            return handler(code)
        if not system or implicit is None:
            return NOTHING
        if implicit == CODE_SYSTEM:  # system| is any code: a version of that code system with a concept
            return self.by_url(system) & (handler(code) if code else defines_concept())
        if system != implicit:
            return NOTHING
        return handler(code) if code else ANY_CODE[name]

    @staticmethod
    def by_string(field, modifier, value):
        """
        FHIR string search: :exact is the whole value, case and accents included; no modifier (starts with) and
        :contains ignore case and accents, unaccenting both sides in the database (LIKE's wildcards are escaped).
        """
        lookup = STRING_LOOKUPS[modifier]
        if lookup is None:
            return Q(Exact(field, Value(value))) if isinstance(field, Case) else Q(**{field: value})
        return lookup(ImmutableUnaccent(field), ImmutableUnaccent(Value(value)))

    def by_id(self, value):
        """The opaque id at the global base, the mnemonic at an owner base."""
        if self.namespace is None:
            return Q(id=int(value)) if OPAQUE_ID.fullmatch(value) else NOTHING
        return Q(mnemonic=value)

    @staticmethod
    def by_last_updated(value):
        prefix, start, end = date_range(value)
        before_end = Q(updated_at__lt=end) if end else EVERYTHING
        after_end = Q(updated_at__gte=end) if end else NOTHING
        return {
            'eq': Q(updated_at__gte=start) & before_end,
            'ne': Q(updated_at__lt=start) | after_end,
            'gt': after_end, 'sa': after_end,
            'ge': Q(updated_at__gte=start),
            'lt': Q(updated_at__lt=start), 'eb': Q(updated_at__lt=start),
            'le': before_end,
        }[prefix]

    def by_url(self, value):
        return canonical_q(value, self.found[('repositories', value)])

    @staticmethod
    def by_version(value):
        return Q(version=value)

    @staticmethod
    def by_status(value):
        return STATUSES.get(value, NOTHING)

    def by_identifier(self, value):
        """
        A token, matched on one identifier as it's shown: value (any system), system|value, |value (no system) or
        system| (any value). Besides the identifiers a repository holds, every resource without a stored accession
        identifier shows a generated one: system API_BASE_URL, value /{orgs|users}/{owner}/{type}/{id}/.
        """
        system, value = token(value)
        value = value or None
        if system == '' and value is None:
            return NOTHING
        criterion = held_identifier(system, value)
        if system not in (None, settings.API_BASE_URL):
            return criterion
        generated = Q(identifier__isnull=True) | ~Q(
            Exact(JsonbTypeof('identifier'), 'array') & JsonPathExists('identifier', STORED_ACCESSION, {}))
        if value is None:
            return criterion | generated
        accession = re.fullmatch(r'/(orgs|users)/([^/]+)/([^/]+)/([^/]+)/?', value)
        if accession and accession.group(3) == self.resource_type:
            owner = 'organization__mnemonic' if accession.group(1) == 'orgs' else 'user__username'
            criterion |= Q(Q(**{owner: accession.group(2), 'mnemonic': accession.group(4)}) & generated)
        return criterion

    @staticmethod
    def by_code(value):
        """A CodeSystem that defines the code."""
        return defines_concept(concept__mnemonic=value)

    @staticmethod
    def by_language(value):
        return Q(default_locale=value)

    def by_reference(self, value):
        """
        A ValueSet whose stored default expansion draws on a version of the code system (canonical URL) named, among
        those the base shows the caller. An uncorrelated IN, which the planner hashes once, rather than a correlated
        subquery.
        """
        sources = self.viewable_sources().filter(canonical_q(value, self.found[('referenced', value)])).values('id')
        return reduce(or_, (
            Q(expansion_uri__in=versions.through.objects.filter(source_id__in=sources).values('expansion__uri'))
            for versions in (Expansion.explicit_source_versions, Expansion.evaluated_source_versions)))

    def by_source(self, value):
        """A group's source is the system named."""
        return mapped('from', value, self.found[('sources', value)], self.found[('from', value)], self.namespace,
                      self.user)

    def by_target(self, value):
        """A group's target is the system named."""
        return mapped('to', value, self.found[('sources', value)], self.found[('to', value)], self.namespace,
                      self.user)


def page_size(query):
    value = query.get('_count')
    if value is None:
        return MAX_COUNT
    if not re.fullmatch(r'[0-9]{1,9}', value.strip()):
        raise outcomes.InvalidError(f"_count must be a whole number, got '{value}'.", expression='_count')
    return min(int(value), MAX_COUNT)


def within_search_timeout(build, too_costly=None):
    """
    build() within SEARCH_TIMEOUT_MS in all: each query it runs may take only the time the search has left, so a
    search of several queries is bounded as a whole. A query the database cancels, or one with no time left, makes the
    search too costly (422): too_costly() says so, the search's own wording by default (an operation passes its own).
    Inside a transaction (tests) the timeout would outlive the search, so it isn't set there.
    """
    too_costly = too_costly or search_too_costly
    if connection.vendor != 'postgresql' or connection.in_atomic_block:
        return build()
    deadline = time.monotonic() + SEARCH_TIMEOUT_MS / 1000

    def within_time_left(execute, sql, params, many, context):
        left = math.ceil((deadline - time.monotonic()) * 1000)
        if left <= 0:
            raise too_costly()
        with context['connection'].connection.cursor() as cursor:  # the driver's own cursor, past this wrapper
            cursor.execute('SET LOCAL statement_timeout = %s', [left])
        return execute(sql, params, many, context)

    try:
        with transaction.atomic(), connection.execute_wrapper(within_time_left):
            return build()
    except OperationalError as ex:
        if getattr(ex.__cause__, 'pgcode', None) != errorcodes.QUERY_CANCELED:
            raise
        raise too_costly() from ex


def search_too_costly():
    return outcomes.TooCostlyError(
        'The search would take too long to answer. Narrow it, for example with url, _id or version.')


def search_bundle(view):
    """The searchset Bundle a FHIR search view answers: the base's matching versions, one page of them."""
    request, resource_type = view.request, view.resource_type
    count = page_size(request.query_params)
    search = Search(resource_type, view.kwargs, request.user)
    ids = list(visible_versions(resource_type, access=search.access).filter(
        search.criteria(request.query_params)).order_by('-id').values_list('id', flat=True))
    paginator, entries = None, []
    if count:
        page = to_int(request.GET.get('page', '1'), 1)
        rows = with_repository_canonical(REPO_TYPES[resource_type].objects.filter(
            id__in=ids[(page - 1) * count:page * count])).order_by('-id')
        if resource_type == 'CodeSystem':
            rows = with_concepts_over_limit(rows)
        paginator = CustomPaginator(
            request=request, total_count=len(ids), queryset=list(rows), page_size=count, is_sliced=True)
        resources = view.get_serializer(paginator.current_page_results, many=True).data
        entries = FHIRBundleSerializer.convert_to_entry(
            resources, f'{fhir_base_url(request, view.kwargs)}/{resource_type}')
    bundle = {'meta': {'lastUpdated': timezone.now()}, 'type': 'searchset', 'total': len(ids)}
    if entries:
        bundle['entry'] = entries
    return FHIRBundleSerializer(bundle, context={'paginator': paginator, 'request': request}).data


def read(view):
    """
    The repository version a FHIR read view names: at the global base by its opaque id, at an owner base by its
    mnemonic, the version ?version= names (an OCL extension) or else the latest release the requester is shown. It
    must be one search lists: released, never HEAD, visible to the requester there. Raises NotFoundError.
    """
    return read_version(view.resource_type, view.kwargs[view.lookup_field], view.request.query_params.get('version'),
                        view.kwargs, view.request.user)


def read_version(resource_type, resource_id, version, kwargs, user):
    """
    read's rule for a resource `resource_id` names at the base `kwargs` names, for `user`: the id at the global base,
    the mnemonic and `version` (else the latest release shown) at an owner base. For the instance-level operations too.
    """
    namespace = namespace_of(kwargs)
    versions = REPO_TYPES[resource_type].objects.filter(is_active=True, released=True).exclude(version=HEAD)
    latest = False
    if namespace is None:
        versions = versions.filter(id=int(resource_id)) if OPAQUE_ID.fullmatch(resource_id) else versions.none()
    else:
        owner = {'user__username': kwargs['user']} if kwargs.get('user') else {'organization__mnemonic': kwargs['org']}
        versions = versions.filter(mnemonic=resource_id, **owner)
        latest = not version
        versions = versions.filter(version=version) if version else versions.filter(
            retired=False).order_by('-created_at')

    instance = with_repository_access(with_repository_canonical(versions)).first()
    if latest and instance and not is_shown(instance, namespace, user) and is_public(
            instance.repository_public_access or ''):
        # the latest is private, in a public repository: the latest public one
        instance = with_repository_access(with_repository_canonical(
            versions.filter(public_access__in=PUBLIC_ACCESS))).first()
    if not instance or not is_shown(instance, namespace, user):
        raise outcomes.NotFoundError(f'{resource_type}/{resource_id} not found')
    return instance


class FhirRepositoryViewMixin:
    """The search and read views of CodeSystem, ValueSet and ConceptMap (after FhirViewMixin)."""
    resource_type = None

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'opaque_ids': namespace_of(self.kwargs) is None}


class FhirSearchMixin(FhirRepositoryViewMixin):
    """GET on a resource type is its search."""

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        """The searchset Bundle, in at most SEARCH_TIMEOUT_MS of database time."""
        return Response(within_search_timeout(lambda: search_bundle(self)))

    head = get


class FhirReadMixin(FhirRepositoryViewMixin):
    """
    GET on an id is its read, at the global base without the owner check the view's writes keep (verify_scope), and
    without the source and collection views' recounts.
    """

    def verify_scope(self):
        if self.request.method not in ('GET', 'HEAD'):
            super().verify_scope()

    def get_object(self, queryset=None):
        if self.request.method not in ('GET', 'HEAD'):
            return super().get_object(queryset)
        return read(self)


class CanonicalUrlField(CharField):
    """A resource's canonical URL: the version's own, else its repository's (repository_canonical_url, on a read)."""

    def get_attribute(self, instance):
        return super().get_attribute(instance) or getattr(instance, 'repository_canonical_url', None)


class TitleField(CharField):
    """
    A repository's FHIR title: its full name, or its name when it has no full name (empty or only whitespace). Writes
    still go to full_name.
    """

    def get_attribute(self, instance):
        full_name = instance.full_name
        return full_name if full_name and full_name.strip() else instance.name


class FhirRepositorySerializerMixin:
    """
    For the CodeSystem, ValueSet and ConceptMap serializers: the opaque id at the global base (context opaque_ids), and
    in a search (many=True, context has_many) the summary, marked SUBSETTED, without `content_field`.
    """
    content_field = None

    @classmethod
    def many_init(cls, *args, **kwargs):
        kwargs['context'] = {**kwargs.get('context', {}), 'has_many': True}
        return super().many_init(*args, **kwargs)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.context.get('has_many'):
            self.fields.pop(self.content_field, None)

    def with_fhir_view(self, instance, rep):
        """
        The id (opaque at the global base), and meta.versionId: the version's opaque id at both bases, so the
        releases of one repository, which share their mnemonic id at an owner base, stay distinct in a Bundle (bdl-7).
        A search entry is also tagged SUBSETTED. meta keeps what the serializer put there (lastUpdated).
        """
        if instance.id:
            if self.context.get('opaque_ids'):
                rep['id'] = str(instance.id)
            rep['meta'] = {'versionId': str(instance.id), **(rep.get('meta') or {})}
        if self.context.get('has_many'):
            rep['meta'] = {**(rep.get('meta') or {}), 'tag': [SUBSETTED]}
        return rep
