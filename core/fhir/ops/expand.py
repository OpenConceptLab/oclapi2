"""
ValueSet $expand (SVCM ITI-97, FHIR R4's $expand), version-neutral: one page of the stored expansion of the value set
version the request names. core.fhir.adapters.r4 writes the answer as an R4 ValueSet.

- The value set: url (and valueSetVersion, or url|version) at the type level, or ValueSet/{id} at the instance level,
  resolved as $validate-code resolves it (core.fhir.ops.validate_code.value_set_version): a released version, the
  newest the caller is shown unless one is named (HEAD only when named); public only at /fhir/; at an owner base,
  private ones to those who may view them. One that doesn't resolve, or that the caller isn't shown, is a 422 not-found,
  the same for both.
- The expansion: the version's default stored expansion, read and never computed or saved, so a POST reads exactly as
  a GET does. A version without one, or whose expansion is still being computed, can't be expanded (422); so can't one
  whose references named a code system or value set OCL didn't hold when it was computed (422 not-found, as FHIR
  answers a value set that draws on an unknown code system), because its stored expansion is incomplete.
- Members: the expansion's concepts (not deleted) whose code system the caller is shown (core.fhir.resolve's
  viewable_rows), as ValueSet $validate-code counts them. A member of a code system the caller isn't shown leaves no
  trace: not in contains, total or used-codesystem, and no other statement runs.
- Each member: its code system (the canonical URL of the release it is reported in, else its repository's, else the
  repository's OCL path as a CodeSystem), its code as FHIR spells it, its display (with
  displayLanguage, its preferred name in that language when it has one), inactive when retired, its names as
  designations with includeDesignations, and the version: a live release the caller is shown that holds the member's
  row, the expansion's explicit pin first, then its evaluated pin, then the newest. A versioned-object row carries
  the concept's newest content, so it uses the newest holding release of another, not deleted, version, whatever the
  expansion pins. None without one, never HEAD or an unreleased version (as $validate-code answers).
- Inputs: activeOnly leaves retired concepts out; filter keeps the concepts whose code (as FHIR spells it) or one of
  whose names has a word starting with each of the filter's words, ignoring case; offset and count page the members in
  a fixed order (count at most FHIR_EXPAND_MAX_COUNT, which is also the default); count=0 answers the total alone. total counts
  what activeOnly and filter keep. The inputs that shaped the answer are echoed in expansion.parameter, then each code
  system the members come from, with each version they are reported in, as used-codesystem. Every other R4 $expand
  input is one a stored expansion always satisfies (SATISFIED: OCL's expansions are flat, with no abstract codes), one
  supported only when false (ONLY_FALSE: an expansion has no definitions, and doesn't record which of its codes are
  post-coordinated), or a 400 not-supported naming it (NOT_SUPPORTED): an inline value set, a context, a date, code
  system versions and designations would each need an expansion computed for the request, and OCL serves the ones it
  stores. Names that aren't $expand inputs are ignored, as _format is.
- Cost: a fixed number of queries whatever the size of the expansion; for members whose rows no release holds,
  links of each repository's newest live release the caller is shown are read as a set, then older holding releases
  probed per member only for those still unresolved. Full concepts and names are read for the page alone. A filtered
  expansion reads the ids of its matching members once, and its total and page come from them. All queries within
  FHIR_SEARCH_TIMEOUT_MS together, past which the expansion is too costly (422).
"""
import re

from django.conf import settings
from django.db.models import BooleanField, Case, Exists, ExpressionWrapper, F, Func, IntegerField, JSONField, \
    OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce, JSONObject
from django.db.models.lookups import Regex

from core.collections.models import Expansion
from core.common.constants import HEAD
from core.common.serializers import IdentifierSerializer
from core.concepts.models import Concept, ConceptName
from core.fhir import outcomes
from core.fhir.constants import EXPANSION_TOO_COSTLY
from core.fhir.ops.concepts import designations, display_of, fhir_code, text
from core.fhir.ops.validate_code import value_set_version
from core.fhir.resolve import namespace_of, viewable_rows
from core.fhir.search import within_search_timeout
from core.sources.models import Source

