"""
FHIR's canonical resolution: the resolver $resolveReference uses (ConceptContainerModel.resolve_reference_expression,
with or without the URL registry), with FHIR's rules on top.

- Resource type: CodeSystem and ConceptMap resolve to a Source, ValueSet to a Collection.
- Namespace: the base URL's. /fhir/ is global; /orgs/{org}/ and /users/{user}/ are that owner's. It comes from the
  route, never from the request.
- Version: the one named (the operation's version parameter, else url|version, else the version an OCL path names;
  a path naming one version with another named names none), HEAD included, as it is stored: a request's value is
  already decoded, so 2.0+build names 2.0+build. With none named, the newest released
  version the caller is shown, never HEAD. A repository with nothing released isn't visible, whatever version is
  named.
- A version named, with a canonical URL (decision 9): when the repository the resolver picks doesn't serve the caller
  a release of that name, the release of the lowest-id repository with that canonical URL the base shows the caller
  that holds one; failing that, the version so named (unreleased, or HEAD) of the lowest-id one it shows, as before;
  else not found. A repository or version the caller isn't shown never holds it. This applies where the repositories
  with the canonical URL are the step that picks: without the URL registry, at /fhir/ (when it finds one) and at an
  owner base (whether or not it finds one, so a hidden repository is answered as a missing one is); with it, at an
  owner base whose own registry has no entry for the URL and that has a repository with it. A registry entry decides
  (at /fhir/ only an entry resolves), and where resolution finds no repository it still finds none.
  At an owner base a repository the base doesn't show the caller, and one whose HEAD it shows but whose releases are
  all private, are both answered as a missing one is, byte for byte; the statement count can still differ by one,
  the A.3 residual of the shared resolver's early stop rather than of this rule, which a change in the shared
  resolver would close. tests_resolve_named_version.HiddenHolderCases measures both, and measures the same
  differences on the code before this rule.
- Access, after resolving: at the global base public repositories only, at an owner base what the caller may view.
  The version and its repository (HEAD) must both be viewable. Without the URL registry, the global base's canonical
  match considers only the repositories it shows, so a private copy of a canonical never hides a public one. Otherwise
  (an owner base, a registry entry) a repository the caller can't see is not found, and no other repository is tried;
  none of its versions is read.

Every failure is the same UnresolvedError, so it never tells a caller that a repository they can't see exists.

served_repositories is the set form of the resolver's choices for code systems, for TerminologyCapabilities: the
identifier each repository a search shows is served at, and the versions of it a caller is served.
"""
from collections import namedtuple
from urllib.parse import quote_plus

from django.db.models import Case, Exists, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError

from core.collections.models import Collection
from core.common.constants import HEAD, ACCESS_TYPE_VIEW, RETIRED_ACCESS_TYPE_EDIT
from core.common.mixins import SourceContainerMixin
from core.common.serializers import IdentifierSerializer
from core.common.utils import decode_string, is_canonical_uri, normalize_public_access, to_parent_uri
from core.fhir.outcomes import NotFoundError
from core.sources.models import Source
from core.toggles.models import Toggle
from core.url_registry.constants import URL_REGISTRY_IN_RESOLVE_REFERENCE_TOGGLE
from core.url_registry.models import URLRegistry

REPO_TYPES = {'CodeSystem': Source, 'ConceptMap': Source, 'ValueSet': Collection}
REPO_PATHS = {'CodeSystem': 'sources', 'ConceptMap': 'sources', 'ValueSet': 'collections'}
PUBLIC_ACCESS = (ACCESS_TYPE_VIEW, RETIRED_ACCESS_TYPE_EDIT)  # what public_can_view reads as public
# a HEAD served_repositories reads: what the resolver's pick and a CodeSystem's identifier depend on
ServedHead = namedtuple(
    'ServedHead', 'id mnemonic organization_id user_id canonical_url uri organization_uri user_uri')


class UnresolvedError(NotFoundError):
    """
    No repository version of the resource type the caller may see for a canonical URL (and version). `url` is the
    url without a |version, `version` the one asked for: the version parameter, else the url's.
    """

    def __init__(self, resource_type, url, version=None, expression=None):
        self.resource_type, self.url, self.version = resource_type, url, version
        named = f" version '{version}'" if version else ''
        super().__init__(
            f"A definition for {resource_type} '{url}'{named} could not be found", expression=expression,
            tx_issue_type='not-found')


