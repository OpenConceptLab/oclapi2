from django.core.management import BaseCommand, CommandError
from elasticsearch import ApiError
from elasticsearch_dsl.connections import connections

from core.concepts.documents import ConceptDocument, VECTOR_PROVENANCE_MAPPING


class Command(BaseCommand):
    help = (
        'Adds the vector provenance fields (OpenConceptLab/ocl_online#247: the text each vector encoded, and the model) '
        'to an existing concepts index, then checks them. Additive: nothing is reindexed. Run it before deploying the '
        'code that writes them: once a doc carries them, ES has mapped them dynamically and this can no longer apply.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='Only check the mapping, changing nothing.')
        parser.add_argument('--index', help='The index (default: the concepts index).')

    def handle(self, *args, **options):
        index = options['index'] or ConceptDocument._index._name  # pylint: disable=protected-access
        es = connections.get_connection()
        if not options['check']:
            try:
                es.indices.put_mapping(index=index, properties=VECTOR_PROVENANCE_MAPPING)
            except ApiError as ex:
                raise CommandError(f'{index}: the mapping was not changed: {ex}') from ex

        problems = []
        for path, expected in self.get_expected_fields():
            found = es.indices.get_field_mapping(index=index, fields=path)[index]['mappings'].get(path)
            actual = found['mapping'][path.split('.')[-1]] if found else None
            if actual != expected:
                problems.append(f'{path} is {actual or "missing"}, expected {expected}')
        if problems:
            raise CommandError(f'{index}: ' + '; '.join(problems))
        self.stdout.write(f'{index}: vector provenance fields ok')

    @staticmethod
    def get_expected_fields():
        for field, mapping in VECTOR_PROVENANCE_MAPPING.items():
            if mapping.get('type') == 'nested':
                for sub_field, sub_mapping in mapping['properties'].items():
                    yield f'{field}.{sub_field}', sub_mapping
            else:
                yield field, mapping
