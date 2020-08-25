import json

from django.utils.text import compress_string
from ocldev.oclfleximporter import OclFlexImporter

from core.common.utils import get_base_url
from core.users.models import UserProfile


class ImportResults:
    def __init__(self, importer):
        self.json = compress_string(importer.import_results.to_json())
        self.detailed_summary = importer.import_results.get_detailed_summary()
        self.report = importer.import_results.display_report()


class BulkImport:
    def __init__(self, content, username, update_if_exists):
        self.input_list = []
        self.user = None
        self.result = None
        self.importer = None
        self.content = content
        self.username = username
        self.update_if_exists = update_if_exists
        self.populate_input_list()
        self.set_user()
        self.prepare_importer()

    def populate_input_list(self):
        for line in self.content.splitlines():
            self.input_list.append(json.loads(line))

    def set_user(self):
        self.user = UserProfile.objects.get(username=self.username)

    def prepare_importer(self):
        self.importer = OclFlexImporter(
            input_list=self.input_list,
            api_url_root=get_base_url(),
            api_token=self.user.auth_token.key,
            do_update_if_exists=self.update_if_exists
        )

    def run(self):
        self.importer.process()
        self.result = ImportResults(self.importer)
