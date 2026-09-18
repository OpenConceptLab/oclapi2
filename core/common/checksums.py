import time

from django.conf import settings
from django.db import models
from ocldev.checksum import Checksum as ChecksumBase
from pydash import get


# Return only aggregate counts for changed resource groups.
SUMMARY_VERBOSITY = 0

# Include aggregate counts for resources that stayed the same.
SAME_STATS_VERBOSITY = 1

# Include resource IDs for new, removed, retired, and changed resources.
DIFF_RESOURCE_IDS_VERBOSITY = 2

# Include resource IDs for resources that stayed the same.
SAME_RESOURCE_IDS_VERBOSITY = 3

# Include expanded changelog fields such as names, descriptions, and previous values.
CHANGELOG_ENRICHMENT_VERBOSITY = 4


class ChecksumModel(models.Model):
    class Meta:
        abstract = True

    checksums = models.JSONField(null=True, blank=True, default=dict)

    STANDARD_CHECKSUM_KEY = 'standard'
    SMART_CHECKSUM_KEY = 'smart'

    def get_checksum_base(self, resource=None, data=None, checksum_type='standard'):
        resource_name = resource or self.__class__.__name__.lower()
        if resource_name == 'userprofile':
            resource_name = 'user'
        if resource_name == 'org':
            resource_name = 'organization'
        return ChecksumBase(resource_name, data or self, checksum_type)

    def get_checksums(self, queue=False, recalculate=False):
        _checksums = None
        if not recalculate and self.checksums and self.has_all_checksums():
            _checksums = self.checksums
        elif queue:
            self.queue_checksum_calculation()
            _checksums = self.checksums or {}
        else:
            self.set_checksums()
            _checksums = self.checksums
        return _checksums

    def queue_checksum_calculation(self):
        from core.common.tasks import calculate_checksums
        if get(settings, 'TEST_MODE', False):
            calculate_checksums(self.__class__.__name__, self.id)
            self.refresh_from_db()
        else:
            calculate_checksums.apply_async((self.__class__.__name__, self.id), queue='default', permanent=False)

    def has_all_checksums(self):
        return self.has_standard_checksum() and self.has_smart_checksum()

    def has_standard_checksum(self):
        return self.STANDARD_CHECKSUM_KEY in self.checksums if self.STANDARD_CHECKSUM_KEY else True

    def has_smart_checksum(self):
        return self.SMART_CHECKSUM_KEY in self.checksums if self.SMART_CHECKSUM_KEY else True

    def set_checksums(self, sync=True):
        if sync:
            self.checksums = self._calculate_checksums()
            self.__class__.objects.filter(id=self.id).update(checksums=self.checksums)
        else:
            self.queue_checksum_calculation()

    @property
    def checksum(self):
        """Returns the checksum of the model instance or standard only checksum."""
        _checksum = None
        if get(self, f'checksums.{self.STANDARD_CHECKSUM_KEY}'):
            _checksum = self.checksums[self.STANDARD_CHECKSUM_KEY]
        else:
            self.get_checksums()
            _checksum = self.checksums.get(self.STANDARD_CHECKSUM_KEY)
        return _checksum

    def get_all_checksums(self):
        checksums = {}
        if self.STANDARD_CHECKSUM_KEY:
            checksums[self.STANDARD_CHECKSUM_KEY] = self._calculate_standard_checksum()
        if self.SMART_CHECKSUM_KEY:
            checksums[self.SMART_CHECKSUM_KEY] = self._calculate_smart_checksum()
        return checksums

    def generate_checksum(self, checksum_type='standard'):
        checksum_base = self.get_checksum_base(checksum_type=checksum_type)
        return checksum_base.generate()

    @staticmethod
    def generate_checksum_from_many(resource, data, checksum_type='standard'):
        return ChecksumBase(resource, data, checksum_type).generate()

    def _calculate_standard_checksum(self):
        return self.generate_checksum('standard')

    def _calculate_smart_checksum(self):
        return self.generate_checksum('smart')

    def _calculate_checksums(self):
        return self.get_all_checksums()


class Checksum:
    @classmethod
    def generate(cls, obj):
        return ChecksumBase(None, obj).generate()


