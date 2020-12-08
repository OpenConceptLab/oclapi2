import json
import time
from datetime import datetime

from django.db.models import F
from ocldev.oclfleximporter import OclFlexImporter
from pydash import compact

from core.collections.models import Collection
from core.common.constants import HEAD
from core.common.services import RedisService
from core.common.utils import get_api_internal_base_url, drop_version, queue_bulk_import
from core.concepts.models import Concept
from core.mappings.models import Mapping
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile


class ImportResults:
    def __init__(self, importer):
        self.json = json.loads(importer.import_results.to_json())
        self.detailed_summary = importer.import_results.get_detailed_summary()
        self.report = importer.import_results.display_report()

    def to_dict(self):
        return dict(
            json=self.json, detailed_summary=self.detailed_summary, report=self.report
        )


class BaseImporter:
    def __init__(
            self, content, username, update_if_exists, user=None, parse_data=True, set_user=True
    ):  # pylint: disable=too-many-arguments
        self.input_list = []
        self.user = None
        self.result = None
        self.importer = None
        self.content = content
        self.username = username
        self.update_if_exists = update_if_exists
        if parse_data:
            self.populate_input_list()

        if set_user:
            self.set_user()
        if user:
            self.user = user

    def populate_input_list(self):
        if isinstance(self.content, list):
            self.input_list = self.content
        else:
            for line in self.content.splitlines():
                self.input_list.append(json.loads(line))

    def set_user(self):
        self.user = UserProfile.objects.get(username=self.username)

    def run(self):
        raise NotImplementedError()


class BulkImport(BaseImporter):
    def __init__(self, content, username, update_if_exists):
        super().__init__(content, username, update_if_exists)
        self.initialize_importer()

    def initialize_importer(self):
        self.importer = OclFlexImporter(
            input_list=self.input_list,
            api_url_root=get_api_internal_base_url(),
            api_token=self.user.get_token(),
            do_update_if_exists=self.update_if_exists
        )

    def run(self):
        self.importer.process()
        self.result = ImportResults(self.importer)

        return self.result.to_dict()


CREATED = 1
UPDATED = 2
FAILED = 3


class BaseResourceImporter:
    mandatory_fields = set()
    allowed_fields = []

    def __init__(self, data, user, update_if_exists=False):
        self.user = user
        self.data = data
        self.update_if_exists = update_if_exists

    def get(self, attr, default_value=None):
        return self.data.get(attr, default_value)

    def parse(self):
        self.data = self.get_filter_allowed_fields()
        self.data['created_by'] = self.data['updated_by'] = self.user

    def get_filter_allowed_fields(self):
        return {k: v for k, v in self.data.items() if k in self.allowed_fields}

    def is_valid(self):
        return self.mandatory_fields.issubset(self.data.keys())

    def get_owner_type(self):
        return self.get('owner_type', '').lower()

    def is_user_owner(self):
        return self.get_owner_type() == 'user'

    def is_org_owner(self):
        return self.get_owner_type() == 'organization'

    def get_owner_type_filter(self):
        if self.is_user_owner():
            return 'user__username'

        return 'organization__mnemonic'

    def get_owner(self):
        owner = self.get('owner')

        if self.is_org_owner():
            return Organization.objects.filter(mnemonic=owner).first()

        return UserProfile.objects.filter(username=owner).first()

    @staticmethod
    def exists():
        return False

    def clean(self):
        if not self.is_valid():
            return False
        if self.exists():
            return None

        self.parse()
        return True

    def run(self):
        is_clean = self.clean()
        if not is_clean:
            return is_clean

        return self.process()

    def process(self):
        raise NotImplementedError()


class OrganizationImporter(BaseResourceImporter):
    mandatory_fields = {'id', 'name'}
    allowed_fields = ["id", "company", "extras", "location", "name", "public_access", "website"]

    def exists(self):
        return Organization.objects.filter(mnemonic=self.get('id')).exists()

    def parse(self):
        super().parse()
        self.data['mnemonic'] = self.data.pop('id')

    def process(self):
        org = Organization.objects.create(**self.data)
        if org:
            return CREATED
        return FAILED