def namespace_of(kwargs):
    """The namespace of the FHIR base a route's kwargs name: the owner's URI, or None for the global base."""
    kwargs = kwargs or {}
    if kwargs.get('org'):
        return f"/orgs/{kwargs['org']}/"
    if kwargs.get('user'):
        return f"/users/{kwargs['user']}/"
    return None


def resolve(resource_type, url, version=None, kwargs=None, user=None, expression=None):  # pylint: disable=too-many-arguments,too-many-locals
    """
    The repository version a FHIR request names: `url` (a canonical URL, or an OCL path such as
    /orgs/{org}/CodeSystem/{id}/) and `version`, at the base `kwargs` names, for `user`. A view passes its own
    self.kwargs and self.request.user. Raises UnresolvedError, whose `expression` is the parameter the url came in.
    """
    url = url or ''
    lookup_url, _, piped_version = url.partition('|')
    in_path, elsewhere = path_version(lookup_url), version or piped_version or None
    error = UnresolvedError(resource_type, lookup_url, elsewhere or in_path, expression)
    namespace = namespace_of(kwargs)
    # a second pipe, or a path naming one version with another named, names no version OCL holds
    conflicting = '|' in piped_version or bool(in_path and elsewhere and in_path != elsewhere)
    if not lookup_url or conflicting or (namespace and not SourceContainerMixin.get_object_from_namespace(namespace)):
        raise error

    # the version as named (HTTP has decoded the query already), so the shared resolver, which decodes it again,
    # reads it back as it is stored: 2.0+build stays 2.0+build
    repo_type = REPO_TYPES[resource_type]
    instance, entry, picked = shared_resolution(
        repo_type, lookup_url, as_stored(elsewhere) if elsewhere else None, namespace, user)
    if instance.id and not (elsewhere or in_path) and not is_shown(instance, namespace, user):
        instance = latest_public_release(instance) or instance  # the newest release is private: the newest public one
    shown = bool(instance.id) and is_shown(instance, namespace, user)
    # a version named that the repository picked doesn't serve as a release: its holder's, where the repositories with
    # the canonical URL are the step that picks (decision 9)
    if elsewhere and holders_pick(instance, entry, picked, namespace) and not (
            shown and (instance.is_head or instance.released)):
        held = held_version(repo_type, instance.resolution_url, elsewhere, namespace, user)
        if held and held.id != instance.id:
            if namespace and hasattr(picked, 'fhir_may_view_private'):  # the same owner: the same right
                held.fhir_may_view_private = picked.fhir_may_view_private
            instance, shown = held, is_shown(held, namespace, user)

    if not shown:
        raise error
    return instance


def shared_resolution(repo_type, url, version, namespace, user):
    """
    (the shared resolver's answer, the registry entry that decided it or None, the HEAD it picked or None), with FHIR's
    flags: no HEAD by default; at the global base only the HEADs it shows; and a HEAD the caller isn't shown resolves
    to nothing before any version is read. The version found shares the HEAD's check of the caller's right to the
    owner's private repositories, so it isn't asked twice.
    """
    heads = []

    def head_shown(head):
        heads.append(head)
        return is_visible(head, namespace, user)
    instance, entry = repo_type.resolve_reference_expression(
        to_ocl_path(url), namespace, version, repo_type=repo_type, allow_head=False,
        heads=None if namespace else shown_heads(repo_type), head_shown=head_shown)
    if heads and hasattr(heads[0], 'fhir_may_view_private'):
        instance.fhir_may_view_private = heads[0].fhir_may_view_private
    return instance, entry, heads[0] if heads else None


def holders_pick(instance, entry, picked, namespace):
    """
    Whether the repositories with the canonical URL a request names are the step that picked `instance` (the shared
    resolver's answer, `entry` the registry entry that decided it, `picked` the HEAD it found): no entry decided, and
    at /fhir/ a HEAD was found (with the URL registry on, at /fhir/ only an entry finds one), at an owner base whether
    or not one was. An OCL path names its repository itself.
    """
    return instance.is_fqdn and entry is None and bool(namespace or picked)


