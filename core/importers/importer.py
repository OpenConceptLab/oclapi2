import json
import logging
import os
import shutil
from datetime import datetime
from functools import cached_property

import tarfile
import tempfile
import zipfile
from zipfile import ZipFile
from celery.result import AsyncResult, result_from_tuple
from celery import group

import ijson
import requests
from ijson import JSONError
from kombu import uuid
from packaging.version import Version
from pydantic import BaseModel, computed_field, PrivateAttr

from django.utils import timezone
from pydash import get
from rest_framework.exceptions import ValidationError

from core import settings
from core.common.serializers import IdentifierSerializer
from core.common.tasks import bulk_import_subtask, bulk_import_subtask_empty, bulk_import_queue
from core.common.tasks import import_finisher
from core.code_systems.converter import CodeSystemConverter
from core.common.utils import get_export_service
from core.importers.models import SourceImporter, SourceVersionImporter, ConceptImporter, OrganizationImporter, \
    CollectionImporter, CollectionVersionImporter, MappingImporter, ReferenceImporter, CREATED, UPDATED, FAILED, \
    DELETED, NOT_FOUND, PERMISSION_DENIED, UNCHANGED
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile
from core.collections.models import Collection
from core.code_systems.serializers import CodeSystemDetailSerializer
from core.concept_maps.serializers import ConceptMapDetailSerializer
from core.value_sets.serializers import ValueSetDetailSerializer


logger = logging.getLogger('oclapi')


class ImporterUtils:

    @staticmethod
    def fetch_to_file(remote_file, local_file):
        for rf_block in remote_file.iter_content(1024):
            local_file.write(rf_block)
        local_file.flush()
        local_file.seek(0)

    @staticmethod
    def is_zipped_or_tarred(temp):
        temp.seek(0)
        is_zipped = zipfile.is_zipfile(temp)
        is_tarred = False
        if not is_zipped:
            temp.seek(0)
            is_tarred = tarfile.is_tarfile(temp)
        temp.seek(0)
        return is_zipped, is_tarred


class ImportTaskSummary(BaseModel):
    total: int = 0
    processed: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    existing: int = 0
    failed: int = 0
    permission_denied: int = 0
    unchanged: int = 0
    failures: list = []
    dependencies: list = []


class ImportTask(BaseModel):
    import_task: tuple = ()
    _import_async_result: AsyncResult = PrivateAttr(default=None)
    time_started: datetime = timezone.now()
    _time_finished: datetime = PrivateAttr(default=None)
    dependencies: list = []
    subtask_ids: list = []
    initial_summary: ImportTaskSummary = ImportTaskSummary()
    final_summary: ImportTaskSummary = None

    @staticmethod
    def import_task_from_async_result(async_result: AsyncResult):
        if async_result and async_result.state == 'SUCCESS' and async_result.result.get('import_task', None):
            return ImportTask(**async_result.result)
        return None

    @staticmethod
    def import_task_from_json(result_json):
        if get(result_json, 'import_task', None):
            return ImportTask(**result_json)
        return None

    @property
    def import_async_result(self):
        if self._import_async_result:
            return self._import_async_result

        if self.import_task:
            self._import_async_result = result_from_tuple(self.import_task)
            return self._import_async_result
        return None

    def revoke(self):
        import_final_task = self.import_async_result
        import_final_task.revoke()
        for task_id in self.subtask_ids:
            child = AsyncResult(task_id)
            child.revoke()

    @import_async_result.setter
    def import_async_result(self, import_async_result):
        self._import_async_result = import_async_result
        if import_async_result:
            self.import_task = import_async_result.as_tuple()

    @computed_field
    @property
    def time_finished(self) -> datetime:
        if self._time_finished:
            return self._time_finished
        if self.import_async_result and self.import_async_result.ready():
            self._time_finished = self.import_async_result.result.get('time_finished')
            return self._time_finished
        return None

    @time_finished.setter
    def time_finished(self, value):
        self._time_finished = value

    @computed_field
    @cached_property
    def summary(self) -> ImportTaskSummary:  # pylint: disable=too-many-branches
        if self.import_async_result and self.import_async_result.ready():
            return ImportTaskSummary(**self.import_async_result.result.get('final_summary'))
        summary = self.initial_summary.copy()

        if not self.import_async_result:
            return summary

        for task_id in self.subtask_ids:
            child = AsyncResult(task_id)
            if child.ready():
                results = child.result
                if not isinstance(results, list):
                    results = [child.result]

                for result in results:
                    summary.processed += 1
                    if result == CREATED:
                        summary.created += 1
                    elif result == UPDATED:
                        summary.updated += 1
                    elif result == DELETED:
                        summary.deleted += 1
                    elif result == PERMISSION_DENIED:
                        summary.permission_denied += 1
                    elif result == UNCHANGED:
                        summary.unchanged += 1
                    else:
                        summary.failed += 1
                        summary.failures.append(result)
            else:
                break  # inspect further only if the current one is ready
        return summary

    @computed_field
    def json(self) -> str:  # pylint: disable=arguments-differ
        return self.model_dump(exclude={'json', 'import_task', 'initial_summary', 'final_summary'})

    @computed_field
    def report(self) -> str:
        return self.detailed_summary

    @computed_field
    def elapsed_seconds(self) -> int:
        if self.time_finished:
            return (self.time_finished - self.time_started).total_seconds()
        return (timezone.now() - self.time_started).total_seconds()

    @computed_field
    def detailed_summary(self) -> str:
        summary = self.summary
        failures = ''
        count = 1
        for failure in summary.failures:
            failures += f" {count}) {failure}"
            count += 1
        return f"Started: {self.time_started} | Processed: {summary.processed}/{summary.total} | " \
               f"Created: {summary.created} | Updated: {summary.updated} | " \
               f"Deleted: {summary.deleted} | Existing: {summary.existing} | " \
               f"Permission Denied: {summary.permission_denied} | " \
               f"Unchanged: {summary.unchanged} | Dependencies: {summary.dependencies} | " \
               f"Failed: {summary.failed} | " \
               f"Time: {self.elapsed_seconds}secs | " \
               f"Failures: {failures} "


