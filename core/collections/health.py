"""Expansion health: the class of each member, and the expansion's state, reasons and counts.

A member is judged against the repo version each of its references resolved to, from what the
expansion already recorded. Nothing is resolved anew and no member changes.
"""
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.functional import cached_property

from core.collections.constants import REFERENCE_VALUESET_NOT_EXPANDED
from core.common.constants import HEAD
from core.common.utils import drop_version

RULES_VERSION = 1

# member classes
RELEASE = 'release'
HEAD_MEMBER = 'head'
UNRELEASED = 'unreleased'
INTERIM = 'interim'
MISMATCH = 'mismatch'
AMBIGUOUS = 'ambiguous'
UNATTRIBUTED = 'unattributed'
MEMBER_CLASSES = (RELEASE, UNRELEASED, HEAD_MEMBER, INTERIM, MISMATCH, AMBIGUOUS, UNATTRIBUTED)

# expansion states, in the order they are checked
EMPTY = 'empty'
UNHEALTHY = 'unhealthy'
HEAD_RESOLVED = 'head_resolved'
HEALTHY = 'healthy'

SYSTEM_VERSION_PARAMETER_TYPE = 'expansion.parameters.system-version'
RESOURCE_TYPES = ('concepts', 'mappings')
MEMBER_REASONS = (
    # references can't resolve to an unreleased version yet, so its owner needs to see it
    (UNRELEASED, 'unreleased_versions'),
    (INTERIM, 'interim_resource_versions'),
    (MISMATCH, 'version_mismatches'),
    (AMBIGUOUS, 'ambiguous_members'),
    (UNATTRIBUTED, 'unattributed_members'),
)