class SourceImporter(BaseResourceImporter):
    mandatory_fields = {'id', 'short_code', 'name', 'full_name', 'owner_type', 'owner', 'source_type'}
    allowed_fields = [
        "id", "short_code", "name", "full_name", "description", "source_type", "custom_validation_schema",
        "public_access", "default_locale", "supported_locales", "website", "extras", "external_id",
    ]

    def exists(self):
        return Source.objects.filter(
            **{self.get_owner_type_filter(): self.get('owner'), 'mnemonic': self.get('id')}
        ).exists()

    def parse(self):
        owner_type = self.get('owner_type').lower()
        owner = self.get_owner()

        super().parse()

        self.data['mnemonic'] = self.data.pop('id')
        self.data[owner_type] = owner
        self.data['version'] = 'HEAD'

        supported_locales = self.get('supported_locales')
        if isinstance(supported_locales, str):
            self.data['supported_locales'] = supported_locales.split(',')

        self.data.pop('short_code')

    def process(self):
        source = Source(**self.data)
        errors = Source.persist_new(source, self.user)
        return errors or CREATED


class SourceVersionImporter(BaseResourceImporter):
    mandatory_fields = {"id"}
    allowed_fields = ["id", "external_id", "description", "released"]

    def exists(self):
        return Source.objects.filter(
            **{self.get_owner_type_filter(): self.get('owner'),
               'mnemonic': self.get('source'), 'version': self.get('id')}
        ).exists()

    def parse(self):
        owner_type = self.get('owner_type').lower()
        owner = self.get_owner()
        source = self.get('source')

        super().parse()

        self.data['version'] = self.data.pop('id')
        self.data['mnemonic'] = source
        self.data[owner_type] = owner

    def process(self):
        source = Source(**self.data)
        errors = Source.persist_new_version(source, self.user)
        return errors or UPDATED


class CollectionImporter(BaseResourceImporter):
    mandatory_fields = {'id', 'short_code', 'name', 'full_name', 'owner_type', 'owner', 'collection_type'}
    allowed_fields = [
        "id", "short_code", "name", "full_name", "description", "collection_type", "custom_validation_schema",
        "public_access", "default_locale", "supported_locales", "website", "extras", "external_id",
    ]

    def exists(self):
        return Collection.objects.filter(
            **{self.get_owner_type_filter(): self.get('owner'), 'mnemonic': self.get('id')}
        ).exists()

    def parse(self):
        owner_type = self.get('owner_type').lower()
        owner = self.get_owner()

        super().parse()

        self.data['mnemonic'] = self.data.pop('id')
        self.data[owner_type] = owner
        self.data['version'] = 'HEAD'

        supported_locales = self.get('supported_locales')
        if isinstance(supported_locales, str):
            self.data['supported_locales'] = supported_locales.split(',')

        self.data.pop('short_code')

    def process(self):
        coll = Collection(**self.data)
        errors = Collection.persist_new(coll, self.user)
        return errors or CREATED


class CollectionVersionImporter(BaseResourceImporter):
    mandatory_fields = {"id"}
    allowed_fields = ["id", "external_id", "description", "released"]

    def exists(self):
        return Collection.objects.filter(
            **{self.get_owner_type_filter(): self.get('owner'),
               'mnemonic': self.get('collection'), 'version': self.get('id')}
        ).exists()

    def parse(self):
        owner_type = self.get('owner_type').lower()
        owner = self.get_owner()
        collection = self.get('collection')

        super().parse()

        self.data['version'] = self.data.pop('id')
        self.data['mnemonic'] = collection
        self.data[owner_type] = owner

    def process(self):
        coll = Collection(**self.data)
        errors = Collection.persist_new_version(coll, self.user)
        return errors or UPDATED


