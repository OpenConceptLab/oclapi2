import json
import tempfile
import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone
from django.test import override_settings, tag
from elasticsearch.helpers import streaming_bulk, BulkIndexError
from mock import patch, ANY

from core.celery import app
from core.common.es import ESScript
from core.common.exceptions import BatchIndexingError
from core.common.tasks import run_deferred_import_indexing, batch_index_resources, get_batch_index_relations, \
    index_concepts_mapped_codes
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.importers.constants import DEFERRED_INDEX_LOCK_KEY, DEFERRED_INDEX_MAX_ATTEMPTS, DEFERRED_INDEX_RETRY_SECONDS
from core.importers.importer import ImporterSubtask
from core.importers.models import BulkImportInline, DeferredImportIndex, ImportIndexer, CREATED
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile

LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
INDEX, MAPPED_CODES, LATEST_FLAGS = (
    DeferredImportIndex.Stage.INDEX, DeferredImportIndex.Stage.MAPPED_CODES, DeferredImportIndex.Stage.LATEST_FLAGS)


def concept_line(source, mnemonic, name=None, **extra):
    return {
        'type': 'Concept', 'id': mnemonic, 'concept_class': 'Misc', 'datatype': 'None',
        'owner_type': 'Organization', 'owner': source.organization.mnemonic, 'source': source.mnemonic,
        'names': [
            {'name': name or mnemonic, 'locale': 'en', 'locale_preferred': True, 'name_type': 'Fully Specified'}],
        **extra
    }


def mapping_line(source, from_mnemonic, to_mnemonic, **extra):
    return {
        'type': 'Mapping', 'map_type': 'Same As', 'from_concept_url': source.uri + f'concepts/{from_mnemonic}/',
        'to_concept_url': source.uri + f'concepts/{to_mnemonic}/', 'owner_type': 'Organization',
        'owner': source.organization.mnemonic, 'source': source.mnemonic, **extra
    }


def run_import(lines, **kwargs):
    importer = BulkImportInline(content=None, username='ocladmin', update_if_exists=True, input_list=lines, **kwargs)
    importer.run()
    return importer


def deferred():
    """{(stage, resource): ids} of the recorded rows."""
    rows = {}
    for row in DeferredImportIndex.objects.all():
        rows.setdefault((row.stage, row.resource), set()).update(row.ids)
    return rows


def versions(source, mnemonic, model=Concept):
    """A concept's (or mapping's) versioned object and its versions, oldest first."""
    versioned_object = model.objects.get(parent=source, mnemonic=mnemonic, id=F('versioned_object_id'))
    return versioned_object, list(versioned_object.versions.order_by('id'))