class Importer:
    task_id: str
    path: str
    username: str
    owner_type: str
    owner: str
    import_type: str = 'default'
    MIN_BATCH_SIZE: int = 50
    IMPORT_CACHE: str = "import_cache/"

    # pylint: disable=too-many-arguments
    def __init__(self, task_id, path, username, owner_type, owner, import_type='default'):
        super().__init__()
        self.task_id = task_id
        self.path = path
        self.username = username
        self.owner_type = owner_type
        self.owner = owner
        self.import_type = import_type

    def is_npm_import(self) -> bool:
        return self.import_type == 'npm'

    def run(self):  # pylint: disable=too-many-locals
        time_started = timezone.now()
        resource_types = ['CodeSystem', 'ValueSet', 'ConceptMap']
        resource_types.extend(ResourceImporter.get_resource_types())
        if not self.path.startswith('/'):  # not local path
            key = self.path
            protocol_index = key.find('://')
            if protocol_index:
                key = key[protocol_index+3:]
            key = key.replace('/', '_')
            if settings.DEBUG:
                file_url = os.path.join(settings.MEDIA_ROOT, 'import_uploads')
                os.makedirs(file_url, exist_ok=True)
                file_url = os.path.join(file_url, key)

                with requests.get(self.path, stream=True) as import_file:
                    if not import_file.ok:
                        raise ImportError(f"Failed to GET {self.path}, responded with {import_file.status_code}")
                    with open(file_url, 'wb') as temp:
                        shutil.copyfileobj(import_file.raw, temp)
                self.path = file_url
            else:
                upload_service = get_export_service()
                if not (self.path.startswith(self.IMPORT_CACHE) and upload_service.exists(self.path)):
                    # if not already uploaded by the view
                    if not key.startswith(self.IMPORT_CACHE):
                        key = self.IMPORT_CACHE + key

                    with requests.get(self.path, stream=True) as import_file:
                        if not import_file.ok:
                            raise ImportError(f"Failed to GET {self.path}, responded with {import_file.status_code}")
                        upload_service.upload(key, import_file.raw,
                                              metadata={'ContentType': 'application/octet-stream'},
                                              headers={'content-type': 'application/octet-stream'})
                    self.path = key

        resources = {}
        dependencies = []

        self.prepare_resources(self.path, resource_types, dependencies, [], resources)
        tasks = self.prepare_tasks(resource_types, dependencies, resources)
        if tasks:
            # In the future we will let the user approve the import before scheduling tasks.
            task, subtask_ids = self.schedule_tasks(tasks)

            # Return the task id of the chain to track the end of execution.
            # We do not wait for the end of execution of tasks here to free up worker and memory.
            # It is also to be able to pick up running tasks in the event of restart and not having to handle restarting
            # the main task.
            result = ImportTask(import_task=task.as_tuple(), subtask_ids=subtask_ids, time_started=time_started,
                                dependencies=dependencies)

            for _, files in resources.items():
                for _, count in files.items():
                    result.initial_summary.total += count

            result.initial_summary.dependencies = dependencies
            return result.model_dump(exclude={'summary', 'final_summary'})

        import_task = ImportTask(time_started=time_started, dependencies=dependencies)
        import_task.initial_summary.dependencies = dependencies
        import_task.time_finished = timezone.now()
        return import_task.model_dump()

    def prepare_resources(self, path, resource_types, dependencies, visited_dependencies, resources):
        # pylint: disable=too-many-locals,too-many-branches
        with open(path, 'rb') if path.startswith('/') else tempfile.NamedTemporaryFile() as temp:
            request_path = path
            if not path.startswith('/'):  # not local file
                if path.startswith(self.IMPORT_CACHE):
                    request_path = get_export_service().url_for(path)
                remote_file = requests.get(request_path, stream=True)
                ImporterUtils.fetch_to_file(remote_file, temp)

            is_zipped, is_tarred = ImporterUtils.is_zipped_or_tarred(temp)
            if is_zipped:
                if self.is_npm_import():
                    package_json = zipfile.Path(temp, "package/package.json")
                    if package_json.is_file():
                        with package_json.open(mode="r") as package_file:
                            self.traverse_dependencies(package_file, path, resource_types, dependencies,
                                                       visited_dependencies, resources)

                with ZipFile(temp) as package:
                    files = package.namelist()
                    for file_path in files:
                        if self.is_importable_file(file_path):
                            with package.open(file_path) as json_file:
                                self.categorize_resources(json_file, path, file_path, resource_types, resources)
            elif is_tarred:
                with tarfile.open(fileobj=temp, mode='r') as package:
                    if self.is_npm_import():
                        try:
                            with package.extractfile('package/package.json') as package_file:
                                self.traverse_dependencies(package_file, path, resource_types, dependencies,
                                                           visited_dependencies, resources)
                        except KeyError:
                            pass

                    for file_path in package.getnames():
                        if self.is_importable_file(file_path):
                            with package.extractfile(file_path) as json_file:
                                self.categorize_resources(json_file, path, file_path, resource_types, resources)
            else:
                self.categorize_resources(temp, path, '', resource_types, resources)

            if not dependencies:
                dependencies.append(path)

    # pylint: disable=too-many-locals
    def traverse_dependencies(self, package_file, path, resource_types, dependencies, visited_dependencies, resources):
        # Implements Deep First Search algorithm
        visited_dependencies.append(path)

        package_json = json.loads(package_file.read())
        for package_name, package_version in package_json.get('dependencies', {}).items():
            if package_version.endswith('.x'):
                # Fetch the latest patch version
                packages = requests.get('https://packages.simplifier.net/' + package_name)
                if packages.ok:
                    versions_list = list(packages.json().get('versions', {}).keys())
                    versions_list.sort(key=Version, reverse=True)
                    package_major_version = package_version.removesuffix('.x')
                    found_version = None
                    for version in versions_list:
                        if version.startswith(package_major_version):
                            found_version = version
                            break
                    if found_version:
                        package_version = found_version
                    else:
                        raise LookupError(f'No version matching {package_version} found in {versions_list} for package '
                                          f'{package_name}')
            dependency_path = f'https://packages.simplifier.net/{package_name}/{package_version}/'
            if dependency_path not in dependencies:
                if dependency_path in visited_dependencies:
                    # Found circular dependency... Ignore and continue.
                    continue

                if dependency_path == 'https://packages.simplifier.net/hl7.fhir.r4.core/4.0.1/':
                    # Do not import the core package if it's on the list of dependencies
                    visited_dependencies.append(dependency_path)
                    dependencies.append(dependency_path)
                    continue
                self.prepare_resources(dependency_path, resource_types, dependencies, visited_dependencies, resources)

        dependencies.append(path)

    def prepare_tasks(self, resource_types, packages, resources):
        tasks = []
        task_batch_size = self.calculate_batch_size(resources)

        # Import in groups in order. Resources within groups are imported in parallel.
        for package in packages:
            # Import dependencies in order.
            for resource_type in resource_types:
                # Import resource types in order.
                files = []
                groups = []
                batch_size = task_batch_size
                for filepath, count in resources.get(resource_type).items():
                    if not filepath.startswith(package):
                        continue

                    start_index = 0
                    while start_index < count:
                        if (count - start_index) < batch_size:
                            # If a file contains less than batch resources then include in a single task.
                            end_index = count
                        else:
                            # If a file contains more than batch resources then split in multiple tasks in batches.
                            end_index = start_index + batch_size
                        filepath = filepath.removeprefix(package)
                        filepath = filepath.removeprefix('/')
                        files.append({"filepath": filepath, "start_index": start_index, "end_index": end_index})

                        batch_size -= end_index - start_index
                        start_index = end_index

                        if batch_size <= 0:
                            groups.append({"path": package, "username": self.username, "owner_type": self.owner_type,
                                           "owner": self.owner, "resource_type": resource_type, "files": files})
                            files = []
                            batch_size = task_batch_size

                if files:
                    # Append last task to the group
                    groups.append({"path": package, "username": self.username, "owner_type": self.owner_type,
                                   "owner": self.owner, "resource_type": resource_type, "files": files})

                if groups:
                    tasks.append(groups)
        return tasks

    def calculate_batch_size(self, resources):
        # Count all items to determine batch size
        all_count = 0
        for _, item in resources.items():
            for _, count in item.items():
                all_count += count
        if all_count > 50000:
            task_batch_size = round(all_count / 1000)
        else:
            task_batch_size = self.MIN_BATCH_SIZE
        return task_batch_size

    def schedule_tasks(self, tasks):
        subtask_ids = []
        group_queue = []
        for task in tasks:
            group_tasks = []
            for group_task in task:
                # TODO: create 2 queues for new bulk import subtasks: bulk_import_subtask and bulk_import_subtask_root
                subtask_id = uuid()
                subtask_ids.append(subtask_id)
                group_tasks.append(bulk_import_subtask.si(group_task['path'], group_task['username'],
                                                          group_task['owner_type'], group_task['owner'],
                                                          group_task['resource_type'], group_task['files'])
                                   .set(task_id=subtask_id))
            if len(group_tasks) == 1:  # Prevent celery from converting group to a single task
                group_tasks.append(bulk_import_subtask_empty.si())

            group_queue.append(group(group_tasks))

        final_task_id = uuid()
        group_queue.append(import_finisher.si(self.task_id).set(task_id=final_task_id))

        # Celery cannot handle chain of groups that have hundreds of tasks thus we use a task that schedules
        # a group of tasks once the previous group is done.
        bulk_import_queue.si(group_queue).apply_async(queue='concurrent')

        # We pass the final task id to be able to track the end of execution and track progress.
        final_task = AsyncResult(final_task_id)
        return final_task, subtask_ids

    def is_importable_file(self, file_name):
        return file_name.endswith('.json') and ((self.is_npm_import() and file_name.startswith('package/')
                                                 and file_name.count('/') == 1)
                                                or not self.is_npm_import())

    def categorize_resources(self, json_file, path, file_path, resource_types, resources):
        for resource_type in resource_types:
            if resource_type not in resources:
                resources.update({resource_type: {}})

        try:
            parser = ijson.parse(json_file, multiple_values=True, allow_comments=True)
            for prefix, event, value in parser:
                # Expect {"resourceType": ""}{"resourceType": ""} or {"type": ""}{"type": ""}
                if event == 'string' and (prefix in {'resourceType', 'type'}):
                    # Categorize files based on resourceType
                    if value in resource_types:
                        resource_type = resources.get(value)
                        # Remember count of resources of the given type within a file
                        if file_path:
                            path_key = path + '/' + file_path
                        else:
                            path_key = path
                        resource_type.update({path_key: resource_type.get(path_key, 0) + 1})

                    if self.is_npm_import():  # Expect only one resource per file for npm
                        break
        except JSONError as json_error:
            raise JSONError(f'Failed to process {path}/{file_path}') from json_error


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
            url = resource.get('url')
            if resource_type == 'CodeSystem':
                # TODO: use url registry
                return self.import_code_system(owner, owner_type, resource, resource_type, url, username)
            if resource_type == 'ValueSet':
                # TODO: use url registry
                return self.import_value_set(owner, owner_type, resource, resource_type, url, username)
            if resource_type == 'ConceptMap':
                # TODO: use url registry
                return self.import_concept_map(owner, owner_type, resource, resource_type, url, username)
        else:
            # Handle other resources
            for resource_importer in self.resource_importers:
                if resource_importer.can_handle(resource):
                    user_profile = UserProfile.objects.get(username=username)
                    result = resource_importer(resource, user_profile, True).run()
                    return result
        return None

    # pylint: disable=too-many-arguments
    @staticmethod
    def import_concept_map(owner, owner_type, resource, resource_type, url, username):
        source = ResourceImporter.find_existing_source(owner, owner_type, url)
        context = {
            'request': ImportRequest(owner_type, owner, username, resource_type)
        }
        if source:
            serializer = ConceptMapDetailSerializer(source.first(), data=resource, context=context)
            result = UPDATED
        else:
            serializer = ConceptMapDetailSerializer(data=resource, context=context)
            result = CREATED
        if serializer.is_valid():
            serializer.save()
        return serializer.errors if serializer.errors else result

    @staticmethod
    def find_existing_source(owner, owner_type, url):
        org, user = None, None
        if owner_type.lower() in ['orgs', 'organization']:
            org = Organization.objects.filter(mnemonic=owner).first()
        else:
            user = UserProfile.objects.filter(username=owner).first()

        if not org and not user:
            raise ValidationError(f"Cannot find owner of type {owner_type} and id {owner}")

        if org:
            source = Source.objects.filter(canonical_url=url, organization=org)
        else:
            source = Source.objects.filter(canonical_url=url, user=user)
        if not source:
            url = IdentifierSerializer.convert_fhir_url_to_ocl_uri(url, 'sources')
            if org:
                source = Source.objects.filter(uri=url, organization=org)
            else:
                source = Source.objects.filter(uri=url, user=user)
        return source

    # pylint: disable=too-many-arguments
    @staticmethod
    def import_value_set(owner, owner_type, resource, resource_type, url, username):
        org, user = None, None
        if owner_type.lower() in ['orgs', 'organization']:
            org = Organization.objects.filter(mnemonic=owner).first()
        else:
            user = UserProfile.objects.filter(username=owner).first()

        if not org and not user:
            raise ValidationError(f"Cannot find owner of type {owner_type} and id {owner}")

        if org:
            collection = Collection.objects.filter(canonical_url=url, organization=org)
        else:
            collection = Collection.objects.filter(canonical_url=url, user=user)

        if not collection:
            url = IdentifierSerializer.convert_fhir_url_to_ocl_uri(url, 'collections')
            if org:
                collection = Collection.objects.filter(uri=url, organization=org)
            else:
                collection = Collection.objects.filter(uri=url, user=user)
        context = {
            'request': ImportRequest(owner_type, owner, username, resource_type)
        }
        if collection:
            serializer = ValueSetDetailSerializer(collection.first(), data=resource, context=context)
            result = UPDATED
        else:
            serializer = ValueSetDetailSerializer(data=resource, context=context)
            result = CREATED
        if serializer.is_valid():
            serializer.save()
        return serializer.errors if serializer.errors else result

    # pylint: disable=too-many-arguments
    @staticmethod
    def import_code_system(owner, owner_type, resource, resource_type, url, username):
        source = ResourceImporter.find_existing_source(owner, owner_type, url)
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