class ConceptImporter(BaseResourceImporter):
    mandatory_fields = {"id"}
    allowed_fields = ["id", "external_id", "concept_class", "datatype", "names", "descriptions", "retired", "extras"]

    def __init__(self, data, user, update_if_exists):
        super().__init__(data, user, update_if_exists)
        self.version = False
        self.queryset = None

    def exists(self):
        return self.get_queryset().exists()

    def get_queryset(self):
        if self.queryset:
            return self.queryset

        self.queryset = Concept.objects.filter(
            **{'parent__' + self.get_owner_type_filter(): self.get('owner'),
               'parent__mnemonic': self.get('source'),
               'mnemonic': self.get('id'), 'id': F('versioned_object_id')}
        )
        return self.queryset

    def parse(self):
        source = Source.objects.filter(
            **{self.get_owner_type_filter(): self.get('owner')}, mnemonic=self.get('source'), version=HEAD
        ).first()
        super().parse()
        self.data['parent'] = source
        self.data['name'] = self.data['mnemonic'] = self.data.pop('id')

    def clean(self):
        if not self.is_valid():
            return False
        if self.exists() and self.update_if_exists:
            self.version = True

        self.parse()
        return True

    def process(self):
        if self.version:
            instance = self.get_queryset().first().clone()
            errors = Concept.create_new_version_for(instance, self.data, self.user)
            return errors or UPDATED

        instance = Concept.persist_new(self.data, self.user)
        if instance.id:
            return CREATED
        return instance.errors or FAILED


class MappingImporter(BaseResourceImporter):
    mandatory_fields = {"map_type", "from_concept_url"}
    allowed_fields = [
        "id", "map_type", "from_concept_url", "to_source_url", "to_concept_url", "to_concept_code",
        "to_concept_name", "extras", "external_id"
    ]

    def __init__(self, data, user, update_if_exists):
        super().__init__(data, user, update_if_exists)
        self.version = False
        self.queryset = None

    def exists(self):
        return self.get_queryset().exists()

    def get_queryset(self):
        if self.queryset:
            return self.queryset

        from_concept_url = self.get('from_concept_url')
        to_concept_url = self.get('to_concept_url')
        to_concept_code = self.get('to_concept_code')
        to_source_url = self.get('to_source_url')
        filters = {
            'parent__' + self.get_owner_type_filter(): self.get('owner'),
            'parent__mnemonic': self.get('source'),
            'id': F('versioned_object_id'),
            'map_type': self.get('map_type'),
            'from_concept__uri__icontains': drop_version(from_concept_url),
        }
        if to_concept_url:
            filters['to_concept__uri__icontains'] = drop_version(to_concept_url)
        if to_concept_code and to_source_url:
            filters['to_concept_code'] = to_concept_code
            filters['to_source__uri__icontains'] = drop_version(to_source_url)

        self.queryset = Mapping.objects.filter(**filters)

        return self.queryset

    def parse(self):
        source = Source.objects.filter(
            **{self.get_owner_type_filter(): self.get('owner')}, mnemonic=self.get('source'), version=HEAD
        ).first()
        self.data = self.get_filter_allowed_fields()
        self.data['parent'] = source

        if self.get('id'):
            self.data['mnemonic'] = self.data.pop('id')

    def clean(self):
        if not self.is_valid():
            return False
        if self.exists() and self.update_if_exists:
            self.version = True

        self.parse()
        return True

    def process(self):
        if self.version:
            instance = self.get_queryset().first().clone()
            errors = Mapping.create_new_version_for(instance, self.data, self.user)
            return errors or UPDATED
        instance = Mapping.persist_new(self.data, self.user)
        if instance.id:
            return CREATED
        return instance.errors or FAILED


