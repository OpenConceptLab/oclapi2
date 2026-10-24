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

FhirResolver holds these rules for one request: its base and its caller, whose rights over an owner's private
repositories it asks once per owner. FhirResolution is the shared resolver's steps under FHIR's rules.
FhirResolver.served_repositories is the set form of its choices for code systems, for TerminologyCapabilities.
"""
from collections import namedtuple
from functools import cached_property
from urllib.parse import quote_plus

from django.db.models import Case, Exists, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce

from core.collections.models import Collection
from core.common.constants import HEAD
from core.common.fhir_helpers import REPOSITORY_KINDS, to_fhir_path, to_ocl_path
from core.common.mixins import SourceContainerMixin
from core.common.models import BaseModel, ReferenceResolution
from core.common.utils import canonical_url_to_url_and_version, decode_string, is_canonical_uri, to_parent_uri
from core.fhir.outcomes import NotFoundError
from core.fhir.utils import namespace_of
from core.sources.models import Source
from core.toggles.models import Toggle
from core.url_registry.constants import URL_REGISTRY_IN_RESOLVE_REFERENCE_TOGGLE
from core.url_registry.models import URLRegistry

REPO_TYPES = {'CodeSystem': Source, 'ConceptMap': Source, 'ValueSet': Collection}
REPO_PATHS = REPOSITORY_KINDS
PUBLIC_ACCESS = BaseModel.PUBLIC_ACCESS_VALUES  # what public_can_view reads as public, as stored
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


class FhirResolution(ReferenceResolution):
    """
    The shared resolver's steps under FHIR's rules, for one resolution.
    - Only `repo_type`'s repositories (Source or Collection). A canonical URL matches the active, non-retired HEAD of
      a repository (its current canonical URL) in every step, so a registry entry whose namespace holds no such
      repository resolves to nothing, and nothing else is tried. A named version must be active.
    - No HEAD by default: with no version named, the latest release other than HEAD (even a HEAD flagged released), or
      nothing. A version named HEAD is still served.
    - `heads` (a Q on HEAD rows): without the registry, a canonical URL matches only the HEADs it holds, so a
      repository the caller isn't shown never stands in for one they are (FHIR's global base).
    - `shown` (a callable): a HEAD it refuses resolves to nothing before any version of it is read, so a repository the
      caller isn't shown costs what one that doesn't exist does.
    The version found carries its repository's (HEAD's) access and canonical URL; `head` is the HEAD it found, if any.
    """

    def __init__(self, repo_type, heads=None, shown=None):
        self.repo_type, self.heads, self.shown = repo_type, heads, shown
        self.head = None

    def repository(self, criteria):
        return self.repo_type.objects.filter(criteria).first()

    def canonical_criteria(self, criteria):
        criteria &= Q(version=HEAD)
        return criteria if self.heads is None else criteria & self.heads

    def owner_repository(self, owner, url):
        url, version = canonical_url_to_url_and_version(url)
        rows = owner.source_set if self.repo_type is Source else owner.collection_set
        return rows.filter(canonical_url=url, is_active=True, retired=False).filter(
            version=decode_string(version) if version else HEAD).first()

    def entry_repository(self, entry):
        owner = entry.namespace_owner
        return self.owner_repository(owner, entry.url) if owner else None

    def version(self, repository, version):
        if repository.is_head:
            self.head = repository
            if self.shown and not self.shown(repository):
                return None
        if version:
            found = repository.active_versions.filter(version=decode_string(version)).first()
        elif repository.is_head:
            found = repository.get_latest_released_version(exclude_head=True)
        else:
            found = repository
        if found and self.head:
            found.repository_public_access = self.head.public_access
            found.repository_canonical_url = self.head.canonical_url
        return found

    def unresolved(self):
        return self.repo_type()


class FhirResolver:
    """
    One request's FHIR base, the global /fhir/ or an owner's (`kwargs`, the route's), for its caller (`user`): what
    the base shows them, and the repository version each canonical URL or OCL path they name resolves to.
    """

    def __init__(self, kwargs=None, user=None):
        self.kwargs = kwargs or {}
        self.namespace = namespace_of(self.kwargs)
        self.user = user
        self.private_access = {}  # whether the caller may view an owner's private repositories, by owner

    @cached_property
    def owner(self):
        """The owner of an owner base (None when it doesn't exist), None at the global base."""
        return SourceContainerMixin.get_object_from_namespace(self.namespace) if self.namespace else None

    @cached_property
    def access(self):
        """
        (owner filter, may view private): ({}, False) at the global base; at an owner base, the filter on the owner's
        rows (None when the owner doesn't exist) and whether the caller may view its private repositories (staff, the
        org's members, the user: the owner's has_edit_access).
        """
        if self.namespace is None:
            return {}, False
        if not self.owner:
            return None, False
        return {'organization_id' if self.kwargs.get('org') else 'user_id': self.owner.id}, \
            self.owner.has_edit_access(self.user)

    def resolve(self, resource_type, url, version=None, expression=None):
        """
        The repository version a FHIR request names: `url` (a canonical URL, or an OCL path such as
        /orgs/{org}/CodeSystem/{id}/) and `version`. Raises UnresolvedError, whose `expression` is the parameter the
        url came in.
        """
        lookup_url, in_path, named, conflicting = self.versions_named(url, version)
        error = UnresolvedError(resource_type, lookup_url, named or in_path, expression)
        if not lookup_url or conflicting or (self.namespace and not self.owner):
            raise error
        repo_type = REPO_TYPES[resource_type]
        resolution = FhirResolution(
            repo_type, heads=None if self.namespace else shown_heads(repo_type), shown=self.is_visible)
        # the version as named (HTTP has decoded the query already), so the shared resolver, which decodes it again,
        # reads it back as it is stored: 2.0+build stays 2.0+build
        instance, entry = repo_type.resolve_reference_expression(
            to_ocl_path(lookup_url), self.namespace, as_stored(named) if named else None, resolution)
        if instance.id and not (named or in_path) and not self.is_shown(instance):
            instance = latest_public_release(instance) or instance  # the newest release is private: the newest public
        shown = bool(instance.id) and self.is_shown(instance)
        if named and self.holders_pick(instance, entry, resolution.head) and not (
                shown and (instance.is_head or instance.released)):
            instance, shown = self.holders_release(repo_type, instance, named) or (instance, shown)
        if not shown:
            raise error
        return instance

    @staticmethod
    def versions_named(url, version):
        """
        (the url without a |version, the version its OCL path names, the version named otherwise: the version
        parameter, else url|version, whether they name no version OCL holds: a second pipe, or a path naming one
        version with another named).
        """
        lookup_url, _, piped = (url or '').partition('|')
        in_path, named = path_version(lookup_url), version or piped or None
        return lookup_url, in_path, named, '|' in piped or bool(in_path and named and in_path != named)

    def holders_pick(self, instance, entry, head):
        """
        Whether the repositories with the canonical URL a request names are the step that picked `instance` (the
        shared resolver's answer, `entry` the registry entry that decided it, `head` the HEAD it found), so a version
        named is the holder's (decision 9): no entry decided, and at /fhir/ a HEAD was found (with the URL registry on,
        at /fhir/ only an entry finds one), at an owner base whether or not one was. An OCL path names its repository
        itself.
        """
        return instance.is_fqdn and entry is None and bool(self.namespace or head)

    def holders_release(self, repo_type, instance, version):
        """(the version named that its holder serves, whether it's shown) when that isn't `instance`, else None."""
        held = self.held_version(repo_type, instance.resolution_url, version)
        return (held, self.is_shown(held)) if held and held.id != instance.id else None

    def held_version(self, repo_type, url, version):
        """
        The version named `version` the base shows the caller among the repositories with the canonical URL `url` it
        shows (their active HEAD has it, isn't retired, and has a live release the caller is shown; at an owner base,
        the owner's), as resolve serves a version named when the repository the resolver picks serves no release of it:
        a release (retired or not), of the lowest-id repository holding one; else the version so named (unreleased, or
        HEAD) of the lowest-id repository; None without either. Its repository's access and canonical URL are set on it,
        as the resolver sets them. One query, whatever the number of repositories; what the caller isn't shown is left
        out in it.
        """
        shown = self.shown_q()
        owner = {}
        if self.namespace:
            owner = {'organization__uri' if self.namespace.startswith('/orgs/') else 'user__uri': self.namespace}
        same_repository = Q(organization_id=OuterRef('organization_id')) | Q(user_id=OuterRef('user_id'))
        live = repo_type.objects.filter(
            shown, same_repository, mnemonic=OuterRef('mnemonic'), is_active=True, released=True,
            retired=False).exclude(version=HEAD)
        heads = repo_type.objects.filter(
            shown, Exists(live), canonical_url=url, version=HEAD, is_active=True, retired=False, **owner)
        head = heads.filter(same_repository, mnemonic=OuterRef('mnemonic'))
        release = Q(released=True) & ~Q(version=HEAD)
        held = repo_type.objects.filter(shown, version=version, is_active=True, **owner).annotate(
            head_id=Subquery(head.values('id')[:1]),
            repository_public_access=Subquery(head.values('public_access')[:1]),
        ).filter(release | Q(head_id=Subquery(heads.order_by('id').values('id')[:1])), head_id__isnull=False).order_by(
            Case(When(release, then=Value(0)), default=Value(1)), 'head_id').first()
        if held:
            held.is_fqdn, held.resolution_url, held.repository_canonical_url = True, url, url
            held.canonical_url = held.canonical_url or url
        return held

    def is_shown(self, instance):
        """Whether a base shows the caller a repository version: it is visible there, and so is a release of it."""
        return self.is_visible(instance) and self.has_release(instance)

    def is_visible(self, instance):
        """
        Whether a base shows a repository version: the version and its repository (its active HEAD, which governs access
        to the repository's concepts and mappings) must both be viewable there. At the global base both are public, to
        anyone; at an owner base the same, or any of the owner's versions to those who may view its private repositories
        (staff, the org's members, the user).
        """
        access = instance.repository_public_access
        if access is None:
            return False
        return (instance.public_can_view and BaseModel.is_public_access(access)) or self.may_view_private(instance)

    def has_release(self, instance):
        """
        Whether the repository of a version has a released version that isn't retired, and that the base shows the
        caller: a public one, or any to those who may view the owner's private repositories. HEAD's released flag isn't
        one. (The repository's own access, HEAD's, is is_visible's to check.)
        """
        if instance.released and not instance.retired and not instance.is_head and (
                instance.public_can_view or self.may_view_private(instance)):
            return True
        live = instance.released_versions.filter(retired=False).exclude(version=HEAD)
        if not self.may_view_private(instance):
            live = live.filter(public_access__in=PUBLIC_ACCESS)
        return live.exists()

    def may_view_private(self, instance):
        """At an owner base, whether the caller may view the private repositories of a version's owner."""
        if self.namespace is None or not (self.user and self.user.is_authenticated):
            return False
        owner = (instance.organization_id, instance.user_id)
        if owner not in self.private_access:
            self.private_access[owner] = instance.has_parent_edit_access(self.user)
        return self.private_access[owner]

    def shown_q(self):
        """
        Q for the rows viewable_rows requires of a row and of its HEAD: public; at an owner base also, to those signed
        in, their own by their rights (everything to staff, their orgs' and their own).
        """
        public = Q(public_access__in=PUBLIC_ACCESS)
        if self.namespace is None or not (self.user and self.user.is_authenticated):
            return public
        if self.user.is_staff:
            return Q()
        return public | Q(organization_id__in=self.user.organizations.values('id')) | Q(user_id=self.user.id)

    def viewable_rows(self, model):
        """
        The queryset form of is_visible(row) over every row of `model`, HEAD and versions alike: anywhere a public row
        of a public repository (active HEAD); at an owner base also, whoever owns them, the rows `user` may view by
        their own rights (all to staff, their orgs' and their own), with an active HEAD. For what a version refers to (a
        mapping's source, the code systems an expansion drew on), so only repositories the caller is shown match.

        A row and its HEAD share their owner, so "both public, or the caller's own" is "the row public or the caller's
        own, and its HEAD public or the caller's own": one EXISTS, which the planner runs as a semi-join.
        """
        shown = self.shown_q()
        return model.objects.filter(shown, with_head(model, shown))

    def visible_versions(self, resource_type):
        """
        The queryset form of is_visible and has_release, for search: the active released versions, never HEAD, that the
        base shows the caller. A row is in it exactly when is_visible(row) and has_release(row): at the global base
        public versions of public repositories (HEAD), whoever asks; at an owner base the owner's, private ones only to
        those who may view them (staff, the org's members, the user). An owner base whose owner doesn't exist shows
        nothing.

        A retired release counts only beside a live one the caller is shown; retired releases are so few that they're
        checked on their own, which keeps the planner on a plain scan of the table.
        """
        model = REPO_TYPES[resource_type]
        owner, private = self.access
        if owner is None:
            return model.objects.none()
        access = {} if private else {'public_access__in': PUBLIC_ACCESS}
        releases = model.objects.filter(is_active=True, released=True, **owner, **access).exclude(version=HEAD).filter(
            with_head(model, **access))
        org_live, user_live = repository_rows(model, is_active=True, released=True, retired=False, **access)
        retired_beside_live = releases.filter(retired=True).filter(
            Exists(org_live.exclude(version=HEAD)) | Exists(user_live.exclude(version=HEAD)))
        return releases.filter(Q(retired=False) | Q(id__in=retired_beside_live.values('id')))

    def served_repositories(self):  # pylint: disable=too-many-locals
        """
        [(identifier, [(version, retired)], newest first)] for each repository a CodeSystem search at the base `kwargs`
        names shows `user` (visible_versions), under the identifier resolve() serves it at there, as it would were the
        repositories the caller isn't shown not there (so a hidden repository changes nothing here): its canonical URL
        (its HEAD's) when the resolver picks it for that URL, else its OCL path as a CodeSystem. A repository whose HEAD
        the resolver passes over (a retired one) isn't listed. The versions are those search shows: what resolve()
        serves by name. Under a canonical URL, after the repository's own, those of the other repositories with it the
        base shows that resolve() serves by name there (decision 9), the default still the repository's own.

        The resolver's pick for a canonical URL (one it looks up as written), among active HEADs that aren't retired, as
        resolve_reference_expression makes it for Sources. Without the URL registry: at /fhir/ the lowest-id HEAD the
        global base shows (shown_heads); at an owner base the owner's lowest-id one. With it: at /fhir/ the lowest-id
        HEAD in the global registry entry's namespace, none without an entry; at an owner base the owner's entry's
        namespace's if the owner has one for the URL, else the owner's lowest-id HEAD.

        A fixed number of queries: the owner and the caller's rights over it, the toggle, the registry's entries (with
        it on), the versions, the HEADs.
        """
        owner = self.access[0]
        if owner is None:
            return []
        registry = Toggle.get(URL_REGISTRY_IN_RESOLVE_REFERENCE_TOGGLE)
        own_entries, global_entries = registry_entries(owner) if registry else ({}, {})
        versions = {}
        for *repository, version, retired in self.visible_versions('CodeSystem').order_by(
                '-created_at', '-id').values_list('mnemonic', 'organization_id', 'user_id', 'version', 'retired'):
            versions.setdefault(tuple(repository), []).append((version, retired))
        if not versions:
            return []

        heads = self.viewable_rows(Source).filter(version=HEAD, is_active=True, retired=False)
        if owner:  # the owner's, and where its registry entries point
            heads = heads.filter(Q(**owner) | Q(canonical_url__in=list(own_entries)))
        elif registry:  # the repositories searched, and where the global registry's entries point
            heads = heads.filter(Q(mnemonic__in={mnemonic for mnemonic, _, _ in versions}) | Q(
                canonical_url__in=list(global_entries)))
        else:
            heads = heads.filter(shown_heads(Source))
        by_url, by_repository = {}, {}  # the HEADs with each canonical URL, lowest id first; each repository's
        for row in heads.order_by('id').values_list(*ServedHead._fields[:6], 'organization__uri', 'user__uri'):
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
            repositories with the URL are the step that picks (no registry entry decides), each other one's the base
            shows that no earlier one holds, lowest id first (decision 9).
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


def path_version(url):
    """The version an OCL path names (/orgs/{org}/sources/{id}/{version}/, or as a CodeSystem path), or None."""
    parts = to_parent_uri(to_ocl_path(url)).split('/')
    if len(parts) > 5 and not parts[0] and parts[1] in ('orgs', 'users') and parts[3] in REPO_PATHS.values():
        return parts[5] or None
    return None


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
        latest.repository_public_access = instance.repository_public_access
        latest.repository_canonical_url = instance.repository_canonical_url
        latest.is_fqdn, latest.resolution_url = instance.is_fqdn, instance.resolution_url
        if instance.is_fqdn and not latest.canonical_url:
            latest.canonical_url = instance.resolution_url
    return latest


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
    path = to_fhir_path(uri, 'CodeSystem')
    if path is None or is_canonical_uri(path):
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
