import logging
from datetime import datetime
import tarfile
import tempfile
import zipfile
from typing import List
from zipfile import ZipFile

import ijson as ijson
import requests
from pydantic import BaseModel

from core.code_systems.serializers import CodeSystemDetailSerializer
from core.common.serializers import IdentifierSerializer
from core.common.tasks import bulk_import_subtask
from core.common.tasks import chordfinisher
from celery import chord, group, chain

from core.code_systems.converter import CodeSystemConverter
from core.importers.models import SourceImporter, SourceVersionImporter, ConceptImporter, OrganizationImporter, \
    CollectionImporter, CollectionVersionImporter, MappingImporter, ReferenceImporter, CREATED, UPDATED, FAILED, \
    DELETED, NOT_FOUND, PERMISSION_DENIED, UNCHANGED
from core.sources.models import Source
from core.users.models import UserProfile

logger = logging.getLogger('oclapi')


class ImporterUtils:

    @staticmethod
    def fetch_to_temp_file(remote_file, temp):
        for rf_block in remote_file.iter_content(1024):
            temp.write(rf_block)
        temp.flush()

    @staticmethod
    def is_zipped_or_tarred(temp):
        temp.seek(0)
        is_zipped = zipfile.is_zipfile(temp)
        if not is_zipped:
            temp.seek(0)
            is_tarred = tarfile.is_tarfile(temp)
        temp.seek(0)
        return is_zipped, is_tarred


class ImportResultSummaryResource(BaseModel):
    type: str
    total: int
    imported: int = 0


class ImportResultSummary(BaseModel):
    resources: List[ImportResultSummaryResource] = []


class ImportResult(BaseModel):
    id: str
    time_started: datetime = datetime.now()
    time_finished: datetime = None
    summary: ImportResultSummary = ImportResultSummary()
    tasks: list = list()