class ExpansionHealth:
    """Classifies one expansion's members in a few set-based queries per resource type"""
    RELEASED_OR_HEAD_BATCH_SIZE = 10000

    def __init__(self, expansion):
        self.expansion = expansion
        self.collection_version = expansion.collection_version
        # {include reference id: keys of the resolved repo versions its classified members came from}
        self.versions_used_by_reference = {}
        # {include reference id: the repo versions it resolved to}, filled for the references a classification needs
        self._resolved = {}
        self._pinned_versions = {}
        self._repo_versions = {}
        self._system_version_cache, self._valueset_version_cache = {}, {}

    @cached_property
    def references(self):
        from core.collections.models import Expansion
        references = list(self.collection_version.references.all())
        namespace = Expansion.get_system_version_namespace(self.collection_version.parent_url)
        for reference in references:
            if not reference.namespace:
                # one toggle read for all of them; each reference would otherwise read it on its own
                reference.__dict__['resolution_namespace'] = namespace
        return references

    @cached_property
    def include_references(self):
        return {reference.id: reference for reference in self.references if reference.include}

    def resolve(self, reference_ids):
        """{include reference id: the repo versions it resolved to} for these; resource-versioned ones are judged
        against their repository instead. Each is resolved once, so a page resolves only the references it needs"""
        for reference_id in reference_ids:
            reference = self.include_references.get(reference_id)
            if reference_id in self._resolved or not reference or reference.is_resource_versioned:
                continue
            versions = reference.get_evaluated_repo_versions(
                self.expansion, self._system_version_cache, self._valueset_version_cache)
            self._resolved[reference_id] = list({self.version_key(version): version for version in versions}.values())
        return {
            reference_id: self._resolved[reference_id]
            for reference_id in reference_ids if reference_id in self._resolved}

    @cached_property
    def resolved_versions(self):
        """{include reference id: the repo versions it resolved to}, for all of them"""
        return self.resolve(list(self.include_references))

    def get_pinned_versions(self, reference_id):
        """The active versions of a resource-versioned reference's repository its owner can view: publicly viewable
        (their HEAD public too) or the owner's own. Its members are judged by which of them hold them, as any member
        is. One query per repository, whichever version a reference names"""
        from core.sources.models import Source
        if reference_id not in self._pinned_versions:
            reference = self.include_references[reference_id]
            key = (reference.system, reference.resolution_namespace)
            if key not in self._repo_versions:
                repo_version, _ = Source.resolve_reference_expression(*key) if reference.system else (None, None)
                version = self.collection_version
                owner = Q(organization_id=version.organization_id) if version.organization_id else Q(
                    user_id=version.user_id)
                self._repo_versions[key] = list(repo_version.active_versions.filter(
                    Source.get_viewable_versions_criteria(None) | owner)) if repo_version and repo_version.id else []
            self._pinned_versions[reference_id] = self._repo_versions[key]
        return self._pinned_versions[reference_id]

    @staticmethod
    def version_key(version):
        from core.sources.models import Source
        return 'source' if isinstance(version, Source) else 'collection', version.id

    @staticmethod
    def get_resource_models(resource_type):
        from core.collections.models import ReferencedConcept, ReferencedMapping
        from core.concepts.models import Concept
        from core.mappings.models import Mapping
        if resource_type == 'concepts':
            return 'concept', Concept, ReferencedConcept
        return 'mapping', Mapping, ReferencedMapping

    def get_member_rows(self, resource_type, member_ids=None):
        members = getattr(self.expansion, resource_type).all()
        if member_ids is not None:
            members = members.filter(id__in=member_ids)
        return members

    def get_attributions(self, resource_type, members):
        """{versioned object id: ids of the include references whose resolved resources hold it}"""
        resource, _, through = self.get_resource_models(resource_type)
        rows = through.objects.filter(
            reference_id__in=list(self.include_references),
            **{f'{resource}__versioned_object_id__in': members.values('versioned_object_id')}
        ).values_list(f'{resource}__versioned_object_id', 'reference_id').distinct()
        attributions = {}
        for versioned_object_id, reference_id in rows:
            attributions.setdefault(versioned_object_id, set()).add(reference_id)
        return attributions

    def get_candidate_versions(self, reference_ids):
        """{key: version} of the repo versions these references resolved to, or whose repository they're pinned in"""
        versions = {
            self.version_key(version): version for versions in self.resolve(reference_ids).values()
            for version in versions}
        for reference_id in reference_ids:
            reference = self.include_references.get(reference_id)
            if reference and reference.is_resource_versioned:
                versions.update({
                    self.version_key(version): version for version in self.get_pinned_versions(reference_id)})
        return versions

    def get_containments(self, resource_type, members, reference_ids, pinned_member_ids):
        """{member id: keys of the repo versions these references resolved to, or are pinned in, that hold it}. A pinned
        repository's other versions are read only for the members a pinned reference judges: an unchanged concept or
        mapping version sits in every version of its source"""
        versions = self.get_candidate_versions(reference_ids)
        resolved_ids = {
            version.id for resolved in self.resolve(reference_ids).values() for version in resolved
            if self.version_key(version)[0] == 'source'}
        pinned_ids = {version_id for kind, version_id in versions if kind == 'source'} - resolved_ids \
            if pinned_member_ids else set()
        containments = {}
        self.add_source_containments(resource_type, resolved_ids, members.values('id'), containments)
        for start in range(0, len(pinned_member_ids), self.RELEASED_OR_HEAD_BATCH_SIZE):
            self.add_source_containments(
                resource_type, pinned_ids, pinned_member_ids[start:start + self.RELEASED_OR_HEAD_BATCH_SIZE],
                containments)
        self.add_value_set_containments(resource_type, members, versions, containments)
        return containments

    def add_source_containments(self, resource_type, source_ids, member_ids, containments):
        resource, klass = self.get_resource_models(resource_type)[:2]
        if source_ids:
            for member_id, source_id in klass.sources.through.objects.filter(
                    source_id__in=list(source_ids), **{f'{resource}_id__in': member_ids}
            ).values_list(f'{resource}_id', 'source_id'):
                containments.setdefault(member_id, set()).add(('source', source_id))

    def get_pinned_member_ids(self, member_rows, attributions):
        """Members some resource-versioned reference judges"""
        pinned = {reference.id for reference in self.include_references.values() if reference.is_resource_versioned}
        return [
            member_id for member_id, versioned_object_id in member_rows
            if pinned & attributions.get(versioned_object_id, set())]

    @staticmethod
    def add_value_set_containments(resource_type, members, versions, containments):
        """A value set's members are judged against that collection version's default expansion"""
        from core.collections.models import Expansion
        resource = resource_type[:-1]
        expansion_uris = {
            version.expansion_uri: version_id for (kind, version_id), version in versions.items()
            if kind == 'collection' and version.expansion_uri
        }
        if expansion_uris:
            collection_version_ids = dict(
                (expansion_id, expansion_uris[uri]) for expansion_id, uri in
                Expansion.objects.filter(uri__in=list(expansion_uris)).values_list('id', 'uri'))
            rows = getattr(Expansion, resource_type).through.objects.filter(
                expansion_id__in=list(collection_version_ids), **{f'{resource}_id__in': members.values('id')}
            ).values_list(f'{resource}_id', 'expansion_id')
            for member_id, expansion_id in rows:
                containments.setdefault(member_id, set()).add(('collection', collection_version_ids[expansion_id]))

    def get_unheld_member_ids(self, member_rows, attributions, containments):
        """Members that some reference judges by whether a release or HEAD holds them: none of its versions does"""
        unheld = []
        for member_id, versioned_object_id in member_rows:
            if member_id == versioned_object_id:
                continue
            held_keys = containments.get(member_id, set())
            if any(
                    not self.include_references[reference_id].is_resource_versioned and
                    not {self.version_key(version) for version in self._resolved.get(reference_id, [])} & held_keys
                    for reference_id in attributions.get(versioned_object_id, set())):
                unheld.append(member_id)
        return unheld

    def get_visible_source_filter(self):
        """Sources the collection's owner can view: publicly viewable ones (their HEAD public too) and the owner's own.
        Health is stored, not computed per viewer: a class must not tell a viewer about a release they couldn't see"""
        from core.sources.models import Source
        version = self.collection_version
        owner = Q(source__organization_id=version.organization_id) if version.organization_id else Q(
            source__user_id=version.user_id)
        public = Exists(Source.objects.filter(Source.get_viewable_versions_criteria(None), id=OuterRef('source_id')))
        return public | owner

    def get_released_or_head_member_ids(self, resource_type, member_ids):
        """Those of member_ids that some visible active release, or HEAD, of their source holds. Looked up by id in
        batches: with a subquery of every member the planner scans the whole membership table"""
        resource, klass, _ = self.get_resource_models(resource_type)
        held = set()
        for start in range(0, len(member_ids), self.RELEASED_OR_HEAD_BATCH_SIZE):
            held.update(klass.sources.through.objects.filter(
                (Q(source__released=True, source__is_active=True) | Q(source__version=HEAD)) &
                self.get_visible_source_filter(),
                **{f'{resource}_id__in': member_ids[start:start + self.RELEASED_OR_HEAD_BATCH_SIZE]}
            ).values_list(f'{resource}_id', flat=True).distinct())
        return held

    def is_unexpanded(self, versions):
        return bool(versions) and all(
            self.version_key(version)[0] == 'collection' and not version.expansion_uri for version in versions)

    @cached_property
    def unexpanded_value_set_reference_ids(self):
        """Include references whose every resolved version is a collection version with no default expansion:
        there is nothing to judge their members against"""
        return {
            reference_id for reference_id, versions in self.resolved_versions.items() if self.is_unexpanded(versions)}

    @cached_property
    def unexpanded_value_set_exclude_ids(self):
        """Exclude references against value sets with no default expansion: they exclude nothing"""
        return {
            reference.id for reference in self.references
            if not reference.include and not reference.system and reference.valueset and self.is_unexpanded(
                reference.get_evaluated_repo_versions(
                    self.expansion, valueset_version_cache=self._valueset_version_cache))
        }

    @cached_property
    def unexpanded_value_set_entry_reference_ids(self):
        """References naming a value set the build recorded as having no default expansion"""
        named = {
            (entry.get('url'), entry.get('namespace') or None)
            for entry in self.expansion.unresolved_repo_versions or []
            if entry.get('reason') == REFERENCE_VALUESET_NOT_EXPANDED}
        urls = {url for url, _ in named}
        return {
            reference.id for reference in self.references
            # a canonical URL can resolve differently in another namespace
            if set(reference.valueset or []) & urls and {
                (valueset, reference.resolution_namespace or None) for valueset in reference.valueset} & named}

    def judge(self, member, reference_id, containments, released_or_head_ids):  # pylint: disable=too-many-arguments
        """The class one reference gives a member (None: nothing to judge it against), and the keys of its
        resolved repo versions that hold it"""
        member_id, versioned_object_id = member
        is_pinned = self.include_references[reference_id].is_resource_versioned
        candidates = self.get_pinned_versions(reference_id) if is_pinned else self._resolved.get(reference_id, [])
        if not is_pinned and self.is_unexpanded(candidates):
            return None, set()
        holders = [
            version for version in candidates if self.version_key(version) in containments.get(member_id, set())]
        holder_keys = {self.version_key(version) for version in holders}
        if member_id == versioned_object_id:
            # a HEAD concept or mapping is never a valid member
            return MISMATCH, holder_keys
        # a HEAD carrying the legacy released flag isn't a release
        if any(version.released and version.version != HEAD for version in holders):
            return RELEASE, holder_keys
        if holders:
            # a pinned member is in HEAD if HEAD is among its repository's holders; a resolved one if all are HEAD
            in_head = (any if is_pinned else all)(version.version == HEAD for version in holders)
            return HEAD_MEMBER if in_head else UNRELEASED, holder_keys
        # a pinned member's repository versions are all candidates, so none holding it leaves it interim
        return (MISMATCH if not is_pinned and member_id in released_or_head_ids else INTERIM), holder_keys

    def classify(self, resource_type, member_ids=None):
        """{member id: (class, ids of the references that judged it, is a HEAD object)}, all members or those given.
        Only the references the members are attributed to are resolved"""
        members = self.get_member_rows(resource_type, member_ids)
        member_rows = list(members.values_list('id', 'versioned_object_id'))
        if not member_rows:
            return {}
        attributions = self.get_attributions(resource_type, members)
        containments = self.get_containments(
            resource_type, members, sorted(set().union(*attributions.values()) if attributions else set()),
            self.get_pinned_member_ids(member_rows, attributions))
        released_or_head_ids = self.get_released_or_head_member_ids(
            resource_type, self.get_unheld_member_ids(member_rows, attributions, containments))
        classes = {}
        for member in member_rows:
            reference_ids = sorted(attributions.get(member[1], set()))
            judgements = set()
            for reference_id in reference_ids:
                member_class, holder_keys = self.judge(member, reference_id, containments, released_or_head_ids)
                if member_class is None:
                    continue
                judgements.add(member_class)
                self.versions_used_by_reference.setdefault(reference_id, set()).update(holder_keys)
            if not judgements:
                member_class = UNATTRIBUTED
            elif len(judgements) > 1:
                member_class = AMBIGUOUS
            else:
                member_class = judgements.pop()
            classes[member[0]] = (member_class, reference_ids, member[0] == member[1])
        return classes

    def get_repository_versions(self):
        """{repository URL: ({ids of its recorded versions}, came from an unversioned reference)}"""
        repositories = {}
        for relation, is_evaluated in (
                ('explicit_source_versions', False), ('evaluated_source_versions', True),
                ('explicit_collection_versions', False), ('evaluated_collection_versions', True)):
            for version_id, uri in getattr(self.expansion, relation).values_list('id', 'uri'):
                version_ids, from_unversioned = repositories.get(drop_version(uri), (set(), False))
                repositories[drop_version(uri)] = (version_ids | {version_id}, from_unversioned or is_evaluated)
        return repositories

    def get_inconsistent_repositories(self):
        """Repositories whose unversioned references took their members from versions no one version is common to;
        read from the members classified, so it follows classify"""
        used_by_repository = {}
        for reference_id, versions in self.resolved_versions.items():
            reference = self.include_references[reference_id]
            # a value-set-only reference that names each value set's version is explicit, as the build records it
            if not reference.is_versionless_expression or (not reference.system and all(
                    reference.is_explicit_valueset(valueset) for valueset in reference.valueset or [] if valueset)):
                continue
            used = self.versions_used_by_reference.get(reference_id, set())
            keys_by_repository = {}
            for version in versions:
                if self.version_key(version) in used:
                    keys_by_repository.setdefault(drop_version(version.uri), set()).add(self.version_key(version))
            for repository, keys in keys_by_repository.items():
                used_by_repository.setdefault(repository, []).append(keys)
        return sorted(
            repository for repository, used_keys in used_by_repository.items()
            if len(used_keys) > 1 and not set.intersection(*used_keys))

    def get_head_resolved_repositories(self):
        return sorted({
            drop_version(version.uri) for versions in self.resolved_versions.values() for version in versions
            if version.version == HEAD
        })

    def get_deleted_locks(self):
        """How many locked versions were deleted, each once, and the URLs of those the collection's owner could see.
        Health is stored, not computed per viewer, so another owner's private version is counted, not named"""
        entries = {entry['url']: entry for entry in self.expansion.deleted_locked_repo_versions or []}
        owner_url = self.collection_version.parent_url
        named = sorted(
            url for url, entry in entries.items() if entry.get('public') or entry.get('owner_url') == owner_url)
        return len(entries), named

    def get_reasons(self, member_counts, head_counts):
        reasons = []

        def add(code, count, **extra):
            if count:
                reasons.append({'code': code, 'count': count, **extra})

        inconsistent = self.get_inconsistent_repositories()
        add('inconsistent_resolution', len(inconsistent), repositories=inconsistent)
        repositories = self.get_repository_versions()
        duplicates = sorted(
            uri for uri, (version_ids, from_unversioned) in repositories.items()
            if len(version_ids) > 1 and from_unversioned)
        add('duplicate_repo_versions', len(duplicates), repositories=duplicates)
        deleted_count, deleted_named = self.get_deleted_locks()
        add('locked_version_deleted', deleted_count, versions=deleted_named)
        add('resource_versioned_references', len(
            [reference for reference in self.references if reference.is_resource_versioned]))
        unresolved = self.expansion.unresolved_repo_versions or []
        # a value set with no expansion is counted once, as one without expansion
        add('unresolved_references', len([
            entry for entry in unresolved if entry.get('type') != SYSTEM_VERSION_PARAMETER_TYPE and
            entry.get('reason') != REFERENCE_VALUESET_NOT_EXPANDED]))
        add('value_set_without_expansion', len(
            self.unexpanded_value_set_reference_ids | self.unexpanded_value_set_exclude_ids |
            self.unexpanded_value_set_entry_reference_ids))
        add('system_version_unresolved', len(
            [entry for entry in unresolved if entry.get('type') == SYSTEM_VERSION_PARAMETER_TYPE]))
        for member_class, code in MEMBER_REASONS:
            count = sum(member_counts[resource_type][member_class] for resource_type in RESOURCE_TYPES)
            if member_class in (MISMATCH, AMBIGUOUS):
                # HEAD concepts and mappings are never valid members, so the reasons that can hold them name them
                add(code, count, head_concept=head_counts['concepts'][member_class],
                    head_mapping=head_counts['mappings'][member_class])
            else:
                add(code, count)
        multiple_named = sorted(
            uri for uri, (version_ids, from_unversioned) in repositories.items()
            if len(version_ids) > 1 and not from_unversioned)
        return reasons, multiple_named

    def compute(self):
        """The expansion's health summary; changes nothing"""
        member_counts, head_counts, total = {}, {}, 0
        for resource_type in RESOURCE_TYPES:
            classes = self.classify(resource_type)
            counts = {member_class: 0 for member_class in MEMBER_CLASSES}
            head_counts[resource_type] = {member_class: 0 for member_class in MEMBER_CLASSES}
            for member_class, _, is_head_object in classes.values():
                counts[member_class] += 1
                if is_head_object:
                    head_counts[resource_type][member_class] += 1
            member_counts[resource_type] = counts
            total += len(classes)
        reasons, multiple_named = self.get_reasons(member_counts, head_counts)
        head_resolved = self.get_head_resolved_repositories()
        if not total:
            state = EMPTY
        elif reasons:
            state = UNHEALTHY
        elif head_resolved:
            state = HEAD_RESOLVED
        else:
            state = HEALTHY
        return {
            'state': state,
            'reasons': reasons,
            'members': member_counts,
            'head_resolved_repositories': head_resolved,
            'multiple_named_versions': multiple_named,
            'rules_version': RULES_VERSION,
            'computed_at': timezone.now().isoformat(),
        }
