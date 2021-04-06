import json
import time
import urllib
from datetime import datetime

from pydash import get

from core.collections.models import CollectionReference, Collection
from core.collections.utils import is_concept
from core.common.constants import HEAD
from core.common.tasks import populate_indexes
from core.common.utils import generate_temp_version, drop_version
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept, LocalizedText
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile


class V1BaseImporter:
    total = 0
    processed = 0

    created = []
    updated = []
    existed = []
    failed = []
    not_found = []
    not_found_references = []
    old_users = []

    elapsed_seconds = 0
    start_time = None

    result_attrs = []
    start_message = None

    users = dict()
    orgs = dict()
    sources = dict()
    collections = dict()
    concepts = dict()
    mappings = dict()

    def __init__(self, file_url, **kwargs):  # pylint: disable=unused-argument
        self.file_url = file_url
        self.lines = []
        self.read_file()

    def read_file(self):
        if self.file_url:
            print("***", self.file_url)
            file = urllib.request.urlopen(self.file_url)
            self.lines = file.readlines()
            self.total = len(self.lines)

    @staticmethod
    def log(msg):
        print("*******{}*******".format(msg))

    @property
    def common_result(self):
        return dict(
            total=self.total, processed=self.processed, start_time=self.start_time,
            elapsed_seconds=self.elapsed_seconds
        )

    def get_resource_result_attrs(self, only_length):
        result = {}
        for attr in self.result_attrs:
            value = get(self, attr)
            result[attr] = len(value or []) if only_length else value
        return result

    def result(self, only_length):
        return {**self.common_result, **self.get_resource_result_attrs(only_length)}

    @property
    def summary(self):
        return self.result(True)

    @property
    def details(self):
        return self.result(False)

    @property
    def total_result(self):
        result_details = self.details
        return dict(report=result_details, json=result_details, detailed_summary=self.summary)

    def get_user(self, username):
        if username not in self.users:
            user = UserProfile.objects.filter(username=username).first()
            self.users[username] = user

        return self.users[username]

    def get_org(self, mnemonic=None, internal_reference_id=None):
        filters = dict()
        key = mnemonic or internal_reference_id
        if not key:
            return None

        if mnemonic:
            filters['mnemonic'] = key
        else:
            filters['internal_reference_id'] = key

        if key not in self.orgs:
            org = Organization.objects.filter(**filters).first()
            if org:
                _internal_reference_id = org.internal_reference_id
                _mnemonic = org.mnemonic
                self.orgs[_mnemonic] = org
                self.orgs[_internal_reference_id] = org
            else:
                self.orgs[key] = org

        return self.orgs[key]

    def get_concept(self, internal_reference_id):
        if internal_reference_id not in self.concepts:
            self.concepts[internal_reference_id] = Concept.objects.filter(
                internal_reference_id=internal_reference_id).first()

        return self.concepts[internal_reference_id]

    def get_mapping(self, internal_reference_id):
        if internal_reference_id not in self.mappings:
            self.mappings[internal_reference_id] = Mapping.objects.filter(
                internal_reference_id=internal_reference_id).first()

        return self.mappings[internal_reference_id]

    def get_source(self, internal_reference_id):
        if internal_reference_id not in self.sources:
            self.sources[internal_reference_id] = Source.objects.filter(
                internal_reference_id=internal_reference_id).first()

        return self.sources[internal_reference_id]

    def get_collection(self, internal_reference_id):
        if internal_reference_id not in self.collections:
            self.collections[internal_reference_id] = Collection.objects.filter(
                internal_reference_id=internal_reference_id).first()

        return self.collections[internal_reference_id]

    def process_line(self, line):
        pass

    def after_run(self):
        pass

    def run(self):
        self.start_time = time.time()
        if not isinstance(self.lines, list):
            return None

        self.log(self.start_message)
        self.log('TOTAL: {}'.format(self.total))

        for line in self.lines:
            self.process_line(line)

        self.after_run()

        self.elapsed_seconds = time.time() - self.start_time

        return self.total_result

    @staticmethod
    def get_importer_class_from_string(klass_str):  # pylint: disable=too-many-return-statements,too-many-branches
        name = klass_str.lower()
        if name in ['org', 'organization', 'orgs', 'organizations']:
            return V1OrganizationImporter
        if name in ['user', 'users']:
            return V1UserImporter
        if name in ['source', 'sources']:
            return V1SourceImporter
        if name in ['source_version', 'source_versions']:
            return V1SourceVersionImporter
        if name in ['source_id', 'source_ids', 'source_version_id', 'source_version_ids']:
            return V1SourceIdsImporter
        if name in ['collection', 'collections']:
            return V1CollectionImporter
        if name in ['collection_version', 'collection_versions']:
            return V1CollectionVersionImporter
        if name in ['collection_id', 'collection_ids', 'collection_version_id', 'collection_version_ids']:
            return V1CollectionIdsImporter
        if name in ['concept', 'concepts']:
            return V1ConceptImporter
        if name in ['concept_version', 'concept_versions']:
            return V1ConceptVersionImporter
        if name in ['concept_id', 'concept_ids', 'concept_version_id', 'concept_version_ids']:
            return V1ConceptIdsImporter
        if name in ['mapping', 'mappings']:
            return V1MappingImporter
        if name in ['mapping_version', 'mapping_versions']:
            return V1MappingVersionImporter
        if name in ['web_user_credential']:
            return V1WebUserCredentialsImporter
        if name in ['tokens']:
            return V1UserTokensImporter
        if name in ['collection_reference']:
            return V1CollectionReferencesImporter

        return None

    @staticmethod
    def get_locales(data):
        data = data or []
        locales = []
        for locale in data:
            params = locale.copy()
            internal_reference_id = params.pop('uuid')
            params['internal_reference_id'] = internal_reference_id
            queryset = LocalizedText.objects.filter(internal_reference_id=internal_reference_id)
            if queryset.exists():
                locales.append(queryset.first())
            else:
                locales.append(LocalizedText.objects.create(**params))
        return locales


