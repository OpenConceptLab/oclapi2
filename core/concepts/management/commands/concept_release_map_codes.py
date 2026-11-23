import time
from collections import defaultdict

from django.core.management import BaseCommand, CommandError
from django.db.models import F
from elasticsearch import ApiError
from elasticsearch_dsl.connections import connections

from core.common.constants import HEAD
from core.common.utils import chunks
from core.concepts.documents import ConceptDocument, RELEASE_MAP_CODES
from core.concepts.models import Concept
from core.sources.models import Source

BACKFILL_CHUNK_SIZE = 1000  # rows, about: a concept's rows are never split between chunks
BACKFILL_PAGE_SIZE = 200  # the concepts whose rows one query reads
MAX_PAUSE = 3600  # seconds


class Command(BaseCommand):
    help = (
        f'Adds {RELEASE_MAP_CODES} (OpenConceptLab/ocl_online#703: the mapped codes of each release whose own '
        'mappings differ from those of the doc it shares) to an existing concepts index, then checks it. Additive: '
        'nothing is reindexed. Run it before deploying the code that writes the field: once a doc carries it, ES has '
        'mapped it dynamically, as an object rather than nested, and this can no longer apply. With --source, it then '
        "rewrites the mapped codes of that source's release docs, and of its older rows' docs, in the concepts index: "
        "a doc indexed before this carries HEAD's codes, a row HEAD doesn't hold now takes its public releases', and "
        "one no release holds (a version only a collection holds) HEAD's active codes. It goes concept by concept, a "
        "chunk of concepts at a time, and prints a line per chunk; --after, with the last concept id a line printed, "
        "picks a stopped run up again."
    )

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='Only check the mapping, changing nothing.')
        parser.add_argument('--index', help='The index (default: the concepts index).')
        parser.add_argument(
            '--source', action='append', default=[],
            help="Rewrite mapped codes for release members and older concept rows of this source. "
                 "Use the source URL, for example /orgs/Regenstrief/sources/LOINC/. "
                 "Repeatable. Do not combine with --index.")
        parser.add_argument(
            '--after', type=int,
            help='With one --source: start after this concept (the versioned object id a progress line printed).')
        parser.add_argument(
            '--pause', type=float, default=0, help='Seconds to wait after each chunk, to leave the database room.')

    def handle(self, *args, **options):
        if options['index'] and options['source']:  # the updates go to the concepts index
            raise CommandError('--source rewrites docs in the concepts index only: leave out --index')
        if options['after'] is not None and len(options['source']) != 1:
            raise CommandError('--after picks up one source: give exactly one --source')
        if not 0 <= options['pause'] <= MAX_PAUSE:  # not after the first chunk is written
            raise CommandError(f'--pause takes 0 to {MAX_PAUSE} seconds')
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
                docs = self.backfill(source, options['after'], options['pause'])
                self.stdout.write(f'{source.uri}: {docs} docs updated')

    @staticmethod
    def get_source(url):
        source = Source.objects.filter(uri=url, version=HEAD).first()
        if not source:
            raise CommandError(f'{url}: no such source')
        return source

    def backfill(self, source, after=None, pause=0):
        """
        Rewrites the mapped codes of the docs of every row `source`'s releases hold, and of each older row of it (not
        the versioned object or the latest version): one no release holds, such as a version only a collection holds,
        now takes HEAD's active codes. Returns how many.

        A concept's rows go in one chunk, so its mappings are read once (OpenConceptLab/ocl_online#748). After each
        chunk it prints the last concept done, which `after` takes to skip those, then, if it wrote any doc, waits
        `pause` seconds.
        """
        started = time.monotonic()
        versioned_objects = Concept.objects.filter(parent_id=source.id, id=F('versioned_object_id'))
        if after is not None:
            versioned_objects = versioned_objects.filter(id__gt=after)
        versioned_object_ids = sorted(versioned_objects.values_list('id', flat=True))
        total, done, docs = len(versioned_object_ids), 0, 0
        chunk_started = time.monotonic()
        for number, (last, concepts, ids) in enumerate(self.get_chunks(source, versioned_object_ids, after), 1):
            if ids:
                Concept.batch_index_partial_by_ids(
                    Concept.objects.filter(id__in=ids), ConceptDocument, Concept.get_mapped_codes_actions,
                    single_batch=True, parallel=False, refresh=False)
            done, docs = done + concepts, docs + len(ids)
            self.stdout.write(
                f'{source.uri}: chunk {number}: {len(ids)} docs of {concepts} concepts in '
                f'{time.monotonic() - chunk_started:.1f}s; {done}/{total} concepts, {docs} docs, '
                f'{time.monotonic() - started:.0f}s so far'
                + (f'; to resume: --after {last}' if last is not None else ''))
            if pause and ids:  # before the next chunk's rows are read too
                time.sleep(pause)
            chunk_started = time.monotonic()
        return docs

    @staticmethod
    def get_chunks(source, versioned_object_ids, after=None):
        """
        Yields (the last versioned object id, how many concepts, the row ids) for each chunk of the rows backfill
        rewrites: the rows of `versioned_object_ids`, in that order, whole concepts at a time, BACKFILL_PAGE_SIZE
        concepts at most (first, unless resuming, the rows that have no versioned object, with None as the last).
        """
        release_ids = list(source.versions.exclude(version=HEAD).values_list('id', flat=True))

        def get_rows(queryset):  # {versioned object id: [the ids of its rows that the backfill rewrites]}
            rows = list(queryset.values_list('versioned_object_id', 'id', 'parent_id', 'is_latest_version'))
            older = {id_ for versioned_object_id, id_, parent_id, is_latest_version in rows
                     if parent_id == source.id and not is_latest_version and id_ != versioned_object_id}
            held = set(Concept.sources.through.objects.filter(
                source_id__in=release_ids, concept_id__in=[row[1] for row in rows if row[1] not in older]
            ).values_list('concept_id', flat=True)) if release_ids else set()
            ids = defaultdict(list)
            for versioned_object_id, id_, *_ in sorted(rows, key=lambda row: row[1]):
                if id_ in older or id_ in held:
                    ids[versioned_object_id].append(id_)
            return ids

        if after is None:
            if ids := get_rows(Concept.objects.filter(parent_id=source.id, versioned_object_id__isnull=True))[None]:
                yield None, 0, ids
        last = None
        # a chunk ends with its page, so a long run of concepts with no rows to rewrite still checkpoints page by page
        for page in chunks(versioned_object_ids, BACKFILL_PAGE_SIZE):
            rows = get_rows(Concept.objects.filter(versioned_object_id__in=page))
            concepts, ids = 0, []
            for versioned_object_id in page:
                if ids and len(ids) + len(rows[versioned_object_id]) > BACKFILL_CHUNK_SIZE:
                    yield last, concepts, ids
                    concepts, ids = 0, []
                last, concepts = versioned_object_id, concepts + 1
                ids.extend(rows[versioned_object_id])
            yield last, concepts, ids
