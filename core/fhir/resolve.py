"""
FHIR's canonical resolution: the resolver $resolveReference uses (ConceptContainerModel.resolve_reference_expression,
with or without the URL registry), with FHIR's rules on top.

- Resource type: CodeSystem and ConceptMap resolve to a Source, ValueSet to a Collection.
- Namespace: the base URL's. /fhir/ is global; /orgs/{org}/ and /users/{user}/ are that owner's. It comes from the
  route, never from the request.
- Version: the one named (the operation's version parameter, else url|version), HEAD included. With none named, the
  latest released version, never HEAD. A repository with nothing released isn't visible, whatever version is named.
- Access, after resolving: at the global base public repositories only, at an owner base what the caller may view.
  A repository the caller can't see is not found, and no other repository is tried.

Every failure is the same UnresolvedError, so it never tells a caller that a repository they can't see exists.
"""
from django.db.models import Exists, OuterRef, Q

from core.collections.models import Collection
from core.common.constants import HEAD, ACCESS_TYPE_VIEW, RETIRED_ACCESS_TYPE_EDIT
from core.common.mixins import SourceContainerMixin
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
    error = UnresolvedError(resource_type, lookup_url, version or piped_version or None, expression)
    namespace = namespace_of(kwargs)
    if not lookup_url or '|' in piped_version or (
            namespace and not SourceContainerMixin.get_object_from_namespace(namespace)):
        raise error

    repo_type = REPO_TYPES[resource_type]
    instance, _ = repo_type.resolve_reference_expression(
        to_ocl_path(url), namespace, version, repo_type=repo_type, allow_head=False)

    if not instance.id or not is_visible(instance, namespace, user) or not has_release(instance):
        raise error
    return instance


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
    """At the global base, public repositories to anyone; at an owner base, what the user may view."""
    return instance.public_can_view if namespace is None else instance.has_view_access(user)


def has_release(instance):
    """Whether the repository of a version has a released version that isn't retired; HEAD's released flag isn't one"""
    if instance.released and not instance.retired and not instance.is_head:
        return True
    return instance.released_versions.filter(retired=False).exclude(version=HEAD).exists()


def repository_rows(model, **filters):
    """
    For a subquery: the rows of the outer row's repository (same mnemonic and owner) matching `filters`, as two
    querysets, one for an org-owned outer row and one for a user-owned one (an owner column is NULL on the other).
    """
    rows = model.objects.filter(mnemonic=OuterRef('mnemonic'), **filters)
    return (rows.filter(organization_id=OuterRef('organization_id'), user_id__isnull=True),
            rows.filter(user_id=OuterRef('user_id'), organization_id__isnull=True))


def visible_versions(resource_type, kwargs=None, user=None):
    """
    The queryset form of is_visible and has_release, for search: the active released versions, never HEAD, that the
    base `kwargs` names shows `user`. A row is in it exactly when is_visible(row, namespace, user) and has_release(row):
    at the global base public ones, whoever asks; at an owner base the owner's, private ones only to those who may view
    them (staff, the org's members, the user). An owner base whose owner doesn't exist shows nothing.

    A retired release counts only beside a live one; retired releases are so few that they're checked on their own,
    which keeps the planner on a plain scan of the table.
    """
    model = REPO_TYPES[resource_type]
    releases = model.objects.filter(is_active=True, released=True).exclude(version=HEAD)
    org_live, user_live = repository_rows(model, is_active=True, released=True, retired=False)
    retired_beside_live = releases.filter(retired=True).filter(
        Exists(org_live.exclude(version=HEAD)) | Exists(user_live.exclude(version=HEAD)))
    versions = releases.filter(Q(retired=False) | Q(id__in=retired_beside_live.values('id')))
    namespace = namespace_of(kwargs)
    if namespace is None:
        return versions.filter(public_access__in=PUBLIC_ACCESS)
    owner = SourceContainerMixin.get_object_from_namespace(namespace)
    if not owner:
        return versions.none()
    is_org = bool(kwargs.get('org'))
    versions = versions.filter(**{'organization_id' if is_org else 'user_id': owner.id})
    may_view_private = bool(user and user.is_authenticated) and (
        user.is_staff or (owner.is_member(user) if is_org else owner.id == user.id))
    return versions if may_view_private else versions.filter(public_access__in=PUBLIC_ACCESS)
