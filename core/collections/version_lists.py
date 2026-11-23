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
    'newest live release holding all of them, else HEAD. When no one version holds them all, it records the '
    'recorded versions that between them hold the most, newest first, each adding members the newer ones lack. A '
    'reference with no members, or none held by a recorded version, keeps what the expansion records for its repo, '
    'unless the list it reads already holds that repo. A version both kinds of reference use is in both lists. '
    'Members are never changed.')


class ExpansionVersionLists:
    """An expansion's explicit and evaluated repo version lists, rebuilt from its references and members"""
    RELATIONS = Expansion.REPO_VERSION_RELATIONS

    def __init__(self, expansion):
        self.expansion = expansion
        self.before = {relation: list(getattr(expansion, relation).all()) for relation in self.RELATIONS}
        self.after = {relation: {} for relation in self.RELATIONS}
        self.notes = []
        self.fallbacks = []
        self._resolved = {}
        self._heads = {}
        self._releases = {}
        self._expansion_ids = {}
        self.built_from = None

    @staticmethod
    def newest_first(versions):
        return sorted(versions, key=lambda version: (version.created_at, version.id), reverse=True)

    def recorded_versions_of(self, relations, repo_uri):
        versions = {
            version.id: version for relation in relations for version in self.before[relation]
            if drop_version(version.uri) == repo_uri
        }
        return self.newest_first(versions.values())

    def get_head(self, version):
        if version.id not in self._heads:
            self._heads[version.id] = version.get_head()
        return self._heads[version.id]

    def releases_then_head(self, repo_version):
        key = (type(repo_version).__name__, drop_version(repo_version.uri))
        if key not in self._releases:
            versions = list(repo_version.active_versions.filter(released=True, retired=False).exclude(version=HEAD))
            head = self.get_head(repo_version)
            self._releases[key] = [*self.newest_first(versions), *([head] if head else [])]
        return self._releases[key]

    def get_expansion_id(self, collection_version):
        if collection_version.id not in self._expansion_ids:
            self._expansion_ids[collection_version.id] = collection_version.expansions.filter(
                uri=collection_version.expansion_uri).values_list('id', flat=True).first() \
                if collection_version.expansion_uri else None
        return self._expansion_ids[collection_version.id]

    @staticmethod
    def get_covering_versions(versions, held):
        """Newest first, each version holding a member none before it holds"""
        covered = set()
        covering = []
        for version in versions:
            members = held.get(version.id, set()) - covered
            if members:
                covering.append(version)
                covered |= members
        return covering

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
        """Keeps what the expansion records for the reference's repo, once the others are done and only if none of
        them recorded a version of it"""
        self.fallbacks.append((reference, relations, repo_uri, reason))

    def keep_fallbacks(self):
        # skipped only when the list the reference reads already holds its repo
        recorded = {
            (relation, drop_version(version.uri))
            for relation, versions in self.after.items() for version in versions.values()}
        for reference, relations, repo_uri, reason in self.fallbacks:
            if (relations[0], repo_uri) in recorded:
                self.note(reference, kept=[], reason=f'{reason}; other references record its repo')
                continue
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

    @cached_property
    def references(self):
        collection_version = self.expansion.collection_version
        namespace = Expansion.get_system_version_namespace(collection_version.parent_url)
        references = list(collection_version.references.order_by('id'))
        for reference in references:
            # one collection and one toggle read for all of them, not one per reference
            reference.collection = collection_version
            if not reference.namespace:
                reference.__dict__['resolution_namespace'] = namespace
        return references

    def get_state(self):
        """What the rebuild is computed from, so apply can tell when it changed"""
        return {
            'lists': {
                relation: set(getattr(self.expansion, relation).values_list('id', flat=True))
                for relation in self.RELATIONS},
            'references': set(self.expansion.collection_version.references.values_list('id', flat=True)),
            'members': self.get_member_summary(),
        }

    def build(self):
        self.built_from = {**self.get_state(), 'lists': {
            relation: {version.id for version in versions} for relation, versions in self.before.items()}}
        versionless = {}
        for reference in self.references:
            self.add_system_versions(reference, versionless)
            self.add_valueset_versions(reference)
        for (_, include), (repo_version, references) in versionless.items():
            self.add_versionless_system_versions(repo_version, references, include)
        self.keep_fallbacks()
        return self

    def add_system_versions(self, reference, versionless):
        if not reference.system:
            return
        from core.sources.models import Source
        relation = EXPLICIT_SOURCES if self.is_explicit(reference) else EVALUATED_SOURCES
        version = self.resolve(Source, reference.system, reference.resolution_namespace, reference.version)
        parameter_versions = [
            parameter_version for parameter_version in self.parameter_system_versions
            if version and reference.can_compute_against_other_system_version and
            drop_version(parameter_version.uri) == drop_version(version.uri)
        ]
        if parameter_versions:
            for parameter_version in parameter_versions:
                self.record(
                    EVALUATED_SOURCES if reference.is_versionless_expression else EXPLICIT_SOURCES, parameter_version)
            return
        if not version:
            self.note(reference, kept=[], reason='its system does not resolve')
        elif reference.can_resolve_to_repo_version_with_resource_version:
            self.add_resource_versioned_system_version(reference, version)
        elif relation == EXPLICIT_SOURCES:
            self.record(relation, version)
        else:
            head = self.get_head(version)
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
        head = self.get_head(repo_version)
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
        for chosen_by in ('recorded', 'release'):
            if not pending:
                break
            candidates = recorded if chosen_by == 'recorded' else self.releases_then_head(repo_version)
            pending = self.pick_holders(pending, include, parent_id, totals, candidates, chosen_by)
        for reference in pending:
            self.record_partial_holders(
                reference, self.get_covering_versions(
                    recorded, self.get_held_members(reference, include, parent_id, recorded)),
                (EVALUATED_SOURCES, EXPLICIT_SOURCES), repo_uri)

    def get_held_members(self, reference, include, parent_id, versions):
        """Per version, the reference's members it holds"""
        held = {}
        for through, field in ((ReferencedConcept, 'concept'), (ReferencedMapping, 'mapping')):
            rows = self.member_rows(through, field, [reference.id], include).filter(**{
                f'{field}__parent_id': parent_id, f'{field}__sources__id__in': [version.id for version in versions]})
            for member_id, version_id in rows.values_list(f'{field}_id', f'{field}__sources__id'):
                held.setdefault(version_id, set()).add((field, member_id))
        return held

    def record_partial_holders(self, reference, partial, relations, repo_uri):
        """Members from several versions: recorded ones covering them go in the list it reads, else it keeps what
        is recorded"""
        for version in partial:
            self.record(relations[0], version)
        if partial:
            self.note(
                reference, partial_holders=[version.uri for version in partial],
                reason='no one version holds all its members')
        else:
            self.keep(reference, relations, repo_uri, 'no one version holds all its members')

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
                self.note(reference, recorded=version.uri, chosen_by='resolved', reason='it has no members')
            return
        for chosen_by in ('recorded', 'release'):
            candidates = recorded if chosen_by == 'recorded' else self.releases_then_head(version)
            holder = next((
                candidate for candidate in candidates if self.valueset_holds(candidate, reference, total)), None)
            if holder:
                self.record(EVALUATED_COLLECTIONS, holder)
                if chosen_by != 'recorded':
                    self.note(reference, recorded=holder.uri, chosen_by='HEAD' if holder.is_head else chosen_by)
                return
        self.record_partial_holders(
            reference, self.get_covering_versions(recorded, {
                holder.id: self.get_held_by_valueset(holder, reference) for holder in recorded}), relations, repo_uri)

    def valueset_holds(self, valueset_version, reference, total):
        return len(self.get_held_by_valueset(valueset_version, reference)) == total

    def get_held_by_valueset(self, valueset_version, reference):
        """The reference's members the value set version's default expansion holds"""
        expansion_id = self.get_expansion_id(valueset_version)
        if not expansion_id:
            return set()
        held = set()
        for through, field in ((ReferencedConcept, 'concept'), (ReferencedMapping, 'mapping')):
            member_through = getattr(Expansion, f'{field}s').through
            held |= {(field, member_id) for member_id in self.member_rows(
                through, field, [reference.id], reference.include).filter(Exists(member_through.objects.filter(
                    expansion_id=expansion_id, **{f'{field}_id': OuterRef(f'{field}_id')}))
            ).values_list(f'{field}_id', flat=True)}
        return held

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
        """Writes the rebuilt lists; nothing, if its lists, references or members changed since build"""
        with transaction.atomic():
            if self.get_state() != self.built_from:
                raise RuntimeError(f'{self.expansion.uri} changed since it was rebuilt; nothing was written')
            for relation in self.RELATIONS:
                getattr(self.expansion, relation).set(list(self.after[relation]))
            if self.get_member_summary() != self.built_from['members']:
                raise RuntimeError(f'Members of {self.expansion.uri} changed; nothing was written')
