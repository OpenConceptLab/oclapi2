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
    help = 'import v1 mappings'

    total = 0
    processed = 0
    created = []
    existed = []
    failed = []
    start_time = None
    elapsed_seconds = 0
    sources = dict()
    users = dict()
    concepts = dict()

    @staticmethod
    def log(msg):
        print("*******{}*******".format(msg))

    def get_concept(self, internal_reference_id):
        if internal_reference_id not in self.concepts:
            self.concepts[internal_reference_id] = Concept.objects.filter(
                internal_reference_id=internal_reference_id).first()

        return self.concepts[internal_reference_id]

    def get_source(self, internal_reference_id):
        if internal_reference_id not in self.sources:
            self.sources[internal_reference_id] = Source.objects.filter(
                internal_reference_id=internal_reference_id).first()

        return self.sources[internal_reference_id]

    def handle(self, *args, **options):
        self.start_time = time.time()
        FILE_PATH = '/code/core/importers/v1_dump/data/exported_mappings.json'
        lines = open(FILE_PATH, 'r').readlines()

        self.log('STARTING MAPPINGS IMPORT')
        self.total = len(lines)
        self.log('TOTAL: {}'.format(self.total))

        for line in lines:
            data = json.loads(line)
            original_data = data.copy()
            self.processed += 1
            created_at = data.pop('created_at')
            updated_at = data.pop('updated_at')
            created_by = data.get('created_by')
            updated_by = data.get('updated_by')
            _id = data.pop('_id')
            parent_id = get(data.pop('parent_id'), '$oid')
            mnemonic = data.get('mnemonic')
            to_source_id = get(data.pop('to_source_id'), '$oid')
            to_concept_id = get(data.pop('to_concept_id'), '$oid')
            from_concept_id = get(data.pop('from_concept_id'), '$oid')
            from_concept = self.get_concept(from_concept_id)
            if from_concept:
                data['from_concept'] = from_concept
                data['from_concept_code'] = get(data, 'from_concept_code') or from_concept.mnemonic
                data['from_source'] = from_concept.parent
            if to_concept_id:
                to_concept = self.get_concept(to_concept_id)
                if to_concept:
                    data['to_concept'] = to_concept
                    data['to_source'] = to_concept.parent
            elif to_source_id:
                to_source = self.get_source(to_source_id)
                if to_source:
                    data['to_source'] = to_source

            if get(data, 'from_concept', None) is None:
                self.failed.append({**original_data, 'errors': ['From concept not found']})

            data['internal_reference_id'] = get(_id, '$oid')
            data['created_at'] = get(created_at, '$date')
            data['updated_at'] = get(updated_at, '$date')

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
                    source = self.get_source(parent_id)
                    mapping = Mapping(**data, version=mnemonic, is_latest_version=False, parent=source)
                    mapping.full_clean()
                    mapping.save()
                    mapping.versioned_object_id = mapping.id
                    mapping.sources.add(source)
                    mapping.save()
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