class V1OrganizationImporter(V1BaseImporter):
    result_attrs = ['created', 'existed', 'failed']
    start_message = 'STARTING ORGS IMPORT'

    def after_run(self):
        populate_indexes.delay(['orgs'])

    def process_line(self, line):
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


class V1UserImporter(V1BaseImporter):
    result_attrs = ['created', 'updated', 'existed', 'failed']
    start_message = 'STARTING USERS IMPORT'

    def process_line(self, line):  # pylint: disable=too-many-locals
        data = json.loads(line)
        original_data = data.copy()
        self.processed += 1
        data.pop('_id')
        _id = data.pop('user_id')
        last_login = data.pop('last_login')
        date_joined = data.pop('date_joined')
        full_name = data.pop('full_name') or ''
        name_parts = list(set(full_name.split(' ')))
        first_name = data.pop('first_name', '') or ' '.join(name_parts[:-1])
        last_name = data.pop('last_name', '') or name_parts[-1]
        orgs = data.pop('organizations', [])
        password = data.pop('password')
        hashed_password = data.pop('hashed_password')
        password = password or hashed_password
        data['verified'] = data.pop('verified_email', True)

        data['last_name'] = last_name
        data['internal_reference_id'] = get(_id, '$oid')
        data['date_joined'] = get(date_joined, '$date')
        data['last_login'] = get(last_login, '$date')
        data['first_name'] = first_name
        username = data.get('username')
        self.log("Processing: {} ({}/{})".format(username, self.processed, self.total))
        queryset = UserProfile.objects.filter(username=username)
        if queryset.exists():
            user = queryset.first()
            user.organizations.add(*Organization.objects.filter(internal_reference_id__in=orgs).all())
            self.updated.append(original_data)
        else:
            try:
                user = UserProfile.objects.create(**data)
                if user:
                    user.password = password
                    user.save()
                    user.organizations.add(*Organization.objects.filter(internal_reference_id__in=orgs).all())
                    self.created.append(original_data)
                else:
                    self.failed.append(original_data)
            except Exception as ex:
                args = get(ex, 'message_dict') or str(ex)
                self.failed.append({**original_data, 'errors': args})

    def after_run(self):
        populate_indexes.delay(['users', 'orgs'])


