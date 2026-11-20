from django.core.management import BaseCommand, CommandError
from django.db.models import F
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
        "rewrites the mapped codes of that source's release docs, and of its older rows' docs, in the concepts index: a "
        "doc indexed before this carries HEAD's codes, a row HEAD doesn't hold now takes its public releases', and one "
        "no release holds (a version only a collection holds) HEAD's active codes."
    )

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='Only check the mapping, changing nothing.')
        parser.add_argument('--index', help='The index (default: the concepts index).')
        parser.add_argument(
            '--source', action='append', default=[],
            help="A source's URL, such as /orgs/Regenstrief/sources/LOINC/, whose release docs to rewrite. "
                 "Repeatable; not with --index.")

    def handle(self, *args, **options):
        if options['index'] and options['source']:  # the updates go to the concepts index
            raise CommandError('--source rewrites docs in the concepts index only: leave out --index')
        index = options['index'] or ConceptDocument._index._name  # pylint: disable=protected-access
        sources = [self.get_source(url) for url in options['source']]
        es = connections.get_connection()
        expected = ConceptDocument._doc_type.mapping.to_dict()['properties'][RELEASE_MAP_CODES]  # pylint: disable=protected-access
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
                self.stdout.write(f'{source.uri}: {self.backfill(source)} docs updated')

    @staticmethod
    def get_source(url):
        source = Source.objects.filter(uri=url, version=HEAD).first()
        if not source:
            raise CommandError(f'{url}: no such source')
        return source

    @staticmethod
    def backfill(source):
        """
        Rewrites the mapped codes of the docs of every row `source`'s releases hold, and of each older row of it (not
        the versioned object or the latest version): one no release holds, such as a version only a collection holds,
        now takes HEAD's active codes. Returns how many.
        """
        release_rows = Concept.sources.through.objects.filter(
            source_id__in=source.versions.exclude(version=HEAD).values('id')).values_list('concept_id', flat=True)
        older_rows = Concept.objects.filter(parent_id=source.id, is_latest_version=False).exclude(
            id=F('versioned_object_id')).values_list('id', flat=True)
        concept_ids = sorted(set(release_rows) | set(older_rows))
        for ids in chunks(concept_ids, BACKFILL_CHUNK_SIZE):
            Concept.batch_index_partial_by_ids(
                Concept.objects.filter(id__in=ids), ConceptDocument, Concept.get_mapped_codes_actions,
                single_batch=True, parallel=False, refresh=False)
        return len(concept_ids)
