"""
FHIR search and read of CodeSystem, ValueSet and ConceptMap: SVCM's Query transactions (ITI-95, ITI-96, ITI-100).

- What a base shows is core.fhir.resolve's rule: released versions only, never HEAD; at the global base /fhir/ the
  public ones, whoever asks; at an owner base the owner's, private ones to those who may view them. Search uses its
  queryset form (visible_versions), a read checks is_visible and has_release on the version it finds.
- Ids: at the global base every released version has an opaque, stable id, its database key; an owner base keeps the
  repository's mnemonic (so its versions share one), and a read there serves the latest release or the `version` named.
- Search parameters: SVCM's, with :exact and :contains on the string ones (no modifier is starts-with, ignoring case).
  A comma separates alternatives, a repeated parameter must hold every time. Anything else is a 400 not-supported.
- Paging: _count (at most MAX_COUNT, which is also the default) and page; Bundle.total is the full count, and every
  entry has a fullUrl and a search mode.
- A search's queries are bounded by SEARCH_TIMEOUT_MS: one the database cancels makes the search too costly (422).
- A search entry is the resource's summary, marked SUBSETTED: no concept, compose or group, which only a read returns.
  A CodeSystem is `complete` (with every concept, retired ones flagged) up to CONCEPT_LIMIT concepts, else
  `not-present`.
- A version released before its repository had a canonical URL has the repository's (HEAD's), in its `url` and for
  the url parameter.
"""
import re
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import connection, transaction, OperationalError
from django.db.models import Exists, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from psycopg2 import errorcodes
from rest_framework.fields import CharField
from rest_framework.response import Response

from core.bundles.serializers import FHIRBundleSerializer
from core.collections.models import Expansion
from core.common.constants import HEAD
from core.common.mixins import CustomPaginator
from core.concepts.models import Concept
from core.fhir import outcomes
from core.fhir.base import CONTROL_PARAMETERS, fhir_base_url
from core.fhir.resolve import REPO_TYPES, namespace_of, visible_versions, is_visible, has_release, repository_rows
from core.mappings.models import Mapping
from core.sources.models import Source

MAX_COUNT = 100  # the most entries a search page holds, and how many it holds when _count isn't given
SEARCH_TIMEOUT_MS = 5000  # the database time a search may take; past it the search is too costly
CONCEPT_LIMIT = 1000  # a CodeSystem is `complete` up to this many concepts, else `not-present` (design D9)
SUBSETTED = {'system': 'http://terminology.hl7.org/CodeSystem/v3-ObservationValue', 'code': 'SUBSETTED'}
PAGING_PARAMETERS = frozenset({'_count', 'page'})
OPAQUE_ID = re.compile(r'[1-9][0-9]{0,17}')  # a database key, as str() writes it
NOTHING = Q(pk__in=[])

# string parameters: the field each searches, and the lookup of each modifier
STRING_FIELDS = {'name': 'name', 'title': 'full_name', 'description': 'description', 'publisher': 'publisher'}
STRING_LOOKUPS = {'': 'istartswith', 'exact': 'exact', 'contains': 'icontains'}
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
STATUSES = {
    'active': Q(released=True, retired=False), 'retired': Q(retired=True), 'draft': Q(released=False, retired=False)}

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


def unescape(value):
    """FHIR's search escapes: a backslash makes the next character (, | $ or \\) literal."""
    return re.sub(r'\\(.)', r'\1', value)


def zone_of(zone):
    """A date search value's zone (Z or ±hh:mm), UTC when it has none."""
    if not zone or zone == 'Z':
        return dt_timezone.utc
    offset = timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6]))
    return dt_timezone(offset if zone[0] == '+' else -offset)