WORD = re.compile(r'\w+')
# an escape of an ASCII character that is no word character (not a letter, digit or _), matched ignoring case
NON_WORD_ESCAPE = '%(?:[01][0-9a-f]|2[0-9a-f]|3[a-f]|40|5[b-e]|60|7[b-f])'
# the escapes of one non-ASCII character as a stored code writes them, matched ignoring case: a well-formed UTF-8
# sequence of 2 to 4 bytes (Unicode's Table 3-7), else a single byte, which makes no character
NON_ASCII_CHARACTER = ('(?:%c[2-9a-f]|%d[0-9a-f])%[89ab][0-9a-f]'
                       '|(?:%e0%[ab][0-9a-f]|%e[1-9a-cef]%[89ab][0-9a-f]|%ed%[89][0-9a-f])%[89ab][0-9a-f]'
                       '|(?:%f0%[9ab][0-9a-f]|%f[1-3]%[89ab][0-9a-f]|%f4%8[0-9a-f])%[89ab][0-9a-f]%[89ab][0-9a-f]'
                       '|%[89a-f][0-9a-f]')
STORED = 'OCL serves the expansion it stores for the value set version'
VERSIONS = f'Choosing code system versions is not supported: {STORED}, made from the versions used-codesystem names.'
NOT_SUPPORTED = {  # R4 $expand inputs that need an expansion computed for the request
    'valueSet': f'An inline valueSet is not supported: {STORED}, named by url.',
    'context': 'The context parameter is not supported.',
    'contextDirection': 'The contextDirection parameter is not supported.',
    'date': f'An expansion as of a date is not supported: {STORED}.',
    'designation': 'Choosing designations is not supported: includeDesignations includes every name.',
    'exclude-system': f'Excluding a code system is not supported: {STORED}.',
    'system-version': VERSIONS,
    'check-system-version': VERSIONS,
    'force-system-version': VERSIONS,
}
ONLY_FALSE = {  # boolean R4 $expand inputs supported when false
    'includeDefinition': 'Definitions are not included in an expansion.',
    'excludePostCoordinated': f'Excluding post-coordinated codes is not supported: {STORED}, which does not record '
                              'which of its codes are post-coordinated.',
}
SATISFIED = ('excludeNested', 'excludeNotForUI')  # booleans every stored expansion meets
# a reference an expansion couldn't resolve, by its type: (message id, message) without and with a version named
UNRESOLVED = {
    'reference.valueset': (
        ('VS_EXP_IMPORT_UNK', "Unable to find included value set '{url}'"),
        ('VS_EXP_IMPORT_UNK_PINNED', "Unable to find included value set '{url}' version '{version}'")),
    'system': (
        ('UNKNOWN_CODESYSTEM_EXP',
         "A definition for CodeSystem '{url}' could not be found, so the value set cannot be expanded"),
        ('UNKNOWN_CODESYSTEM_VERSION_EXP',
         "A definition for CodeSystem '{url}' version '{version}' could not be found, so the value set cannot be "
         "expanded")),
}


def whole_number(parameters, name):
    """A whole-number input (an integer, or its digits as text), or None when it's absent; anything else is a 400."""
    value = parameters.get(name)
    if value is None or value == '':
        return None
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,9}', value.strip()):
        return int(value)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise outcomes.InvalidError(f"'{name}' must be a whole number, got '{value}'.", expression=name)


def flag(parameters, name):
    """A boolean input (a boolean, or true or false as text), or None when it's absent; anything else is a 400."""
    value = parameters.get(name)
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and (lowered := value.strip().lower()) in ('true', 'false'):
        return lowered == 'true'
    raise outcomes.InvalidError(f"'{name}' must be true or false, got '{value}'.", expression=name)


class Inputs:  # pylint: disable=too-few-public-methods
    """$expand's inputs other than the value set, read and checked before anything is: the echo is what was given."""

    def __init__(self, parameters):
        for name, message in NOT_SUPPORTED.items():
            if name in parameters:
                raise outcomes.NotSupportedError(message, expression=name)
        for name, message in ONLY_FALSE.items():
            if flag(parameters, name):
                raise outcomes.NotSupportedError(message, expression=name)
        for name in SATISFIED:
            flag(parameters, name)  # either value is met; one that isn't a boolean is still a 400
        self.filter = text(parameters, 'filter')
        self.language = text(parameters, 'displayLanguage')
        self.designations = flag(parameters, 'includeDesignations')
        self.active_only = flag(parameters, 'activeOnly')
        self.offset = whole_number(parameters, 'offset')
        count = whole_number(parameters, 'count')
        self.count = settings.FHIR_EXPAND_MAX_COUNT if count is None else min(count, settings.FHIR_EXPAND_MAX_COUNT)
        self.echo = [item for item in (
            ('filter', 'valueString', self.filter),
            ('displayLanguage', 'valueCode', self.language),
            ('includeDesignations', 'valueBoolean', self.designations),
            ('activeOnly', 'valueBoolean', self.active_only),
            ('offset', 'valueInteger', self.offset),
            ('count', 'valueInteger', None if count is None else self.count),
        ) if item[2] is not None]


