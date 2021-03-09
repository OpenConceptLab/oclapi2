import json
from pprint import pprint

from django.core.management import BaseCommand
from pydash import get

from core.common.constants import HEAD
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'import v1 sources'

    total = 0
    processed = 0
    created = []
    existed = []
    failed = []

    @staticmethod
    def log(msg):
        print("*******{}*******".format(msg))

    def handle(self, *args, **options):
        FILE_PATH = '/code/core/importers/v1_dump/data/exported_sourceversions.json'
        lines = open(FILE_PATH, 'r').readlines()

        self.log('STARTING SOURCE VERSIONS IMPORT')
        self.total = len(lines)
        self.log('TOTAL: {}'.format(self.total))

        for line in lines:
            data = json.loads(line)
            original_data = data
            self.processed += 1
            _id = data.pop('_id')
            data['internal_reference_id'] = get(_id, '$oid')
            for attr in [
                'active_concepts', 'active_mappings', 'last_child_update', 'last_concept_update', 'last_mapping_update',
                'parent_version_id', 'previous_version_id', 'versioned_object_type_id',
            ]:
                data.pop(attr, None)

            data['snapshot'] = data.pop('source_snapshot', None)
            data['external_id'] = data.pop('version_external_id', None)

            versioned_object_id = data.pop('versioned_object_id')
            versioned_object = Source.objects.filter(internal_reference_id=versioned_object_id).first()
            version = data.pop('mnemonic')
            created_at = data.pop('created_at')
            updated_at = data.pop('updated_at')
            created_by = data.get('created_by')
            updated_by = data.get('updated_by')
            qs = UserProfile.objects.filter(username=created_by)
            if qs.exists():
                data['created_by'] = qs.first()
            qs = UserProfile.objects.filter(username=updated_by)
            if qs.exists():
                data['updated_by'] = qs.first()
            data['created_at'] = get(created_at, '$date')
            data['updated_at'] = get(updated_at, '$date')
            data['organization'] = versioned_object.organization
            data['user'] = versioned_object.user

            self.log("Processing: {} ({}/{})".format(version, self.processed, self.total))
            if Source.objects.filter(uri=data['uri']).exists():
                self.existed.append(original_data)
            else:
                source = Source.objects.create(**data, version=version, mnemonic=versioned_object.mnemonic)
                if source:
                    source.update_mappings()
                    source.save()
                    self.created.append(original_data)
                else:
                    self.failed.append(original_data)

        self.log(
            "Result: Created: {} | Existed: {} | Failed: {}".format(
                len(self.created), len(self.existed), len(self.failed)
            )
        )
        if self.existed:
            self.log("Existed")
            pprint(self.existed)

        if self.failed:
            self.log("Failed")
            pprint(self.failed)