def get_map_id(resource_map, identity):
    """
    Look up a resource's db id in a {identity: {..., 'id': ...}} map by plain dict access.
    pydash.get's dotted-path parsing must NOT be used here (e.g. get(map, f'{identity}.id')) --
    it splits on every '.', so an identity that itself contains a literal dot (a common case for
    mnemonics such as ICD-10 codes like 'R63.6') gets misparsed as a nested path and silently
    resolves to None instead of the real id.
    """
    return (resource_map.get(identity) or {}).get('id')


class ChecksumDiff:
    def __init__(  # pylint: disable=too-many-arguments
            self, resources1=None, resources2=None, resources1_map=None, resources2_map=None,
            identity='mnemonic', verbosity=0):
        self.resources1 = resources1  # older version resources (queryset; unused if resources1_map given)
        self.resources2 = resources2  # newer version resources (queryset; unused if resources2_map given)
        self.identity = identity
        self.verbosity = verbosity
        self.same_standard = {}
        self.same_smart = {}
        self.changed_smart = {}
        self.changed_standard = {}
        self.result = {}
        self.result_concise = {}
        self._resources1_map = get(resources1_map, 'active')
        self._resources1_map_retired = get(resources1_map, 'retired')
        self._resources2_map = get(resources2_map, 'active')
        self._resources2_map_retired = get(resources2_map, 'retired')
        self._resources1_set = None
        self._resources1_set_retired = None
        self._resources2_set = None
        self._resources2_set_retired = None
        self._retired = None

    @staticmethod
    def build_map(resources, identity='mnemonic'):
        resources = list(resources)
        ChecksumDiff._repair_incomplete_checksums(resources)

        active = {}
        retired = {}
        for resource in resources:
            target = retired if resource.retired else active
            target[get(resource, identity)] = {
                'checksums': resource.checksums,
                'id': resource.id
            }
        return {'active': active, 'retired': retired}

    @staticmethod
    def _repair_incomplete_checksums(resources):
        """
        Batch-repair legacy resources with missing/incomplete checksums, instead of computing
        (and saving) them one at a time -- each of which would otherwise cost several N+1 queries
        (names/descriptions/parent_concepts/child_concepts for concepts, to/from_concept for
        mappings) plus its own UPDATE statement.
        """
        incomplete = [r for r in resources if not r.checksums or not r.has_all_checksums()]
        if not incomplete:
            return

        model = incomplete[0].__class__
        queryset = model.objects.filter(id__in=[r.id for r in incomplete])
        if model.__name__ == 'Concept':
            queryset = queryset.prefetch_related('names', 'descriptions', 'parent_concepts', 'child_concepts')
        elif model.__name__ == 'Mapping':
            queryset = queryset.select_related('to_concept', 'from_concept')

        to_repair = list(queryset)
        for resource in to_repair:
            resource.checksums = resource.get_all_checksums()
        model.objects.bulk_update(to_repair, ['checksums'], batch_size=500)

        repaired_by_id = {resource.id: resource.checksums for resource in to_repair}
        for resource in incomplete:
            if resource.id in repaired_by_id:
                resource.checksums = repaired_by_id[resource.id]

    @property
    def resources1_map(self):
        if self._resources1_map is not None:
            return self._resources1_map
        resources_map = self.build_map(self.resources1, self.identity)
        self._resources1_map = resources_map['active']
        self._resources1_map_retired = resources_map['retired']
        return self._resources1_map

    @property
    def resources1_map_retired(self):
        if self._resources1_map_retired is not None:
            return self._resources1_map_retired
        resources_map = self.build_map(self.resources1, self.identity)
        self._resources1_map = resources_map['active']
        self._resources1_map_retired = resources_map['retired']
        return self._resources1_map_retired

    @property
    def resources2_map(self):
        if self._resources2_map is not None:
            return self._resources2_map
        resources_map = self.build_map(self.resources2, self.identity)
        self._resources2_map = resources_map['active']
        self._resources2_map_retired = resources_map['retired']
        return self._resources2_map

    @property
    def resources2_map_retired(self):
        if self._resources2_map_retired is not None:
            return self._resources2_map_retired
        resources_map = self.build_map(self.resources2, self.identity)
        self._resources2_map = resources_map['active']
        self._resources2_map_retired = resources_map['retired']
        return self._resources2_map_retired

    @property
    def resources1_set(self):
        if self._resources1_set is not None:
            return self._resources1_set
        self._resources1_set = set(self.resources1_map.keys())
        return self._resources1_set

    @property
    def resources1_set_retired(self):
        if self._resources1_set_retired is not None:
            return self._resources1_set_retired
        self._resources1_set_retired = set(self.resources1_map_retired.keys())
        return self._resources1_set_retired

    @property
    def resources2_set(self):
        if self._resources2_set is not None:
            return self._resources2_set
        self._resources2_set = set(self.resources2_map.keys())
        return self._resources2_set

    @property
    def resources2_set_retired(self):
        if self._resources2_set_retired is not None:
            return self._resources2_set_retired
        self._resources2_set_retired = set(self.resources2_map_retired.keys())
        return self._resources2_set_retired

    @property
    def new(self):
        return {key: self.resources2_map[key] for key in self.resources2_set - self.resources1_set}

    @property
    def deleted(self):
        diff_set = self.resources1_set - self.resources2_set
        return {key: self.resources1_map[key] for key in diff_set if key not in self.retired}

    @property
    def retired(self):
        if self._retired is not None:
            return self._retired
        self._retired = {
            key: self.resources2_map_retired[key] for key in self.resources2_set_retired - self.resources1_set_retired}
        return self._retired

    @property
    def common(self):
        return {key: self.resources2_map[key] for key in self.resources1_set & self.resources2_set}

    @property
    def is_verbose(self):
        # include same stats, count only
        return self.verbosity >= SAME_STATS_VERBOSITY

    @property
    def is_very_verbose(self):
        # include IDS of new/changed/removed/retired
        return self.verbosity >= DIFF_RESOURCE_IDS_VERBOSITY

    @property
    def is_very_very_verbose(self):
        # include IDS of same
        return self.verbosity >= SAME_RESOURCE_IDS_VERBOSITY

    @property
    def include_same_stats(self):
        return self.is_verbose

    @property
    def include_same_details(self):
        return self.is_very_very_verbose

    def populate_diff_from_common(self):
        common = self.common
        resources1_map = self.resources1_map
        resources2_map = self.resources2_map

        for key, info in common.items():
            checksums1 = resources1_map[key]['checksums']
            checksums2 = resources2_map[key]['checksums']
            if checksums1['smart'] != checksums2['smart']:
                self.changed_smart[key] = info
            elif checksums1['standard'] != checksums2['standard']:
                self.changed_standard[key] = info
            elif self.include_same_stats and checksums1['smart'] == checksums2['smart']:
                self.same_smart[key] = info
            elif self.include_same_stats and checksums1['standard'] == checksums2['standard']:
                self.same_standard[key] = info

    def get_struct(self, values, is_same=False):
        """
        Return either a simple count or a detailed structure for a diff group.

        Changed groups include resource IDs at verbosity>=2, while unchanged
        groups only include resource IDs at verbosity>=3 to keep lower
        verbosity responses compact.
        """
        total = len(values or [])
        include_ids = self.is_very_very_verbose if is_same else self.is_very_verbose
        if include_ids:
            if values:
                return {'total': total, self.identity: list(values.keys())}
            return total

        return total

    def prepare(self):
        self.result = {
            'new': self.get_struct(self.new),
            'removed': self.get_struct(self.deleted),
            'changed_total': len(self.retired or []) + len(self.changed_standard or []) + len(self.changed_smart or []),
            'changed_retired': self.get_struct(self.retired),
            'changed_major': self.get_struct(self.changed_smart),
            'changed_minor': self.get_struct(self.changed_standard),
        }
        if self.include_same_stats:
            self.result['same_total'] = len(self.same_standard or []) + len(self.same_smart or [])
            self.result['same_minor'] = self.get_struct(self.same_standard, True)
            self.result['same_major'] = self.get_struct(self.same_smart, True)

    def set_concise_result(self):
        self.result_concise = {
            'new': len(self.new or []),
            'removed': len(self.deleted or []),
            'changed_total': self.result['changed_total'],
            'changed_retired': len(self.retired or []),
            'changed_major': len(self.changed_smart or []),
            'changed_minor': len(self.changed_standard or []),
        }

    def process(self, refresh=False):
        if refresh:
            self.result = {}
        if self.result:
            return self.result

        self.populate_diff_from_common()
        self.prepare()

        return self.result

    def pretty_print_dict(self, d, indent=0):  # pragma: no cover
        res = ""
        for k, v in d.items():
            res += "\t" * indent + str(k) + "\n"
            if isinstance(v, dict):
                res += self.pretty_print_dict(v, indent + 1)
            else:
                res += "\t" * (indent + 1) + str(v) + "\n"
        return res

    def print(self):
        print(self.pretty_print_dict(self.result))

    def get_db_id_for(self, diff_key, identity):
        """Return the concrete resource DB id represented by a changelog diff key."""
        if diff_key == 'changed_retired':
            db_id = get_map_id(self.resources2_map_retired, identity)
        elif diff_key == 'removed':
            db_id = get_map_id(self.resources1_map, identity)
        else:
            db_id = get_map_id(self.resources2_map, identity) or get_map_id(self.resources1_map, identity)

        if not db_id:
            raise KeyError(f'Unable to resolve DB id for {diff_key}:{identity}')

        return db_id


