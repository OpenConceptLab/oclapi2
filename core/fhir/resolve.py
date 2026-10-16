"""
FHIR's canonical resolution: the resolver $resolveReference uses (ConceptContainerModel.resolve_reference_expression,
with or without the URL registry), with FHIR's rules on top.

- Resource type: CodeSystem and ConceptMap resolve to a Source, ValueSet to a Collection.
- Namespace: the base URL's. /fhir/ is global; /orgs/{org}/ and /users/{user}/ are that owner's. It comes from the
  route, never from the request.
- Version: the one named (the operation's version parameter, else url|version, else the version an OCL path names),
  HEAD included. With none named, the newest released version the caller is shown, never HEAD. A repository with
  nothing released isn't visible, whatever version is named.
- Access, after resolving: at the global base public repositories only, at an owner base what the caller may view.
  The version and its repository (HEAD) must both be viewable. Without the URL registry, the global base's canonical
  match considers only the repositories it shows, so a private copy of a canonical never hides a public one. Otherwise
  (an owner base, a registry entry) a repository the caller can't see is not found, and no other repository is tried.

Every failure is the same UnresolvedError, so it never tells a caller that a repository they can't see exists.
"""
from django.db.models import Exists, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce

from core.collections.models import Collection
from core.common.constants import HEAD, ACCESS_TYPE_VIEW, RETIRED_ACCESS_TYPE_EDIT
from core.common.mixins import SourceContainerMixin
from core.common.utils import normalize_public_access, to_parent_uri
from core.fhir.outcomes import NotFoundError
from core.sources.models import Source

REPO_TYPES = {'CodeSystem': Source, 'ConceptMap': Source, 'ValueSet': Collection}
REPO_PATHS = {'CodeSystem': 'sources', 'ConceptMap': 'sources', 'ValueSet': 'collections'}
PUBLIC_ACCESS = (ACCESS_TYPE_VIEW, RETIRED_ACCESS_TYPE_EDIT)  # what public_can_view reads as public


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


def resolve(resource_type, url, version=None, kwargs=None, user=None, expression=None):  # pylint: disable=too-many-arguments
    """
    The repository version a FHIR request names: `url` (a canonical URL, or an OCL path such as
    /orgs/{org}/CodeSystem/{id}/) and `version`, at the base `kwargs` names, for `user`. A view passes its own
    self.kwargs and self.request.user. Raises UnresolvedError, whose `expression` is the parameter the url came in.
    """
    url = url or ''
    lookup_url, _, piped_version = url.partition('|')
    named = version or piped_version or path_version(lookup_url)
    error = UnresolvedError(resource_type, lookup_url, named, expression)
    namespace = namespace_of(kwargs)
    if not lookup_url or '|' in piped_version or (
            namespace and not SourceContainerMixin.get_object_from_namespace(namespace)):
        raise error

    repo_type = REPO_TYPES[resource_type]
    instance, _ = repo_type.resolve_reference_expression(
        to_ocl_path(url), namespace, version, repo_type=repo_type, allow_head=False,
        heads=None if namespace else shown_heads(repo_type))
    if instance.id and not named and not is_shown(instance, namespace, user):
        instance = latest_public_release(instance) or instance  # the newest release is private: the newest public one

    if not instance.id or not is_shown(instance, namespace, user):
        raise error
    return instance


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
    and the resolver (resolve_repo) set it on the version they find, so it isn't looked up again.
    """
    if instance.is_head:
        return instance.public_access if instance.is_active else None
    if hasattr(instance, 'repository_public_access'):
        return instance.repository_public_access
    head = instance.head
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
    public = Q(public_access__in=PUBLIC_ACCESS)
    if namespace is None or not (user and user.is_authenticated):
        return model.objects.filter(public, with_head(model, public))
    if user.is_staff:
        return model.objects.filter(with_head(model))
    shown = public | Q(organization_id__in=user.organizations.values('id')) | Q(user_id=user.id)
    return model.objects.filter(shown, with_head(model, shown))


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
