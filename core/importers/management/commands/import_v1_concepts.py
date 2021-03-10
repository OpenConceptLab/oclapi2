import json
import time
from pprint import pprint

from django.core.management import BaseCommand
from pydash import get

from core.common.constants import HEAD
from core.common.utils import generate_temp_version
from core.concepts.models import LocalizedText, Concept
from core.sources.models import Source
from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'import v1 sources'

    total = 0
    processed = 0
    created = []
    existed = []
    failed = []
    start_time = None
    elapsed_seconds = 0
    sources = dict()
    users = dict()

    @staticmethod
    def log(msg):
        print("*******{}*******".format(msg))

    def handle(self, *args, **options):
        self.start_time = time.time()
        FILE_PATH = '/code/core/importers/v1_dump/data/exported_concepts.json'
        lines = open(FILE_PATH, 'r').readlines()

        self.log('STARTING CONCEPTS IMPORT')
        self.total = len(lines)
        self.log('TOTAL: {}'.format(self.total))

        for line in lines:
            data = json.loads(line)
            original_data = data
            self.processed += 1
            data.pop('parent_type_id', None)
            created_at = data.pop('created_at')
            updated_at = data.pop('updated_at')
            created_by = data.get('created_by')
            updated_by = data.get('updated_by')
            _id = data.pop('_id')
            parent_id = data.pop('parent_id')
            descriptions_data = data.pop('descriptions', [])
            names_data = data.pop('names', [])
            mnemonic = data.get('mnemonic')

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
            if Concept.objects.filter(uri=data['uri']).exists():
                self.existed.append(original_data)
            else:
                try:
                    if parent_id in self.sources:
                        source = self.sources[parent_id]
                    else:
                        source = Source.objects.filter(internal_reference_id=parent_id, version=HEAD).first()
                        self.sources[parent_id] = source

                    names = self.get_locales(names_data)
                    descriptions = self.get_locales(descriptions_data)
                    concept = Concept.objects.create(
                        **data, version=generate_temp_version(), is_latest_version=False, parent=source)
                    concept.version = concept.id
                    concept.versioned_object_id = concept.id
                    concept.parent = source
                    concept.names.set(names)
                    concept.sources.add(source)
                    concept.descriptions.set(descriptions)
                    concept.update_mappings()
                    concept.save()
                    self.created.append(original_data)
                except Exception as ex:
                    self.log("Failed: ")
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

    @staticmethod
    def get_locales(names_data):
        names_data = names_data or []
        names = []
        for data in names_data:
            params = data.copy()
            internal_reference_id = params.pop('uuid')
            params['internal_reference_id'] = internal_reference_id
            qs = LocalizedText.objects.filter(internal_reference_id=internal_reference_id)
            if qs.exists():
                names.append(qs.first())
            else:
                names.append(LocalizedText.objects.create(**params))
        return names