@patch('core.importers.models.index_concepts_mapped_codes')
@patch('core.importers.models.batch_index_resources')
class DeferredImportIndexRecordTest(OCLTestCase):
    @override_settings(IMPORT_INDEX_MAX_LINES=1)
    def test_update_import_over_the_limit_records_what_it_skipped(self, batch_index_mock, mapped_codes_mock):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='DeferredUpdateOrg'))
        run_import([concept_line(source, 'C1')])  # one line: within the limit, so it indexes as it finishes
        self.assertEqual(batch_index_mock.apply_async.call_count, 1)
        self.assertFalse(DeferredImportIndex.objects.exists())
        batch_index_mock.reset_mock()

        importer = run_import([concept_line(source, 'C1', datatype='Text'), concept_line(source, 'C2')])

        self.assertEqual((len(importer.updated), len(importer.created)), (1, 1))
        batch_index_mock.apply_async.assert_not_called()
        mapped_codes_mock.apply_async.assert_not_called()
        c1, (c1_first, c1_new) = versions(source, 'C1')
        c2, (c2_first, ) = versions(source, 'C2')
        self.assertEqual(
            set(Concept.objects.filter(parent=source, is_latest_version=True).values_list('id', flat=True)),
            {c1_new.id, c2_first.id}
        )
        self.assertEqual(deferred(), {
            (INDEX, 'concept'): {c1.id, c1_new.id, c2.id, c2_first.id},
            (LATEST_FLAGS, 'concept'): {c1_first.id},  # the version it superseded
        })
        rows = list(DeferredImportIndex.objects.order_by('stage'))
        self.assertEqual(len({(row.group, row.created_at) for row in rows}), 1)  # one part
        self.assertEqual({(row.attempts, row.last_error) for row in rows}, {(0, None)})
        self.assertFalse(Concept.objects.filter(parent=source, _index=False).exists())  # later saves index as usual

    def test_index_false_defers_a_small_import_too(self, batch_index_mock, _):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='DeferredFalseOrg'))

        run_import([concept_line(source, 'C1')], index=False)

        batch_index_mock.apply_async.assert_not_called()
        concept, (version, ) = versions(source, 'C1')
        self.assertEqual(deferred(), {(INDEX, 'concept'): {concept.id, version.id}})

    @override_settings(IMPORT_INDEX_MAX_LINES=1)
    def test_index_true_and_small_imports_still_index_as_they_finish(self, batch_index_mock, _):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='DeferredTrueOrg'))

        run_import([concept_line(source, 'C1'), concept_line(source, 'C2')], index=True)
        run_import([concept_line(source, 'C3')])

        self.assertEqual(batch_index_mock.apply_async.call_count, 2)
        batch_index_mock.apply_async.assert_called_with(
            ('concept', {'id__in': ANY}, True, False), queue='indexing', permanent=False)
        self.assertFalse(DeferredImportIndex.objects.exists())

    def test_mapping_import_records_the_mapped_codes_of_the_concepts_it_maps_from(self, batch_index_mock, _):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='DeferredMappingOrg'))
        from_concept = ConceptFactory(parent=source, mnemonic='From')
        ConceptFactory(parent=source, mnemonic='To')

        importer = run_import([mapping_line(source, 'From', 'To')], index=False)

        self.assertEqual(len(importer.created), 1)
        batch_index_mock.apply_async.assert_not_called()
        mapping = Mapping.objects.get(parent=source, id=F('versioned_object_id'))
        self.assertEqual(deferred(), {
            (INDEX, 'mapping'): {mapping.id, mapping.get_latest_version().id},
            (MAPPED_CODES, 'concept'): {from_concept.versioned_object_id, from_concept.get_latest_version().id},
        })

    @patch('core.importers.models.ImportIndexer.CHUNK_SIZE', 3)
    def test_rows_hold_at_most_a_chunk_of_ids(self, *_):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='DeferredChunkOrg'))

        run_import([concept_line(source, 'C1'), concept_line(source, 'C2')], index=False)

        rows = list(DeferredImportIndex.objects.order_by('id'))
        self.assertEqual([len(row.ids) for row in rows], [3, 1])  # 2 concepts and their 2 initial versions
        self.assertEqual(
            sorted(rows[0].ids + rows[1].ids),
            sorted(Concept.objects.filter(parent=source).values_list('id', flat=True))
        )

    def test_import_type_subtask_records_its_rows_with_the_import_task(self, batch_index_mock, _):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='DeferredSubtaskOrg'))
        with tempfile.NamedTemporaryFile('w', suffix='.json') as file:
            file.write(json.dumps(concept_line(source, 'C1')))
            file.flush()
            results = ImporterSubtask(
                file.name, 'ocladmin', 'organization', source.organization.mnemonic, 'Concept',
                [{'start_index': 0, 'end_index': 1}], False, import_task_id='import-task-id'
            ).run()

        self.assertEqual(results, [CREATED])
        batch_index_mock.apply_async.assert_not_called()
        concept, (version, ) = versions(source, 'C1')
        self.assertEqual(deferred(), {(INDEX, 'concept'): {concept.id, version.id}})
        self.assertEqual(set(DeferredImportIndex.objects.values_list('task_id', flat=True)), {'import-task-id'})

    def test_nothing_to_defer_records_nothing(self, *_):
        self.assertEqual(DeferredImportIndex.record(ImportIndexer(False)), [])
        ImportIndexer(False).finish()
        self.assertFalse(DeferredImportIndex.objects.exists())


class DeferredImportIndexWindowTest(OCLTestCase):
    @staticmethod
    def is_open(hour, minute=0):
        return DeferredImportIndex.is_window_open(datetime(2026, 11, 20, hour, minute, tzinfo=dt_timezone.utc))

    @override_settings(IMPORT_INDEX_WINDOW='01:00-04:30')
    def test_window_is_in_utc(self):
        self.assertEqual(
            [self.is_open(0, 59), self.is_open(1), self.is_open(4, 29), self.is_open(4, 30), self.is_open(12)],
            [False, True, True, False, False]
        )
        eastern = dt_timezone(timedelta(hours=-5))  # 21:00 there is 02:00 UTC
        self.assertTrue(DeferredImportIndex.is_window_open(datetime(2026, 11, 19, 21, 0, tzinfo=eastern)))

    @override_settings(IMPORT_INDEX_WINDOW=' 23:00 - 02:00 ')
    def test_window_can_span_midnight(self):
        self.assertEqual(
            [self.is_open(22, 59), self.is_open(23, 30), self.is_open(1, 59), self.is_open(2)],
            [False, True, True, False]
        )

    def test_empty_or_invalid_window_pauses_the_job(self):
        for window in ['', None]:
            with override_settings(IMPORT_INDEX_WINDOW=window):
                self.assertFalse(self.is_open(2))
        for window in ['nightly', '01:00', '01:00-25:00', '01:00-02:00-03:00']:
            with override_settings(IMPORT_INDEX_WINDOW=window), self.assertLogs('core.importers.models', 'ERROR'):
                self.assertFalse(self.is_open(2))

    @override_settings(IMPORT_INDEX_WINDOW='01:00-04:30')
    def test_window_is_checked_against_the_current_time_by_default(self):
        for hour, is_open in [(3, True), (5, False)]:
            with patch('core.importers.models.timezone.now',
                       return_value=datetime(2026, 11, 20, hour, tzinfo=dt_timezone.utc)):
                self.assertEqual(DeferredImportIndex.is_window_open(), is_open)


