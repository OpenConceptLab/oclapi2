import json
import time
from collections import deque
from datetime import datetime

from celery import group
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import F
from ocldev.oclfleximporter import OclFlexImporter
from pydash import compact, get

from core.collections.models import Collection
from core.common.constants import HEAD
from core.common.services import RedisService
from core.common.tasks import bulk_import_parts_inline, delete_organization, batch_index_resources
from core.common.utils import drop_version, is_url_encoded_string, encode_string, to_parent_uri, chunks
from core.concepts.models import Concept
from core.mappings.models import Mapping
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile

logger = get_task_logger(__name__)


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
            api_url_root=settings.API_BASE_URL,
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
DELETED = 4
NOT_FOUND = 5
PERMISSION_DENIED = 6


class BaseResourceImporter:
    mandatory_fields = set()
    allowed_fields = []

    def __init__(self, data, user, update_if_exists=False):
        self.user = user
        self.data = data
        self.update_if_exists = update_if_exists
        self.queryset = None

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

    def exists(self):
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
        return self.get_queryset().exists()

    def get_queryset(self):
        return Organization.objects.filter(mnemonic=self.get('id'))

    def parse(self):
        super().parse()
        self.data['mnemonic'] = self.data.pop('id')

    def process(self):
        if not self.exists():
            org = Organization.objects.create(**self.data)
            org.members.add(org.created_by)
            if org:
                return CREATED
            return FAILED
        return None

    def delete(self):
        if self.exists():
            org = self.get_queryset().first()
            if self.user and (self.user.is_staff or org.is_member(self.user)):
                delete_organization(org.id)
                return DELETED
            return PERMISSION_DENIED
        return NOT_FOUND


class SourceImporter(BaseResourceImporter):
    mandatory_fields = {'id', 'name', 'owner_type', 'owner'}
    allowed_fields = [
        "id", "short_code", "name", "full_name", "description", "source_type", "custom_validation_schema",
        "public_access", "default_locale", "supported_locales", "website", "extras", "external_id",
        'canonical_url', 'identifier', 'contact', 'jurisdiction', 'publisher', 'purpose', 'copyright',
        'revision_date', 'text', 'content_type', 'experimental', 'case_sensitive', 'collection_reference',
        'hierarchy_meaning', 'compositional', 'version_needed', 'meta',
    ]

    def exists(self):
        return self.get_queryset().exists()

    def get_queryset(self):
        return Source.objects.filter(
            **{self.get_owner_type_filter(): self.get('owner'), 'mnemonic': self.get('id')}
        )

    def parse(self):
        owner_type = self.get('owner_type').lower()
        owner = self.get_owner()

        super().parse()

        self.data['mnemonic'] = self.data.pop('id')
        self.data[owner_type] = owner
        self.data['version'] = HEAD

        supported_locales = self.get('supported_locales')
        if isinstance(supported_locales, str):
            self.data['supported_locales'] = supported_locales.split(',')

        self.data.pop('short_code', None)

    def process(self):
        source = Source(**self.data)
        if source.has_parent_edit_access(self.user):
            errors = Source.persist_new(source, self.user)
            return errors or CREATED
        return PERMISSION_DENIED

    def delete(self):
        if self.exists():
            source = self.get_queryset().first()
            try:
                if source.has_parent_edit_access(self.user):
                    source.delete()
                    return DELETED
                return PERMISSION_DENIED
            except Exception as ex:
                return dict(errors=ex.args)

        return NOT_FOUND


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
        if source.has_parent_edit_access(self.user):
            errors = Source.persist_new_version(source, self.user)
            return errors or CREATED
        return PERMISSION_DENIED