def held_version(repo_type, url, version, namespace, user):
    """
    The version named `version` the base shows the caller among the repositories with the canonical URL `url` it
    shows (their active HEAD has it, isn't retired, and has a live release the caller is shown; at an owner base, the
    owner's), as resolve() serves a version named when the repository the resolver picks serves no release of it: a
    release (retired or not), of the lowest-id repository holding one; else the version so named (unreleased, or HEAD)
    of the lowest-id repository; None without either. Its repository's access and canonical URL are set on it, as the
    resolver sets them. One query, whatever the number of repositories; what the caller isn't shown is left out in it.
    """
    shown = shown_q(namespace, user)
    owner = {}
    if namespace:
        owner = {'organization__uri' if namespace.startswith('/orgs/') else 'user__uri': namespace}
    same_repository = Q(organization_id=OuterRef('organization_id')) | Q(user_id=OuterRef('user_id'))
    live = repo_type.objects.filter(
        shown, same_repository, mnemonic=OuterRef('mnemonic'), is_active=True, released=True,
        retired=False).exclude(version=HEAD)
    heads = repo_type.objects.filter(
        shown, Exists(live), canonical_url=url, version=HEAD, is_active=True, retired=False, **owner)
    head = heads.filter(same_repository, mnemonic=OuterRef('mnemonic'))
    release = Q(released=True) & ~Q(version=HEAD)
    held = repo_type.objects.filter(shown, version=version, is_active=True, **owner).annotate(
        head_id=Subquery(head.values('id')[:1]), repository_public_access=Subquery(head.values('public_access')[:1]),
    ).filter(release | Q(head_id=Subquery(heads.order_by('id').values('id')[:1])), head_id__isnull=False).order_by(
        Case(When(release, then=Value(0)), default=Value(1)), 'head_id').first()
    if held:
        held.is_fqdn, held.resolution_url, held.repository_canonical_url = True, url, url
        held.canonical_url = held.canonical_url or url
    return held


def path_version(url):
    """The version an OCL path names (/orgs/{org}/sources/{id}/{version}/, or as a CodeSystem path), or None."""
    parts = to_parent_uri(to_ocl_path(url)).split('/')
    if len(parts) > 5 and not parts[0] and parts[1] in ('orgs', 'users') and parts[3] in REPO_PATHS.values():
        return parts[5] or None
    return None


def is_shown(instance, namespace, user):
    """Whether a base shows the caller a repository version: it is visible there, and so is a release of it."""
    return is_visible(instance, namespace, user) and has_release(instance, namespace, user)


def shown_heads(model):
    """
    Q for the HEAD rows the global base shows: public, with a live (released, not retired) public release. The
    shared resolver's canonical match takes the first of them, without the URL registry. One EXISTS with the owner
    matched inside it, as with_head, so the planner runs it as a semi-join on the few rows the canonical URL matches.
    """
    return Q(public_access__in=PUBLIC_ACCESS) & Exists(model.objects.filter(
        mnemonic=OuterRef('mnemonic'), is_active=True, released=True, retired=False,
        public_access__in=PUBLIC_ACCESS).exclude(version=HEAD).filter(
        Q(organization_id=OuterRef('organization_id')) | Q(user_id=OuterRef('user_id'))))


def latest_public_release(instance):
    """The newest live public release of a version's repository, resolved as the version was, or None."""
    owner = {'organization_id': instance.organization_id} if instance.organization_id else {'user_id': instance.user_id}
    latest = type(instance).objects.filter(
        mnemonic=instance.mnemonic, is_active=True, released=True, retired=False, public_access__in=PUBLIC_ACCESS,
        **owner).exclude(version=HEAD).order_by('-created_at').first()
    if latest:
        latest.repository_public_access = repository_access(instance)
        latest.repository_canonical_url = getattr(instance, 'repository_canonical_url', None)
        latest.is_fqdn, latest.resolution_url = instance.is_fqdn, instance.resolution_url
        if instance.is_fqdn and not latest.canonical_url:
            latest.canonical_url = instance.resolution_url
    return latest


def to_ocl_path(url):
    """
    An OCL FHIR path (/orgs/{org}/CodeSystem/{id}/...) as the OCL path of the repository it names, by its own
    resource type, so a reference to the other type finds nothing; anything else as it is.
    """
    parts = url.split('/')
    if len(parts) > 3 and not parts[0] and parts[1] in ('orgs', 'users') and parts[3] in REPO_PATHS:
        parts[3] = REPO_PATHS[parts[3]]
    return '/'.join(parts)


def is_visible(instance, namespace, user):
    """
    Whether a base shows a repository version: the version and its repository (its active HEAD, which governs access
    to the repository's concepts and mappings) must both be viewable there. At the global base both are public, to
    anyone; at an owner base the same, or any of the owner's versions to those who may view its private repositories
    (staff, the org's members, the user).
    """
    access = repository_access(instance)
    if access is None:
        return False
    return (instance.public_can_view and is_public(access)) or may_view_private(instance, namespace, user)


