import json
import time
from pprint import pprint

from django.core.management import BaseCommand
from pydash import get

from core.concepts.models import Concept
from core.mappings.models import Mapping
from core.sources.models import Source
from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'import v1 mapping versions'

    total = 0
    processed = 0
    created = []
    existed = []
    failed = []
    start_time = None
    elapsed_seconds = 0
    users = dict()

    @staticmethod
    def log(msg):
        print("*******{}*******".format(msg))

    def handle(self, *args, **options):
        self.start_time = time.time()
        FILE_PATH = '/code/core/importers/v1_dump/data/exported_mappingversions.json'
        lines = open(FILE_PATH, 'r').readlines()

        self.log('STARTING MAPPING VERSIONS IMPORT')
        self.total = len(lines)
        self.log('TOTAL: {}'.format(self.total))

        for line in lines:
            data = json.loads(line)
            original_data = data.copy()
            self.processed += 1
            created_at = data.pop('created_at')
            updated_at = data.pop('updated_at')
            created_by = data.get('created_by', None) or data.pop('version_created_by', None) or 'ocladmin'
            updated_by = data.get('updated_by') or created_by
            source_version_ids = data.pop('source_version_ids', None) or None

            for attr in [
                'root_version_id', 'parent_version_id', 'previous_version_id', 'root_version_id', 'version_created_by',
                'versioned_object_type_id'
            ]:
                data.pop(attr, None)

            data['comment'] = data.pop('update_comment', None)
            _id = data.pop('_id')
            versioned_object_id = data.pop('versioned_object_id')
            versioned_object = Mapping.objects.filter(internal_reference_id=versioned_object_id).first()
            if not versioned_object:
                self.failed.append({**original_data, 'errors': ['versioned_object not found']})
                continue
            mnemonic = versioned_object.mnemonic
            data['version'] = data.pop('mnemonic')
            data['internal_reference_id'] = get(_id, '$oid')
            data['created_at'] = get(created_at, '$date')
            data['updated_at'] = get(updated_at, '$date')
            from_concept_id = get(data.pop('from_concept_id'), '$oid')
            to_concept_id = get(data.pop('to_concept_id'), '$oid')
            to_source_id = get(data.pop('to_source_id'), '$oid')
            from_concept = Concept.objects.filter(internal_reference_id=from_concept_id).first()
            to_concept = None
            to_source = None
            if to_concept_id:
                to_concept = Concept.objects.filter(internal_reference_id=to_concept_id).first()
            if to_source_id:
                to_source = Source.objects.filter(internal_reference_id=to_source_id).first()

            if created_by in self.users:
                data['created_by'] = self.users[created_by]
            elif created_by:
                qs = UserProfile.objects.filter(username=created_by)
                if qs.exists():
                    user = qs.first()
                    self.users[created_by] = user
                    data['created_by'] = user

            if updated_by in self.users:
                data['updated_by'] = self.users[updated_by]
            elif updated_by:
                qs = UserProfile.objects.filter(username=updated_by)
                if qs.exists():
                    user = qs.first()
                    self.users[created_by] = user
                    data['updated_by'] = user

            self.log("Processing: {} ({}/{})".format(mnemonic, self.processed, self.total))
            if Mapping.objects.filter(uri=data['uri']).exists():
                self.existed.append(original_data)
            else:
                try:
                    source = versioned_object.parent
                    data.pop('parent_id', None)
                    mapping = Mapping(
                        **data, mnemonic=mnemonic, parent=source, versioned_object_id=versioned_object.id,
                    )
                    mapping.to_concept_id = get(to_concept, 'id') or versioned_object.to_concept_id
                    mapping.to_concept_code = data.get('to_concept_code') or versioned_object.to_concept_code
                    mapping.to_concept_name = data.get('to_concept_name') or versioned_object.to_concept_name
                    mapping.to_source_id = get(to_source, 'id') or get(
                        to_concept, 'parent_id') or versioned_object.to_source_id
                    mapping.from_concept_id = get(from_concept, 'id') or versioned_object.from_concept_id
                    mapping.from_concept_code = get(from_concept, 'mnemonic') or versioned_object.from_concept_code
                    mapping.from_source_id = get(from_concept, 'parent_id') or versioned_object.from_source_id
                    mapping.save()

                    source_versions = [source]
                    if source_version_ids:
                        source_versions += list(Source.objects.filter(internal_reference_id__in=source_version_ids))
                    mapping.sources.set(source_versions)
                    mapping.save()

                    # other_versions = versioned_object.versions.exclude(id=mapping.id)
                    # if other_versions.exists():
                    #     other_versions.update(is_latest_version=False)

                    self.created.append(original_data)
                except Exception as ex:
                    self.log("Failed: {}".format(data['uri']))
                    self.log(ex.args)
                    self.failed.append({**original_data, 'errors': ex.args})

        self.elapsed_seconds = time.time() - self.start_time

        self.log(
            "Result (in {} secs) : Total: {} | Created: {} | Existed: {} | Failed: {}".format(
                self.elapsed_seconds, self.total, len(self.created), len(self.existed), len(self.failed)
            )
        )
        if self.existed:
            self.log("Existed")
            pprint(self.existed)

        if self.failed:
            self.log("Failed")
            pprint(self.failed)