def escapes(char):
    """A character percent-encoded, as OCL stores it in a code: %XX for each of its UTF-8 bytes."""
    return ''.join(f'%{byte:02X}' for byte in char.encode())


def spelled(word):
    """
    A regex, matched ignoring case, for `word` as a stored code may spell it: each character as itself, or as the
    escapes of itself or of its other case (é: é, %C3%A9 or %C3%89). \\w characters need no escaping in a regex.
    """
    spellings = (dict.fromkeys([char, *(escapes(case) for case in (char, char.lower(), char.upper())
                                        if len(case) == 1)]) for char in word)
    return ''.join(f"(?:{'|'.join(spelling)})" for spelling in spellings)


class NonWordBefore(Func):  # pylint: disable=abstract-method
    """
    Whether, somewhere in the code, a non-ASCII character's escapes stand right before `spelling` (a word, spelled) and
    that character is no word character: a well-formed UTF-8 sequence, decoded, for a character Postgres doesn't count
    as a letter, digit or _; or a single byte, which makes no character (FHIR's decoding spells it U+FFFD). A regex
    takes the longest match where it starts, so a character's escapes are matched whole.
    """
    template = (
        "EXISTS (SELECT FROM regexp_matches(%(expressions)s, 'gi') AS characters (escapes) WHERE CASE WHEN "
        "length(escapes[1]) = 3 THEN true ELSE convert_from(decode(regexp_replace(escapes[1], '[^0-9A-Fa-f]', '', "
        "'g'), 'hex'), 'UTF8') COLLATE \"default\" !~ '[[:alnum:]_]' END)")
    output_field = BooleanField()

    def __init__(self, spelling):
        super().__init__(F('mnemonic'), Value(f'(?:{NON_ASCII_CHARACTER})(?={spelling})'))


def code_has_word(word):
    """
    Whether a member's code, as FHIR spells it (core.fhir.ops.concepts.fhir_code decodes OCL's percent-encoding), has
    a word starting with `word`, ignoring case, by the rule filter applies to names (Postgres's \\m: a word character
    is a letter or digit of any script, or _). The stored code is matched a whole character at a time, never from inside
    an escape. A word starts there:
    - in a code with no escape, where \\m says; so too for a word that doesn't start with a word character (filter's
      words are Python's \\w+, which counts a few characters more, ² for one): \\m starts no word with one;
    - at its start, or after a character that is no word character: written as itself, as an ASCII escape, or a % that
      starts no escape;
    - after a non-ASCII character's escapes, when that character, decoded, is no word character (NonWordBefore).
    Each branch is tried only when the ones before it haven't decided, so a code without the word in any spelling costs
    one regex, and one without escapes the regex names get.
    """
    spelling = spelled(word)
    return Case(
        When(~Q(mnemonic__contains='%') | ~Q(Regex(Value(word), '^[[:alnum:]_]')),
             then=ExpressionWrapper(Q(mnemonic__iregex=rf'\m{word}'), output_field=BooleanField())),
        When(~Q(mnemonic__iregex=spelling), then=False),
        When(mnemonic__iregex=rf'(?:^|[^[:alnum:]_%]){spelling}', then=True),
        When(mnemonic__iregex=rf'{NON_WORD_ESCAPE}{spelling}', then=True),
        When(mnemonic__iregex=rf'%(?![0-9a-f]{{2}}){spelling}', then=True),
        When(mnemonic__iregex=rf'%[89a-f][0-9a-f]{spelling}', then=NonWordBefore(spelling)),
        default=False, output_field=BooleanField())


def expand(parameters, kwargs, user, instance_id=None):
    """
    $expand's answer for its FhirParameters at the base `kwargs` names (ValueSet/{instance_id}/$expand at the instance
    level), for `user`: {value_set, identifier, timestamp, total, offset, parameters, used, contains}.
    """
    inputs = Inputs(parameters)
    if not instance_id and not text(parameters, 'url'):
        raise outcomes.InvalidError('url is required: the value set to expand.', expression='url')
    return within_search_timeout(
        lambda: Expander(parameters, inputs, kwargs, user, instance_id).answer(), EXPANSION_TOO_COSTLY)


