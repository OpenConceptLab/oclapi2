from collections import Counter

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Sum
from django.utils.functional import cached_property

from core.collections.models import (
    Collection, CollectionReference, Expansion, ExpansionParameters, ReferencedConcept, ReferencedMapping)
from core.common.constants import HEAD
from core.common.utils import drop_version

EXPLICIT_SOURCES = 'explicit_source_versions'
EVALUATED_SOURCES = 'evaluated_source_versions'
EXPLICIT_COLLECTIONS = 'explicit_collection_versions'
EVALUATED_COLLECTIONS = 'evaluated_collection_versions'

RULE = (
    'A reference naming a repo version records it as explicit. A resource-versioned reference records as explicit '
    'the version holding its resource version: one the expansion already records, else the one adding it picks. '
    'A version-less reference records as evaluated the version its members came from: when members sit in several '
    'versions of the repo, a version the expansion already records that holds all of them (newest first), else the '
    'newest live release holding all of them, else HEAD. A reference that no version holds entirely, or that has no '
    'members, keeps the versions the expansion records for its repo. A version both kinds of reference use is in '
    'both lists. Members are never changed.')


class ExpansionVersionLists:
    """An expansion's explicit and evaluated repo version lists, rebuilt from its references and members"""
    RELATIONS = Expansion.REPO_VERSION_RELATIONS

    def __init__(self, expansion):
        self.expansion = expansion
        self.before = {relation: list(getattr(expansion, relation).all()) for relation in self.RELATIONS}
        self.after = {relation: {} for relation in self.RELATIONS}
        self.notes = []
        self._resolved = {}

    @staticmethod
    def newest_first(versions):
        return sorted(versions, key=lambda version: (version.created_at, version.id), reverse=True)

    def recorded_versions_of(self, relations, repo_uri):
        versions = {
            version.id: version for relation in relations for version in self.before[relation]
            if drop_version(version.uri) == repo_uri
        }
        return self.newest_first(versions.values())

    @staticmethod
    def releases_then_head(repo_version):
        versions = list(repo_version.active_versions.filter(released=True, retired=False).exclude(version=HEAD))
        head = repo_version.get_head()
        return [*ExpansionVersionLists.newest_first(versions), *([head] if head else [])]

    def resolve(self, klass, expression, namespace, version=None):
        key = (klass.__name__, expression, namespace, version)
        if key not in self._resolved:
            resolved, _ = klass.resolve_reference_expression(expression, namespace, version)
            self._resolved[key] = resolved if resolved.id else None
        return self._resolved[key]

    def record(self, relation, version):
        self.after[relation][version.id] = version

    def note(self, reference, **kwargs):
        self.notes.append({'reference': reference.id, 'expression': reference.expression, **kwargs})

    def keep(self, reference, relations, repo_uri, reason):
        """Keeps what the expansion records for the reference's repo"""
        kept = []
        for relation in relations:
            for version in self.before[relation]:
                if drop_version(version.uri) == repo_uri:
                    self.record(relation, version)
                    kept.append(version.uri)
        self.note(reference, kept=sorted(set(kept)), reason=reason)

    @cached_property
    def parameter_system_versions(self):
        value = (self.expansion.parameters or {}).get(ExpansionParameters.INCLUDE_SYSTEM)
        if not value:
            return []
        namespace = Expansion.get_system_version_namespace(self.expansion.collection_version.parent_url)
        return [version for _, version in Expansion.resolve_system_version_entries(value, namespace) if version.id]

    def build(self):
        versionless = {}
        for reference in self.expansion.collection_version.references.order_by('id'):
            self.add_system_versions(reference, versionless)
            self.add_valueset_versions(reference)
        for (_, include), (repo_version, references) in versionless.items():
            self.add_versionless_system_versions(repo_version, references, include)
        return self

    def add_system_versions(self, reference, versionless):
        if not reference.system:
            return
        relation = EXPLICIT_SOURCES if self.is_explicit(reference) else EVALUATED_SOURCES
        parameter_versions = [
            version for version in self.parameter_system_versions
            if reference.can_compute_against_system_version(version)]
        if parameter_versions:
            for version in parameter_versions:
                self.record(EVALUATED_SOURCES if reference.is_versionless_expression else EXPLICIT_SOURCES, version)
            return
        from core.sources.models import Source
        version = self.resolve(Source, reference.system, reference.resolution_namespace, reference.version)
        if not version:
            self.note(reference, kept=[], reason='its system does not resolve')
        elif reference.can_resolve_to_repo_version_with_resource_version:
            self.add_resource_versioned_system_version(reference, version)
        elif relation == EXPLICIT_SOURCES:
            self.record(relation, version)
        else:
            head = version.get_head()
            key = (head.id if head else version.id, reference.include)
            versionless.setdefault(key, (version, []))[1].append(reference)

    @staticmethod
    def is_explicit(reference):
        return not reference.is_versionless_expression or reference.names_repo_version

    def add_resource_versioned_system_version(self, reference, version):
        repo_uri = drop_version(version.uri)
        holders = list(reference.get_repo_versions_with_resource_version(version))
        recorded_ids = {
            recorded.id for recorded in self.recorded_versions_of((EXPLICIT_SOURCES, EVALUATED_SOURCES), repo_uri)}
        locked = self.recorded_versions_of((EVALUATED_SOURCES,), repo_uri)
        picked = CollectionReference.pick_repo_version_with_resource_version(
            [holder for holder in holders if holder.id in recorded_ids], locked
        ) or CollectionReference.pick_repo_version_with_resource_version(holders, locked)
        if picked:
            self.record(EXPLICIT_SOURCES, picked)
        else:
            self.keep(
                reference, (EXPLICIT_SOURCES, EVALUATED_SOURCES), repo_uri, 'no version holds its resource version')

    def member_rows(self, through, field, reference_ids, include):
        rows = through.objects.filter(reference_id__in=reference_ids)
        if include:
            member_through = getattr(Expansion, f'{field}s').through
            rows = rows.filter(Exists(member_through.objects.filter(
                expansion_id=self.expansion.id, **{f'{field}_id': OuterRef(f'{field}_id')})))
        return rows

    def count_members(self, reference_ids, include, parent_id=None, version_ids=None):
        """Per reference, its members; with version_ids, per (reference, version) those that version holds"""
        counts = Counter()
        for through, field in ((ReferencedConcept, 'concept'), (ReferencedMapping, 'mapping')):
            rows = self.member_rows(through, field, reference_ids, include)
            if parent_id:
                rows = rows.filter(**{f'{field}__parent_id': parent_id})
            group = ['reference_id']
            if version_ids is not None:
                rows = rows.filter(**{f'{field}__sources__id__in': version_ids})
                group.append(f'{field}__sources__id')
            for *key, total in rows.values(*group).annotate(
                    total=Count(f'{field}_id', distinct=True)).values_list(*group, 'total'):
                counts[tuple(key) if version_ids is not None else key[0]] += total
        return counts

    def add_versionless_system_versions(self, repo_version, references, include):
        repo_uri = drop_version(repo_version.uri)
        head = repo_version.get_head()
        parent_id = head.id if head else repo_version.id
        reference_ids = [reference.id for reference in references]
        totals = self.count_members(reference_ids, include, parent_id)
        pending = []
        for reference in references:
            if not totals[reference.id]:
                self.keep(reference, (EVALUATED_SOURCES, EXPLICIT_SOURCES), repo_uri, 'it has no members')
            else:
                pending.append(reference)
        recorded = self.recorded_versions_of((EVALUATED_SOURCES, EXPLICIT_SOURCES), repo_uri)
        for candidates, chosen_by in ((recorded, 'recorded'), (self.releases_then_head(repo_version), 'release')):
            if not pending:
                break
            pending = self.pick_holders(pending, include, parent_id, totals, candidates, chosen_by)
        for reference in pending:
            self.keep(
                reference, (EVALUATED_SOURCES, EXPLICIT_SOURCES), repo_uri, 'no one version holds all its members')

    def pick_holders(self, references, include, parent_id, totals, candidates, chosen_by):  # pylint: disable=too-many-arguments
        """Records for each reference the first candidate holding all its members; returns those none holds"""
        if not candidates:
            return references
        held = self.count_members(
            [reference.id for reference in references], include, parent_id, [version.id for version in candidates])
        left = []
        for reference in references:
            holders = [version for version in candidates if held[(reference.id, version.id)] == totals[reference.id]]
            if not holders:
                left.append(reference)
                continue
            self.record(EVALUATED_SOURCES, holders[0])
            if len(holders) > 1 or chosen_by != 'recorded':
                self.note(
                    reference, recorded=holders[0].uri, holders=[holder.uri for holder in holders],
                    chosen_by='HEAD' if holders[0].is_head else chosen_by)
        return left

    def add_valueset_versions(self, reference):
        for valueset in reference.valueset if isinstance(reference.valueset, list) else []:
            if not valueset:
                continue
            version = self.resolve(Collection, valueset, reference.resolution_namespace)
            if not version:
                self.note(reference, kept=[], reason=f'value set {valueset} does not resolve')
            elif CollectionReference.is_explicit_valueset(valueset):
                self.record(EXPLICIT_COLLECTIONS, version)
            else:
                self.add_versionless_valueset_version(reference, version)

    def add_versionless_valueset_version(self, reference, version):
        repo_uri = drop_version(version.uri)
        relations = (EVALUATED_COLLECTIONS, EXPLICIT_COLLECTIONS)
        recorded = self.recorded_versions_of(relations, repo_uri)
        total = self.count_members([reference.id], reference.include)[reference.id]
        if not total:
            if recorded:
                self.keep(reference, relations, repo_uri, 'it has no members')
            else:
                self.record(EVALUATED_COLLECTIONS, version)
            return
        for candidates, chosen_by in ((recorded, 'recorded'), (self.releases_then_head(version), 'release')):
            holder = next((
                candidate for candidate in candidates if self.valueset_holds(candidate, reference, total)), None)
            if holder:
                self.record(EVALUATED_COLLECTIONS, holder)
                if chosen_by != 'recorded':
                    self.note(reference, recorded=holder.uri, chosen_by='HEAD' if holder.is_head else chosen_by)
                return
        self.keep(reference, relations, repo_uri, 'no one version holds all its members')

    def valueset_holds(self, valueset_version, reference, total):
        expansion = valueset_version.expansion
        if not expansion:
            return False
        held = 0
        for through, field in ((ReferencedConcept, 'concept'), (ReferencedMapping, 'mapping')):
            member_through = getattr(Expansion, f'{field}s').through
            held += self.member_rows(through, field, [reference.id], reference.include).filter(Exists(
                member_through.objects.filter(expansion_id=expansion.id, **{f'{field}_id': OuterRef(f'{field}_id')})
            )).values(f'{field}_id').distinct().count()
        return held == total

    def get_uris(self, lists):
        return {relation: sorted({version.uri for version in versions}) for relation, versions in lists.items()}

    @staticmethod
    def get_repo_uris(uris):
        return {drop_version(uri) for uri in uris}

    def get_member_summary(self):
        summary = {}
        for field in ('concepts', 'mappings'):
            summary[field] = getattr(self.expansion, field).aggregate(count=Count('id'), id_sum=Sum('id'))
        return summary

    def to_report(self):
        before = self.get_uris(self.before)
        after = self.get_uris({relation: versions.values() for relation, versions in self.after.items()})
        changes = {}
        for relation in self.RELATIONS:
            added = sorted(set(after[relation]) - set(before[relation]))
            removed = sorted(set(before[relation]) - set(after[relation]))
            if added or removed:
                changes[relation] = {'added': added, 'removed': removed}
        locked_before = self.get_repo_uris([*before[EVALUATED_SOURCES], *before[EVALUATED_COLLECTIONS]])
        locked_after = self.get_repo_uris([*after[EVALUATED_SOURCES], *after[EVALUATED_COLLECTIONS]])
        collection_version = self.expansion.collection_version
        return {
            'expansion': self.expansion.uri,
            'id': self.expansion.id,
            'collection_version': collection_version.uri,
            'is_default': collection_version.expansion_uri == self.expansion.uri,
            'built_with': self.expansion.built_with,
            'changed': bool(changes),
            'changes': changes,
            'gains_lock': sorted(locked_after - locked_before),
            'loses_lock': sorted(locked_before - locked_after),
            'in_both': sorted(
                (set(after[EXPLICIT_SOURCES]) & set(after[EVALUATED_SOURCES])) |
                (set(after[EXPLICIT_COLLECTIONS]) & set(after[EVALUATED_COLLECTIONS]))),
            'references': self.notes,
            'members': self.get_member_summary(),
            'before': before,
            'after': after,
        }

    def apply(self):
        """Writes the rebuilt lists, rolled back if the expansion's members changed meanwhile"""
        with transaction.atomic():
            members = self.get_member_summary()
            for relation in self.RELATIONS:
                getattr(self.expansion, relation).set(list(self.after[relation]))
            if self.get_member_summary() != members:
                raise RuntimeError(f'Members of {self.expansion.uri} changed; nothing was written')
