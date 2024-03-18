import hashlib
import json
from uuid import UUID

from django.conf import settings
from django.db import models
from pydash import get

from core.common.utils import generic_sort
from core.toggles.models import Toggle


class ChecksumModel(models.Model):
    class Meta:
        abstract = True

    checksums = models.JSONField(null=True, blank=True, default=dict)

    CHECKSUM_EXCLUSIONS = []
    CHECKSUM_INCLUSIONS = []
    STANDARD_CHECKSUM_KEY = 'standard'
    SMART_CHECKSUM_KEY = 'smart'

    def get_checksums(self, queue=False, recalculate=False):
        _checksums = None
        if Toggle.get('CHECKSUMS_TOGGLE'):
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

    def set_checksums(self):
        if Toggle.get('CHECKSUMS_TOGGLE'):
            self.checksums = self._calculate_checksums()
            self.save(update_fields=['checksums'])

    @property
    def checksum(self):
        """Returns the checksum of the model instance or standard only checksum."""
        _checksum = None
        if Toggle.get('CHECKSUMS_TOGGLE'):
            if get(self, f'checksums.{self.STANDARD_CHECKSUM_KEY}'):
                _checksum = self.checksums[self.STANDARD_CHECKSUM_KEY]
            else:
                self.get_checksums()
                _checksum = self.checksums.get(self.STANDARD_CHECKSUM_KEY)
        return _checksum

    def get_checksum_fields(self):
        return {field: getattr(self, field) for field in self.CHECKSUM_INCLUSIONS}

    def get_standard_checksum_fields(self):
        return self.get_checksum_fields()

    def get_smart_checksum_fields(self):
        return {}

    def get_all_checksums(self):
        checksums = None
        if Toggle.get('CHECKSUMS_TOGGLE'):
            checksums = {}
            if self.STANDARD_CHECKSUM_KEY:
                checksums[self.STANDARD_CHECKSUM_KEY] = self._calculate_standard_checksum()
            if self.SMART_CHECKSUM_KEY:
                checksums[self.SMART_CHECKSUM_KEY] = self._calculate_smart_checksum()
        return checksums

    @staticmethod
    def generate_checksum(data):
        return Checksum.generate(ChecksumModel._cleanup(data))

    @staticmethod
    def generate_checksum_from_many(data):
        checksums = [
            Checksum.generate(ChecksumModel._cleanup(_data)) for _data in data
        ] if isinstance(data, list) else [
            Checksum.generate(ChecksumModel._cleanup(data))
        ]
        if len(checksums) == 1:
            return checksums[0]
        return Checksum.generate(checksums)

    def _calculate_standard_checksum(self):
        fields = self.get_standard_checksum_fields()
        return None if fields is None else self.generate_checksum(fields)

    def _calculate_smart_checksum(self):
        fields = self.get_smart_checksum_fields()
        return self.generate_checksum(fields) if fields else None

    @staticmethod
    def _cleanup(fields):
        result = fields
        if isinstance(fields, dict):
            result = {}
            for key, value in fields.items():
                if value is None:
                    continue
                if key in ['retired'] and not value:
                    continue
                if key in ['is_active'] and value:
                    continue
                if key in ['extras'] and not value:
                    continue
                result[key] = value
        return result

    def _calculate_checksums(self):
        return self.get_all_checksums()


class Checksum:
    @classmethod
    def generate(cls, obj, hash_algorithm='MD5'):
        # hex encoding is used to make the hash more readable
        serialized_obj = cls._serialize(obj).encode('utf-8')
        hash_func = hashlib.new(hash_algorithm)
        hash_func.update(serialized_obj)

        return hash_func.hexdigest()

    @classmethod
    def _serialize(cls, obj):
        if isinstance(obj, list) and len(obj) == 1:
            obj = obj[0]
        if isinstance(obj, list):
            return f"[{','.join(map(cls._serialize, generic_sort(obj)))}]"
        if isinstance(obj, dict):
            keys = generic_sort(obj.keys())
            acc = f"{{{json.dumps(keys)}"
            for key in keys:
                acc += f"{cls._serialize(obj[key])},"
            return f"{acc}}}"
        if isinstance(obj, UUID):
            return json.dumps(str(obj))
        return json.dumps(obj)


