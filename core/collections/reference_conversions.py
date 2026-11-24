from django.db import transaction

from core.collections.models import CollectionReference, Expansion, ReferencedConcept, ReferencedMapping
from core.common.utils import drop_version, canonical_url_to_url_and_version, is_canonical_uri

REPO_VERSIONED = 'repo_versioned'
UNVERSIONED = 'unversioned'
CONVERSIONS = (REPO_VERSIONED, UNVERSIONED)
MAX_REFERENCES = 500
MAX_MEMBERS_LISTED = 25


class ConversionFailed(Exception):
    def __init__(self, errors):
        super().__init__('conversion failed')
        self.errors = errors


class ReferenceConversions:
    """What converting a HEAD collection's resource-versioned references to repo-versioned or unversioned ones would
    bring in, and the conversion itself. Only HEAD is changed; released collection versions stay as they are"""

    def __init__(self, collection, user):
        self.collection = collection
        # converted references are evaluated as the requester, as the conversion adds them
        self.user = user
        self.expansion = collection.expansion
        self.namespace = Expansion.get_system_version_namespace(collection.parent_url)
        self._holders = {}
        self._resolved = {}
        self._visible = {}

    @staticmethod
    def get_candidates(collection):
        return collection.references.exclude(resource_version__isnull=True).exclude(resource_version='').order_by('id')

    def prime(self, reference):
        # one collection and one toggle read for all of them, not one per reference
        reference.collection = self.collection
        if not reference.namespace:
            reference.__dict__['resolution_namespace'] = self.namespace
        # a legacy reference with no system resolves to nothing, as resolve_expression_system_version does
        system_version = self.resolve(
            reference.system, reference.resolution_namespace, reference.version) if reference.system else None
        reference.__dict__['resolve_expression_system_version'] = system_version
        return reference

    def resolve(self, system, namespace, version):
        from core.sources.models import Source
        key = (system, namespace, version)
        if key not in self._resolved:
            resolved, _ = Source.resolve_reference_expression(system, namespace, version)
            self._resolved[key] = resolved if resolved.id else None
        return self._resolved[key]

    @staticmethod
    def get_unsupported_reason(reference):
        if reference.code and reference.resource_version and not reference.system:
            return 'it has no system'
        if not reference.is_resource_versioned:
            return 'not resource-versioned'
        if reference.valueset:
            return 'it also names a value set'
        if reference.filter:
            return 'it has a filter'
        # unpinned, an exclusion removes every version of the concept, which the preview can't judge yet
        if not reference.include:
            return 'it is an exclusion'
        return None

    @staticmethod
    def get_members(references):
        """Per reference, what it holds now"""
        members = {reference.id: set() for reference in references}
        for through, field in ((ReferencedConcept, 'concept'), (ReferencedMapping, 'mapping')):
            for reference_id, member_id in through.objects.filter(
                    reference_id__in=list(members)).values_list('reference_id', f'{field}_id'):
                members[reference_id].add((field, member_id))
        return members

    def get_recorded_versions(self, repo_version):
        """The expansion's explicit and evaluated (locked) versions of repo_version's repository"""
        if not self.expansion or not repo_version:
            return [], []
        repo_uri = drop_version(repo_version.uri)
        return tuple(
            [version for version in versions if drop_version(version.uri) == repo_uri]
            for versions in (self.expansion.recorded_explicit_source_versions,
                             self.expansion.recorded_evaluated_source_versions))

    def get_holders(self, reference, repo_version, references):
        """The active versions of the repo holding the reference's resource version, newest first; looked up for all
        the references of that repo at once"""
        cache = self._holders.setdefault(repo_version.id, {})
        key = reference.resource_version_key
        if key not in cache:
            batch = [
                other for other in references
                if other.is_resource_versioned and other.resource_version_key not in cache and
                get_id(other.resolve_expression_system_version) == repo_version.id]
            found = CollectionReference.get_repo_versions_holding_resource_versions([*batch, reference], repo_version)
            for other in [*batch, reference]:
                cache[other.resource_version_key] = found.get(other.resource_version_key, [])
        return cache[key]

    def pick_repo_version(self, reference, repo_version, references):
        """The version a repo-versioned conversion names: one the expansion records that holds the resource version,
        else the one adding the reference picks (the lock, then the newest release, then HEAD) among those the
        collection's owner can view, so a conversion never pins it to another owner's private version"""
        if reference.names_repo_version:
            return repo_version, 'named'
        holders = self.get_holders(reference, repo_version, references)
        explicit, evaluated = self.get_recorded_versions(repo_version)
        recorded_ids = {version.id for version in [*explicit, *evaluated]}
        picked = CollectionReference.pick_repo_version_with_resource_version(
            [holder for holder in holders if holder.id in recorded_ids], evaluated)
        if picked:
            return picked, 'recorded'
        picked = CollectionReference.pick_repo_version_with_resource_version(
            [holder for holder in holders if self.is_visible(holder)], evaluated)
        return picked, self.get_chosen_by(picked, evaluated) if picked else None

    def is_visible(self, repo_version):
        """Whether the collection's owner can view it: a publicly viewable version (its HEAD public too) or the
        owner's own. Once per version, as is_publicly_viewable reads the HEAD"""
        if repo_version.id not in self._visible:
            if self.collection.organization_id:
                owned = repo_version.organization_id == self.collection.organization_id
            else:
                owned = repo_version.user_id == self.collection.user_id
            self._visible[repo_version.id] = owned or repo_version.is_publicly_viewable
        return self._visible[repo_version.id]

    @staticmethod
    def get_chosen_by(version, locked):
        if version.id in {locked_version.id for locked_version in locked}:
            return 'lock'
        if version.is_head:
            return 'HEAD'
        return 'release' if version.released else 'latest'

    @staticmethod
    def get_unversioned_system(system):
        if is_canonical_uri(system):
            return canonical_url_to_url_and_version(system)[0]
        return drop_version(system if system.endswith('/') else system + '/')

    def build(self, reference, version=None):
        """The reference a conversion makes: the same options, naming version or none, and no resource version"""
        converted = CollectionReference(
            reference_type=reference.reference_type, created_by=self.user, filter=reference.filter,
            display=reference.display, namespace=reference.namespace, code=reference.code,
            system=self.get_unversioned_system(reference.system), version=version.version if version else None,
            valueset=reference.valueset, cascade=reference.cascade, include=reference.include,
            collection=self.collection)
        converted.expression = converted.build_expression()
        if not converted.namespace:
            converted.__dict__['resolution_namespace'] = self.namespace
        converted.__dict__['resolve_expression_system_version'] = version or self.resolve(
            converted.system, converted.resolution_namespace, None)
        return converted

    def get_conversion(self, reference, conversion, references):
        """(the converted reference, the version it resolves to, how that was chosen), or None if it can't be made"""
        repo_version = reference.resolve_expression_system_version
        if not repo_version:
            return None
        if conversion == REPO_VERSIONED:
            picked, chosen_by = self.pick_repo_version(reference, repo_version, references)
            return (self.build(reference, picked), picked, chosen_by) if picked else None
        converted = self.build(reference)
        # as adding it resolves: under the collection's lock, else the latest release
        target = converted.resolve_system_version
        # default resolution doesn't check access: a version the requester can't view can't be offered
        if not target or not target.has_view_access(self.user):
            return None
        return converted, target, self.get_chosen_by(target, self.get_recorded_versions(target)[1])

    @staticmethod
    def evaluate(reference, version):
        """What the reference would bring in from version, as adding it evaluates it"""
        if reference.is_mapping:
            mappings = reference.get_mappings(version)
            return {('mapping', mapping_id) for mapping_id in mappings.values_list('id', flat=True)}
        concepts, mappings = reference.get_concepts(version)
        members = {('concept', member_id) for member_id in concepts.values_list('id', flat=True)}
        if reference.cascade:
            members |= {('mapping', member_id) for member_id in mappings.values_list('id', flat=True)}
        return members

    def preview(self, references):
        references = [self.prime(reference) for reference in references]
        members = self.get_members(references)
        results = [self.get_preview_item(reference, members[reference.id], references) for reference in references]
        self.to_urls(results)
        supported = [item for item in results if item['supported']]
        summary = {'references': len(results), 'supported': len(supported)}
        for conversion in CONVERSIONS:
            summary[f'{conversion}_keeps_all'] = bool(supported) and all(
                item[conversion] and item[conversion]['keeps_members'] for item in supported)
        return {'summary': summary, 'results': results}

    def get_preview_item(self, reference, now, references):
        item = {'id': reference.id, 'expression': reference.expression, 'exclude': not reference.include}
        reason = self.get_unsupported_reason(reference)
        if reason:
            return {**item, 'supported': False, 'reason': reason}
        item.update({'supported': True, 'members_count': len(now), 'members': now})
        for conversion in CONVERSIONS:
            result = self.get_conversion(reference, conversion, references)
            if not result:
                item[conversion] = None
                continue
            converted, version, chosen_by = result
            would = self.evaluate(converted, version)
            item[conversion] = {
                'expression': converted.expression, 'repo_version': version.uri, 'chosen_by': chosen_by,
                'keeps_members': would == now, 'members_count': len(would), 'members': would,
                'added_count': len(would - now), 'added': would - now,
                'removed_count': len(now - would), 'removed': now - would}
            if conversion == UNVERSIONED:
                # adding a version-less reference records the version it resolved to as the collection's lock
                item[conversion]['creates_lock'] = chosen_by != 'lock'
        return item

    @staticmethod
    def to_urls(results):
        """Replaces the member ids in results with their URLs, two queries for all of them. Each list names at most
        MAX_MEMBERS_LISTED; its count says how many there are"""
        from core.concepts.models import Concept
        from core.mappings.models import Mapping
        member_sets = []
        for item in results:
            if item['supported']:
                member_sets.append(item)
                member_sets += [item[conversion] for conversion in CONVERSIONS if item[conversion]]
        keys = ('members', 'added', 'removed')
        ids = {'concept': set(), 'mapping': set()}
        for holder in member_sets:
            for key in keys:
                if key in holder:
                    holder[key] = sorted(holder[key])[:MAX_MEMBERS_LISTED]
                    for field, member_id in holder[key]:
                        ids[field].add(member_id)
        urls = {}
        for field, klass in (('concept', Concept), ('mapping', Mapping)):
            if ids[field]:
                urls.update({
                    (field, member_id): uri
                    for member_id, uri in klass.objects.filter(id__in=ids[field]).values_list('id', 'uri')})
        for holder in member_sets:
            for key in keys:
                if key in holder:
                    holder[key] = sorted(urls[member] for member in holder[key] if member in urls)

    def plan(self, conversions):
        """Per conversion its result so far, the references that go, and the new ones by expression"""
        ids = [item['id'] for item in conversions]
        references = {
            reference.id: self.prime(reference) for reference in self.collection.references.filter(id__in=ids)}
        all_references = list(references.values())
        results, old_ids, new_references = [], [], {}
        for item in conversions:
            reference = references.get(item['id'])
            result = {'id': item['id'], 'to': item['to']}
            reason = self.get_unsupported_reason(reference) if reference else 'not in this collection'
            conversion = None if reason else self.get_conversion(reference, item['to'], all_references)
            if not reason and not conversion:
                reason = 'no version holds its resource version' if item['to'] == REPO_VERSIONED else \
                    'its system does not resolve to a version you can view'
            if not reason and item.get('repo_version') and item['repo_version'] != conversion[1].uri:  # pylint: disable=unsubscriptable-object
                # the preview showed another version: a release was published, or a lock changed, since
                reason = 'changed since the preview'
            converted = None if reason else conversion[0]  # pylint: disable=unsubscriptable-object
            status = None if reason else self.get_presence(converted, ids, new_references)
            if status == 'different_cascade':
                reason = 'the collection already has it with another cascade'
            if reason:
                results.append({**result, 'status': 'not_converted', 'reason': reason})
                continue
            old_ids.append(reference.id)
            result['expression'] = converted.expression
            if status == 'already_present':
                result['status'] = 'already_present'
            else:
                new_references[(converted.expression, converted.include)] = converted
            results.append(result)
        return results, old_ids, new_references

    def get_presence(self, converted, ids, new_references):
        """'already_present' when the collection, or this batch, already has the converted reference with the same
        cascade; 'different_cascade' when it has the expression with another one, which adding would refuse"""
        same = new_references.get((converted.expression, converted.include))
        cascades = [same.cascade] if same else list(self.collection.references.filter(
            expression=converted.expression, include=converted.include).exclude(id__in=ids).values_list(
                'cascade', flat=True))
        if not cascades:
            return None
        return 'already_present' if converted.cascade in cascades else 'different_cascade'

    def convert(self, conversions, user):
        """Swaps each reference for its conversion, all in one transaction: the old ones and their members go, then
        the new ones are added and evaluated as any added reference is. If any can't be added, nothing changes"""
        results, old_ids, new_references = self.plan(conversions)
        try:
            with transaction.atomic():
                if old_ids:
                    self.collection.remove_references(self.collection.references.filter(id__in=old_ids))
                added, errors = self.collection.add_references(list(new_references.values()), user) \
                    if new_references else ([], {})
                if errors or len(added) != len(new_references):
                    raise ConversionFailed(errors)
        except ConversionFailed as ex:
            for result in results:
                if 'reason' not in result:
                    result.update(status='rolled_back', errors=ex.errors.get(result['expression']))
            return results
        added_ids = {reference.expression: reference.id for reference in added}
        for result in results:
            if result.get('expression') in added_ids and 'status' not in result:
                result.update(status='converted', reference=added_ids[result['expression']])
        return results


def get_id(instance):
    return instance.id if instance else None
