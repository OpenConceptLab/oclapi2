import json
import time
from pprint import pprint

from django.core.management import BaseCommand
from pydash import get

from core.sources.models import Source


class Command(BaseCommand):
    help = 'import v1 source/version ids'

    total = 0
    processed = 0
    created = []
    existed = []
    failed = []
    not_found = []
    start_time = None
    elapsed_seconds = 0

    @staticmethod
    def log(msg):
        print("*******{}*******".format(msg))

    def handle(self, *args, **options):
        self.start_time = time.time()
        FILE_PATH = '/code/core/importers/v1_dump/data/exported_source_ids.json'
        lines = open(FILE_PATH, 'r').readlines()
        FILE_PATH = '/code/core/importers/v1_dump/data/exported_sourceversion_ids.json'
        lines += open(FILE_PATH, 'r').readlines()

        self.log('STARTING SOURCE/VERSION IDS IMPORT')
        self.total = len(lines)
        self.log('TOTAL: {}'.format(self.total))

        for line in lines:
            data = json.loads(line)
            original_data = data.copy()
            try:
                _id = get(data.pop('_id'), '$oid')
                uri = data.pop('uri')
                self.processed += 1
                updated = Source.objects.filter(uri=uri).update(internal_reference_id=_id)
                if updated:
                    self.created.append(original_data)
                    self.log("Updated: {} ({}/{})".format(uri, self.processed, self.total))
                else:
                    self.not_found.append(original_data)
                    self.log("Not Found: {} ({}/{})".format(uri, self.processed, self.total))

            except Exception as ex:
                self.log("Failed: ")
                self.log(ex.args)
                self.failed.append({**original_data, 'errors': ex.args})

        self.elapsed_seconds = time.time() - self.start_time

        self.log(
            "Result (in {} secs) : Total: {} | Created: {} | NotFound: {} | Failed: {}".format(
                self.elapsed_seconds, self.total, len(self.created), len(self.not_found), len(self.failed)
            )
        )

        if self.existed:
            self.log("Existed")
            pprint(self.existed)

        if self.failed:
            self.log("Failed")
            pprint(self.failed)

        if self.not_found:
            self.log("Not Found")
            pprint(self.not_found)