class CollectionImporter(BaseResourceImporter):
    mandatory_fields = {'id', 'name', 'owner_type', 'owner'}
    allowed_fields = [
        "id", "short_code", "name", "full_name", "description", "collection_type", "custom_validation_schema",
        "public_access", "default_locale", "supported_locales", "website", "extras", "external_id",
        'canonical_url', 'identifier', 'contact', 'jurisdiction', 'publisher', 'purpose', 'copyright',
        'revision_date', 'text', 'immutable', 'experimental', 'locked_date', 'meta',
    ]

    def exists(self):
        return self.get_queryset().exists()

    def get_queryset(self):
        return Collection.objects.filter(
            **{self.get_owner_type_filter(): self.get('owner'), 'mnemonic': self.get('id')}
        )

    def parse(self):
        owner_type = self.get('owner_type').lower()
        owner = self.get_owner()

        super().parse()

        self.data['mnemonic'] = self.data.pop('id')
        self.data[owner_type] = owner
        self.data['version'] = HEAD

        supported_locales = self.get('supported_locales')
        if isinstance(supported_locales, str):
            self.data['supported_locales'] = supported_locales.split(',')

        self.data.pop('short_code', None)

    def process(self):
        coll = Collection(**self.data)
        if coll.has_parent_edit_access(self.user):
            errors = Collection.persist_new(coll, self.user)
            return errors or CREATED
        return PERMISSION_DENIED

    def delete(self):
        if self.exists():
            collection = self.get_queryset().first()
            try:
                if collection.has_parent_edit_access(self.user):
                    collection.delete()
                    return DELETED
                return PERMISSION_DENIED
            except Exception as ex:
                return dict(errors=ex.args)

        return NOT_FOUND


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
        if coll.has_parent_edit_access(self.user):
            errors = Collection.persist_new_version(obj=coll, user=self.user, sync=True)
            return errors or CREATED
        return PERMISSION_DENIED


class ConceptImporter(BaseResourceImporter):
    mandatory_fields = {"id"}
    allowed_fields = [
        "id", "external_id", "concept_class", "datatype", "names", "descriptions", "retired", "extras",
        "parent_concept_urls", 'update_comment', 'comment'
    ]

    def __init__(self, data, user, update_if_exists):
        super().__init__(data, user, update_if_exists)
        self.version = False
        self.instance = None

    def exists(self):
        return self.get_queryset().exists()

    def get_queryset(self):
        if self.queryset:
            return self.queryset

        parent_uri = f'/{"users" if self.is_user_owner() else "orgs"}/{self.get("owner")}/sources/{self.get("source")}/'
        self.queryset = Concept.objects.filter(
            parent__uri=parent_uri, mnemonic=self.get('id'), id=F('versioned_object_id')
        )
        return self.queryset

    def parse(self):
        source = Source.objects.filter(
            **{self.get_owner_type_filter(): self.get('owner')}, mnemonic=self.get('source'), version=HEAD
        ).first()
        super().parse()
        self.data['parent'] = source
        self.data['name'] = self.data['mnemonic'] = str(self.data.pop('id', ''))
        if not is_url_encoded_string(self.data['mnemonic']):
            self.data['mnemonic'] = encode_string(self.data['mnemonic'])

    def clean(self):
        if not self.is_valid():
            return False
        if self.exists() and self.update_if_exists:
            self.version = True

        self.parse()
        return True

    def process(self):
        parent = self.data.get('parent')
        if not parent:
            return FAILED
        if parent.has_edit_access(self.user):
            if self.version:
                self.instance = self.get_queryset().first().clone()
                self.instance._counted = None  # pylint: disable=protected-access
                self.instance._index = False  # pylint: disable=protected-access
                errors = Concept.create_new_version_for(
                    instance=self.instance, data=self.data, user=self.user, create_parent_version=False,
                    add_prev_version_children=False
                )
                return errors or UPDATED

            if 'update_comment' in self.data:
                self.data['comment'] = self.data['update_comment']
                self.data.pop('update_comment')
            self.instance = Concept.persist_new(
                data={**self.data, '_counted': None, '_index': False}, user=self.user, create_parent_version=False)
            if self.instance.id:
                return CREATED
            return self.instance.errors or FAILED

        return PERMISSION_DENIED

    def delete(self):
        is_clean = self.clean()
        if not is_clean:
            return is_clean
        if self.exists():
            parent = self.data.get('parent')
            try:
                if parent.has_edit_access(self.user):
                    concept = self.get_queryset().first()
                    concept.retire(self.user)
                    return DELETED
                return PERMISSION_DENIED
            except Exception as ex:
                return dict(errors=ex.args)

        return NOT_FOUND