def has_release(instance, namespace=None, user=None):
    """
    Whether the repository of a version has a released version that isn't retired, and that the base shows the
    caller: a public one, or any to those who may view the owner's private repositories. HEAD's released flag isn't
    one. (The repository's own access, HEAD's, is is_visible's to check.)
    """
    if instance.released and not instance.retired and not instance.is_head and (
            instance.public_can_view or may_view_private(instance, namespace, user)):
        return True
    live = instance.released_versions.filter(retired=False).exclude(version=HEAD)
    if not may_view_private(instance, namespace, user):
        live = live.filter(public_access__in=PUBLIC_ACCESS)
    return live.exists()


def is_public(public_access):
    """A public_access value as public_can_view reads it."""
    return normalize_public_access(public_access).lower() == ACCESS_TYPE_VIEW.lower()


def repository_access(instance):
    """
    The public_access of a version's repository, its active HEAD's, or None without one. A read (with_repository_access)
    and the resolver (resolve_repo) set it on the version they find, so it isn't looked up again; with the canonical
    URL of its repository, which the HEAD looked up here gives the version too (one an OCL path names itself).
    """
    if instance.is_head:
        return instance.public_access if instance.is_active else None
    if hasattr(instance, 'repository_public_access'):
        return instance.repository_public_access
    head = instance.head
    if head and not hasattr(instance, 'repository_canonical_url'):
        instance.repository_canonical_url = head.canonical_url
    return head.public_access if head else None


def may_view_private(instance, namespace, user):
    """At an owner base, whether `user` may view the owner's private repositories; asked once per version."""
    if namespace is None or not (user and user.is_authenticated):
        return False
    if not hasattr(instance, 'fhir_may_view_private'):
        instance.fhir_may_view_private = instance.has_parent_edit_access(user)
    return instance.fhir_may_view_private


def repository_rows(model, **filters):
    """
    For a subquery: the rows of the outer row's repository (same mnemonic and owner) matching `filters`, as two
    querysets, one for an org-owned outer row and one for a user-owned one (an owner column is NULL on the other).
    """
    rows = model.objects.filter(mnemonic=OuterRef('mnemonic'), **filters)
    return (rows.filter(organization_id=OuterRef('organization_id'), user_id__isnull=True),
            rows.filter(user_id=OuterRef('user_id'), organization_id__isnull=True))


def with_head(model, *conditions, **filters):
    """
    For a subquery: whether the outer row's repository has an active HEAD matching `conditions` and `filters`. One
    EXISTS, the owner matched inside it (an owner column is NULL on the other kind of row, and NULL equals nothing),
    which the planner runs as one semi-join; two EXISTS joined by OR it plans as an index scan costing far more.
    """
    return Exists(model.objects.filter(
        *conditions, mnemonic=OuterRef('mnemonic'), version=HEAD, is_active=True, **filters).filter(
        Q(organization_id=OuterRef('organization_id')) | Q(user_id=OuterRef('user_id'))))


def with_repository_access(queryset):
    """Each row annotated with repository_public_access, its repository's (active HEAD's) public_access."""
    org_heads, user_heads = repository_rows(queryset.model, version=HEAD, is_active=True)
    return queryset.annotate(repository_public_access=Coalesce(
        Subquery(org_heads.values('public_access')[:1]), Subquery(user_heads.values('public_access')[:1])))


def owner_access(kwargs, user):
    """
    (owner filter, may view private) of the base `kwargs` names, for `user`: ({}, False) at the global base; at an
    owner base, the filter on the owner's rows (None when the owner doesn't exist) and whether `user` may view its
    private repositories (staff, the org's members, the user).
    """
    namespace = namespace_of(kwargs)
    if namespace is None:
        return {}, False
    owner = SourceContainerMixin.get_object_from_namespace(namespace)
    if not owner:
        return None, False
    is_org = bool(kwargs.get('org'))
    return {'organization_id' if is_org else 'user_id': owner.id}, bool(user and user.is_authenticated) and (
        user.is_staff or (owner.is_member(user) if is_org else owner.id == user.id))