class V1SourceImporter(V1BaseImporter):
    result_attrs = ['created', 'updated', 'existed', 'failed']
    start_message = 'STARTING SOURCES IMPORT'

    def process_line(self, line):  # pylint: disable=too-many-locals
        data = json.loads(line)
        original_data = data.copy()
        self.processed += 1
        _id = data.pop('_id')
        data.pop('parent_type_id')
        parent_id = data.pop('parent_id')
        created_at = data.pop('created_at')
        updated_at = data.pop('updated_at')
        created_by = data.get('created_by')
        updated_by = data.get('updated_by')
        creator = self.get_user(created_by)
        updater = self.get_user(updated_by)

        if creator:
            data['created_by'] = creator
        if updater:
            data['updated_by'] = updater
        data['internal_reference_id'] = get(_id, '$oid')
        data['created_at'] = get(created_at, '$date')
        data['updated_at'] = get(updated_at, '$date')
        mnemonic = data.get('mnemonic')

        uri = data['uri']
        if '/orgs/' in uri:
            org = self.get_org(internal_reference_id=parent_id)
            data['organization'] = org

        self.log("Processing: {} ({}/{})".format(mnemonic, self.processed, self.total))
        if Source.objects.filter(uri=uri).exists():
            self.existed.append(original_data)
        else:
            source = Source.objects.create(**data, version=HEAD)
            if source:
                source.update_mappings()
                source.save()
                self.created.append(original_data)
            else:
                self.failed.append(original_data)


class V1SourceVersionImporter(V1BaseImporter):
    result_attrs = ['created', 'updated', 'existed', 'failed']
    start_message = 'STARTING SOURCE VERSIONS IMPORT'

    def process_line(self, line):  # pylint: disable=too-many-locals
        data = json.loads(line)
        original_data = data.copy()
        self.processed += 1
        _id = data.pop('_id')
        data['internal_reference_id'] = get(_id, '$oid')
        for attr in [
                'active_concepts', 'active_mappings', 'last_child_update', 'last_concept_update', 'last_mapping_update',
                'parent_version_id', 'previous_version_id', 'versioned_object_type_id',
        ]:
            data.pop(attr, None)

        data['snapshot'] = data.pop('source_snapshot', None)
        data['external_id'] = data.pop('version_external_id', None)

        versioned_object_id = data.pop('versioned_object_id')
        versioned_object = self.get_source(versioned_object_id)
        version = data.pop('mnemonic')
        created_at = data.pop('created_at')
        updated_at = data.pop('updated_at')
        created_by = data.get('created_by')
        updated_by = data.get('updated_by')
        creator = self.get_user(created_by)
        updater = self.get_user(updated_by)

        if creator:
            data['created_by'] = creator
        if updater:
            data['updated_by'] = updater
        data['created_at'] = get(created_at, '$date')
        data['updated_at'] = get(updated_at, '$date')
        data['organization_id'] = versioned_object.organization_id
        data['user_id'] = versioned_object.user_id
        data['source_type'] = versioned_object.source_type

        self.log("Processing: {} ({}/{})".format(version, self.processed, self.total))
        if Source.objects.filter(uri=data['uri']).exists():
            self.existed.append(original_data)
        else:
            source = Source.objects.create(**data, version=version, mnemonic=versioned_object.mnemonic)
            if source:
                source.update_mappings()
                source.save()
                self.created.append(original_data)
            else:
                self.failed.append(original_data)