def stored_expansion(value_set):
    """The value set version's default stored expansion, when it is one that can be served; else the 422 why not."""
    name = f'{value_set.canonical_url or value_set.uri}|{value_set.version}'
    expansion = Expansion.objects.filter(uri=value_set.expansion_uri).only(
        'id', 'uri', 'created_at', 'is_processing', 'unresolved_repo_versions').first() if \
        value_set.expansion_uri else None
    if expansion is None:
        raise outcomes.InvalidError(
            f'The value set {name} has no stored expansion, so it cannot be expanded.', tx_issue_type='vs-invalid',
            status_code=422)
    if expansion.is_processing:
        raise outcomes.ProcessingError(f'The expansion of the value set {name} is being computed: try again shortly.')
    if expansion.unresolved_repo_versions:
        raise unresolved(expansion.unresolved_repo_versions[0])
    return expansion


def unresolved(reference):
    """The 422 not-found for the first reference an expansion couldn't resolve: a value set, else a code system."""
    url, version = reference.get('url') or '', reference.get('version') or None
    message_id, message = UNRESOLVED.get(reference.get('type'), UNRESOLVED['system'])[bool(version)]
    return outcomes.NotFoundError(message.format(url=url, version=version), tx_issue_type='not-found',
                                  message_id=message_id, status_code=422)


def code_system(canonical_url, uri, release=None):
    """
    (system, version) of a member of the repository with this canonical URL and OCL path, reported in `release`
    ({version, url}, or None): the release's own canonical URL, else the repository's, else its OCL path as a
    CodeSystem (/orgs/{org}/CodeSystem/{id}/), as a read names the release; and the release's version.
    """
    release = release or {}
    system = release.get('url') or canonical_url or IdentifierSerializer.convert_ocl_uri_to_fhir_url(uri, 'CodeSystem')
    return system, release.get('version')