def viewable_rows(model, namespace, user):
    """
    The queryset form of is_visible(row, namespace, user) over every row of `model`, HEAD and versions alike: anywhere
    a public row of a public repository (active HEAD); at an owner base also, whoever owns them, the rows `user` may
    view by their own rights (all to staff, their orgs' and their own), with an active HEAD. For what a version refers
    to (a mapping's source, the code systems an expansion drew on), so only repositories the caller is shown match.

    A row and its HEAD share their owner, so "both public, or the caller's own" is "the row public or the caller's own,
    and its HEAD public or the caller's own": one EXISTS, which the planner runs as a semi-join.
    """
    shown = shown_q(namespace, user)
    return model.objects.filter(shown, with_head(model, shown))


def shown_q(namespace, user):
    """
    Q for the rows viewable_rows requires of a row and of its HEAD: public; at an owner base also, to those signed in,
    their own by their rights (everything to staff, their orgs' and their own).
    """
    public = Q(public_access__in=PUBLIC_ACCESS)
    if namespace is None or not (user and user.is_authenticated):
        return public
    if user.is_staff:
        return Q()
    return public | Q(organization_id__in=user.organizations.values('id')) | Q(user_id=user.id)


def visible_versions(resource_type, kwargs=None, user=None, access=None):
    """
    The queryset form of is_visible and has_release, for search: the active released versions, never HEAD, that the
    base `kwargs` names shows `user`. A row is in it exactly when is_visible(row, namespace, user) and
    has_release(row, namespace, user): at the global base public versions of public repositories (HEAD), whoever asks;
    at an owner base the owner's, private ones only to those who may view them (staff, the org's members, the user).
    An owner base whose owner doesn't exist shows nothing. `access`, owner_access's, saves looking it up again.

    A retired release counts only beside a live one the caller is shown; retired releases are so few that they're
    checked on their own, which keeps the planner on a plain scan of the table.
    """
    model = REPO_TYPES[resource_type]
    owner, private = access or owner_access(kwargs, user)
    if owner is None:
        return model.objects.none()
    access = {} if private else {'public_access__in': PUBLIC_ACCESS}
    releases = model.objects.filter(is_active=True, released=True, **owner, **access).exclude(version=HEAD).filter(
        with_head(model, **access))
    org_live, user_live = repository_rows(model, is_active=True, released=True, retired=False, **access)
    retired_beside_live = releases.filter(retired=True).filter(
        Exists(org_live.exclude(version=HEAD)) | Exists(user_live.exclude(version=HEAD)))
    return releases.filter(Q(retired=False) | Q(id__in=retired_beside_live.values('id')))


def is_looked_up_as_written(url):
    """
    Whether the resolver looks a canonical URL up as it is written (resolve_reference_expression's reading of a url):
    one it takes for canonical (':'), with no |version or ?query part, that isn't an OCL path.
    """
    return bool(url) and is_canonical_uri(url) and '|' not in url and '?' not in url and to_ocl_path(url) == url


def ocl_path(uri):
    """
    A repository's OCL path as a CodeSystem (/orgs/{org}/CodeSystem/{id}/), as $expand names the system of a member
    without a canonical URL, if the resolver reads it back to the repository (`uri`), naming no version; else None.
    """
    try:
        path = IdentifierSerializer.convert_ocl_uri_to_fhir_url(uri, 'CodeSystem')
    except ValidationError:
        return None
    if is_canonical_uri(path):
        return None
    return path if to_parent_uri(to_ocl_path(path)) == uri else None


def in_namespace(head, namespace):
    """Whether a HEAD is the repository of the owner a registry entry's namespace names (get_object_from_namespace)."""
    if namespace and '/orgs/' in namespace:
        return head.organization_uri == namespace
    if namespace and '/users/' in namespace:
        return head.user_uri == namespace
    return False


def registry_entries(owner):
    """(the owner's, the global registry's) active entries, each {url: namespace}, for an owner filter: one query."""
    scope = Q(organization__isnull=True, user__isnull=True)
    if owner:
        scope |= Q(**owner)
    own, shared = {}, {}
    for url, namespace, organization_id, user_id in URLRegistry.objects.filter(is_active=True).filter(
            scope).values_list('url', 'namespace', 'organization_id', 'user_id'):
        (shared if organization_id is None and user_id is None else own)[url] = namespace
    return own, shared


def as_stored(version):
    """A version as the shared resolver must be given it to look up `version` as it is stored: it decodes what it's
    given (decode_string), so one that decoding would change is given encoded."""
    return version if decode_string(version) == version else quote_plus(version)