class V1ConceptImporter(V1BaseImporter):
    result_attrs = ['created', 'existed', 'failed']
    start_message = 'STARTING CONCEPTS IMPORT'

    def process_line(self, line):  # pylint: disable=too-many-locals
        data = json.loads(line)
        original_data = data.copy()
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
        creator = self.get_user(created_by)
        updater = self.get_user(updated_by)

        if creator:
            data['created_by'] = creator
        if updater:
            data['updated_by'] = updater

        self.log("Processing: {} ({}/{})".format(mnemonic, self.processed, self.total))
        if Concept.objects.filter(uri=data['uri']).exists():
            self.existed.append(original_data)
        else:
            try:
                if parent_id in self.sources:
                    source = self.sources[parent_id]
                else:
                    source = self.get_source(parent_id)
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
                self.log("Failed: {}".format(data['uri']))
                args = get(ex, 'message_dict') or str(ex)
                self.log(args)
                self.failed.append({**original_data, 'errors': args})


class V1ConceptVersionImporter(V1BaseImporter):
    start_message = 'STARTING CONCEPT VERSIONS IMPORT'
    result_attrs = ['created', 'existed', 'failed']

    def process_line(self, line):  # pylint: disable=too-many-locals,too-many-statements
        data = json.loads(line)
        original_data = data.copy()
        self.processed += 1
        data.pop('parent_type_id', None)
        created_at = data.pop('created_at')
        updated_at = data.pop('updated_at')
        created_by = data.get('created_by') or data.pop('version_created_by') or 'ocladmin'
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
        versioned_object = self.get_concept(versioned_object_id)
        if not versioned_object:
            self.failed.append({**original_data, 'errors': ['versioned_object not found']})
            return
        mnemonic = versioned_object.mnemonic
        descriptions_data = data.pop('descriptions', [])
        names_data = data.pop('names', [])
        data['version'] = data.pop('mnemonic')
        data['internal_reference_id'] = get(_id, '$oid')
        data['created_at'] = get(created_at, '$date')
        data['updated_at'] = get(updated_at, '$date')

        creator = self.get_user(created_by)
        updater = self.get_user(updated_by)

        if creator:
            data['created_by'] = creator
        if updater:
            data['updated_by'] = updater

        self.log("Processing: {} ({}/{})".format(mnemonic, self.processed, self.total))
        if Concept.objects.filter(uri=data['uri']).exists():
            self.existed.append(original_data)
        else:
            try:
                data.pop('parent_id', None)
                source = versioned_object.parent
                names = self.get_locales(names_data)
                descriptions = self.get_locales(descriptions_data)
                concept = Concept.objects.create(
                    **data, mnemonic=mnemonic, parent=source, versioned_object_id=versioned_object.id
                )
                concept.names.set(names)
                concept.descriptions.set(descriptions)
                source_versions = [source]
                if source_version_ids:
                    source_versions += list(Source.objects.filter(internal_reference_id__in=source_version_ids))
                concept.sources.set(source_versions)
                concept.update_mappings()
                concept.index()
                self.created.append(original_data)
            except Exception as ex:
                self.log("Failed: {}".format(data['uri']))
                args = get(ex, 'message_dict') or str(ex)
                self.log(args)
                self.failed.append({**original_data, 'errors': args})


class V1MappingImporter(V1BaseImporter):
    start_message = 'STARTING MAPPINGS IMPORT'
    result_attrs = ['created', 'existed', 'failed']

    def process_line(self, line):  # pylint: disable=too-many-locals,too-many-statements
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
                data['to_concept_code'] = get(data, 'to_concept_code') or to_concept.mnemonic
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

        creator = self.get_user(created_by)
        updater = self.get_user(updated_by)

        if creator:
            data['created_by'] = creator
        if updater:
            data['updated_by'] = updater

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
                args = get(ex, 'message_dict') or str(ex)
                self.log(args)
                self.log(str(data))
                self.failed.append({**original_data, 'errors': args})