class Importer:
    path: str
    username: str
    owner_type: str
    owner: str
    import_type: str = 'default'
    BATCH_SIZE: int = 100

    def __init__(self, path, username, owner_type, owner, import_type='default'):
        super().__init__()
        self.path = path
        self.username = username
        self.owner_type = owner_type
        self.owner = owner
        self.import_type = import_type

    def is_npm_import(self) -> bool:
        return self.import_type == 'npm'

    def run(self):
        resource_types = ['CodeSystem']  # , 'ValueSet', 'ConceptMap']
        resource_types.extend(ResourceImporter.get_resource_types())

        resources = {}
        remote_file = requests.get(self.path, stream=True)
        with tempfile.NamedTemporaryFile() as temp:
            ImporterUtils.fetch_to_temp_file(remote_file, temp)

            is_zipped, is_tarred = ImporterUtils.is_zipped_or_tarred(temp)

            if is_zipped:
                with ZipFile(temp) as package:
                    files = package.namelist()
                    for file_name in files:
                        if self.is_importable_file(file_name):
                            with package.open(file_name) as json_file:
                                self.categorize_resources(json_file, file_name, resource_types, resources)
            elif is_tarred:
                with tarfile.open(fileobj=temp, mode='r') as package:
                    for file_name in package.getnames():
                        if self.is_importable_file(file_name):
                            with package.extractfile(file_name) as json_file:
                                self.categorize_resources(json_file, file_name, resource_types, resources)
            else:
                self.categorize_resources(temp, '', resource_types, resources)

        tasks = self.prepare_tasks(resource_types, resources)

        task = Importer.schedule_tasks(tasks)

        # Return the task id of the chain to track the end of execution.
        # We do not wait for the end of execution of tasks here to free up worker and memory.
        # It is also to be able to pick up running tasks in the event of restart and not having to handle restarting the
        # main task.
        # In the future we will let the user approve the import before scheduling tasks, thus we save tasks in results.
        result = ImportResult(id=task.id, tasks=tasks)

        for resource_type, files in resources.items():
            resource_count = 0
            for file_name, count in files.items():
                resource_count += count
            if resource_count > 0:
                result.summary.resources.append(ImportResultSummaryResource(type=resource_type, total=resource_count))

        return result.model_dump()

    def prepare_tasks(self, resource_types, resources):
        tasks = []
        # Import in groups in order. Resources within groups are imported in parallel.
        for resource_type in resource_types:
            files = []
            groups = []
            batch_size = self.BATCH_SIZE
            for file, count in resources.get(resource_type).items():
                start_index = 0
                while start_index < count:
                    if (count - start_index) < batch_size:
                        # If a file contains less than batch resources then include in a single task.
                        end_index = count
                    else:
                        # If a file contains more than batch resources then split in multiple tasks in batches.
                        end_index = start_index + batch_size

                    files.append({"file": file, "start_index": start_index, "end_index": end_index})

                    batch_size -= end_index - start_index
                    start_index = end_index

                    if batch_size <= 0:
                        groups.append({"path": self.path, "username": self.username, "owner_type": self.owner_type,
                                       "owner": self.owner, "resource_type": resource_type, "files": files})
                        files = []
                        batch_size = self.BATCH_SIZE

            if groups:
                tasks.append(groups)
        return tasks

    @staticmethod
    def schedule_tasks(tasks):
        chord_tasks = []
        for task in tasks:
            group_tasks = []
            for group_task in task:
                # Wrap groups in chords with chordfinisher to wait for group results before running another group.
                # TODO: create 2 queues for new bulk import subtasks: bulk_import_subtask and bulk_import_subtask_root
                group_tasks.append(bulk_import_subtask.s(group_task['path'], group_task['username'],
                                                         group_task['owner_type'], group_task['owner'],
                                                         group_task['resource_type'], group_task['files'])
                                   .set(queue='concurrent'))
            chord_tasks.append(chord(group(group_tasks), chordfinisher.si()))
        task = chain(chord_tasks)()
        return task

    def is_importable_file(self, file_name):
        return file_name.endswith('.json') and ((self.is_npm_import() and file_name.startswith('package/')
                                                 and file_name.count('/') == 1)
                                                or not self.is_npm_import())

    def categorize_resources(self, json_file, file_name, resource_types, resources={}):
        for resource_type in resource_types:
            if resource_type not in resources:
                resources.update({resource_type: {}})

        parser = ijson.parse(json_file, multiple_values=True, allow_comments=True)
        for prefix, event, value in parser:
            # Expect {"resourceType": ""}{"resourceType": ""} or {"type": ""}{"type": ""}
            if event == 'string' and (prefix == 'resourceType' or prefix == 'type'):
                # Categorize files based on resourceType
                if value in resource_types:
                    resource_type = resources.get(value)
                    # Remember count of resources of the given type within a file
                    resource_type.update({file_name: resource_type.get(file_name, 0) + 1})

                if self.is_npm_import():  # Expect only one resource per file for npm
                    break


class ImportRequest:
    path: str
    user: UserProfile

    def __init__(self, owner_type, owner, username, resource_type):
        self.path = f'/{owner_type}/{owner}/{resource_type}/'
        self.user = UserProfile.objects.filter(username=username).first()


class ResourceImporter:
    resource_importers = [OrganizationImporter, SourceImporter, SourceVersionImporter, ConceptImporter, MappingImporter,
                          CollectionImporter, CollectionVersionImporter, ReferenceImporter]
    converters = [CodeSystemConverter]
    result_type = [CREATED, UPDATED, FAILED, DELETED, NOT_FOUND, PERMISSION_DENIED, UNCHANGED]

    @staticmethod
    def get_resource_types():
        resource_types = []
        for resource_importer in ResourceImporter.resource_importers:
            resource_types.append(resource_importer.get_resource_type())
        return resource_types

    def import_resource(self, resource, username, owner_type, owner):
        resource_type = resource.get('resourceType', None)
        if resource_type:
            # Handle fhir resources
            if resource_type == 'CodeSystem':
                url = resource.get('url')
                source = Source.objects.filter(canonical_url=url)
                if not source:
                    url = IdentifierSerializer.convert_fhir_url_to_ocl_uri(url, 'sources')
                    source = Source.objects.filter(uri=url)

                context = {
                    'request': ImportRequest(owner_type, owner, username, resource_type)
                }

                if source:
                    serializer = CodeSystemDetailSerializer(source.first(), data=resource, context=context)
                    result = UPDATED
                else:
                    serializer = CodeSystemDetailSerializer(data=resource, context=context)
                    result = CREATED
                if serializer.is_valid():
                    serializer.save()
                return serializer.errors if serializer.errors else result
        else:
            # Handle other resources
            for resource_importer in self.resource_importers:
                if resource_importer.can_handle(resource):
                    user_profile = UserProfile.objects.get(username=username)
                    result = resource_importer(resource, user_profile, True).run()
                    return result
        return None


