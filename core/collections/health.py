"""Expansion health: the class of each member, and the expansion's state, reasons and counts.

A member is judged against the repo version each of its references resolved to, from what the
expansion already recorded. Nothing is resolved anew and no member changes.
"""
from django.db.models import Q
from django.utils import timezone
from django.utils.functional import cached_property

from core.common.constants import HEAD
from core.common.utils import drop_version

RULES_VERSION = 1

# member classes
RELEASE = 'release'
HEAD_MEMBER = 'head'
RESOURCE_VERSIONED = 'resource_versioned'
INTERIM = 'interim'
MISMATCH = 'mismatch'
AMBIGUOUS = 'ambiguous'
UNATTRIBUTED = 'unattributed'
MEMBER_CLASSES = (RELEASE, HEAD_MEMBER, RESOURCE_VERSIONED, INTERIM, MISMATCH, AMBIGUOUS, UNATTRIBUTED)

# expansion states, in the order they are checked
EMPTY = 'empty'
UNHEALTHY = 'unhealthy'
HEAD_RESOLVED = 'head_resolved'
HEALTHY = 'healthy'

SYSTEM_VERSION_PARAMETER_TYPE = 'expansion.parameters.system-version'
RESOURCE_TYPES = ('concepts', 'mappings')
MEMBER_REASONS = (
    (INTERIM, 'interim_resource_versions'),
    (MISMATCH, 'version_mismatches'),
    (AMBIGUOUS, 'ambiguous_members'),
    (UNATTRIBUTED, 'unattributed_members'),
)


class ExpansionHealth:
    """Classifies one expansion's members in a few set-based queries per resource type"""

    def __init__(self, expansion):
        self.expansion = expansion
        self.collection_version = expansion.collection_version

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

    @cached_property
    def resolved_versions(self):
        """{include reference id: the repo versions it resolved to}; resource-versioned ones are judged without"""
        system_version_cache, valueset_version_cache = {}, {}
        resolved = {}
        for reference in self.include_references.values():
            if reference.is_resource_versioned:
                continue
            versions = reference.get_evaluated_repo_versions(
                self.expansion, system_version_cache, valueset_version_cache)
            resolved[reference.id] = list({self.version_key(version): version for version in versions}.values())
        return resolved

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

    def get_containments(self, resource_type, members):
        """{member id: keys of the resolved repo versions that hold it}"""
        from core.collections.models import Expansion
        resource, klass, _ = self.get_resource_models(resource_type)
        versions = {
            self.version_key(version): version for versions in self.resolved_versions.values() for version in versions}
        source_ids = [version_id for kind, version_id in versions if kind == 'source']
        containments = {}
        if source_ids:
            rows = klass.sources.through.objects.filter(
                source_id__in=source_ids, **{f'{resource}_id__in': members.values('id')}
            ).values_list(f'{resource}_id', 'source_id')
            for member_id, source_id in rows:
                containments.setdefault(member_id, set()).add(('source', source_id))
        # a value set's members are judged against that collection version's default expansion
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
        return containments

    def get_released_or_head_member_ids(self, resource_type, members):
        """Ids of members that some active release, or HEAD, of their source holds"""
        resource, klass, _ = self.get_resource_models(resource_type)
        return set(klass.sources.through.objects.filter(
            Q(source__released=True, source__is_active=True) | Q(source__version=HEAD),
            **{f'{resource}_id__in': members.values('id')}
        ).values_list(f'{resource}_id', flat=True).distinct())

    def judge(self, member, reference_id, containments, released_or_head_ids):  # pylint: disable=too-many-arguments
        """The class one reference gives a member"""
        member_id, versioned_object_id = member
        if self.include_references[reference_id].is_resource_versioned:
            return RESOURCE_VERSIONED
        if member_id == versioned_object_id:
            # a HEAD concept or mapping is never a valid member
            return MISMATCH
        holders = [
            version for version in self.resolved_versions.get(reference_id, [])
            if self.version_key(version) in containments.get(member_id, set())
        ]
        if holders:
            return HEAD_MEMBER if all(version.version == HEAD for version in holders) else RELEASE
        return MISMATCH if member_id in released_or_head_ids else INTERIM

    def classify(self, resource_type, member_ids=None):
        """{member id: (class, ids of the references that judged it, is a HEAD object)}, all members or those given"""
        members = self.get_member_rows(resource_type, member_ids)
        attributions = self.get_attributions(resource_type, members)
        containments = self.get_containments(resource_type, members)
        released_or_head_ids = self.get_released_or_head_member_ids(resource_type, members)
        classes = {}
        for member in members.values_list('id', 'versioned_object_id'):
            reference_ids = sorted(attributions.get(member[1], set()))
            judgements = {
                self.judge(member, reference_id, containments, released_or_head_ids) for reference_id in reference_ids}
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
        """Repositories that unversioned references resolved to more than one version of"""
        repositories = {}
        for reference_id, versions in self.resolved_versions.items():
            if self.include_references[reference_id].is_versionless_expression:
                for version in versions:
                    repositories.setdefault(drop_version(version.uri), set()).add(self.version_key(version))
        return sorted(repository for repository, keys in repositories.items() if len(keys) > 1)

    def get_head_resolved_repositories(self):
        return sorted({
            drop_version(version.uri) for versions in self.resolved_versions.values() for version in versions
            if version.version == HEAD
        })

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
        add('resource_versioned_references', len(
            [reference for reference in self.references if reference.is_resource_versioned]))
        unresolved = self.expansion.unresolved_repo_versions or []
        add('unresolved_references', len(
            [entry for entry in unresolved if entry.get('type') != SYSTEM_VERSION_PARAMETER_TYPE]))
        add('system_version_unresolved', len(
            [entry for entry in unresolved if entry.get('type') == SYSTEM_VERSION_PARAMETER_TYPE]))
        for member_class, code in MEMBER_REASONS:
            count = sum(member_counts[resource_type][member_class] for resource_type in RESOURCE_TYPES)
            if member_class == MISMATCH:
                add(code, count, head_concept=head_counts['concepts'], head_mapping=head_counts['mappings'])
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
            head_counts[resource_type] = 0
            for member_class, _, is_head_object in classes.values():
                counts[member_class] += 1
                if member_class == MISMATCH and is_head_object:
                    head_counts[resource_type] += 1
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
