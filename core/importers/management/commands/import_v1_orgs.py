import json

from django.core.management import BaseCommand
from pydash import get

from core.common.tasks import populate_indexes
from core.orgs.models import Organization
from pprint import pprint


class Command(BaseCommand):
    help = 'import v1 orgs'

    total = 0
    processed = 0
    created = []
    existed = []
    failed = []

    @staticmethod
    def log(msg):
        print("*******{}*******".format(msg))

    def handle(self, *args, **options):
        FILE_PATH = '/code/core/importers/v1_dump/data/exported_orgs.json'
        lines = open(FILE_PATH, 'r').readlines()

        self.log('STARTING ORGS IMPORT')
        self.total = len(lines)
        self.log('TOTAL: {}'.format(self.total))

        for line in lines:
            data = json.loads(line)
            original_data = data.copy()
            self.processed += 1
            _id = data.pop('_id')
            created_at = data.pop('created_at')
            updated_at = data.pop('updated_at')
            data['internal_reference_id'] = get(_id, '$oid')
            data['created_at'] = get(created_at, '$date')
            data['updated_at'] = get(updated_at, '$date')
            mnemonic = data.get('mnemonic')
            self.log("Processing: {} ({}/{})".format(mnemonic, self.processed, self.total))
            if Organization.objects.filter(mnemonic=mnemonic).exists():
                self.existed.append(original_data)
            else:
                org = Organization.objects.create(**data)
                if org:
                    self.created.append(original_data)
                else:
                    self.failed.append(original_data)

        populate_indexes.delay(['orgs'])

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