class ChecksumChangelog:
    def __init__(self, concepts_diff, mappings_diff, identity='mnemonic', verbosity=0):  # pylint: disable=too-many-arguments
        self.concepts_diff = concepts_diff
        self.mappings_diff = mappings_diff
        self.identity = identity
        self.verbosity = verbosity
        self.result = {}

    def get_mapping_summary(self, mapping, mapping_id=None, v1_mapping=None):
        summary = {
            'id': mapping_id or get(mapping, self.identity),
            'from_concept': mapping.from_concept_code or get(mapping.from_concept, 'mnemonic'),
            'from_source': mapping.from_source_url,
            'to_concept': mapping.to_concept_code or get(mapping.to_concept, 'mnemonic'),
            'to_source': mapping.to_source_url,
            'map_type': mapping.map_type,
        }
        if self.verbosity >= CHANGELOG_ENRICHMENT_VERBOSITY:
            summary['external_id'] = getattr(mapping, 'external_id', None)
            if v1_mapping is not None:
                summary['prev_to_concept'] = (
                    v1_mapping.to_concept_code or get(v1_mapping.to_concept, 'mnemonic')
                )
                summary['prev_to_source'] = v1_mapping.to_source_url
                summary['prev_map_type'] = v1_mapping.map_type
        return summary

    def _collect_v1_v2_ids(self, diff, diff_obj):
        """For each mnemonic in each category, collect the v2 and v1 DB ids needed for enrichment."""
        ids = set()
        for key in diff:
            entry = diff[key]
            if not isinstance(entry, dict):
                continue
            for mnemonic in entry[self.identity]:
                db_id = diff_obj.get_db_id_for(key, mnemonic)
                if db_id:
                    ids.add(db_id)
                if key in ('changed_major', 'changed_minor'):
                    v1_id = get_map_id(diff_obj.resources1_map, mnemonic)
                    if v1_id and v1_id != db_id:
                        ids.add(v1_id)
        return ids

    def _build_concepts_cache(self, diff_keys):
        """Batch-fetch concepts with names/descriptions for enrich mode (avoids N+1 queries)."""
        from core.concepts.models import Concept
        ids = self._collect_v1_v2_ids(
            {k: self.concepts_diff.result.get(k, False) for k in diff_keys},
            self.concepts_diff,
        )
        return {
            c.id: c
            for c in Concept.objects.filter(id__in=ids).prefetch_related('names', 'descriptions')
        }

    def _build_mappings_cache(self, diff_keys):
        """Batch-fetch mappings for enrich mode (to resolve v1 state of changed mappings)."""
        from core.mappings.models import Mapping
        ids = self._collect_v1_v2_ids(
            {k: self.mappings_diff.result.get(k, False) for k in diff_keys},
            self.mappings_diff,
        )
        return {
            m.id: m
            for m in Mapping.objects.filter(id__in=ids).select_related('from_concept', 'to_concept')
        }

    def _build_mapping_diff_index(self, diff_keys, mappings_cache=None):
        """
        Group every new/removed/changed mapping by its from_concept's versioned_object_id, in a
        single batched pass. Without this, attaching diff'd mappings to their concept required
        one query PER concept PER diff key, each with an IN-clause spanning every mapping in that
        category -- for a source with tens of thousands of changed mappings and concepts, that's
        effectively quadratic and dominates both runtime and memory.
        """
        from core.mappings.models import Mapping
        ids_by_key = {}
        all_ids = set()
        for key in diff_keys:
            diff = self.mappings_diff.result.get(key, False)
            if isinstance(diff, dict):
                db_ids = [self.mappings_diff.get_db_id_for(key, mnemonic) for mnemonic in diff[self.identity]]
                ids_by_key[key] = db_ids
                all_ids.update(db_ids)

        if mappings_cache:
            # mappings_cache already covers every id here (plus v1 state for changed items) --
            # reuse it instead of fetching and holding the same rows in memory a second time.
            mappings_by_id = mappings_cache
        else:
            mappings_by_id = {
                m.id: m
                for m in Mapping.objects.filter(id__in=all_ids).select_related('from_concept', 'to_concept')
            }

        index = {}
        for key, ids in ids_by_key.items():
            for mapping_id in ids:
                mapping = mappings_by_id.get(mapping_id)
                from_concept = get(mapping, 'from_concept')
                if not from_concept:
                    continue
                index.setdefault(from_concept.versioned_object_id, {}).setdefault(key, []).append(mapping)
        return index

    @staticmethod
    def _names_list(concept):
        return [
            {
                'external_id': n.external_id,
                'name': n.name,
                'type': n.type,
                'locale': n.locale,
                'locale_preferred': n.locale_preferred,
            }
            for n in concept.names.all()
        ]

    @staticmethod
    def _descriptions_list(concept):
        return [
            {'external_id': d.external_id, 'description': d.name, 'type': d.type, 'locale': d.locale}
            for d in concept.descriptions.all()
        ]

    def _v1_mapping_for(self, mnemonic, mapping_db_id, mappings_cache):
        """Look up the v1 mapping instance for a changed mapping (for prev_* fields)."""
        v1_id = get_map_id(self.mappings_diff.resources1_map, mnemonic)
        if v1_id and v1_id != mapping_db_id:
            return mappings_cache.get(v1_id)
        return None

    def process(self):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        from core.mappings.models import Mapping
        from core.concepts.models import Concept
        concepts_result = {}
        mappings_result = {}
        traversed_mappings = set()
        traversed_concepts = set()
        diff_keys = ['new', 'removed', 'changed_retired', 'changed_major', 'changed_minor']
        include_changelog_enrichment = self.verbosity >= CHANGELOG_ENRICHMENT_VERBOSITY

        concepts_cache = self._build_concepts_cache(diff_keys) if include_changelog_enrichment else {}
        mappings_cache = self._build_mappings_cache(diff_keys) if include_changelog_enrichment else {}
        mapping_diff_index = self._build_mapping_diff_index(diff_keys, mappings_cache)

        for key in diff_keys:  # pylint: disable=too-many-nested-blocks
            diff = self.concepts_diff.result.get(key, False)
            if isinstance(diff, dict):
                section_summary = {}
                for concept_id in diff[self.identity]:
                    if concept_id in traversed_concepts:
                        continue
                    traversed_concepts.add(concept_id)
                    concept_db_id = self.concepts_diff.get_db_id_for(key, concept_id)
                    if include_changelog_enrichment:
                        concept = concepts_cache.get(concept_db_id)
                    else:
                        concept = Concept.objects.filter(id=concept_db_id).first()
                    concept_display_name = get(concept, 'display_name')
                    if concept_display_name:
                        concept_display_name = concept_display_name.replace('"', "'")
                    summary = {
                        'id': concept_id,
                        'display_name': concept_display_name
                    }
                    if include_changelog_enrichment and concept:
                        summary['concept_class'] = getattr(concept, 'concept_class', None)
                        summary['datatype'] = getattr(concept, 'datatype', None)
                        summary['names'] = self._names_list(concept)
                        summary['descriptions'] = self._descriptions_list(concept)
                        if key in ('changed_major', 'changed_minor'):
                            v1_id = get_map_id(self.concepts_diff.resources1_map, concept_id)
                            if v1_id and v1_id != concept_db_id:
                                v1_concept = concepts_cache.get(v1_id)
                                if v1_concept:
                                    summary['prev_names'] = self._names_list(v1_concept)
                                    summary['prev_descriptions'] = self._descriptions_list(v1_concept)
                                    summary['prev_concept_class'] = getattr(v1_concept, 'concept_class', None)
                                    summary['prev_datatype'] = getattr(v1_concept, 'datatype', None)
                    mappings_diff_summary = {}
                    for mapping_diff_key, mappings in mapping_diff_index.get(
                            get(concept, 'versioned_object_id'), {}).items():
                        for mapping in mappings:
                            mnemonic = get(mapping, self.identity)
                            if mnemonic in traversed_mappings:
                                continue
                            if mapping_diff_key not in mappings_diff_summary:
                                mappings_diff_summary[mapping_diff_key] = []
                            v1_mapping = None
                            if (
                                include_changelog_enrichment and
                                mapping_diff_key in ('changed_major', 'changed_minor')
                            ):
                                v1_mapping = self._v1_mapping_for(mnemonic, mapping.id, mappings_cache)
                            mappings_diff_summary[mapping_diff_key].append(
                                self.get_mapping_summary(mapping, v1_mapping=v1_mapping)
                            )
                            traversed_mappings.add(mnemonic)
                    if mappings_diff_summary:
                        summary['mappings'] = mappings_diff_summary
                    section_summary[concept_id] = summary
                if section_summary:
                    concepts_result[key] = section_summary
        # Read the raw same_standard/same_smart dicts directly rather than the exposed
        # result['same_minor']/result['same_major'] -- those are only populated with full
        # id lists at verbosity>=SAME_RESOURCE_IDS_VERBOSITY, which for a large source means
        # materializing (and persisting) a near-total dump of unchanged concept mnemonics.
        same_concept_ids = {
            *self.concepts_diff.same_standard.keys(),
            *self.concepts_diff.same_smart.keys(),
        }
        for key in diff_keys:  # pylint: disable=too-many-nested-blocks
            diff = self.mappings_diff.result.get(key, False)
            if isinstance(diff, dict):
                section_summary = {}
                for mapping_id in diff[self.identity]:
                    if mapping_id in traversed_mappings:
                        continue
                    traversed_mappings.add(mapping_id)
                    mapping_db_id = self.mappings_diff.get_db_id_for(key, mapping_id)
                    if mappings_cache:
                        mapping = mappings_cache.get(mapping_db_id)
                    else:
                        mapping = Mapping.objects.filter(id=mapping_db_id).first()
                    v1_mapping = None
                    if include_changelog_enrichment and key in ('changed_major', 'changed_minor'):
                        v1_mapping = self._v1_mapping_for(mapping_id, mapping_db_id, mappings_cache)
                    mapping_summary = self.get_mapping_summary(mapping, mapping_id, v1_mapping=v1_mapping)
                    from_concept_code = get(mapping, 'from_concept_code') or get(mapping.from_concept, 'mnemonic')
                    if from_concept_code and from_concept_code in same_concept_ids:
                        if 'changed_mappings_only' not in concepts_result:
                            concepts_result['changed_mappings_only'] = {}
                        if from_concept_code not in concepts_result['changed_mappings_only']:
                            concept_display_name = get(mapping.from_concept, 'display_name')
                            if concept_display_name:
                                concept_display_name = concept_display_name.replace('"', "'")
                            concepts_result['changed_mappings_only'][from_concept_code] = {
                                'id': from_concept_code,
                                'display_name': concept_display_name,
                                'mappings': {}
                            }
                        mappings_dict = concepts_result['changed_mappings_only'][from_concept_code]['mappings']
                        mappings_dict.setdefault(key, []).append(mapping_summary)
                    else:
                        section_summary[mapping_id] = mapping_summary
                if section_summary:
                    mappings_result[key] = section_summary
        self.result = {
            'concepts': concepts_result,
            'mappings': mappings_result,
        }