class ImporterSubtask:
    path: str
    username: str
    owner_type: str
    owner: str
    resource_type: str
    files: bytes
    progress: int

    # pylint: disable=too-many-arguments
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

        try:
            with open(self.path, 'rb') if self.path.startswith('/') else tempfile.NamedTemporaryFile() as temp:
                if not self.path.startswith('/'):  # not local file
                    request_path = self.path
                    if self.path.startswith(Importer.IMPORT_CACHE):
                        request_path = get_export_service().url_for(self.path)
                    remote_file = requests.get(request_path, stream=True)
                    if not remote_file.ok:
                        raise ImportError(f"Failed to GET {request_path}, responded with {remote_file.status_code}")
                    ImporterUtils.fetch_to_file(remote_file, temp)

                is_zipped, is_tarred = ImporterUtils.is_zipped_or_tarred(temp)

                if is_zipped:
                    self.import_zip(temp, results)

                elif is_tarred:
                    self.import_tar(temp, results)
                elif self.files:
                    self.import_files(temp, results)
        except Exception as ex:
            results_count = 0
            for file in self.files:
                start_index = file.get('start_index', 0)
                end_index = file.get('end_index', None)
                logger.exception("Failed to process %s(%s:%s) due to %s", file.get('filepath', ''), start_index,
                                 end_index, ex)
                if end_index:
                    count = end_index - start_index
                else:
                    count = None
                results_count += count
            if len(results) < results_count:
                results.extend([str(ex)] * (results_count - len(results)))

        return results

    def import_zip(self, temp, results):
        with ZipFile(temp) as package:
            for file in self.files:
                start_index = file.get("start_index", 0)
                end_index = file.get("end_index", None)
                if end_index:
                    count = end_index - start_index
                else:
                    count = 1

                try:
                    with package.open(file.get("filepath")) as json_file:
                        result = self.import_resource(json_file, file.get("filepath"),
                                                      start_index,
                                                      end_index)
                        results.extend(result)
                except Exception as ex:
                    self.add_exception_to_results(file, start_index, end_index, count, ex, results)

    def import_tar(self, temp, results):
        with tarfile.open(fileobj=temp, mode='r') as package:
            for file in self.files:
                start_index = file.get("start_index", 0)
                end_index = file.get("end_index", None)
                if end_index:
                    count = end_index - start_index
                else:
                    count = 1

                try:
                    with package.extractfile(file.get("filepath")) as json_file:
                        result = self.import_resource(json_file, file.get("filepath"),
                                                      file.get("start_index", 0),
                                                      file.get("end_index", None))
                        results.extend(result)
                except Exception as ex:
                    self.add_exception_to_results(file, start_index, end_index, count, ex, results)

    def import_files(self, temp, results):
        for file in self.files:
            start_index = file.get("start_index", 0)
            end_index = file.get("end_index", None)
            if end_index:
                count = end_index - start_index
            else:
                count = 1
            try:
                result = self.import_resource(temp, file.get('filepath', ''),
                                              file.get('start_index', 0),
                                              file.get('end_index', None))
                results.extend(result)
            except Exception as ex:
                self.add_exception_to_results(file, start_index, end_index, count, ex, results)

    @staticmethod
    def add_exception_to_results(file, start_index, end_index, count, ex, results):
        error = f"Failed to process {file.get('filepath', '')}({start_index}:{end_index}) " \
                f"due to: {ex}"
        logger.exception(error)
        results.extend([error] * count)

    def import_resource(self, json_file, filepath, start_index, end_index):  # pylint: disable=too-many-branches
        parse = self.move_to_start_index(json_file, start_index)

        if end_index:
            count = end_index - start_index
        else:
            count = None

        results = []
        for resource in ijson.items(parse, '', multiple_values=True, allow_comments=True):
            if resource.get('__action', None) == 'DELETE':
                continue

            if resource.get('resourceType', None) == self.resource_type \
                    or resource.get('type', None) == self.resource_type:

                error = f'Failed to import resource with id {resource.get("id", None)} from {self.path}/' \
                        f'{filepath} to {self.owner_type}/{self.owner} by {self.username}'
                try:
                    if self.resource_type.lower() in ['source', 'collection']:
                        if 'owner_type' not in resource:
                            resource['owner_type'] = self.owner_type
                        if 'owner' not in resource:
                            resource['owner'] = self.owner
                    result = ResourceImporter().import_resource(resource, self.username, self.owner_type, self.owner)
                    if isinstance(result, int):
                        results.append(result)
                    else:
                        results.append(f'{error} due to: {result}')
                except Exception as e:
                    logger.exception(error)
                    results.append(f'{error} due to: {str(e)}')
                if count is not None:
                    count -= 1
                    if count <= 0:
                        break
        return results

    # pylint: disable=too-many-nested-blocks
    def move_to_start_index(self, json_file, start_index):
        json_file.seek(0)  # position at the beginning of file
        if start_index != 0:
            index = 0
            parse_events = ijson.parse(json_file, multiple_values=True, allow_comments=True)
            for prefix, event, value in parse_events:
                if event == 'string' and prefix in ('resourceType', 'type') and value == self.resource_type:
                    index += 1
                    if index == start_index:
                        # Move the pointer to the next json top-level object
                        for prefix_inner, event_inner, _ in parse_events:
                            if event_inner == 'end_map' and prefix_inner == '':
                                return parse_events
            # We should end up here only if no resources of the specified type
            return parse_events

        return json_file