class V1MappingVersionImporter(V1BaseImporter):
    start_message = 'STARTING MAPPING VERSIONS IMPORT'
    result_attrs = ['created', 'existed', 'failed']

    def process_line(self, line):  # pylint: disable=too-many-locals,too-many-statements
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
        versioned_object = self.get_mapping(versioned_object_id)
        if not versioned_object:
            self.failed.append({**original_data, 'errors': ['versioned_object not found']})
            return

        mnemonic = versioned_object.mnemonic
        data['version'] = data.pop('mnemonic')
        data['internal_reference_id'] = get(_id, '$oid')
        data['created_at'] = get(created_at, '$date')
        data['updated_at'] = get(updated_at, '$date')
        from_concept_id = get(data.pop('from_concept_id'), '$oid')
        to_concept_id = get(data.pop('to_concept_id'), '$oid')
        to_source_id = get(data.pop('to_source_id'), '$oid')
        from_concept = self.get_concept(from_concept_id)
        to_concept = None
        to_source = None
        if to_concept_id:
            to_concept = self.get_concept(to_concept_id)
        if to_source_id:
            to_source = self.get_source(to_source_id)

        creator = self.get_user(created_by)
        updater = self.get_user(updated_by)

        if creator:
            data['created_by'] = creator
        if updater:
            data['updated_by'] = updater

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
                args = get(ex, 'message_dict') or str(ex)
                self.log(args)
                self.failed.append({**original_data, 'errors': args})


class V1CollectionImporter(V1BaseImporter):
    start_message = 'STARTING COLLECTION IMPORT'
    result_attrs = ['created', 'updated', 'existed', 'failed', 'not_found_expressions']
    not_found_expressions = dict()

    def add_in_not_found_expression(self, collection_uri, expression):
        if collection_uri not in self.not_found_expressions:
            self.not_found_expressions[collection_uri] = []

        self.not_found_expressions[collection_uri].append(expression)

    def process_line(self, line):  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
        data = json.loads(line)
        original_data = data.copy()
        self.processed += 1
        _id = data.pop('_id')
        for attr in ['parent_type_id', 'concepts', 'mappings']:
            data.pop(attr, None)

        parent_id = data.pop('parent_id')
        created_at = data.pop('created_at')
        updated_at = data.pop('updated_at')
        created_by = data.get('created_by')
        updated_by = data.get('updated_by')
        references = data.pop('references') or []

        creator = self.get_user(created_by)
        updater = self.get_user(updated_by)
        if creator:
            data['created_by'] = creator
        if updater:
            data['updated_by'] = updater

        data['internal_reference_id'] = get(_id, '$oid')
        data['created_at'] = get(created_at, '$date')
        data['updated_at'] = get(updated_at, '$date')
        mnemonic = data.get('mnemonic')
        data['organization'] = self.get_org(internal_reference_id=parent_id)

        self.log("Processing: {} ({}/{})".format(mnemonic, self.processed, self.total))
        uri = data['uri']
        if Collection.objects.filter(uri=uri).exists():
            self.existed.append(original_data)
        else:
            collection = Collection.objects.create(**data, version=HEAD)
            if collection.id:
                self.created.append(original_data)
            else:
                self.failed.append(original_data)
                return
            saved_references = []
            concepts = []
            mappings = []
            for ref in references:
                expression = ref.get('expression')
                __is_concept = is_concept(expression)
                concept = None
                mapping = None
                if __is_concept:
                    concept = Concept.objects.filter(uri=expression).first()
                    if concept:
                        concepts.append(concept)
                else:
                    mapping = Mapping.objects.filter(uri=expression).first()
                    if mapping:
                        mappings.append(mapping)

                if not concept and not mapping:
                    self.add_in_not_found_expression(uri, expression)
                    continue

                reference = CollectionReference(expression=expression)
                reference.save()
                saved_references.append(reference)

            collection.references.set(saved_references)
            collection.concepts.set(concepts)
            collection.mappings.set(mappings)
            collection.batch_index(collection.concepts, ConceptDocument)
            collection.batch_index(collection.mappings, MappingDocument)