class Expander:
    """One $expand: the value set version, its stored expansion, and the members the caller is shown."""

    def __init__(self, parameters, inputs, kwargs, user, instance_id):  # pylint: disable=too-many-arguments
        self.inputs = inputs
        self.value_set = value_set_version(parameters, kwargs, user, instance_id)
        self.expansion = stored_expansion(self.value_set)
        self.namespace, self.user = namespace_of(kwargs), user

    def members(self):
        """The expansion's concepts that are its members for the caller: not deleted, of a code system it is shown."""
        return Concept.objects.filter(
            Exists(viewable_rows(Source, self.namespace, self.user).filter(id=OuterRef('parent_id'))),
            expansion_set=self.expansion.id, is_active=True)

    def matching(self):
        """The members activeOnly and filter keep."""
        members = self.members()
        if self.inputs.active_only:
            members = members.filter(retired=False)
        # \m: a word starts here; \w characters need no escaping in a regex
        for word in WORD.findall(self.inputs.filter or ''):
            members = members.filter(Q(code_has_word(word)) | Exists(ConceptName.objects.filter(
                concept_id=OuterRef('id'), name__iregex=rf'\m{word}')))
        return members

    def live_releases(self):
        """The active, non-retired releases the caller is shown, never HEAD."""
        return viewable_rows(Source, self.namespace, self.user).filter(
            is_active=True, released=True, retired=False).exclude(version=HEAD)

    def release(self):
        """
        The live release the caller is shown that holds the member's row, as {version, url}: explicit pin first,
        then evaluated, then newest by source id; None without one. The newest lookup runs only when no pin holds it.
        """
        links = Concept.sources.through.objects.filter(
            source_id__in=self.live_releases().values('id'), concept_id=OuterRef('id'))
        explicit, evaluated = (
            versions.through.objects.filter(expansion_id=self.expansion.id).values('source_id')
            for versions in (Expansion.explicit_source_versions, Expansion.evaluated_source_versions))
        pinned = links.filter(Q(source_id__in=explicit) | Q(source_id__in=evaluated)).annotate(explicit=Case(
            When(source_id__in=explicit, then=Value(1)), default=Value(0), output_field=IntegerField())).order_by(
            '-explicit', '-source_id')
        return Coalesce(*(Subquery(holding.annotate(
            release=JSONObject(version='source__version', url='source__canonical_url')).values('release')[:1],
            output_field=JSONField()) for holding in (pinned, links.order_by('-source_id'))),
            output_field=JSONField())

    def releases(self, members, rows):
        """
        {member id: release} from (id, versioned-object id, parent id, direct release) rows of `members`.
        Members held by releases keep their direct release. A versioned-object member carries its concept's newest
        content, so it uses the newest live release the caller is shown that holds an active version, ignoring pins.
        Only a repository's own releases hold its concepts' versions, so a match in its newest visible live release
        is also the newest holding release. Those links are read as a set; any still unresolved use a correlated
        newest lookup, restricted to their member ids. Every member remains a key, with None if no release holds it.
        """
        rows = list(rows)
        found = {member: release for member, _, _, release in rows}
        missing = {member: (root, parent) for member, root, parent, release in rows if release is None}
        if not missing:
            return found
        newest = self.live_releases().filter(mnemonic=OuterRef('mnemonic')).filter(
            Q(organization_id=OuterRef('organization_id')) | Q(user_id=OuterRef('user_id'))).order_by('-id')
        heads = Source.objects.filter(id__in={parent for _, parent in missing.values()}).annotate(
            newest=Subquery(newest.values('id')[:1])).values('newest')
        held = dict(Concept.sources.through.objects.filter(
            source_id__in=heads, concept__is_active=True,
            concept__versioned_object_id__in=members.order_by().values('versioned_object_id')).annotate(
            release=JSONObject(version='source__version', url='source__canonical_url')).values_list(
            'concept__versioned_object_id', 'release'))
        for member, (root, _) in list(missing.items()):
            if root in held:
                found[member] = held[root]
                del missing[member]
        if missing:
            holding = Concept.sources.through.objects.filter(
                source_id__in=self.live_releases().values('id'), concept__is_active=True,
                concept__versioned_object_id=OuterRef('versioned_object_id')).order_by('-source_id')
            found.update(Concept.objects.filter(id__in=list(missing)).annotate(release=Subquery(holding.annotate(
                release=JSONObject(version='source__version', url='source__canonical_url')).values('release')[:1],
                output_field=JSONField())).values_list('id', 'release'))
        return found

    def used(self):
        """Each code system and release of all members, including those held only in other versions: system|version."""
        members = self.members()
        rows = list(members.annotate(release=self.release()).values_list(
            'id', 'versioned_object_id', 'parent_id', 'release', 'parent__canonical_url', 'parent__uri').order_by())
        releases = self.releases(members, (row[:4] for row in rows))
        used = {code_system(url, uri, releases[member]) for member, _, _, _, url, uri in rows}
        return sorted(f'{system}|{version}' if version else system for system, version in used)

    def page(self, ids):
        """
        The members `ids` names (the page offset and count chose), in a fixed order, with what each is shown with.
        Releases, concepts and names are read for the page's members alone, however deep the offset.
        """
        page = Concept.objects.filter(id__in=ids)
        concepts = page.annotate(release=self.release()).select_related('parent').only(
            'id', 'mnemonic', 'retired', 'parent_id', 'versioned_object_id', 'parent__canonical_url', 'parent__uri',
            'parent__default_locale', 'parent__supported_locales').prefetch_related('names').order_by('id')
        releases = self.releases(page, (
            (concept.id, concept.versioned_object_id, concept.parent_id, concept.release) for concept in concepts))
        members = []
        for concept in concepts:
            parent = concept.parent
            system, version = code_system(parent.canonical_url, parent.uri, releases[concept.id])
            members.append({
                'system': system, 'version': version, 'code': fhir_code(concept.mnemonic),
                'display': display_of(concept, self.inputs.language), 'inactive': concept.retired or None,
                'designations': designations(concept) if self.inputs.designations else None,
            })
        return members

    def answer(self):
        matching = self.matching()
        if self.inputs.filter:
            ids = list(matching.order_by('id').values_list('id', flat=True))
            total = len(ids)
        else:
            total = matching.count()
            ids = matching.order_by('id').values('id')
        offset = self.inputs.offset or 0
        return {
            'value_set': self.value_set, 'identifier': self.expansion.uri, 'timestamp': self.expansion.created_at,
            'total': total, 'offset': offset, 'parameters': self.inputs.echo,
            'used': self.used(),
            'contains': self.page(ids[offset:offset + self.inputs.count]) if self.inputs.count else [],
        }