class MappingImporter(BaseResourceImporter):
    mandatory_fields = {"map_type", "from_concept_url"}
    allowed_fields = [
        "id", "map_type", "from_concept_url", "to_source_url", "to_concept_url", "to_concept_code",
        "to_concept_name", "extras", "external_id", "retired", 'update_comment', 'comment', 'sort_weight'
    ]

    def __init__(self, data, user, update_if_exists):
        super().__init__(data, user, update_if_exists)
        self.version = False
        self.instance = None

    def exists(self):
        return self.get_queryset().exists()

    def get_queryset(self):  # pylint: disable=too-many-branches
        if self.queryset:
            return self.queryset

        from_concept_url = self.get('from_concept_url')
        to_concept_url = self.get('to_concept_url')
        to_concept_code = self.get('to_concept_code')
        from_concept_code = self.get('from_concept_code')
        to_source_url = self.get('to_source_url')
        parent_uri = f'/{"users" if self.is_user_owner() else "orgs"}/{self.get("owner")}/sources/{self.get("source")}/'
        filters = {
            'parent__uri': parent_uri,
            'id': F('versioned_object_id'),
            'map_type': self.get('map_type'),
        }
        if from_concept_code:
            filters['from_concept_code'] = Concept.get_mnemonic_variations_for_filter(from_concept_code)

        versionless_from_concept_url = drop_version(from_concept_url)
        from_concept = Concept.objects.filter(id=F('versioned_object_id'), uri=versionless_from_concept_url).first()
        if from_concept:
            filters['from_concept__versioned_object_id'] = from_concept.versioned_object_id
        elif not from_concept_code:
            filters['from_concept_code'] = compact(versionless_from_concept_url.split('/'))[-1]
        if to_concept_url:
            versionless_to_concept_url = drop_version(to_concept_url)
            to_concept = Concept.objects.filter(id=F('versioned_object_id'), uri=versionless_to_concept_url).first()
            if to_concept:
                filters['to_concept__versioned_object_id'] = to_concept.versioned_object_id
            else:
                filters['to_concept_code'] = compact(versionless_to_concept_url.split('/'))[-1]
                if not to_source_url:
                    to_source_uri = to_parent_uri(versionless_to_concept_url)
                    if Source.objects.filter(uri=drop_version(to_source_uri)).exists():
                        filters['to_source__uri'] = to_source_uri

        if self.get('id'):
            filters['mnemonic'] = self.get('id')

        if to_source_url:
            to_source_uri = drop_version(to_source_url)
            filters['to_source_url'] = to_source_uri

        if to_concept_code:
            filters['to_concept_code__in'] = Concept.get_mnemonic_variations_for_filter(to_concept_code)

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

        from_concept_code = self.data.get('from_concept_code')
        to_concept_code = self.data.get('to_concept_code')
        if from_concept_code and not is_url_encoded_string(from_concept_code):
            self.data['from_concept_code'] = encode_string(from_concept_code, safe='')
        if to_concept_code and not is_url_encoded_string(to_concept_code):
            self.data['to_concept_code'] = encode_string(to_concept_code, safe='')

    def clean(self):
        if not self.is_valid():
            return False
        if self.exists() and self.update_if_exists:
            self.version = True

        self.parse()
        return True

    def process(self):
        parent = self.data.get('parent')
        if not parent:
            return FAILED
        if parent.has_edit_access(self.user):
            if self.version:
                self.instance = self.get_queryset().first().clone()
                self.instance._counted = None  # pylint: disable=protected-access
                self.instance._index = False  # pylint: disable=protected-access
                errors = Mapping.create_new_version_for(self.instance, self.data, self.user)
                return errors or UPDATED
            if 'update_comment' in self.data:
                self.data['comment'] = self.data['update_comment']
                self.data.pop('update_comment')
            self.instance = Mapping.persist_new({**self.data, '_counted': None, '_index': False}, self.user)
            if self.instance.id:
                return CREATED
            return self.instance.errors or FAILED

        return PERMISSION_DENIED

    def delete(self):
        is_clean = self.clean()
        if not is_clean:
            return is_clean
        if self.exists():
            parent = self.data.get('parent')
            try:
                if parent.has_edit_access(self.user):
                    mapping = self.get_queryset().first()
                    mapping.retire(self.user)
                    return DELETED
                return PERMISSION_DENIED
            except Exception as ex:
                return dict(errors=ex.args)

        return NOT_FOUND