class V1CollectionVersionImporter(V1BaseImporter):
    start_message = 'STARTING COLLECTION VERSIONS IMPORT'
    result_attrs = ['created', 'updated', 'existed', 'failed', 'not_found_expressions']
    not_found_expressions = dict()

    def add_in_not_found_expression(self, collection_uri, expression):
        if collection_uri not in self.not_found_expressions:
            self.not_found_expressions[collection_uri] = []

        self.not_found_expressions[collection_uri].append(expression)

    def process_line(self, line):  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
        data = json.loads(line)
        original_data = data.copy()
        self.processed += 1
        _id = data.pop('_id')
        data['internal_reference_id'] = get(_id, '$oid')
        for attr in [
                'active_concepts', 'active_mappings', 'last_child_update', 'last_concept_update', 'last_mapping_update',
                'parent_version_id', 'previous_version_id', 'versioned_object_type_id', 'concepts', 'mappings'
        ]:
            data.pop(attr, None)

        data['snapshot'] = data.pop('collection_snapshot', None)
        data['external_id'] = data.pop('version_external_id', None)

        versioned_object_id = data.pop('versioned_object_id')
        versioned_object = self.get_collection(versioned_object_id)
        version = data.pop('mnemonic')
        created_at = data.pop('created_at')
        updated_at = data.pop('updated_at')
        created_by = data.get('created_by')
        updated_by = data.get('updated_by')
        creator = self.get_user(created_by)
        updater = self.get_user(updated_by)
        if creator:
            data['created_by'] = creator
        if updater:
            data['updated_by'] = updater
        data['created_at'] = get(created_at, '$date')
        data['updated_at'] = get(updated_at, '$date')
        data['organization_id'] = versioned_object.organization_id
        data['user_id'] = versioned_object.user_id
        data['collection_type'] = versioned_object.collection_type
        references = data.pop('references') or []

        self.log("Processing: {} ({}/{})".format(version, self.processed, self.total))
        uri = data['uri']
        if Collection.objects.filter(uri=uri).exists():
            self.existed.append(original_data)
        else:
            collection = Collection.objects.create(**data, version=version, mnemonic=versioned_object.mnemonic)
            if collection.id:
                self.created.append(original_data)
            else:
                self.failed.append(original_data)
                return
            saved_references = []
            concepts = []
            mappings = []
            for ref in references:
                expression = ref.get('expression')
                __is_concept = is_concept(expression)
                concept = None
                mapping = None
                if __is_concept:
                    concept = Concept.objects.filter(uri=expression).first()
                    if concept:
                        concepts.append(concept)
                else:
                    mapping = Mapping.objects.filter(uri=expression).first()
                    if mapping:
                        mappings.append(mapping)

                if not concept and not mapping:
                    self.add_in_not_found_expression(uri, expression)
                    continue

                reference = CollectionReference(expression=expression)
                reference.save()
                saved_references.append(reference)

            collection.references.set(saved_references)
            collection.concepts.set(concepts)
            collection.mappings.set(mappings)
            collection.batch_index(collection.concepts, ConceptDocument)
            collection.batch_index(collection.mappings, MappingDocument)


