from collections import Counter

from django.db import transaction
from django.db.models import Count, Exists, Max, OuterRef, Q, Sum
from django.utils.functional import cached_property

from core.collections.models import (
    Collection, CollectionReference, Expansion, ExpansionParameters, ReferencedConcept, ReferencedMapping)
from core.common.constants import HEAD
from core.common.utils import drop_version

EXPLICIT_SOURCES = 'explicit_source_versions'
EVALUATED_SOURCES = 'evaluated_source_versions'
EXPLICIT_COLLECTIONS = 'explicit_collection_versions'
EVALUATED_COLLECTIONS = 'evaluated_collection_versions'
MAX_SETTLE_ROUNDS = 10
MAX_PASSES = 5

RULE = (
    'A reference naming a repo version records it as explicit. A resource-versioned reference records as explicit the '
    'version holding its resource version: one the expansion already records, else the one adding it picks. A '
    'version-less reference records as evaluated the version its members came from: when members sit in several '
    'versions of the repo, a version the expansion already records that holds all of them (those in the list the '
    'reference reads first, then the others, each newest first), else the newest live release holding all of them, '
    'else HEAD. When no one version holds them all, it records the recorded versions that between them hold the most, '
    'its own list first, each adding members the earlier ones lack. A reference with no members, or none held by a '
    'recorded version, keeps what the expansion records for its repo, unless the list it reads already holds that '
    'repo. The version-less references of a repo, includes and excludes together, then settle: each moves to the '
    'newest version recorded for the repo that holds all its members, until none moves, so a second rebuild picks '
    'the same ones. The rebuild is then repeated on its own result until a repeat changes nothing, so a second rebuild '
    'changes nothing. A version both kinds of reference use is in both lists. Members are never changed.')


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
        self._visible = {}
        self._visible_filters = {}
        self.built_from = None
        self.passes = 0
        self.settled = False

    @staticmethod
    def newest_first(versions):
        return sorted(versions, key=lambda version: (version.created_at, version.id), reverse=True)

    def recorded_versions_of(self, relations, repo_uri):
        """The repo's recorded versions, list by list in the order given, each newest first"""
        versions = {}
        for relation in relations:
            for version in self.newest_first(
                    [version for version in self.before[relation] if drop_version(version.uri) == repo_uri]):
                versions.setdefault(version.id, version)
        return list(versions.values())

    def get_head(self, version):
        # Source and Collection ids come from separate tables
        key = (type(version).__name__, version.id)
        if key not in self._heads:
            self._heads[key] = version.get_head()
        return self._heads[key]

    def get_visible_filter(self, klass):
        """klass's repo versions the collection's owner can view: publicly viewable ones (their HEAD public too) and
        the owner's own. The lists are shown to anyone who can view the expansion, so none may name another's
        private one"""
        if klass not in self._visible_filters:
            version = self.expansion.collection_version
            owner = Q(organization_id=version.organization_id) if version.organization_id else Q(
                user_id=version.user_id)
            self._visible_filters[klass] = klass.get_viewable_versions_criteria(None) | owner
        return self._visible_filters[klass]

    def is_visible(self, repo_version):
        """The same rule for one version, once per version, as is_publicly_viewable reads its HEAD"""
        key = (type(repo_version).__name__, repo_version.id)
        if key not in self._visible:
            version = self.expansion.collection_version
            if version.organization_id:
                owned = repo_version.organization_id == version.organization_id
            else:
                owned = repo_version.user_id == version.user_id
            self._visible[key] = owned or repo_version.is_publicly_viewable
        return self._visible[key]

    def releases_then_head(self, repo_version):
        """Candidates beyond what the expansion records: its releases, then HEAD, that the owner can view"""
        key = (type(repo_version).__name__, drop_version(repo_version.uri))
        if key not in self._releases:
            versions = list(repo_version.active_versions.filter(
                self.get_visible_filter(type(repo_version)), released=True, retired=False).exclude(version=HEAD))
            head = self.get_head(repo_version)
            self._releases[key] = [
                *self.newest_first(versions), *([head] if head and self.is_visible(head) else [])]
        return self._releases[key]

    def get_expansion_id(self, collection_version):
        if collection_version.id not in self._expansion_ids:
            self._expansion_ids[collection_version.id] = collection_version.expansions.filter(
                uri=collection_version.expansion_uri).values_list('id', flat=True).first() \
                if collection_version.expansion_uri else None
        return self._expansion_ids[collection_version.id]

    @staticmethod
    def get_covering_versions(versions, held):
        """In the order given, each version holding a member none before it holds"""
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

    @cached_property
    def member_repos(self):
        """Per reference, the repos its members belong to, two queries for all of them"""
        repos = {}
        reference_ids = [reference.id for reference in self.references]
        for through, field in ((ReferencedConcept, 'concept'), (ReferencedMapping, 'mapping')):
            for reference_id, repo_uri in through.objects.filter(reference_id__in=reference_ids).values_list(
                    'reference_id', f'{field}__parent__uri').distinct():
                repos.setdefault(reference_id, set()).add(repo_uri)
        return repos

    def get_state(self):
        """What the rebuild is computed from, so apply can tell when it changed"""
        return {
            'lists': {
                relation: set(getattr(self.expansion, relation).values_list('id', flat=True))
                for relation in self.RELATIONS},
            'references': set(self.expansion.collection_version.references.values_list('id', flat=True)),
            'members': self.get_member_summary(),
        }

    @staticmethod
    def get_ids(lists):
        return {relation: {version.id for version in (
            versions.values() if isinstance(versions, dict) else versions)} for relation, versions in lists.items()}

    def build(self):
        """Rebuilds from the recorded lists, then from its own result, until a pass changes nothing: lists a second
        rebuild would change again aren't written. Notes are the first pass's, then each later pass's new ones"""
        self.built_from = {**self.get_state(), 'lists': self.get_ids(self.before)}
        given, notes = self.before, []
        for passes in range(1, MAX_PASSES + 1):
            self.build_pass()
            notes += self.notes if passes == 1 else [
                {**note, 'pass': passes} for note in self.notes if note not in notes]
            self.passes, self.settled = passes, self.get_ids(self.after) == self.get_ids(self.before)
            if self.settled:
                break
            self.before = {relation: list(versions.values()) for relation, versions in self.after.items()}
        self.before, self.notes = given, notes
        return self

    def build_pass(self):
        self.after = {relation: {} for relation in self.RELATIONS}
        self.notes, self.fallbacks = [], []
        versionless = {}
        for reference in self.references:
            self.add_system_versions(reference, versionless)
            self.add_valueset_versions(reference)
        repositories = {}
        for (parent_id, include), (repo_version, references) in versionless.items():
            repositories.setdefault(parent_id, []).append(
                self.add_versionless_system_versions(repo_version, references, include))
        for groups in repositories.values():
            self.settle_repository(groups)
        self.keep_fallbacks()
        return self

    def add_system_versions(self, reference, versionless):
        if not reference.system:
            return
        from core.sources.models import Source
        relation = EXPLICIT_SOURCES if self.is_explicit(reference) else EVALUATED_SOURCES
        version = self.resolve(Source, reference.system, reference.resolution_namespace, reference.version)
        if self.keep_member_repos(reference, version, relation):
            return
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
        if reference.can_resolve_to_repo_version_with_resource_version:
            self.add_resource_versioned_system_version(reference, version)
        elif relation == EXPLICIT_SOURCES:
            self.record(relation, version)
        else:
            head = self.get_head(version)
            key = (head.id if head else version.id, reference.include)
            versionless.setdefault(key, (version, []))[1].append(reference)

    def keep_member_repos(self, reference, version, relation):
        """A system that doesn't resolve today, or resolves to a repo holding none of the reference's members: the
        versions its members came from are kept as recorded, not dropped as unused. Whether it was handled"""
        repos = self.member_repos.get(reference.id, set())
        if version and (not repos or drop_version(version.uri) in repos):
            return False
        reason = 'its system resolves to a repository that holds none of its members' if version else \
            'its system does not resolve'
        if not repos:
            self.note(reference, kept=[], reason=reason)
        relations = (relation, EXPLICIT_SOURCES if relation == EVALUATED_SOURCES else EVALUATED_SOURCES)
        for repo_uri in sorted(repos):
            self.keep(reference, relations, repo_uri, reason)
        return True

    @staticmethod
    def is_explicit(reference):
        return not reference.is_versionless_expression

    def add_resource_versioned_system_version(self, reference, version):
        repo_uri = drop_version(version.uri)
        holders = list(reference.get_repo_versions_with_resource_version(version))
        recorded_ids = {
            recorded.id for recorded in self.recorded_versions_of((EXPLICIT_SOURCES, EVALUATED_SOURCES), repo_uri)}
        locked = self.recorded_versions_of((EVALUATED_SOURCES,), repo_uri)
        picked = CollectionReference.pick_repo_version_with_resource_version(
            [holder for holder in holders if holder.id in recorded_ids], locked
        ) or CollectionReference.pick_repo_version_with_resource_version(
            [holder for holder in holders if self.is_visible(holder)], locked)
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
        pending, picks = self.pick_versionless(pending, include, parent_id, totals, recorded, repo_version)
        partial = {}
        for reference in pending:
            covering = self.get_covering_versions(
                recorded, self.get_held_members(reference, include, parent_id, recorded))
            if covering:
                partial[reference.id] = covering
            else:
                self.keep(
                    reference, (EVALUATED_SOURCES, EXPLICIT_SOURCES), repo_uri, 'no one version holds all its members')
        # recorded once the repo's groups have settled together
        return {
            'repo_uri': repo_uri, 'parent_id': parent_id, 'include': include, 'totals': totals, 'picks': picks,
            'partial': partial, 'references': {reference.id: reference for reference in references}}

    def pick_versionless(self, pending, include, parent_id, totals, recorded, repo_version):  # pylint: disable=too-many-arguments
        """Each reference's version holding all its members: recorded ones first, then releases and HEAD"""
        picks = {}
        for chosen_by in ('recorded', 'release'):
            if not pending:
                break
            candidates = recorded if chosen_by == 'recorded' else self.releases_then_head(repo_version)
            pending, found = self.pick_holders(pending, include, parent_id, totals, candidates, chosen_by)
            picks.update(found)
        return pending, picks

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
        """A value set's members from several versions: recorded ones covering them go in the list it reads, else it
        keeps what is recorded"""
        for version in partial:
            self.record(relations[0], version)
        if partial:
            self.note(
                reference, partial_holders=[version.uri for version in partial],
                reason='no one version holds all its members')
        else:
            self.keep(reference, relations, repo_uri, 'no one version holds all its members')

    def pick_holders(self, references, include, parent_id, totals, candidates, chosen_by):  # pylint: disable=too-many-arguments
        """For each reference the first candidate holding all its members: (those none holds, {reference id: it})"""
        if not candidates:
            return references, {}
        held = self.count_members(
            [reference.id for reference in references], include, parent_id, [version.id for version in candidates])
        left, picks = [], {}
        for reference in references:
            holders = [version for version in candidates if held[(reference.id, version.id)] == totals[reference.id]]
            if not holders:
                left.append(reference)
                continue
            picks[reference.id] = holders[0]
            if len(holders) > 1 or chosen_by != 'recorded':
                self.note(
                    reference, recorded=holders[0].uri, holders=[holder.uri for holder in holders],
                    chosen_by='HEAD' if holders[0].is_head else chosen_by)
        return left, picks

    def settle_repository(self, groups):
        """A repo's version-less references, includes and excludes together, settle on the versions a second rebuild
        would pick: each whole holder the newest of those recorded for the repo holding all its members, each partial
        one the versions covering its members, read evaluated first. Recorded once nothing moves"""
        repo_uri, parent_id = groups[0]['repo_uri'], groups[0]['parent_id']
        recorded_evaluated = self.recorded_versions_of((EVALUATED_SOURCES,), repo_uri)
        explicit = self.newest_first(
            [version for version in self.after[EXPLICIT_SOURCES].values() if drop_version(version.uri) == repo_uri])
        picks = {key: version for group in groups for key, version in group['picks'].items()}
        coverings = {key: versions for group in groups for key, versions in group['partial'].items()}
        first_picks = dict(picks)
        for _ in range(MAX_SETTLE_ROUNDS):
            evaluated = {version.id: version for version in [*picks.values(), *sum(coverings.values(), [])]}
            whole_candidates = self.newest_first(
                {**{version.id: version for version in recorded_evaluated}, **evaluated}.values())
            partial_candidates = [
                *self.newest_first(evaluated.values()),
                *(version for version in explicit if version.id not in evaluated)]
            settled_picks, settled_coverings = {}, {}
            for group in groups:
                settled_picks.update(self.repick(group, parent_id, whole_candidates, picks))
                settled_coverings.update(self.recover(group, parent_id, partial_candidates, coverings))
            if self.version_ids(settled_picks) == self.version_ids(picks) and \
                    self.version_ids(settled_coverings) == self.version_ids(coverings):
                break
            picks, coverings = settled_picks, settled_coverings
        self.record_settled(groups, picks, coverings, first_picks)

    def recover(self, group, parent_id, candidates, coverings):
        """The group's partial holders, each on the candidates covering its members"""
        return {
            key: self.get_covering_versions(candidates, self.get_held_members(
                group['references'][key], group['include'], parent_id, candidates)) or coverings[key]
            for key in group['partial']}

    def repick(self, group, parent_id, candidates, picks):
        """The group's whole holders, each on the first candidate holding all its members"""
        if not group['picks']:
            return {}
        held = self.count_members(list(group['picks']), group['include'], parent_id, [
            version.id for version in candidates])
        totals = group['totals']
        return {
            key: next((version for version in candidates if held[(key, version.id)] == totals[key]), picks[key])
            for key in group['picks']}

    @staticmethod
    def version_ids(versions):
        """Per key its version's id, or its versions' ids: what a settling round compares"""
        return {
            key: [version.id for version in value] if isinstance(value, list) else value.id
            for key, value in versions.items()}

    def record_settled(self, groups, picks, coverings, first_picks):
        for group in groups:
            for key in group['picks']:
                self.record(EVALUATED_SOURCES, picks[key])
                if picks[key].id != first_picks[key].id:
                    # its first pick, noted earlier, moved while the repo's references settled
                    self.note(
                        group['references'][key], recorded=picks[key].uri, settled_from=first_picks[key].uri,
                        reason='settled with the other references of its repository')
            for key in group['partial']:
                for version in coverings[key]:
                    self.record(EVALUATED_SOURCES, version)
                self.note(
                    group['references'][key], partial_holders=[version.uri for version in coverings[key]],
                    reason='no one version holds all its members')

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
            if recorded or not self.is_visible(version):
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
        return sum(
            rows.values(f'{field}_id').distinct().count()
            for field, rows in self.get_rows_held_by_valueset(valueset_version, reference)) == total

    def get_held_by_valueset(self, valueset_version, reference):
        """The reference's members the value set version's default expansion holds"""
        return {
            (field, member_id) for field, rows in self.get_rows_held_by_valueset(valueset_version, reference)
            for member_id in rows.values_list(f'{field}_id', flat=True)}

    def get_rows_held_by_valueset(self, valueset_version, reference):
        expansion_id = self.get_expansion_id(valueset_version)
        if not expansion_id:
            return []
        rows = []
        for through, field in ((ReferencedConcept, 'concept'), (ReferencedMapping, 'mapping')):
            member_through = getattr(Expansion, f'{field}s').through
            rows.append((field, self.member_rows(through, field, [reference.id], reference.include).filter(Exists(
                member_through.objects.filter(expansion_id=expansion_id, **{f'{field}_id': OuterRef(f'{field}_id')})
            ))))
        return rows

    def get_uris(self, lists):
        return {relation: sorted({version.uri for version in versions}) for relation, versions in lists.items()}

    @staticmethod
    def get_locks(uris):
        """Per locked repo, its evaluated versions"""
        locks = {}
        for uri in uris:
            locks.setdefault(drop_version(uri), set()).add(uri)
        return locks

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
        locks_before = self.get_locks([*before[EVALUATED_SOURCES], *before[EVALUATED_COLLECTIONS]])
        locks_after = self.get_locks([*after[EVALUATED_SOURCES], *after[EVALUATED_COLLECTIONS]])
        locked_before, locked_after = set(locks_before), set(locks_after)
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
            'changes_lock': sorted(
                repo for repo in locked_before & locked_after if locks_before[repo] != locks_after[repo]),
            'in_both': sorted(
                (set(after[EXPLICIT_SOURCES]) & set(after[EVALUATED_SOURCES])) |
                (set(after[EXPLICIT_COLLECTIONS]) & set(after[EVALUATED_COLLECTIONS]))),
            'references': self.notes,
            'members': self.get_member_summary(),
            'collection_version_released': bool(collection_version.released),
            # passes the rebuild took to reach lists a further pass leaves as they are
            'passes': self.passes,
            'settled': self.settled,
            'is_head': collection_version.is_head,
            'reported_release_changes': self.count_reported_release_changes() if changes else 0,
            'before': before,
            'after': after,
        }

    def count_reported_release_changes(self):
        """How many concept members FHIR $expand would report from another release after the rebuild. It reports the
        live release holding the member: explicit pin first, then evaluated, then the newest (by id). $validate-code
        picks the same way among the releases the coding's system names, so it can differ only for a code system whose
        releases carry canonical URLs of their own. Judged as the collection's owner is shown, as a viewer's view can
        only narrow it"""
        before = {relation: {version.id for version in self.before[relation]} for relation in self.RELATIONS}
        after = {relation: set(self.after[relation]) for relation in self.RELATIONS}
        changed, unpinned = 0, {}
        for concept_id, source_ids in self.get_pinned_holders(before, after).items():
            was, will = self.pick_reported(source_ids, before), self.pick_reported(source_ids, after)
            if was == will:
                continue
            if was and will:
                changed += 1
            else:
                # pinned on one side only: the other reports the newest live release holding it
                unpinned[concept_id] = was or will
        return changed + self.count_unpinned_changes(unpinned)

    @cached_property
    def live_releases(self):
        from core.sources.models import Source
        return Source.objects.filter(
            self.get_visible_filter(Source), is_active=True, released=True, retired=False).exclude(version=HEAD)

    def get_pinned_holders(self, before, after):
        """Per concept member, the live releases holding it that either set of lists pins"""
        from core.concepts.models import Concept
        pinned = set().union(*(
            lists[relation] for lists in (before, after) for relation in (EXPLICIT_SOURCES, EVALUATED_SOURCES)))
        member_ids = self.expansion.concepts.through.objects.filter(expansion_id=self.expansion.id).values('concept_id')
        holders = {}
        for concept_id, source_id in Concept.sources.through.objects.filter(
                concept_id__in=member_ids, source_id__in=self.live_releases.filter(id__in=pinned).values('id')
        ).values_list('concept_id', 'source_id'):
            holders.setdefault(concept_id, set()).add(source_id)
        return holders

    @staticmethod
    def pick_reported(source_ids, lists):
        for relation in (EXPLICIT_SOURCES, EVALUATED_SOURCES):
            held = source_ids & lists[relation]
            if held:
                return max(held)
        return None

    def count_unpinned_changes(self, unpinned):
        """Of the members pinned on one side only, those whose newest live release isn't the pinned one"""
        from core.concepts.models import Concept
        if not unpinned:
            return 0
        newest = dict(Concept.sources.through.objects.filter(
            concept_id__in=list(unpinned), source_id__in=self.live_releases.values('id')
        ).values('concept_id').annotate(newest=Max('source_id')).values_list('concept_id', 'newest'))
        return sum(1 for concept_id, source_id in unpinned.items() if newest.get(concept_id) != source_id)

    def apply(self):
        """Writes the rebuilt lists; nothing, if its lists, references or members changed since build"""
        with transaction.atomic():
            if self.get_state() != self.built_from:
                raise RuntimeError(f'{self.expansion.uri} changed since it was rebuilt; nothing was written')
            for relation in self.RELATIONS:
                getattr(self.expansion, relation).set(list(self.after[relation]))
            if self.get_member_summary() != self.built_from['members']:
                raise RuntimeError(f'Members of {self.expansion.uri} changed; nothing was written')
            # health resolves references through these lists, so what's stored came from the old ones
            Expansion.mark_health_not_current([self.expansion.id])
