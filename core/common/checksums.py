import json
import hashlib
from uuid import UUID

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
    CHECKSUM_TYPES = {'meta'}
    BASIC_CHECKSUM_TYPES = {'meta'}
    METADATA_CHECKSUM_KEY = 'meta'
    ALL_CHECKSUM_KEY = 'all'

    def get_checksums(self, basic=False):
        if Toggle.get('CHECKSUMS_TOGGLE'):
            if self.checksums and self.has_checksums(basic):
                return self.checksums
            if basic:
                self.set_basic_checksums()
            else:
                self.set_checksums()

            return self.checksums
        return None

    def set_specific_checksums(self, checksum_type, checksum):
        self.checksums = self.checksums or {}
        self.checksums[checksum_type] = checksum
        self.save()

    def has_checksums(self, basic=False):
        return self.has_basic_checksums() if basic else self.has_all_checksums()

    def has_all_checksums(self):
        return set(self.checksums.keys()) - set(self.CHECKSUM_TYPES) == set()

    def has_basic_checksums(self):
        return set(self.checksums.keys()) - set(self.BASIC_CHECKSUM_TYPES) == set()

    def set_checksums(self):
        if Toggle.get('CHECKSUMS_TOGGLE'):
            self.checksums = self._calculate_checksums()
            self.save(update_fields=['checksums'])

    def set_basic_checksums(self):
        if Toggle.get('CHECKSUMS_TOGGLE'):
            self.checksums = self.get_basic_checksums()
            self.save(update_fields=['checksums'])

    @property
    def checksum(self):
        """Returns the checksum of the model instance or metadata only checksum."""
        if Toggle.get('CHECKSUMS_TOGGLE'):
            if get(self, f'checksums.{self.METADATA_CHECKSUM_KEY}'):
                return self.checksums[self.METADATA_CHECKSUM_KEY]
            self.get_checksums()

            return self.checksums.get(self.METADATA_CHECKSUM_KEY)
        return None

    def get_checksum_fields(self):
        result = {
            field.name: getattr(
                self, field.name
            ) for field in self._meta.fields if field.name not in [*self.CHECKSUM_EXCLUSIONS, 'checksums', 'checksum']
        }
        for field in self.CHECKSUM_INCLUSIONS:
            result[field] = getattr(self, field)

        return result

    def get_basic_checksums(self):
        if Toggle.get('CHECKSUMS_TOGGLE'):
            return {self.METADATA_CHECKSUM_KEY: self._calculate_meta_checksum()}
        return None

    def get_all_checksums(self):
        return self.get_basic_checksums()

    @staticmethod
    def generate_checksum(data):
        return Checksum.generate(data)

    @staticmethod
    def generate_queryset_checksum(queryset, basic=False):
        _checksums = []
        for instance in queryset:
            instance.get_checksums(basic)
            _checksums.append(instance.checksum)
        if len(_checksums) == 1:
            return _checksums[0]
        return ChecksumModel.generate_checksum(_checksums)

    def _calculate_meta_checksum(self):
        return self.generate_checksum(self.get_checksum_fields())

    def _calculate_checksums(self):
        _checksums = self.get_all_checksums()
        if len(_checksums.keys()) > 1:
            _checksums[self.ALL_CHECKSUM_KEY] = self.generate_checksum(list(_checksums.values()))
        return _checksums


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