@override_settings(CACHES=LOCMEM_CACHE)
class DeferredImportIndexRunTest(OCLTestCase):
    """
    run() with the indexing calls recorded instead of made. A row the job fully indexes while it's the latest version
    counts as searchable, as Elasticsearch would have it, for the guard on the latest flags.
    """
    def setUp(self):
        super().setUp()
        cache.clear()
        self.source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic=f'Run{uuid.uuid4().hex[:8]}'))
        self.indexed = []  # (stage, resource, ids) of each indexing call, in order
        self.searchable = set()  # ids with a document flagged latest
        self.failure = None  # called with each indexing call's (stage, resource, ids) first, to raise
        self.missing = set()  # ids with no document, which the latest-flags update reports

    def tearDown(self):
        cache.clear()
        super().tearDown()

    @staticmethod
    def defer_import(*lines):
        with patch('core.importers.models.batch_index_resources'), \
                patch('core.importers.models.index_concepts_mapped_codes'):
            run_import(list(lines), index=False)

    def recorder(self, stage, resource):
        def record(queryset, *_, **__):
            ids = set(queryset.values_list('id', flat=True))
            if self.failure:
                self.failure(stage, resource, ids)
            self.indexed.append((stage, resource, ids))
            if stage == INDEX:
                self.searchable.update(queryset.filter(is_latest_version=True).values_list('id', flat=True))
            return ids & self.missing
        return record

    def run_rows(self, window=True, flags_seconds=0):
        """
        run() with the window open (False: closed; a number: open for that many checks), recording its calls. By
        default it updates the latest flags after every row.
        """
        checks = []

        def is_window_open(*_):
            checks.append(1)
            return window is True or (not isinstance(window, bool) and len(checks) <= window)

        with ExitStack() as stack:
            stack.enter_context(patch.object(DeferredImportIndex, 'is_window_open', side_effect=is_window_open))
            stack.enter_context(patch('core.importers.models.DEFERRED_INDEX_FLAGS_SECONDS', flags_seconds))
            for model, resource in [(Concept, 'concept'), (Mapping, 'mapping')]:
                stack.enter_context(patch.object(
                    model, 'get_ids_flagged_latest_in_search', side_effect=lambda ids: set(ids) & self.searchable))
                stack.enter_context(patch.object(model, 'batch_index', side_effect=self.recorder(INDEX, resource)))
                stack.enter_context(
                    patch.object(model, 'index_latest_flags', side_effect=self.recorder(LATEST_FLAGS, resource)))
            stack.enter_context(
                patch.object(Concept, 'index_mapped_codes', side_effect=self.recorder(MAPPED_CODES, 'concept')))
            return DeferredImportIndex.run()

    def concept_updates(self, count=1):
        """Deferred parts that create C1 and then update it `count` times; C1's versioned object and versions."""
        self.defer_import(concept_line(self.source, 'C1'))
        for datatype in ['Text', 'Numeric', 'Boolean'][:count]:
            self.defer_import(concept_line(self.source, 'C1', datatype=datatype))
        return versions(self.source, 'C1')

    def test_run_outside_the_window_leaves_the_index_rows(self):
        self.defer_import(concept_line(self.source, 'C1'))

        self.assertEqual(self.run_rows(window=False), 0)

        self.assertEqual(self.indexed, [])
        with override_settings(IMPORT_INDEX_WINDOW=''):  # an empty window pauses the indexing
            self.assertEqual(DeferredImportIndex.run(), 0)
        self.assertEqual(DeferredImportIndex.objects.count(), 1)

    def test_run_without_rows_does_nothing(self):
        self.assertEqual(self.run_rows(), 0)
        self.assertIsNone(cache.get(DEFERRED_INDEX_LOCK_KEY))

    def test_run_indexes_the_parts_in_order_and_flags_the_superseded_versions_once_replaced(self):
        concept, (first, second) = self.concept_updates()
        ConceptFactory(parent=self.source, mnemonic='To')
        self.defer_import(mapping_line(self.source, 'C1', 'To'))
        mapping = Mapping.objects.get(parent=self.source, id=F('versioned_object_id'))

        self.assertEqual(self.run_rows(), 5)

        self.assertEqual(self.indexed, [
            (INDEX, 'concept', {concept.id}),  # not the first version, superseded since: it waits for its flags
            (INDEX, 'concept', {concept.id, second.id}),
            (LATEST_FLAGS, 'concept', {first.id}),  # as soon as its replacement is searchable
            (INDEX, 'mapping', {mapping.id, mapping.get_latest_version().id}),
            (MAPPED_CODES, 'concept', {concept.id, second.id}),
        ])
        self.assertFalse(DeferredImportIndex.objects.exists())
        self.assertIsNone(cache.get(DEFERRED_INDEX_LOCK_KEY))  # released

    def test_superseded_version_keeps_its_flag_until_the_current_replacement_is_searchable(self):
        concept, (first, second, third) = self.concept_updates(2)

        # the window closes after the first update's part: its new version is already superseded by the second's
        self.assertEqual(self.run_rows(window=3), 2)  # the run's check, then one before each batch

        self.assertEqual(self.indexed, [(INDEX, 'concept', {concept.id}), (INDEX, 'concept', {concept.id})])
        self.assertEqual(deferred(), {(INDEX, 'concept'): {concept.id, third.id},
                                      (LATEST_FLAGS, 'concept'): {first.id, second.id}})
        self.indexed = []
        self.assertEqual(self.run_rows(), 3)
        self.assertEqual(self.indexed, [(INDEX, 'concept', {concept.id, third.id}),
                                        (LATEST_FLAGS, 'concept', {first.id}),
                                        (LATEST_FLAGS, 'concept', {second.id})])

    def test_flags_update_outside_the_window_once_something_else_indexed_the_replacement(self):
        concept, (first, second) = self.concept_updates()
        self.assertEqual(self.run_rows(window=False), 0)
        self.assertEqual(self.indexed, [])  # the replacement isn't searchable, so the old version stays flagged

        self.searchable.add(second.id)  # as a release would index it
        self.assertEqual(self.run_rows(window=False), 1)

        self.assertEqual(self.indexed, [(LATEST_FLAGS, 'concept', {first.id})])
        self.assertEqual(deferred(), {(INDEX, 'concept'): {concept.id, first.id, second.id}})

    def test_flags_row_keeps_just_the_versions_still_waiting(self):
        self.defer_import(concept_line(self.source, 'C1'), concept_line(self.source, 'C2'))
        self.defer_import(
            concept_line(self.source, 'C1', datatype='Text'), concept_line(self.source, 'C2', datatype='Text'))
        _, (c1_first, c1_second) = versions(self.source, 'C1')
        _, (c2_first, _) = versions(self.source, 'C2')
        self.searchable.add(c1_second.id)

        self.run_rows(window=False)

        self.assertEqual(self.indexed, [(LATEST_FLAGS, 'concept', {c1_first.id})])
        self.assertEqual(
            DeferredImportIndex.objects.get(stage=LATEST_FLAGS).ids, [c2_first.id])

    def test_index_row_hands_over_a_version_superseded_since_while_its_replacement_is_not_searchable(self):
        self.defer_import(concept_line(self.source, 'C1'))
        _, (first, ) = versions(self.source, 'C1')
        self.searchable.add(first.id)  # as a release would index it
        self.defer_import(concept_line(self.source, 'C1', datatype='Text'))
        concept, (first, second) = versions(self.source, 'C1')

        self.assertEqual(self.run_rows(window=2), 1)  # the run's check and the first part's batch

        # rewriting the first version from the database would un-flag the only document search finds
        self.assertEqual(self.indexed, [(INDEX, 'concept', {concept.id})])
        self.assertEqual(
            deferred(), {(INDEX, 'concept'): {concept.id, second.id}, (LATEST_FLAGS, 'concept'): {first.id}})
        self.indexed = []
        self.run_rows()
        self.assertEqual(self.indexed[0], (INDEX, 'concept', {concept.id, second.id}))
        self.assertEqual(self.indexed[1:], [(LATEST_FLAGS, 'concept', {first.id})])  # the second part's flags row
        self.assertFalse(DeferredImportIndex.objects.exists())

    def test_index_row_hands_over_to_a_flags_row_of_its_own_when_none_has_the_version(self):
        self.defer_import(concept_line(self.source, 'C1'))
        with patch('core.importers.models.batch_index_resources'):  # an import that indexes as it finishes
            run_import([concept_line(self.source, 'C1', datatype='Text')])
        concept, (first, second) = versions(self.source, 'C1')

        self.assertEqual(self.run_rows(), 1)

        self.assertEqual(self.indexed, [(INDEX, 'concept', {concept.id})])
        handed_over = DeferredImportIndex.objects.get()
        self.assertEqual((handed_over.stage, handed_over.ids), (LATEST_FLAGS, [first.id]))
        self.searchable.add(second.id)  # once that import's queued indexing ran
        self.indexed = []
        self.assertEqual(self.run_rows(window=False), 1)
        self.assertEqual(self.indexed, [(LATEST_FLAGS, 'concept', {first.id})])
        self.assertFalse(DeferredImportIndex.objects.exists())

    def test_version_created_and_superseded_in_one_part_is_fully_indexed_in_the_window(self):
        self.defer_import(concept_line(self.source, 'C1'), concept_line(self.source, 'C1', datatype='Text'))
        concept, (first, second) = versions(self.source, 'C1')
        self.assertEqual(
            deferred(), {(INDEX, 'concept'): {concept.id, second.id}, (LATEST_FLAGS, 'concept'): {first.id}})
        self.missing = {first.id}  # never indexed
        self.searchable.add(second.id)  # as a release would index it

        self.assertEqual(self.run_rows(window=False), 0)

        self.assertEqual(self.indexed, [(LATEST_FLAGS, 'concept', {first.id})])
        self.assertEqual(deferred()[(LATEST_FLAGS, 'concept')], {first.id})  # no full index outside the window
        self.indexed = []
        self.assertEqual(self.run_rows(), 2)
        self.assertEqual(self.indexed, [(LATEST_FLAGS, 'concept', {first.id}), (INDEX, 'concept', {first.id}),
                                        (INDEX, 'concept', {concept.id, second.id})])
        self.assertFalse(DeferredImportIndex.objects.exists())

    def test_run_in_the_window_updates_the_latest_flags_every_few_minutes_not_after_every_row(self):
        self.concept_updates()
        self.defer_import(concept_line(self.source, 'C2'))

        self.assertEqual(self.run_rows(flags_seconds=600), 4)

        self.assertEqual([stage for stage, _, _ in self.indexed], [INDEX, INDEX, INDEX, LATEST_FLAGS])

    @patch('core.importers.models.INDEX_BATCH_SIZE', 1)
    def test_run_stops_within_a_batch_of_the_window_closing_and_the_next_one_resumes(self):
        concept, (first, second) = self.concept_updates()

        self.assertEqual(self.run_rows(window=2), 0)  # the run's check, then one batch

        self.assertEqual(self.indexed, [(INDEX, 'concept', {concept.id})])
        self.assertEqual(
            list(DeferredImportIndex.objects.filter(stage=INDEX).order_by('created_at').values_list('ids', flat=True)),
            [[first.id], sorted([concept.id, second.id])]  # what's left, saved after each batch
        )
        self.indexed = []
        self.assertEqual(self.run_rows(), 3)
        self.assertEqual([ids for _, _, ids in self.indexed], [{concept.id}, {second.id}, {first.id}])

    def test_failed_row_waits_while_the_rows_after_it_go_on(self):
        concept, (first, second) = self.concept_updates()
        self.defer_import(concept_line(self.source, 'C2'))
        c2, (c2_first, ) = versions(self.source, 'C2')

        def fail_second_part(_stage, _resource, ids):
            if second.id in ids:
                raise RuntimeError('mapping conflict')

        self.failure = fail_second_part
        self.assertEqual(self.run_rows(), 1)

        self.assertEqual(self.indexed, [(INDEX, 'concept', {concept.id})])  # the failure stopped the run
        failed = DeferredImportIndex.objects.get(stage=INDEX, ids__contains=[second.id])
        self.assertEqual((failed.attempts, failed.last_error), (1, 'RuntimeError: mapping conflict'))
        self.assertAlmostEqual(
            (failed.retry_at - failed.updated_at).total_seconds(), DEFERRED_INDEX_RETRY_SECONDS, delta=60)
        self.indexed = []
        self.assertEqual(self.run_rows(), 1)  # the next run goes on without it
        self.assertEqual(self.indexed, [(INDEX, 'concept', {c2.id, c2_first.id})])

        self.failure = None
        self.indexed = []
        DeferredImportIndex.objects.filter(id=failed.id).update(retry_at=timezone.now())
        self.assertEqual(self.run_rows(), 2)
        self.assertEqual(self.indexed, [(INDEX, 'concept', {concept.id, second.id}),
                                        (LATEST_FLAGS, 'concept', {first.id})])
        self.assertFalse(DeferredImportIndex.objects.exists())

    def test_elasticsearch_refusing_writes_is_retried_by_the_next_run_and_not_counted(self):
        self.defer_import(concept_line(self.source, 'C1'))

        def refuse(*_):
            raise BatchIndexingError('ConceptDocument indexing failed', {'failed_docs': 2}, rejected=True)

        self.failure = refuse
        self.assertEqual(self.run_rows(), 0)

        row = DeferredImportIndex.objects.get()
        self.assertEqual((row.attempts, row.retry_at, row.last_error),
                         (0, None, 'BatchIndexingError: ConceptDocument indexing failed'))

    def test_an_outage_costs_a_row_one_attempt_until_its_retry(self):
        self.defer_import(concept_line(self.source, 'C1'))

        def unreachable(*_):
            raise ConnectionError('connection refused')

        self.failure = unreachable
        for _ in range(DEFERRED_INDEX_MAX_ATTEMPTS + 1):
            self.run_rows()

        row = DeferredImportIndex.objects.get()
        self.assertEqual((row.attempts, row.last_error), (1, 'ConnectionError: connection refused'))

    def test_row_that_keeps_failing_is_skipped_and_kept(self):
        _, (_, second) = self.concept_updates()
        first_index = DeferredImportIndex.objects.order_by('created_at').first()
        DeferredImportIndex.objects.filter(id=first_index.id).update(
            attempts=DEFERRED_INDEX_MAX_ATTEMPTS, last_error='BulkIndexError: ...')

        self.assertEqual(self.run_rows(), 2)

        self.assertEqual([stage for stage, _, _ in self.indexed], [INDEX, LATEST_FLAGS])  # other parts go on
        self.assertIn(second.id, self.indexed[0][2])
        self.assertEqual(list(DeferredImportIndex.objects.values_list('id', flat=True)), [first_index.id])

    @patch('core.importers.models.INDEX_BATCH_SIZE', 1)
    def test_rows_survive_a_runner_that_dies(self):
        _, (first, second) = self.concept_updates()

        def die_on_the_second_part(_stage, _resource, ids):
            if second.id in ids:
                raise SystemExit()  # the worker stops mid-row, as on a deploy

        self.failure = die_on_the_second_part
        with self.assertRaises(SystemExit):
            self.run_rows()

        self.assertEqual(deferred(), {  # what's done is gone, and the rest is still there
            (INDEX, 'concept'): {second.id}, (LATEST_FLAGS, 'concept'): {first.id}})
        self.assertEqual(set(DeferredImportIndex.objects.values_list('attempts', flat=True)), {0})
        # a runner killed outright leaves its lock until it expires; until then, other runs leave the rows alone
        cache.set(DEFERRED_INDEX_LOCK_KEY, 'killed-runner')
        self.failure = None
        self.indexed = []
        self.assertEqual(self.run_rows(), 0)
        self.assertEqual(cache.get(DEFERRED_INDEX_LOCK_KEY), 'killed-runner')
        cache.delete(DEFERRED_INDEX_LOCK_KEY)  # expired
        self.assertEqual(self.run_rows(), 2)
        self.assertEqual(self.indexed, [(INDEX, 'concept', {second.id}), (LATEST_FLAGS, 'concept', {first.id})])

    def test_run_holds_the_lock_while_it_indexes(self):
        self.defer_import(concept_line(self.source, 'C1'))
        locks = []
        self.failure = lambda *_: locks.append(cache.get(DEFERRED_INDEX_LOCK_KEY))

        self.run_rows()

        self.assertEqual(len(locks), 1)
        self.assertIsNotNone(locks[0])
        self.assertIsNone(cache.get(DEFERRED_INDEX_LOCK_KEY))

    def test_rows_make_the_calls_the_import_would_have_queued(self):
        self.concept_updates()
        ConceptFactory(parent=self.source, mnemonic='To')
        self.defer_import(mapping_line(self.source, 'C1', 'To'))
        rows = {(row.stage, row.resource): row for row in DeferredImportIndex.objects.all()}
        row_ids = {key: set(row.ids) for key, row in rows.items()}

        def ids(queryset):
            return set(queryset.values_list('id', flat=True))

        with patch.object(DeferredImportIndex, 'is_window_open', return_value=True):
            with patch.object(Mapping, 'batch_index') as batch_index_mock:
                self.assertEqual(rows[(INDEX, 'mapping')].process(), DeferredImportIndex.DONE)
            queryset, document = batch_index_mock.call_args[0]
            self.assertEqual((ids(queryset), document), (row_ids[(INDEX, 'mapping')], MappingDocument))
            self.assertEqual(batch_index_mock.call_args[1], {'refresh': False, **get_batch_index_relations(Mapping)})

            with patch.object(Concept, 'index_mapped_codes') as mapped_codes_mock:
                rows[(MAPPED_CODES, 'concept')].process()
            self.assertEqual(ids(mapped_codes_mock.call_args[0][0]), row_ids[(MAPPED_CODES, 'concept')])

        # in TEST_MODE, every replacement counts as searchable
        with patch.object(Concept, 'index_latest_flags', return_value=set()) as latest_flags_mock:
            self.assertEqual(rows[(LATEST_FLAGS, 'concept')].process(), DeferredImportIndex.DONE)
        self.assertEqual(ids(latest_flags_mock.call_args[0][0]), row_ids[(LATEST_FLAGS, 'concept')])

    def test_beat_runs_it_on_the_indexing_queue(self):
        entry = app.conf.CELERYBEAT_SCHEDULE['deferred-import-indexing']
        self.assertEqual(entry['task'], run_deferred_import_indexing.name)
        queue = app.amqp.router.route({}, run_deferred_import_indexing.name)['queue']
        self.assertEqual(getattr(queue, 'name', queue), 'indexing')
        with patch.object(DeferredImportIndex, 'run', return_value=3) as run_mock:
            self.assertEqual(run_deferred_import_indexing(), 3)
        run_mock.assert_called_once_with()


