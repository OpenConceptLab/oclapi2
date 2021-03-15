import json
from pprint import pprint

from django.core.management import BaseCommand
from pydash import get

from core.collections.models import Collection, CollectionReference
from core.collections.utils import is_concept
from core.common.constants import HEAD
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.orgs.models import Organization
from core.users.models import UserProfile


class Command(BaseCommand):
    help = 'import v1 collections'

    total = 0
    processed = 0
    created = []
    existed = []
    failed = []
    parents = dict()
    users = dict()
    references = dict()
    not_found_expressions = dict()

    @staticmethod
    def log(msg):
        print("*******{}*******".format(msg))

    def get_parent(self, parent_id, uri):
        if parent_id not in self.parents:
            if '/orgs/' in uri:
                result = dict(organization=Organization.objects.filter(internal_reference_id=parent_id).first())
            else:
                result = dict(user=UserProfile.objects.filter(internal_reference_id=parent_id).first())
            self.parents[parent_id] = result

        return self.parents[parent_id]

    def add_in_not_found_expression(self, collection_uri, expression):
        if collection_uri not in self.not_found_expressions:
            self.not_found_expressions[collection_uri] = []

        self.not_found_expressions[collection_uri].append(expression)

    def handle(self, *args, **options):
        FILE_PATH = '/code/core/importers/v1_dump/data/exported_collections.json'
        lines = open(FILE_PATH, 'r').readlines()

        self.log('STARTING COLLECTION IMPORT')
        self.total = len(lines)
        self.log('TOTAL: {}'.format(self.total))

        for line in lines:
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
            qs = UserProfile.objects.filter(username=created_by)
            if qs.exists():
                data['created_by'] = qs.first()
            qs = UserProfile.objects.filter(username=updated_by)
            if qs.exists():
                data['updated_by'] = qs.first()
            data['internal_reference_id'] = get(_id, '$oid')
            data['created_at'] = get(created_at, '$date')
            data['updated_at'] = get(updated_at, '$date')
            mnemonic = data.get('mnemonic')
            data = {**data, **self.get_parent(parent_id, data['uri'])}

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
                    continue
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
        if self.not_found_expressions:
            self.log('Expressions Not Added')
            pprint(self.not_found_expressions)