class BulkImportInline(BaseImporter):
    def __init__(   # pylint: disable=too-many-arguments
            self, content, username, update_if_exists=False, input_list=None, user=None, set_user=True,
            self_task_id=None
    ):
        super().__init__(content, username, update_if_exists, user, not bool(input_list), set_user)
        self.self_task_id = self_task_id
        if input_list:
            self.input_list = input_list
        self.unknown = []
        self.invalid = []
        self.exists = []
        self.created = []
        self.updated = []
        self.failed = []
        self.exception = []
        self.others = []
        self.processed = 0
        self.total = len(self.input_list)
        self.start_time = time.time()
        self.elapsed_seconds = 0

    def handle_item_import_result(self, result, item):
        if result is None:
            self.exists.append(item)
            return
        if result is False:
            self.invalid.append(item)
            return
        if result == FAILED or isinstance(result, dict):
            self.failed.append(item)
            return
        if result == CREATED:
            self.created.append(item)
            return
        if result == UPDATED:
            self.updated.append(item)
            return

        print("****Unexpected Result****", result)
        self.others.append(item)

    def notify_progress(self):
        if self.self_task_id:
            service = RedisService()
            service.set(self.self_task_id, self.processed)

    def run(self):
        for original_item in self.input_list:
            self.processed += 1
            self.notify_progress()
            item = original_item.copy()
            item_type = item.pop('type', '').lower()
            if not item_type:
                self.unknown.append(original_item)
            if item_type == 'organization':
                self.handle_item_import_result(
                    OrganizationImporter(item, self.user, self.update_if_exists).run(), original_item
                )
                continue
            if item_type == 'source':
                self.handle_item_import_result(
                    SourceImporter(item, self.user, self.update_if_exists).run(), original_item
                )
                continue
            if item_type == 'source version':
                self.handle_item_import_result(
                    SourceVersionImporter(item, self.user, self.update_if_exists).run(), original_item
                )
                continue
            if item_type == 'collection':
                self.handle_item_import_result(
                    CollectionImporter(item, self.user, self.update_if_exists).run(), original_item
                )
                continue
            if item_type == 'collection version':
                self.handle_item_import_result(
                    CollectionVersionImporter(item, self.user, self.update_if_exists).run(), original_item
                )
                continue
            if item_type == 'concept':
                self.handle_item_import_result(
                    ConceptImporter(item, self.user, self.update_if_exists).run(), original_item
                )
                continue
            if item_type == 'mapping':
                self.handle_item_import_result(
                    MappingImporter(item, self.user, self.update_if_exists).run(), original_item
                )
                continue

        self.elapsed_seconds = time.time() - self.start_time

        self.make_result()

        return self.result

    @property
    def detailed_summary(self):
        return "Processed: {}/{} | Created: {} | Updated: {} | Existing: {} | Time: {}secs".format(
            self.processed, self.total, len(self.created), len(self.updated), len(self.exists), self.elapsed_seconds
        )

    @property
    def json_result(self):
        return dict(
            total=self.total, processed=self.processed, created=self.created, updated=self.updated,
            invalid=self.invalid, exists=self.exists, failed=self.failed, exception=self.exception,
            others=self.others, unknown=self.unknown, elapsed_seconds=self.elapsed_seconds
        )

    @property
    def report(self):
        return {
            k: len(v) if isinstance(v, list) else v for k, v in self.json_result.items()
        }

    def make_result(self):
        self.result = dict(
            json=self.json_result, detailed_summary=self.detailed_summary, report=self.report
        )