class ReferenceImporter(BaseResourceImporter):
    mandatory_fields = {"data"}
    allowed_fields = ["data", "collection", "owner", "owner_type", "__cascade", "collection_url"]

    def exists(self):
        return False

    def get_queryset(self):
        if self.queryset:
            return self.queryset

        if self.get('collection', None):
            self.queryset = Collection.objects.filter(
                **{self.get_owner_type_filter(): self.get('owner')}, mnemonic=self.get('collection'), version=HEAD
            )
        elif self.get('collection_url', None):
            self.queryset = Collection.objects.filter(uri=self.get('collection_url'))

        return self.queryset

    def process(self):
        collection = self.get_queryset().first()

        if collection:
            if collection.has_edit_access(self.user):
                (added_references, _) = collection.add_expressions(
                    self.get('data'), self.user, self.get('__cascade', False)
                )
                if not get(settings, 'TEST_MODE', False):  # pragma: no cover
                    concept_ids = []
                    mapping_ids = []
                    for ref in added_references:
                        concept_ids += list(ref.concepts.values_list('id', flat=True))
                        mapping_ids += list(ref.mappings.values_list('id', flat=True))

                    if concept_ids:
                        batch_index_resources.apply_async(('concept', dict(id__in=concept_ids)), queue='indexing')
                    if mapping_ids:
                        batch_index_resources.apply_async(('mapping', dict(id__in=mapping_ids)), queue='indexing')

                return CREATED
            return PERMISSION_DENIED
        return FAILED