class ImporterSubtask:
    path: str
    username: str
    owner_type: str
    owner: str
    file: str
    resource_type: str
    start_index: int
    end_index: int
    progress: int

    def __init__(self, path, username, owner_type, owner, resource_type, files):
        super().__init__()
        self.path = path
        self.username = username
        self.owner_type = owner_type
        self.owner = owner
        self.resource_type = resource_type
        self.files = files

    def run(self):
        results = []
        remote_file = requests.get(self.path, stream=True)
        with tempfile.NamedTemporaryFile() as temp:
            ImporterUtils.fetch_to_temp_file(remote_file, temp)

            is_zipped, is_tarred = ImporterUtils.is_zipped_or_tarred(temp)

            if is_zipped:
                with ZipFile(temp) as package:
                    for file in self.files:
                        with package.open(file.get("file")) as json_file:
                            result = self.import_resource(json_file, file.get("start_index"), file.get("end_index"))
                            results.extend(result)
            elif is_tarred:
                with tarfile.open(fileobj=temp, mode='r') as package:
                    for file in self.files:
                        with package.extractfile(file.get("file")) as json_file:
                            result = self.import_resource(file.get("file"), json_file, file.get("start_index"),
                                                          file.get("end_index"))
                            results.extend(result)
            else:
                result = self.import_resource(temp)
                results.extend(result)

        return results

    def import_resource(self, file, json_file, start_index, end_index):
        parse = self.move_to_start_index(json_file, start_index)
        count = end_index - start_index
        results = []
        for resource in ijson.items(parse, '', multiple_values=True, allow_comments=True):
            if resource.get('__action', None) == 'DELETE':
                continue

            if resource.get('resourceType', None) == self.resource_type \
                    or resource.get('type', None) == self.resource_type:
                try:
                    if self.resource_type.lower() in ['source', 'collection']:
                        if 'owner_type' not in resource:
                            resource['owner_type'] = self.owner_type
                        if 'owner' not in resource:
                            resource['owner'] = self.owner
                    result = ResourceImporter().import_resource(resource, self.username, self.owner_type, self.owner)
                    results.append(result)
                except Exception as e:
                    error = f'Failed to import resource with id {resource.get("id", None)} from {self.path}/{file} to ' \
                            f'{self.owner_type}/{self.owner} by {self.username}'
                    logger.exception(error)
                    results.append([f'{error} due to: {str(e)}'])
                count -= 1
                if count <= 0:
                    break
        return results

    def move_to_start_index(self, json_file, start_index):
        if start_index != 0:
            index = 0
            parse_events = ijson.parse(json_file, multiple_values=True, allow_comments=True)
            for prefix, event, value in parse_events:
                if event == 'string' and (prefix == 'resourceType' or prefix == 'type'):
                    if value == self.resource_type:
                        index += 1
                        if index == start_index:
                            # Move the pointer to the next json top-level object
                            for prefix_inner, event_inner, value_inner in parse_events:
                                if event_inner == 'end_map' and prefix_inner == '':
                                    return parse_events
            # We should end up here only if no resources of the specified type
            return parse_events
        else:
            return json_file