def date_range(value):
    """(prefix, start, end): the instants [start, end) a date search value covers at its precision, UTC by default."""
    match = DATE.fullmatch(value)
    if not match:
        raise outcomes.InvalidError(f"'{value}' is not a supported FHIR date search value.", expression='_lastUpdated')
    prefix, *parts, fraction, zone = match.groups()
    year, month, day, hour, minute, second = parts
    try:
        start = datetime(
            int(year), int(month or 1), int(day or 1), int(hour or 0), int(minute or 0), int(second or 0),
            int((fraction or '0')[:6].ljust(6, '0')), tzinfo=zone_of(zone))
    except ValueError as ex:
        raise outcomes.InvalidError(f"'{value}' is not a valid date.", expression='_lastUpdated') from ex
    if fraction:
        end = start + timedelta(microseconds=10 ** max(0, 6 - len(fraction)))
    elif second or minute or day:
        end = start + (timedelta(seconds=1) if second else timedelta(minutes=1) if minute else timedelta(days=1))
    elif month:
        end = start.replace(year=start.year + start.month // 12, month=start.month % 12 + 1)
    else:
        end = start.replace(year=start.year + 1)
    return prefix or 'eq', start, end


def canonical_q(model, url):
    """
    Rows whose canonical URL is `url`, their own or their repository's (HEAD's): a version released before its
    repository had one has none of its own. The repositories are looked up first (one query), so the planner sees
    plain conditions rather than a correlated subquery it can't estimate.
    """
    criterion = Q(canonical_url=url)
    for mnemonic, organization_id, user_id in model.objects.filter(version=HEAD, canonical_url=url).values_list(
            'mnemonic', 'organization_id', 'user_id'):
        criterion |= Q(mnemonic=mnemonic, organization_id=organization_id, user_id=user_id)
    return criterion


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


class Search:
    """The criteria of one search's parameters, on the versions of a resource type at a base."""

    def __init__(self, resource_type, kwargs):
        self.resource_type = resource_type
        self.model = REPO_TYPES[resource_type]
        self.parameters = PARAMETERS[resource_type]
        self.namespace = namespace_of(kwargs)

    def criteria(self, query):
        """
        One Q for a query string: every parameter holds (a repeated one each time), any of a value's comma-separated
        alternatives does. An empty value isn't a criterion; the format and paging parameters aren't criteria.
        """
        criteria = Q()
        for key, values in query.lists():
            if key in CONTROL_PARAMETERS or key in PAGING_PARAMETERS:
                continue
            name, _, modifier = key.partition(':')
            self.check(key, name, modifier)
            for value in values:
                alternatives = [self.criterion(name, modifier, item) for item in split(value, ',') if item]
                if alternatives:
                    either = Q()
                    for alternative in alternatives:
                        either |= alternative
                    criteria &= either
        return criteria

    def check(self, key, name, modifier):
        modifiers = STRING_LOOKUPS if name in STRING_FIELDS else ('',) if name in self.parameters else ()
        if modifier not in modifiers:
            raise outcomes.NotSupportedError(f"The search parameter '{key}' is not supported.", expression=key)

    def criterion(self, name, modifier, value):
        """The Q for one alternative (still escaped) of a parameter's value."""
        if name in STRING_FIELDS:
            return Q(**{f'{STRING_FIELDS[name]}__{STRING_LOOKUPS[modifier]}': unescape(value)})
        if name == 'identifier':
            return self.by_identifier(value)
        return getattr(self, self.parameters[name])(unescape(value))

    def by_id(self, value):
        """The opaque id at the global base, the mnemonic at an owner base."""
        if self.namespace is None:
            return Q(id=int(value)) if OPAQUE_ID.fullmatch(value) else NOTHING
        return Q(mnemonic=value)

    @staticmethod
    def by_last_updated(value):
        prefix, start, end = date_range(value)
        return {
            'eq': Q(updated_at__gte=start, updated_at__lt=end),
            'ne': Q(updated_at__lt=start) | Q(updated_at__gte=end),
            'gt': Q(updated_at__gte=end), 'sa': Q(updated_at__gte=end),
            'ge': Q(updated_at__gte=start),
            'lt': Q(updated_at__lt=start), 'eb': Q(updated_at__lt=start),
            'le': Q(updated_at__lt=end),
        }[prefix]

    def by_url(self, value):
        return canonical_q(self.model, value)

    @staticmethod
    def by_version(value):
        return Q(version=value)

    @staticmethod
    def by_status(value):
        return STATUSES.get(value, NOTHING)

    def by_identifier(self, token):
        """
        A token: system|value, a value with any system, or system| for any value. It matches an identifier the
        repository holds, or the OCL accession identifier (/orgs/{org}/{type}/{id}/) every resource carries.
        """
        system, *value = split(token, '|')
        system, value = (None, unescape(system)) if not value else (unescape(system), unescape('|'.join(value)))
        identifier = {key: item for key, item in (('system', system), ('value', value)) if item}
        criterion = Q(identifier__contains=[identifier]) if identifier else NOTHING
        accession = re.fullmatch(r'/(orgs|users)/([^/]+)/([^/]+)/([^/]+)/?', value or '')
        if accession and accession.group(3) == self.resource_type and system in (None, '', settings.API_BASE_URL):
            owner = 'organization__mnemonic' if accession.group(1) == 'orgs' else 'user__username'
            criterion |= Q(**{owner: accession.group(2), 'mnemonic': accession.group(4)})
        return criterion

    @staticmethod
    def by_code(value):
        """A CodeSystem that defines the code."""
        return Exists(Concept.sources.through.objects.filter(source_id=OuterRef('id'), concept__mnemonic=value))

    @staticmethod
    def by_language(value):
        return Q(default_locale=value)

    @staticmethod
    def by_reference(value):
        """
        A ValueSet whose stored default expansion draws on a version of the code system (canonical URL) named. An
        uncorrelated IN, which the planner hashes once, rather than a correlated subquery.
        """
        sources = Source.objects.filter(canonical_q(Source, value)).values('id')
        drawn_on = Q()
        for versions in (Expansion.explicit_source_versions, Expansion.evaluated_source_versions):
            drawn_on |= Q(expansion_uri__in=versions.through.objects.filter(source_id__in=sources).values(
                'expansion__uri'))
        return drawn_on

    @staticmethod
    def by_source(value):
        """
        A ConceptMap of the code system named, with mappings: OCL's ConceptMap is a source's mappings, so its source
        system is the source itself. OCL keeps no source or target value set on a ConceptMap, so source-uri and
        target-uri are read as the code systems.
        """
        return canonical_q(Source, value) & Exists(Mapping.sources.through.objects.filter(source_id=OuterRef('id')))

    @staticmethod
    def by_target(value):
        """A ConceptMap with a mapping to the code system named (a search bounded by SEARCH_TIMEOUT_MS)."""
        sources = Source.objects.filter(canonical_q(Source, value)).values('id')
        return Exists(Mapping.sources.through.objects.filter(source_id=OuterRef('id')).filter(
            Q(mapping__to_source_id__in=sources) | Q(mapping__to_source_url=value)))


def page_size(query):
    value = query.get('_count')
    if value is None:
        return MAX_COUNT
    if not re.fullmatch(r'[0-9]{1,9}', value.strip()):
        raise outcomes.InvalidError(f"_count must be a whole number, got '{value}'.", expression='_count')
    return min(int(value), MAX_COUNT)


def within_search_timeout(build):
    """
    build() with every query it runs bounded by SEARCH_TIMEOUT_MS; a query the database cancels makes the search too
    costly (422). Inside a transaction (tests) the timeout would outlive the search, so it isn't set there.
    """
    if connection.vendor != 'postgresql' or connection.in_atomic_block:
        return build()
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET LOCAL statement_timeout = %s', [SEARCH_TIMEOUT_MS])
            return build()
    except OperationalError as ex:
        if getattr(ex.__cause__, 'pgcode', None) != errorcodes.QUERY_CANCELED:
            raise
        raise outcomes.TooCostlyError(
            'The search would take too long to answer. Narrow it, for example with url, _id or version.') from ex


def search(view, resource_type):
    """The searchset Bundle a FHIR search view answers, in at most SEARCH_TIMEOUT_MS of database time."""
    return within_search_timeout(lambda: search_bundle(view, resource_type))


def search_bundle(view, resource_type):
    """The searchset Bundle a FHIR search view answers: the base's matching versions, one page of them."""
    request = view.request
    count = page_size(request.query_params)
    versions = visible_versions(resource_type, view.kwargs, request.user).filter(
        Search(resource_type, view.kwargs).criteria(request.query_params)).order_by('-id')
    paginator, entries = None, []
    if count:
        versions = with_repository_canonical(versions)
        if resource_type == 'CodeSystem':
            versions = with_concepts_over_limit(versions)
        paginator = CustomPaginator(
            request=request, total_count=None, queryset=versions, page_size=count, page_by_ids=True)
        total = paginator.total_count
        resources = view.get_serializer(paginator.current_page_results, many=True).data
        entries = FHIRBundleSerializer.convert_to_entry(
            resources, f'{fhir_base_url(request, view.kwargs)}/{resource_type}')
    else:
        total = versions.count()
    bundle = {'meta': {'lastUpdated': timezone.now()}, 'type': 'searchset', 'total': total}
    if entries:
        bundle['entry'] = entries
    return FHIRBundleSerializer(bundle, context={'paginator': paginator, 'request': request}).data


def read(resource_type, resource_id, kwargs=None, user=None, version=None):  # pylint: disable=too-many-arguments
    """
    The repository version a FHIR read names: at the global base by its opaque id, at an owner base by its mnemonic,
    the version `version` names (an OCL extension) or else the latest release. It must be one search lists:
    released, never HEAD, visible to `user` there. Raises NotFoundError.
    """
    namespace = namespace_of(kwargs)
    versions = REPO_TYPES[resource_type].objects.filter(is_active=True, released=True).exclude(version=HEAD)
    if namespace is None:
        versions = versions.filter(id=int(resource_id)) if OPAQUE_ID.fullmatch(resource_id) else versions.none()
    else:
        owner = {'organization__mnemonic': kwargs['org']} if kwargs.get('org') else {'user__username': kwargs['user']}
        versions = versions.filter(mnemonic=resource_id, **owner)
        versions = versions.filter(version=version) if version else versions.filter(
            retired=False).order_by('-created_at')
    instance = with_repository_canonical(versions).first()
    if not instance or not is_visible(instance, namespace, user) or not has_release(instance):
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
        return Response(search(self, self.resource_type))

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
        return read(self.resource_type, self.kwargs[self.lookup_field], self.kwargs, self.request.user,
                    self.request.query_params.get('version'))


class CanonicalUrlField(CharField):
    """A resource's canonical URL: the version's own, else its repository's (repository_canonical_url, on a read)."""

    def get_attribute(self, instance):
        return super().get_attribute(instance) or getattr(instance, 'repository_canonical_url', None)


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
        if self.context.get('opaque_ids') and instance.id:
            rep['id'] = str(instance.id)
        if self.context.get('has_many'):
            rep['meta'] = {**(rep.get('meta') or {}), 'tag': [SUBSETTED]}
        return rep