@patch('core.importers.models.index_concepts_mapped_codes')
@patch('core.importers.models.batch_index_resources')
class LatestFlagsTest(OCLTestCase):
    def test_concept_actions_set_what_a_new_version_changes(self, *_):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='LatestFlagsOrg'))
        ConceptFactory(parent=source, mnemonic='To')
        run_import([concept_line(source, 'C1')])
        run_import([mapping_line(source, 'C1', 'To')])
        run_import([concept_line(source, 'C1', datatype='Text')])
        _, (first, second) = versions(source, 'C1')

        actions = {action['_id']: action for action in Concept.get_latest_flags_actions([first.id, second.id])}

        self.assertEqual(set(actions), {first.id, second.id})
        self.assertEqual(
            {key: value for key, value in actions[first.id].items() if key != 'script'},
            {'_op_type': 'update', '_index': ConceptDocument._index._name,  # pylint: disable=protected-access
             '_id': first.id, 'retry_on_conflict': 3}
        )
        self.assertEqual(actions[first.id]['script']['source'], ESScript.LATEST_FLAGS_SCRIPT)
        empty_codes = {'same_as_map_codes': [], 'other_map_codes': [], 'mapped_codes': []}
        # in no release, the superseded version has no mappings to take codes from
        self.assertEqual(actions[first.id]['script']['params'],
                         {'is_latest_version': False, 'in_head': False, 'fields': empty_codes})
        params = actions[second.id]['script']['params']
        self.assertEqual((params['is_latest_version'], params['in_head']), (True, True))
        self.assertEqual(params['fields']['same_as_map_codes'], ['To'])  # HEAD's mappings

    def test_mapping_actions_set_what_a_new_version_changes(self, *_):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='LatestFlagsMapOrg'))
        ConceptFactory(parent=source, mnemonic='From')
        ConceptFactory(parent=source, mnemonic='To')
        run_import([mapping_line(source, 'From', 'To', id='M1')])
        run_import([mapping_line(source, 'From', 'To', id='M1', map_type='Narrower Than')])
        _, (first, second) = versions(source, 'M1', Mapping)

        actions = {action['_id']: action for action in Mapping.get_latest_flags_actions([first.id, second.id])}

        self.assertEqual(actions[first.id]['_index'], MappingDocument._index._name)  # pylint: disable=protected-access
        self.assertEqual(actions[first.id]['script']['params'],
                         {'is_latest_version': False, 'in_head': False, 'fields': {}})
        self.assertEqual(actions[second.id]['script']['params'],
                         {'is_latest_version': True, 'in_head': True, 'fields': {}})

    def test_index_latest_flags_updates_documents_without_a_refresh_and_returns_the_missing_ones(self, *_):
        queryset = Concept.objects.none()
        missing = {'update': {'_id': '7', 'status': 404, 'error': {'type': 'document_missing_exception'}}}

        def partial(*_, on_bulk_error, **__):
            on_bulk_error(BulkIndexError('1 document(s) failed', [missing]))

        with patch.object(Concept, 'batch_index_partial_by_ids', side_effect=partial) as partial_mock:
            self.assertEqual(Concept.index_latest_flags(queryset), {7})
        partial_mock.assert_called_once_with(
            queryset, ConceptDocument, Concept.get_latest_flags_actions, refresh=False, on_bulk_error=ANY)
        self.assertEqual(Concept.index_latest_flags(queryset), set())  # TEST_MODE: nothing sent

    def test_missing_documents_are_not_failures_of_the_flags_update(self, *_):
        missing = {'update': {'_id': '1', 'status': 404, 'error': {'type': 'document_missing_exception'}}}
        failed = {'update': {'_id': '2', 'status': 400, 'error': {'type': 'mapper_parsing_exception'}}}

        self.assertEqual(
            Concept.get_missing_ids_or_raise(BulkIndexError('2 document(s) failed', [missing, missing])), {1})
        with self.assertRaises(BulkIndexError) as context:
            Concept.get_missing_ids_or_raise(BulkIndexError('2 document(s) failed', [missing, failed]))
        self.assertEqual(context.exception.errors, [failed])

    def test_superseded_versions_wait_while_their_replacement_is_not_searchable(self, *_):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='LatestFlagsWaitOrg'))
        run_import([concept_line(source, 'C1'), concept_line(source, 'C2')])
        run_import([concept_line(source, 'C1', datatype='Text'), concept_line(source, 'C2', datatype='Text')])
        c1, (c1_first, c1_second) = versions(source, 'C1')
        c2, (c2_first, c2_second) = versions(source, 'C2')
        Concept.objects.filter(id=c1.id).update(is_latest_version=True)  # a stray flag on the versioned object

        with patch.object(Concept, 'get_ids_flagged_latest_in_search', return_value={c1_second.id}) as search_mock:
            waiting = Concept.get_ids_with_unsearchable_replacements([c1_first.id, c2.id, c2_first.id, c2_second.id])

        self.assertEqual(waiting, {c2_first.id})  # only a superseded version whose replacement isn't searchable
        search_mock.assert_called_once_with({c1.id, c1_second.id, c2_second.id})  # the latest of C1 and C2
        self.assertEqual(Concept.get_ids_flagged_latest_in_search({c1_first.id}), {c1_first.id})  # TEST_MODE

    def test_no_latest_version_left_lets_the_flags_update(self, *_):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='LatestFlagsGoneOrg'))
        run_import([concept_line(source, 'C1')])
        run_import([concept_line(source, 'C1', datatype='Text')])
        _, (first, _) = versions(source, 'C1')
        Concept.objects.filter(parent=source).update(is_latest_version=False)

        with patch.object(Concept, 'get_ids_flagged_latest_in_search', return_value=set()):
            self.assertEqual(Concept.get_ids_with_unsearchable_replacements([first.id]), set())