class VersionCompareMixin:
    MD_FORMAT = 'markdown'
    JSON_FORMAT = 'json'
    PERSIST_CHANGELOG = True  # expansions are too dynamic/rebuilt-in-place to persist; see Expansion override

    @classmethod
    def get_checksum_map(cls, version, resource):
        queryset = getattr(version, f'get_{resource}_queryset')().only('mnemonic', 'checksums', 'retired')
        if not cls.PERSIST_CHANGELOG:
            return ChecksumDiff.build_map(queryset)

        from core.repos.models import VersionChecksumMap
        field = f'{resource}_map'
        cached = VersionChecksumMap.find_or_build(version, only=[field])
        is_stale = bool(
            cached.pk and version.is_head and version.last_child_update and cached.updated_at
            and version.last_child_update > cached.updated_at
        )
        existing = None if is_stale else getattr(cached, field)
        if not existing:
            existing = ChecksumDiff.build_map(queryset)
            setattr(cached, field, existing)
            cached.save()
        return existing

    @classmethod
    def compare(cls, version1, version2, verbosity=0):
        """
        version1 is the older version
        version2 is the newer version
        """
        concepts_diff = ChecksumDiff(
            resources1_map=cls.get_checksum_map(version1, 'concepts'),
            resources2_map=cls.get_checksum_map(version2, 'concepts'),
            verbosity=verbosity,
        )
        mappings_diff = ChecksumDiff(
            resources1_map=cls.get_checksum_map(version1, 'mappings'),
            resources2_map=cls.get_checksum_map(version2, 'mappings'),
            verbosity=verbosity,
        )
        concepts_diff.process()
        mappings_diff.process()
        return VersionCompareMixin.get_compare_result(concepts_diff, mappings_diff, version1, version2)

    @staticmethod
    def get_compare_result(concepts_diff, mappings_diff, version1, version2):
        return {
            'meta': {
                'version1': {
                    'uri': version1.uri,
                    'concepts': len(concepts_diff.resources1_set),
                    'mappings': len(mappings_diff.resources1_set),
                },
                'version2': {
                    'uri': version2.uri,
                    'concepts': len(concepts_diff.resources2_set),
                    'mappings': len(mappings_diff.resources2_set),
                }
            },
            'concepts': concepts_diff.result,
            'mappings': mappings_diff.result
        }

    @classmethod
    def changelog(cls, version1, version2, verbosity=0):
        """
        version1 is the older version
        version2 is the newer version

        verbosity >= 4 enables full enrichment: concept_class, datatype, names[]
        (with external_id), descriptions[], and prev_* fields for changed concepts
        and mappings.
        """
        # Internal diff runs at verbosity=2: enough to get id lists for changed/new/removed
        # categories (which ChecksumChangelog.process() needs) without also materializing
        # id lists for the same_* categories (which nothing here reads -- same-concept
        # membership is checked against same_standard/same_smart directly, not the result).
        concepts_diff = ChecksumDiff(
            resources1_map=cls.get_checksum_map(version1, 'concepts'),
            resources2_map=cls.get_checksum_map(version2, 'concepts'),
            verbosity=DIFF_RESOURCE_IDS_VERBOSITY
        )
        mappings_diff = ChecksumDiff(
            resources1_map=cls.get_checksum_map(version1, 'mappings'),
            resources2_map=cls.get_checksum_map(version2, 'mappings'),
            verbosity=DIFF_RESOURCE_IDS_VERBOSITY
        )
        concepts_diff.process()
        mappings_diff.process()
        log = ChecksumChangelog(concepts_diff, mappings_diff, verbosity=verbosity)
        log.process()
        result = VersionCompareMixin.get_changelog_result(concepts_diff, mappings_diff, version1,
                                                          version2, log, verbosity)
        return result

    @staticmethod
    def get_changelog_result(  # pylint: disable=too-many-arguments
            concepts_diff, mappings_diff, version1, version2, log, verbosity):
        result = {
            'meta': {
                'version1': {
                    'uri': version1.uri,
                    'concepts': len(concepts_diff.resources1_set),
                    'mappings': len(mappings_diff.resources1_set),
                },
                'version2': {
                    'uri': version2.uri,
                    'concepts': len(concepts_diff.resources2_set),
                    'mappings': len(mappings_diff.resources2_set),
                }
            },
            **log.result
        }
        if verbosity > 0:
            concepts_diff.set_concise_result()
            mappings_diff.set_concise_result()
            result['meta']['diff'] = {
                'concepts': concepts_diff.result_concise,
                'mappings': mappings_diff.result_concise,
            }
        return result

    @classmethod
    def save_changelog_and_comparison(cls, uri1, uri2):
        from core.repos.models import VersionChangelog
        version1, version2 = cls._get_versions(uri1, uri2)
        changelog = VersionChangelog.find_or_build(version1, version2)
        start_time = time.time()

        concepts_diff = ChecksumDiff(
            resources1_map=cls.get_checksum_map(version1, 'concepts'),
            resources2_map=cls.get_checksum_map(version2, 'concepts'),
            verbosity=DIFF_RESOURCE_IDS_VERBOSITY)
        mappings_diff = ChecksumDiff(
            resources1_map=cls.get_checksum_map(version1, 'mappings'),
            resources2_map=cls.get_checksum_map(version2, 'mappings'),
            verbosity=DIFF_RESOURCE_IDS_VERBOSITY)
        concepts_diff.process()
        mappings_diff.process()

        log = ChecksumChangelog(concepts_diff, mappings_diff, verbosity=CHANGELOG_ENRICHMENT_VERBOSITY)
        log.process()
        changelog.changelog = VersionCompareMixin.get_changelog_result(
            concepts_diff, mappings_diff, version1, version2, log, DIFF_RESOURCE_IDS_VERBOSITY)
        changelog.comparison = VersionCompareMixin.get_compare_result(
            concepts_diff, mappings_diff, version1, version2)
        changelog.extras = changelog.extras or {}
        changelog.extras['elapsed_seconds'] = time.time() - start_time
        changelog.save()
        return changelog

    @classmethod
    def run_diff(  # pylint: disable=too-many-arguments
            cls, uri1, uri2, is_changelog, verbosity, format_type=JSON_FORMAT):
        version1, version2 = cls._get_versions(uri1, uri2)

        if not cls.PERSIST_CHANGELOG:
            if is_changelog:
                result = cls.changelog(version1, version2, verbosity)
                if format_type == cls.MD_FORMAT:
                    from core.sources.changelog_markdown import ChangelogMarkdownGenerator
                    result = {format_type: ChangelogMarkdownGenerator(result).generate()}
                return result
            return cls.compare(version1, version2, verbosity)

        from core.repos.models import VersionChangelog
        changelog = VersionChangelog.find_or_build(version1, version2)
        # HEAD is a moving target -- a previously saved changelog/comparison against HEAD is only
        # trustworthy if nothing has changed in HEAD since it was saved. last_child_update (not
        # updated_at) is used because it only moves when concepts/mappings actually change.
        is_stale = bool(
            changelog.pk and version2.is_head and version2.last_child_update and changelog.updated_at
            and version2.last_child_update > changelog.updated_at
        )

        computed_new_result = False
        if is_changelog:
            saved = None if is_stale else get(changelog, 'changelog')
            if not saved:
                saved = cls.changelog(version1, version2, CHANGELOG_ENRICHMENT_VERBOSITY)
                changelog.changelog = saved
                computed_new_result = True
            result = saved
            if format_type == cls.MD_FORMAT:
                result = {format_type: changelog.changelog_md}
        else:
            saved = None if is_stale else get(changelog, 'comparison')
            if not saved:
                saved = cls.compare(version1, version2, DIFF_RESOURCE_IDS_VERBOSITY)
                changelog.comparison = saved
                computed_new_result = True
            result = saved
        if computed_new_result:
            changelog.save()

        return result

    @classmethod
    def _get_versions(cls, uri1, uri2):
        version1 = cls.objects.filter(uri=uri1).first()
        version2 = cls.objects.filter(uri=uri2).first()

        if not version1:
            raise ValueError(f"Version not found: {uri1}")
        if not version2:
            raise ValueError(f"Version not found: {uri2}")

        if (version1.created_at and version2.created_at and version1.created_at > version2.created_at) or (
                version1.is_head and not version2.is_head):
            version1, version2 = version2, version1

        return version1, version2

    @staticmethod
    def _generate_cache_key(*args, **kwargs):
        key_parts = [repr(arg) for arg in args]
        key_parts += [f"{k}={repr(v)}" for k, v in sorted(kwargs.items())]
        return "|".join(key_parts)

    @classmethod
    def build_checksum_map(cls, url, limit=5, build_changelog=False):  # pragma: no cover
        limit = limit or 5
        source = cls.objects.get(uri=url, version='HEAD')
        versions = list(source.versions.exclude(version='HEAD').order_by('-created_at')[:limit])
        total = len(versions)
        print(f'{url}: building checksum maps for {total} version(s)')
        for index, version in enumerate(versions, start=1):
            print(f'[{index}/{total}] {version.version}: building concepts map')
            cls.get_checksum_map(version, 'concepts')
            print(f'[{index}/{total}] {version.version}: building mappings map')
            cls.get_checksum_map(version, 'mappings')
            print(f'[{index}/{total}] {version.version}: done')
        if build_changelog:
            # oldest -> newest, so each pair's changelog reuses the checksum maps just built above
            ordered = list(reversed(versions))
            pairs = list(zip(ordered, ordered[1:]))
            total_pairs = len(pairs)
            print(f'{url}: building changelog for {total_pairs} consecutive version pair(s)')
            for index, (version1, version2) in enumerate(pairs, start=1):
                print(f'[{index}/{total_pairs}] {version1.version} -> {version2.version}: building changelog')
                cls.save_changelog_and_comparison(version1.uri, version2.uri)
                print(f'[{index}/{total_pairs}] {version1.version} -> {version2.version}: done')