def served_repositories(kwargs, user):  # pylint: disable=too-many-locals
    """
    [(identifier, [(version, retired)], newest first)] for each repository a CodeSystem search at the base `kwargs`
    names shows `user` (visible_versions), under the identifier resolve() serves it at there, as it would were the
    repositories the caller isn't shown not there (so a hidden repository changes nothing here): its canonical URL (its
    HEAD's) when the resolver picks it for that URL, else its OCL path as a CodeSystem. A repository whose HEAD the
    resolver passes over (a retired one) isn't listed. The versions are those search shows: what resolve() serves by
    name. Under a canonical URL, after the repository's own, those of the other repositories with it the base shows
    that resolve() serves by name there (decision 9), the default still the repository's own.

    The resolver's pick for a canonical URL (one it looks up as written), among active HEADs that aren't retired, as
    resolve_reference_expression makes it for Sources. Without the URL registry: at /fhir/ the lowest-id HEAD the global
    base shows (shown_heads); at an owner base the owner's lowest-id one. With it: at /fhir/ the lowest-id HEAD in the
    global registry entry's namespace, none without an entry; at an owner base the owner's entry's namespace's if the
    owner has one for the URL, else the owner's lowest-id HEAD.

    A fixed number of queries: the owner and the caller's rights over it, the toggle, the registry's entries (with it
    on), the versions, the HEADs.
    """
    access = owner_access(kwargs, user)
    owner = access[0]
    if owner is None:
        return []
    registry = Toggle.get(URL_REGISTRY_IN_RESOLVE_REFERENCE_TOGGLE)
    own_entries, global_entries = registry_entries(owner) if registry else ({}, {})
    versions = {}
    for *repository, version, retired in visible_versions('CodeSystem', access=access).order_by(
            '-created_at', '-id').values_list('mnemonic', 'organization_id', 'user_id', 'version', 'retired'):
        versions.setdefault(tuple(repository), []).append((version, retired))
    if not versions:
        return []

    heads = viewable_rows(Source, namespace_of(kwargs), user).filter(version=HEAD, is_active=True, retired=False)
    if owner:  # the owner's, and where its registry entries point
        heads = heads.filter(Q(**owner) | Q(canonical_url__in=list(own_entries)))
    elif registry:  # the repositories searched, and where the global registry's entries point
        heads = heads.filter(Q(mnemonic__in={mnemonic for mnemonic, _, _ in versions}) | Q(
            canonical_url__in=list(global_entries)))
    else:
        heads = heads.filter(shown_heads(Source))
    by_url, by_repository = {}, {}  # the HEADs with each canonical URL, lowest id first; each repository's
    for row in heads.order_by('id').values_list(
            'id', 'mnemonic', 'organization_id', 'user_id', 'canonical_url', 'uri', 'organization__uri', 'user__uri'):
        head = ServedHead(*row)
        by_url.setdefault(head.canonical_url, []).append(head)
        by_repository[(head.mnemonic, head.organization_id, head.user_id)] = head

    def pick(url):
        """The HEAD the resolver picks for a canonical URL at this base."""
        candidates = by_url.get(url, [])
        if registry:
            entries = own_entries if owner else global_entries
            if url in entries:
                return next((head for head in candidates if in_namespace(head, entries[url])), None)
            if not owner:
                return None
        return next((head for head in candidates if not owner or all(
            getattr(head, field) == value for field, value in owner.items())), None)

    def held(url, head):
        """
        The releases resolve() serves named under a canonical URL it picks `head` for: the head's; then, where the
        repositories with the URL are the step that picks (no registry entry decides), each other one's the base shows
        that no earlier one holds, lowest id first (decision 9).
        """
        rows = list(versions[(head.mnemonic, head.organization_id, head.user_id)])
        if registry and (not owner or url in own_entries):
            return rows
        names = {version for version, _ in rows}
        for other in by_url[url]:
            for version, retired in versions.get((other.mnemonic, other.organization_id, other.user_id), []):
                if version not in names:
                    names.add(version)
                    rows.append((version, retired))
        return rows

    served = []
    for repository, rows in versions.items():
        head = by_repository.get(repository)
        if not head:
            continue
        url = head.canonical_url
        identifier = url if is_looked_up_as_written(url) and pick(url) == head else ocl_path(head.uri)
        if identifier:
            served.append((identifier, held(url, head) if identifier == url else rows))
    return served
