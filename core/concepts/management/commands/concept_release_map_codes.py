from django.core.management import BaseCommand, CommandError
from elasticsearch import ApiError
from elasticsearch_dsl.connections import connections

from core.common.constants import HEAD
from core.common.utils import chunks
from core.concepts.documents import ConceptDocument, RELEASE_MAP_CODES
from core.concepts.models import Concept
from core.sources.models import Source

BACKFILL_CHUNK_SIZE = 1000


class Command(BaseCommand):
    help = (
        f'Adds {RELEASE_MAP_CODES} (OpenConceptLab/ocl_online#703: the mapped codes of each release whose own '
        'mappings differ from those of the doc it shares) to an existing concepts index, then checks it. Additive: '
        'nothing is reindexed. Run it before deploying the code that writes the field: once a doc carries it, ES has '
        'mapped it dynamically, as an object rather than nested, and this can no longer apply. With --source, it then '
        "fills the field on the docs of that source's releases that need an entry."
    )

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='Only check the mapping, changing nothing.')
        parser.add_argument('--index', help='The index (default: the concepts index).')
        parser.add_argument(
            '--source', action='append', default=[],
            help="A source's URL, such as /orgs/Regenstrief/sources/LOINC/, whose release docs to fill. Repeatable.")

    def handle(self, *args, **options):
        index = options['index'] or ConceptDocument._index._name  # pylint: disable=protected-access
        sources = [self.get_source(url) for url in options['source']]
        es = connections.get_connection()
        expected = self.get_expected_mapping()
        if not options['check']:
            try:
                es.indices.put_mapping(index=index, properties={RELEASE_MAP_CODES: expected})
            except ApiError as ex:
                raise CommandError(f'{index}: the mapping was not changed: {ex}') from ex

        mappings = next(iter(es.indices.get_mapping(index=index).values()))['mappings']
        actual = mappings.get('properties', {}).get(RELEASE_MAP_CODES)
        if actual != expected:
            raise CommandError(f'{index}: {RELEASE_MAP_CODES} is {actual or "missing"}, expected {expected}')
        self.stdout.write(f'{index}: {RELEASE_MAP_CODES} ok')

        if not options['check']:
            for source in sources:
                self.stdout.write(f'{source.uri}: {self.backfill(source)} docs given release entries')

    @staticmethod
    def get_expected_mapping():
        return ConceptDocument._doc_type.mapping.to_dict()['properties'][RELEASE_MAP_CODES]  # pylint: disable=protected-access

    @staticmethod
    def get_source(url):
        source = Source.objects.filter(uri=url, version=HEAD).first()
        if not source:
            raise CommandError(f'{url}: no such source')
        return source

    @staticmethod
    def backfill(source):
        """Updates the mapped codes of the docs of `source`'s release rows that get release entries."""
        concept_ids = sorted(set(Concept.sources.through.objects.filter(
            source_id__in=source.versions.exclude(version=HEAD).values('id')).values_list('concept_id', flat=True)))
        updated = 0

        def get_actions(ids):
            nonlocal updated
            for action in Concept.get_mapped_codes_actions(ids):
                if action['doc'][RELEASE_MAP_CODES]:
                    updated += 1
                    yield action

        for ids in chunks(concept_ids, BACKFILL_CHUNK_SIZE):
            Concept.batch_index_partial_by_ids(
                Concept.objects.filter(id__in=ids), ConceptDocument, get_actions, single_batch=True, parallel=False,
                refresh=False)
        return updated