@tag('es')
class DeferredImportSearchTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.token = UserProfile.objects.get(username='ocladmin').get_token()

    def search(self, url):
        """
        (code, version) of each search result. ES_SYNC is off while the results are serialized: a save that reading
        them makes (checksums) would otherwise reindex those rows here and hide what the documents held.
        """
        with override_settings(ES_SYNC=False):
            response = self.client.get(url, HTTP_AUTHORIZATION='Token ' + self.token)
        self.assertEqual(response.status_code, 200)
        return [(concept['id'], concept['version']) for concept in response.data]

    @staticmethod
    def get_document(concept_id):
        """(is_latest_version, source_version, same_as_map_codes) of a concept's document, or None without one."""
        ConceptDocument._index.refresh()  # pylint: disable=protected-access
        document = ConceptDocument.search().filter('ids', values=[concept_id]).execute()
        if not document:
            return None
        return (document[0].is_latest_version, sorted(document[0].source_version),
                list(document[0].same_as_map_codes or []))

    @staticmethod
    def run_job(window):
        with override_settings(TEST_MODE=False), \
                patch.object(DeferredImportIndex, 'is_window_open', return_value=window):
            return DeferredImportIndex.run()

    # parallel_bulk builds the documents on pool threads, whose DB connections can't see this test's rows
    @patch('core.common.models.parallel_bulk', streaming_bulk)
    @patch('core.importers.models.index_concepts_mapped_codes')
    @patch('core.importers.models.batch_index_resources')
    def test_large_update_import_leaves_one_latest_version_once_the_job_runs(
            self, batch_index_resources_mock, index_concepts_mapped_codes_mock):
        org = OrganizationFactory(mnemonic=f'DeferredSearch{uuid.uuid4().hex[:8]}')  # no documents from earlier runs
        source = OrganizationSourceFactory(organization=org)
        ConceptFactory(parent=source, mnemonic='To')
        run_import([concept_line(source, 'Zephyranthes')])  # within the limit, so indexed as it finishes
        run_import([mapping_line(source, 'Zephyranthes', 'To')])
        with override_settings(TEST_MODE=False):
            for call_args in batch_index_resources_mock.apply_async.call_args_list:
                batch_index_resources(*call_args[0][0])
            for call_args in index_concepts_mapped_codes_mock.apply_async.call_args_list:
                index_concepts_mapped_codes(*call_args[0][0])
            _, (first, ) = versions(source, 'Zephyranthes')
            # a release appending itself to the document, as it doesn't write HEAD's
            Concept.batch_index_source_version_append(Concept.objects.filter(id=first.id), ConceptDocument, 'v1')
        batch_index_resources_mock.reset_mock()
        self.assertEqual(self.get_document(first.id), (True, ['HEAD', 'v1'], ['To']))

        with override_settings(IMPORT_INDEX_MAX_LINES=1):
            run_import([
                concept_line(source, 'Zephyranthes', name='Zephyranthes candida'), concept_line(source, 'Quillwort')])

        batch_index_resources_mock.apply_async.assert_not_called()
        _, (first, second) = versions(source, 'Zephyranthes')
        zephyranthes_url = source.concepts_url + '?q=Zephyranthes'
        # until its replacement is searchable, the updated concept is found by its previous version, not dropped
        self.assertEqual(self.run_job(window=False), 0)
        self.assertEqual((self.get_document(first.id), self.get_document(second.id)), (
            (True, ['HEAD', 'v1'], ['To']), None))
        self.assertEqual(self.search(zephyranthes_url), [('Zephyranthes', first.version)])
        with override_settings(TEST_MODE=False):
            self.assertEqual(Concept.get_ids_flagged_latest_in_search({first.id, second.id}), {first.id})
            batch_index_resources('concept', {'id__in': [second.id]})  # as a release indexes its new members

        # the flags then update outside the window, keeping the release in source_version
        self.assertEqual(self.run_job(window=False), 1)
        self.assertEqual(self.get_document(first.id), (False, ['v1'], []))
        self.assertEqual(self.search(zephyranthes_url), [('Zephyranthes', second.version)])
        self.assertEqual(self.search(source.concepts_url + '?q=Quillwort'), [])

        self.assertEqual(self.run_job(window=True), 1)

        self.assertEqual(self.get_document(second.id), (True, ['HEAD'], ['To']))
        self.assertEqual([code for code, _ in self.search(source.concepts_url + '?q=Quillwort')], ['Quillwort'])
        self.assertEqual(self.search(source.concepts_url + '?q=candida'), [('Zephyranthes', second.version)])
        self.assertFalse(DeferredImportIndex.objects.exists())

    @patch('core.common.models.parallel_bulk', streaming_bulk)
    @patch('core.importers.models.index_concepts_mapped_codes')
    @patch('core.importers.models.batch_index_resources')
    def test_superseded_version_without_a_document_waits_for_the_window(self, *_):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic=f'NoDoc{uuid.uuid4().hex[:8]}'))
        run_import([concept_line(source, 'Zephyranthes')], index=False)
        run_import([concept_line(source, 'Zephyranthes', datatype='Text')], index=False)
        concept, (first, second) = versions(source, 'Zephyranthes')
        with override_settings(TEST_MODE=False):
            batch_index_resources('concept', {'id__in': [concept.id, second.id]})  # as a release would

        self.assertEqual(self.run_job(window=False), 0)

        # outside the window, the flags update doesn't fully index it
        self.assertEqual((self.get_document(first.id), self.get_document(second.id)), (
            None, (True, ['HEAD'], [])))
        self.assertEqual(deferred()[(LATEST_FLAGS, 'concept')], {first.id})
        self.assertEqual(self.run_job(window=True), 3)
        self.assertEqual(self.get_document(first.id), (False, [], []))
        self.assertFalse(DeferredImportIndex.objects.exists())