class V1CollectionReferencesImporter(V1BaseImporter):
    start_message = 'STARTING COLLECTION REFERENCES IMPORTER'
    result_attrs = ['created', 'not_found', 'existed', 'not_found_references']

    def __init__(self, file_url, **kwargs):
        self.data = dict()
        self.drop_version_if_version_missing = kwargs.get('drop_version_if_version_missing', False)
        super().__init__(file_url)

    def read_file(self):
        if self.file_url:
            print("***", self.file_url)
            file = urllib.request.urlopen(self.file_url)

            self.data = json.loads(file.read())
            self.total = len(self.data.keys())

    def run(self):
        self.start_time = time.time()
        if not isinstance(self.lines, list):
            return None

        self.log(self.start_message)
        self.log('TOTAL: {}'.format(self.total))

        for collection_uri, expressions in self.data.items():
            self.process(collection_uri, expressions)

        self.elapsed_seconds = time.time() - self.start_time

        return self.total_result

    def process(self, collection_uri, expressions):
        self.processed += 1
        self.log("Processing: {} ({}/{})".format(collection_uri, self.processed, self.total))

        collection = Collection.objects.filter(uri=collection_uri).first()
        saved_references = []
        concepts = []
        mappings = []

        if collection:
            for expression in expressions:
                self.log("Processing Expression: {} ".format(expression))
                __is_concept = is_concept(expression)
                if __is_concept:
                    model = Concept
                    _instances = concepts
                else:
                    model = Mapping
                    _instances = mappings

                instance = model.objects.filter(uri=expression).first()
                if self.drop_version_if_version_missing and not instance:
                    instance = model.objects.filter(uri=drop_version(expression)).first()
                if not instance:
                    self.not_found_references.append(expression)
                    continue

                latest_version = instance.get_latest_version()
                if not latest_version:
                    latest_version = model.create_initial_version(instance)
                    if __is_concept:
                        latest_version.cloned_names = [name.clone() for name in instance.names.all()]
                        latest_version.cloned_descriptions = [desc.clone() for desc in instance.descriptions.all()]
                        latest_version.set_locales()
                    parent = instance.parent
                    latest_version.sources.set([parent, parent.head])
                reference = CollectionReference(expression=latest_version.uri)
                reference.save()
                saved_references.append(reference)
                _instances.append(latest_version)
                self.created.append(expression)
            collection.references.add(*saved_references)
            if concepts:
                collection.concepts.add(*concepts)
                collection.batch_index(collection.concepts, ConceptDocument)
            if mappings:
                collection.mappings.add(*mappings)
                collection.batch_index(collection.mappings, MappingDocument)

        else:
            self.not_found.append(collection_uri)


class V1UserTokensImporter(V1BaseImporter):
    start_message = 'STARTING TOKEN IMPORT'
    result_attrs = ['updated', 'not_found', 'old_users']

    def process_line(self, line):
        self.processed += 1
        data = json.loads(line)
        original_data = data.copy()
        username = data.get('username')
        token = data.get('token')
        self.log("Processing: {} ({}/{})".format(username, self.processed, self.total))
        user = UserProfile.objects.filter(username=username).first()
        oct_1_2020 = datetime(2020, 10, 1).timestamp()
        if user and (not user.last_login or user.last_login.timestamp() >= oct_1_2020):
            user.set_token(token)
            self.updated.append(original_data)
        else:
            self.not_found.append(original_data)


class V1WebUserCredentialsImporter(V1BaseImporter):
    start_message = 'STARTING WEB USER CREDENTIALS IMPORT'
    result_attrs = ['updated', 'not_found']

    def process_line(self, line):
        self.processed += 1
        data = json.loads(line)
        original_data = data.copy()
        username = data.get('username')
        password = data.get('password')
        last_login = data.get('last_login')
        self.log("Processing: {} ({}/{})".format(username, self.processed, self.total))
        user = UserProfile.objects.filter(username=username).first()
        if user:
            user.password = password
            user.last_login = last_login
            user.save()
            self.updated.append(original_data)
        else:
            self.not_found.append(original_data)


class V1IdsImporter(V1BaseImporter):
    model = None
    result_attrs = ['updated', 'not_found', 'failed']

    def process_line(self, line):
        data = json.loads(line)
        original_data = data.copy()
        try:
            _id = get(data.pop('_id'), '$oid')
            uri = data.pop('uri')
            self.processed += 1
            updated = self.model.objects.filter(uri=uri).update(internal_reference_id=_id)
            if updated:
                self.updated.append(original_data)
                self.log("Updated: {} ({}/{})".format(uri, self.processed, self.total))
            else:
                self.not_found.append(original_data)
                self.log("Not Found: {} ({}/{})".format(uri, self.processed, self.total))

        except Exception as ex:
            self.log("Failed: ")
            self.log(ex.args)
            self.failed.append({**original_data, 'errors': ex.args})


class V1SourceIdsImporter(V1IdsImporter):
    start_message = 'STARTING SOURCE IDS IMPORT'
    model = Source


class V1ConceptIdsImporter(V1IdsImporter):
    start_message = 'STARTING CONCEPT IDS IMPORT'
    model = Concept


class V1CollectionIdsImporter(V1IdsImporter):
    start_message = 'STARTING COLLECTION IDS IMPORT'
    model = Collection