class ChecksumDiff:  # pragma: no cover
    def __init__(self, resources1, resources2, identity='mnemonic', verbose=False, verbosity_level=1):  # pylint: disable=too-many-arguments
        self.resources1 = resources1
        self.resources2 = resources2
        self.identity = identity
        self.verbose = verbose
        self.verbosity_level = verbosity_level
        self.same = {}
        self.same_smart = {}
        self.same_standard = {}
        self.changed = {}
        self.changed_smart = {}
        self.changed_standard = {}
        self.result = {}
        self._resources1_map = None
        self._resources2_map = None
        self._resources1_set = None
        self._resources2_set = None

    def get_resources_map(self, resources):
        return {
            get(resource, self.identity): {
                'checksums': resource.checksums
            } for resource in resources
        }

    @property
    def resources1_map(self):
        if self._resources1_map is not None:
            return self._resources1_map
        self._resources1_map = self.get_resources_map(self.resources1)
        return self._resources1_map

    @property
    def resources2_map(self):
        if self._resources2_map is not None:
            return self._resources2_map
        self._resources2_map = self.get_resources_map(self.resources2)
        return self._resources2_map

    @property
    def resources1_set(self):
        if self._resources1_set is not None:
            return self._resources1_set
        self._resources1_set = set(self.resources1_map.keys())
        return self._resources1_set

    @property
    def resources2_set(self):
        if self._resources2_set is not None:
            return self._resources2_set
        self._resources2_set = set(self.resources2_map.keys())
        return self._resources2_set

    @property
    def new(self):
        return {key: self.resources1_map[key] for key in self.resources1_set - self.resources2_set}

    @property
    def deleted(self):
        return {key: self.resources2_map[key] for key in self.resources2_set - self.resources1_set}

    @property
    def common(self):
        return {key: self.resources1_map[key] for key in self.resources1_set & self.resources2_set}

    def populate_diff_from_common(self):
        common = self.common
        resources1_map = self.resources1_map
        resources2_map = self.resources2_map

        for key, info in common.items():
            if resources1_map[key]['checksums'] == resources2_map[key]['checksums']:
                self.same[key] = info
                self.same_smart[key] = info
                self.same_standard[key] = info
            elif resources1_map[key]['checksums']['smart'] == resources2_map[key]['checksums']['smart']:
                self.same_smart[key] = info
                self.changed_standard[key] = info
            elif resources1_map[key]['checksums']['standard'] == resources2_map[key]['checksums']['standard']:
                self.same_standard[key] = info
                self.changed_smart[key] = info
            else:
                self.changed[key] = info

    @property
    def denominator(self):
        return max(len(self.resources1_set), len(self.resources2_set))

    def get_struct(self, percentage, values, is_verbose=False):
        struct = {
            'percentage': round(percentage * 100, 2),
            'total': len(values or [])
        }
        if is_verbose and values:
            struct[self.identity] = list(values.keys())

        return struct

    def prepare(self):
        denominator = self.denominator
        new = self.new
        deleted = self.deleted
        is_very_verbose = self.verbose and self.verbosity_level == 2

        self.result = {
            'new': self.get_struct(len(new) / denominator, new, self.verbose),
            'removed': self.get_struct(len(deleted) / denominator, deleted, self.verbose),
            'same': self.get_struct(len(self.same) / denominator, self.same, is_very_verbose),
            'changed': self.get_struct(len(self.changed) / denominator, self.changed, self.verbose),
            'standard': {
                'same': self.get_struct(len(self.same_standard) / denominator, self.same_standard, is_very_verbose),
                'changed': self.get_struct(
                    len(self.changed_standard) / denominator, self.changed_standard, self.verbose),
            },
            'smart': {
                'same': self.get_struct(len(self.same_smart) / denominator, self.same_smart, is_very_verbose),
                'changed': self.get_struct(len(self.changed_smart) / denominator, self.changed_smart, self.verbose),
            }
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