class BulkImportParallelRunner(BaseImporter):  # pragma: no cover
    def __init__(
            self, content, username, update_if_exists, parallel=None, self_task_id=None
    ):  # pylint: disable=too-many-arguments
        super().__init__(content, username, update_if_exists, None, False)
        self.start_time = time.time()
        self.self_task_id = self_task_id
        self.username = username
        self.total = 0
        self.resource_distribution = dict()
        self.parallel = int(parallel) if parallel else 5
        self.tasks = []
        self.results = []
        self.elapsed_seconds = 0
        self.resource_wise_time = dict()
        self.parts = [[]]
        self.result = None
        self._json_result = None
        self.redis_service = RedisService()
        if self.content:
            self.input_list = self.content.splitlines()
            self.total = len(self.input_list)
        self.separate_data()
        self.make_parts()

    def separate_data(self):
        for line in self.input_list:
            data = json.loads(line)
            data_type = data['type']
            if data_type not in self.resource_distribution:
                self.resource_distribution[data_type] = []
            self.resource_distribution[data_type].append(data)

    def make_parts(self):
        prev_line = None
        orgs = self.resource_distribution.get('Organization', None)
        sources = self.resource_distribution.get('Source', None)
        collections = self.resource_distribution.get('Collection', None)
        if orgs:
            self.parts = [orgs]
        if sources:
            self.parts.append(sources)
        if collections:
            self.parts.append(collections)

        self.parts = compact(self.parts)

        self.parts.append([])

        for data in self.input_list:
            line = json.loads(data)
            data_type = line.get('type', None).lower()
            if data_type not in ['organization', 'source', 'collection']:
                if prev_line:
                    prev_type = prev_line.get('type').lower()
                    if prev_type == data_type or (
                            data_type not in ['concept', 'mapping'] and prev_type not in ['concept', 'mapping']
                    ):
                        self.parts[-1].append(line)
                    else:
                        self.parts.append([line])
                else:
                    self.parts[-1].append(line)
                prev_line = line

    @staticmethod
    def chunker_list(seq, size):
        return (seq[i::size] for i in range(size))

    def is_any_process_alive(self):
        if not self.tasks:
            return False

        if all(task.state == 'SUCCESS' for task in self.tasks):
            return False
        if any(task.state in ['FAILURE'] for task in self.tasks):
            raise Exception('one of the task failed')
        if any(task.state in ['RETRY'] for task in self.tasks):
            raise Exception('one of the task needs retry')

        return True

    def get_overall_tasks_progress(self):
        total_processed = 0
        if not self.tasks:
            return total_processed

        for task in self.tasks:
            try:
                if task.task_id:
                    total_processed += self.redis_service.get_int(task.task_id)
            except:  # pylint: disable=bare-except
                pass

        return total_processed

    def notify_progress(self):
        if self.self_task_id:
            self.redis_service.set(
                self.self_task_id, "Started: {} | Processed: {}/{} | Time: {}secs".format(
                    self.start_time_formatted, self.get_overall_tasks_progress(), self.total, self.elapsed_seconds
                )
            )

    def wait_till_tasks_alive(self):
        while self.is_any_process_alive():
            self.update_elapsed_seconds()
            self.notify_progress()
            time.sleep(1)

    def run(self):
        for part_list in self.parts:
            part_type = part_list[0].get('type').lower()
            is_child = part_type in ['concept', 'mapping']
            start_time = time.time()
            self.queue_tasks(part_list, is_child)
            self.wait_till_tasks_alive()
            if is_child:
                if part_type not in self.resource_wise_time:
                    self.resource_wise_time[part_type] = 0
                self.resource_wise_time[part_type] += (time.time() - start_time)

        self.update_elapsed_seconds()

        self.make_result()

        return self.result

    def update_elapsed_seconds(self):
        self.elapsed_seconds = time.time() - self.start_time

    @property
    def detailed_summary(self):
        result = self.json_result
        return "Started: {} | Processed: {}/{} | Created: {} | Updated: {} | Existing: {} | Time: {}secs".format(
            self.start_time_formatted, result.get('processed'), result.get('total'),
            len(result.get('created')), len(result.get('updated')), len(result.get('exists')), self.elapsed_seconds
        )

    @property
    def start_time_formatted(self):
        return datetime.fromtimestamp(self.start_time)

    @property
    def json_result(self):
        if self._json_result:
            return self._json_result

        total_result = dict(
            total=0, processed=0, created=[], updated=[],
            invalid=[], exists=[], failed=[], exception=[],
            others=[], unknown=[], elapsed_seconds=self.elapsed_seconds
        )
        for task in self.tasks:
            result = task.result.get('json')
            for key in total_result:
                total_result[key] += result.get(key)

        total_result['start_time'] = self.start_time_formatted
        total_result['elapsed_seconds'] = self.elapsed_seconds
        total_result['child_resource_time_distribution'] = self.resource_wise_time
        self._json_result = total_result
        return self._json_result

    @property
    def report(self):
        data = {
            k: len(v) if isinstance(v, list) else v for k, v in self.json_result.items()
        }

        data['child_resource_time_distribution'] = self.resource_wise_time

        return data

    def make_result(self):
        self.result = dict(
            json=self.json_result, detailed_summary=self.detailed_summary, report=self.report
        )

    def queue_tasks(self, part_list, is_child):
        if is_child:
            chunked_lists = self.chunker_list(part_list, self.parallel)
        else:
            chunked_lists = [part_list]

        for _list in compact(chunked_lists):
            self.tasks.append(
                queue_bulk_import(
                    to_import=_list, import_queue='concurrent', username=self.username,
                    update_if_exists=self.update_if_exists, threads=None, inline=True, sub_task=True,
                )
            )
