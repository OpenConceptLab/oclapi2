import json
import hashlib
from uuid import UUID

from django.db import models
from pydash import get

from core.common.utils import generic_sort


class ChecksumModel(models.Model):
    class Meta:
        abstract = True

    checksums = models.JSONField(null=True, blank=True, default=dict)

    CHECKSUM_EXCLUSIONS = []
    CHECKSUM_INCLUSIONS = []
    METADATA_CHECKSUM_KEY = 'meta'
    ALL_CHECKSUM_KEY = 'all'

    def get_checksums(self):
        if self.checksums:
            return self.checksums

        self.set_checksums()

        return self.checksums

    def set_checksums(self):
        self.checksums = self._calculate_checksums()
        self.save()

    @property
    def checksum(self):
        """Returns the checksum of the model instance or metadata only checksum."""
        if get(self, f'checksums.{self.METADATA_CHECKSUM_KEY}'):
            return self.checksums[self.METADATA_CHECKSUM_KEY]
        self.get_checksums()

        return self.checksums.get(self.METADATA_CHECKSUM_KEY)

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
        return {self.METADATA_CHECKSUM_KEY: self._calculate_meta_checksum()}

    def get_all_checksums(self):
        return self.get_basic_checksums()

    @staticmethod
    def generate_checksum(data):
        return Checksum.generate(data)

    @staticmethod
    def generate_queryset_checksum(queryset):
        _checksums = []
        for instance in queryset:
            instance.get_checksums()
            _checksums.append(instance.checksum)
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