class BulkImportInline(BaseImporter):
    def __init__(  # pylint: disable=too-many-arguments
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
        self.deleted = []
        self.not_found = []
        self.failed = []
        self.exception = []
        self.permission_denied = []
        self.others = []
        self.processed = 0
        self.total = len(self.input_list)
        self.start_time = time.time()
        self.elapsed_seconds = 0

    def handle_item_import_result(self, result, item):  # pylint: disable=too-many-return-statements
        if result is None:
            self.exists.append(item)
            return
        if result is False:
            self.invalid.append(item)
            return
        if result == FAILED:
            self.failed.append(item)
            return
        if result == DELETED:
            self.deleted.append(item)
            return
        if result == NOT_FOUND:
            self.not_found.append(item)
            return
        if isinstance(result, dict):
            item['errors'] = result
            self.failed.append(item)
            return
        if result == CREATED:
            self.created.append(item)
            return
        if result == UPDATED:
            self.updated.append(item)
            return
        if result == PERMISSION_DENIED:
            self.permission_denied.append(item)
            return

        print("****Unexpected Result****", result)
        self.others.append(item)

    def notify_progress(self):
        if self.self_task_id:  # pragma: no cover
            service = RedisService()
            service.set(self.self_task_id, self.processed)

    def run(self):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        if self.self_task_id:  # pragma: no cover
            print("****STARTED SUBPROCESS****")
            print(f"TASK ID: {self.self_task_id}")
            print("***************")
        new_concept_ids = set()
        new_mapping_ids = set()
        for original_item in self.input_list:
            self.processed += 1
            logger.info('Processing %s of %s', str(self.processed), str(self.total))
            self.notify_progress()
            item = original_item.copy()
            item_type = item.pop('type', '').lower()
            action = item.pop('__action', '').lower()
            if not item_type:
                self.unknown.append(original_item)
            if item_type == 'organization':
                org_importer = OrganizationImporter(item, self.user, self.update_if_exists)
                self.handle_item_import_result(
                    org_importer.delete() if action == 'delete' else org_importer.run(), original_item
                )
                continue
            if item_type == 'source':
                source_importer = SourceImporter(item, self.user, self.update_if_exists)
                self.handle_item_import_result(
                    source_importer.delete() if action == 'delete' else source_importer.run(), original_item
                )
                continue
            if item_type == 'source version':
                self.handle_item_import_result(
                    SourceVersionImporter(item, self.user, self.update_if_exists).run(), original_item
                )
                continue
            if item_type == 'collection':
                collection_importer = CollectionImporter(item, self.user, self.update_if_exists)
                self.handle_item_import_result(
                    collection_importer.delete() if action == 'delete' else collection_importer.run(), original_item
                )
                continue
            if item_type == 'collection version':
                self.handle_item_import_result(
                    CollectionVersionImporter(item, self.user, self.update_if_exists).run(), original_item
                )
                continue
            if item_type == 'concept':
                concept_importer = ConceptImporter(item, self.user, self.update_if_exists)
                _result = concept_importer.delete() if action == 'delete' else concept_importer.run()
                if get(concept_importer.instance, 'id'):
                    new_concept_ids.add(concept_importer.instance.versioned_object_id)
                self.handle_item_import_result(_result, original_item)
                continue
            if item_type == 'mapping':
                mapping_importer = MappingImporter(item, self.user, self.update_if_exists)
                _result = mapping_importer.delete() if action == 'delete' else mapping_importer.run()
                if get(mapping_importer.instance, 'id'):
                    new_mapping_ids.add(mapping_importer.instance.versioned_object_id)
                self.handle_item_import_result(_result, original_item)
                continue
            if item_type == 'reference':
                self.handle_item_import_result(
                    ReferenceImporter(item, self.user, self.update_if_exists).run(), original_item
                )
                continue

        if new_concept_ids:
            for chunk in chunks(list(new_concept_ids), 1000):
                batch_index_resources.apply_async(
                    ('concept', dict(versioned_object_id__in=chunk), True), queue='indexing')
        if new_mapping_ids:
            for chunk in chunks(list(new_mapping_ids), 1000):
                batch_index_resources.apply_async(
                    ('mapping', dict(versioned_object_id__in=chunk), True), queue='indexing')

        self.elapsed_seconds = time.time() - self.start_time

        self.make_result()

        return self.result

    @property
    def detailed_summary(self):
        return f"Processed: {self.processed}/{self.total} | Created: {len(self.created)} | " \
            f"Updated: {len(self.updated)} | DELETED: {len(self.deleted)} | Existing: {len(self.exists)} | " \
            f"Permision Denied: {len(self.permission_denied)} | Failed: {len(self.failed)} | " \
            f"Time: {self.elapsed_seconds}secs"

    @property
    def json_result(self):
        return dict(
            total=self.total, processed=self.processed, created=self.created, updated=self.updated,
            invalid=self.invalid, exists=self.exists, failed=self.failed, deleted=self.deleted,
            not_found=self.not_found, exception=self.exception, permission_denied=self.permission_denied,
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
        self.resource_distribution = {}
        self.parallel = int(parallel) if parallel else 5
        self.tasks = []
        self.groups = []
        self.results = []
        self.elapsed_seconds = 0
        self.resource_wise_time = {}
        self.parts = deque([])
        self.result = None
        self._json_result = None
        self.redis_service = RedisService()
        if self.content:
            self.input_list = self.content if isinstance(self.content, list) else self.content.splitlines()
            self.total = len(self.input_list)
        self.make_resource_distribution()
        self.make_parts()
        self.content = None  # memory optimization
        self.input_list = []  # memory optimization

    def make_resource_distribution(self):
        for line in self.input_list:
            data = line if isinstance(line, dict) else json.loads(line)
            data_type = data.get('type', None)
            if not data_type:
                continue
            if data_type not in self.resource_distribution:
                self.resource_distribution[data_type] = []
            self.resource_distribution[data_type].append(data)

    def make_parts(self):
        prev_line = None
        orgs = self.resource_distribution.get('Organization', None)
        sources = self.resource_distribution.get('Source', None)
        collections = self.resource_distribution.get('Collection', None)
        if orgs:
            self.parts = deque([orgs])
        if sources:
            self.parts.append(sources)
        if collections:
            self.parts.append(collections)

        self.parts.append([])

        for data in self.input_list:
            line = data if isinstance(data, dict) else json.loads(data)
            data_type = line.get('type', '').lower()
            if not data_type:
                raise ValidationError('"type" should be present in each line')
            if data_type not in ['organization', 'source', 'collection']:
                if prev_line:
                    prev_type = prev_line.get('type').lower()
                    children_data_types = ['concept', 'mapping', 'reference']
                    if prev_type == data_type or (
                            data_type not in children_data_types and prev_type not in children_data_types
                    ):
                        self.parts[-1].append(line)
                    else:
                        self.parts.append([line])
                else:
                    self.parts[-1].append(line)
                prev_line = line

    @staticmethod
    def chunker_list(seq, size, is_child):  # pylint: disable=too-many-locals
        """
            1. returns n number of sequential chunks from l.
            2. makes sure concept versions are grouped in single list
        """
        sorted_seq = seq
        is_source_child = False
        if is_child:
            part_type = get(seq, '0.type', '').lower()
            is_source_child = part_type in ['concept']
            if is_source_child:
                sorted_seq = sorted(seq, key=lambda x: x['id'])
        quotient, remainder = divmod(len(sorted_seq), size)
        result = []
        for i in range(size):
            si = (quotient+1)*(i if i < remainder else remainder) + quotient*(0 if i < remainder else i - remainder)
            current = list(sorted_seq[si:si + (quotient + 1 if i < remainder else quotient)])
            if not is_source_child or not get(result, '-1', None):
                if current:
                    result.append(current)
                continue
            prev = get(result, '-1', None)
            prev_last_id = get(prev, '-1.id', '').lower()
            current_first_id = get(current, '0.id', '').lower()
            shift = 0
            if prev_last_id == current_first_id:
                for resource in current:
                    if resource['id'].lower() == prev_last_id:
                        shift += 1
                        result[-1].append(resource)
                    else:
                        break
            if shift:
                current = current[shift:]
            if current:
                result.append(current)
        return result

    def is_any_process_alive(self):
        if not self.groups:
            return False

        result = True

        try:
            result = any(grp.completed_count() != len(grp) for grp in self.groups)
        except:  # pylint: disable=bare-except
            pass

        return result

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

    def get_details_to_notify(self):
        summary = f"Started: {self.start_time_formatted} | " \
            f"Processed: {self.get_overall_tasks_progress()}/{self.total} | " \
            f"Time: {self.elapsed_seconds}secs"

        return dict(summary=summary)

    def notify_progress(self):
        if self.self_task_id:
            try:
                self.redis_service.set_json(self.self_task_id, self.get_details_to_notify())
            except:  # pylint: disable=bare-except
                pass

    def wait_till_tasks_alive(self):
        while self.is_any_process_alive():
            self.update_elapsed_seconds()
            self.notify_progress()
            time.sleep(1)

    def run(self):
        if self.self_task_id:
            print("****STARTED MAIN****")
            print(f"TASK ID: {self.self_task_id}")
            print("***************")
        while len(self.parts) > 0:
            part_list = self.parts.popleft()
            if part_list:
                part_type = get(part_list, '0.type', '').lower()
                if part_type:
                    is_child = part_type in ['concept', 'mapping', 'reference']
                    start_time = time.time()
                    self.queue_tasks(part_list, is_child)
                    self.wait_till_tasks_alive()
                    if is_child:
                        if part_type not in self.resource_wise_time:
                            self.resource_wise_time[part_type] = 0
                        self.resource_wise_time[part_type] += (time.time() - start_time)

        print("Updating Active Concepts Count...")
        self.update_concept_counts()

        print("Updating Active Mappings Count...")
        self.update_mappings_counts()

        self.update_elapsed_seconds()

        self.make_result()

        return self.result

    def update_elapsed_seconds(self):
        self.elapsed_seconds = time.time() - self.start_time

    @property
    def detailed_summary(self):
        result = self.json_result
        return f"Started: {self.start_time_formatted} | Processed: {result.get('processed')}/{result.get('total')} | " \
            f"Created: {len(result.get('created'))} | Updated: {len(result.get('updated'))} | " \
            f"Deleted: {len(result.get('deleted'))} | Existing: {len(result.get('exists'))} | " \
            f"Permission Denied: {len(result.get('permission_denied'))} | " \
            f"Time: {self.elapsed_seconds}secs"

    @property
    def start_time_formatted(self):
        return datetime.fromtimestamp(self.start_time)

    @property
    def json_result(self):
        if self._json_result:
            return self._json_result

        total_result = dict(
            total=0, processed=0, created=[], updated=[],
            invalid=[], exists=[], failed=[], exception=[], deleted=[],
            others=[], unknown=[], permission_denied=[], elapsed_seconds=self.elapsed_seconds
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
        has_delete_action = not is_child and any(line.get('__action') == 'DELETE' for line in part_list)
        chunked_lists = [part_list] if has_delete_action else compact(
            self.chunker_list(part_list, self.parallel, is_child))
        jobs = group(bulk_import_parts_inline.s(_list, self.username, self.update_if_exists) for _list in chunked_lists)
        group_result = jobs.apply_async(queue='concurrent')
        self.groups.append(group_result)
        self.tasks += group_result.results

    @staticmethod
    def update_concept_counts():
        uncounted_concepts = Concept.objects.filter(_counted__isnull=True)
        sources = Source.objects.filter(id__in=uncounted_concepts.values_list('parent_id', flat=True))
        for source in sources:
            source.update_concepts_count(sync=False)
            uncounted_concepts.filter(parent_id=source.id).update(_counted=True)

    @staticmethod
    def update_mappings_counts():
        uncounted_mappings = Mapping.objects.filter(_counted__isnull=True)
        sources = Source.objects.filter(
            id__in=uncounted_mappings.values_list('parent_id', flat=True))
        for source in sources:
            source.update_mappings_count(sync=False)
            uncounted_mappings.filter(parent_id=source.id).update(_counted=True)
