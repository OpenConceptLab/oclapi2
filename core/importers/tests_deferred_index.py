import json
import tempfile
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.cache import cache
from django.db.models import F
from django.test import override_settings, tag
from elasticsearch.helpers import streaming_bulk
from mock import patch, ANY

from core.celery import app
from core.common.exceptions import BatchIndexingError
from core.common.tasks import run_deferred_import_indexing, batch_index_resources, get_batch_index_relations
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.importers.constants import DEFERRED_INDEX_LOCK_KEY, DEFERRED_INDEX_MAX_ATTEMPTS
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
    def setUp(self):
        super().setUp()
        cache.clear()
        self.source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic=f'Run{uuid.uuid4().hex[:8]}'))
        self.indexed = []  # (stage, resource, ids) of each row index() ran, in order

    def tearDown(self):
        cache.clear()
        super().tearDown()

    @staticmethod
    def defer_import(*lines):
        with patch('core.importers.models.batch_index_resources'), \
                patch('core.importers.models.index_concepts_mapped_codes'):
            run_import(list(lines), index=False)

    def record_index(self, row):
        self.indexed.append((row.stage, row.resource, set(row.ids)))

    def run_rows(self, window=True, index=None):
        """DeferredImportIndex.run() with the window open (or as `window` says) and index() recording each row."""
        window = {'side_effect': window} if isinstance(window, list) else {'return_value': window}
        with patch.object(DeferredImportIndex, 'is_window_open', **window), \
                patch.object(DeferredImportIndex, 'index', autospec=True, side_effect=index or self.record_index):
            return DeferredImportIndex.run()

    def two_concept_parts(self):
        """Two deferred import parts: C1, then an update of C1. The ids of each row, in the order they should run."""
        self.defer_import(concept_line(self.source, 'C1'))
        self.defer_import(concept_line(self.source, 'C1', datatype='Text'))
        concept, (first, second) = versions(self.source, 'C1')
        return [
            (INDEX, 'concept', {concept.id, first.id}),
            (INDEX, 'concept', {concept.id, second.id}),
            (LATEST_FLAGS, 'concept', {first.id}),  # once the version that superseded it is indexed
        ]

    def test_run_outside_the_window_leaves_the_rows(self):
        self.defer_import(concept_line(self.source, 'C1'))

        self.assertEqual(self.run_rows(window=False), 0)

        self.assertEqual(self.indexed, [])
        with override_settings(IMPORT_INDEX_WINDOW=''):  # an empty window pauses the job
            self.assertEqual(DeferredImportIndex.run(), 0)
        self.assertEqual(DeferredImportIndex.objects.count(), 1)

    def test_run_without_rows_does_nothing(self):
        self.assertEqual(self.run_rows(), 0)
        self.assertIsNone(cache.get(DEFERRED_INDEX_LOCK_KEY))

    def test_run_indexes_each_part_stage_by_stage_and_deletes_its_rows(self):
        expected = self.two_concept_parts()
        ConceptFactory(parent=self.source, mnemonic='To')
        self.defer_import(mapping_line(self.source, 'C1', 'To'))
        concept, (_, second) = versions(self.source, 'C1')
        mapping = Mapping.objects.get(parent=self.source, id=F('versioned_object_id'))

        self.assertEqual(self.run_rows(), 5)

        self.assertEqual(self.indexed, expected + [
            (INDEX, 'mapping', {mapping.id, mapping.get_latest_version().id}),
            (MAPPED_CODES, 'concept', {concept.id, second.id}),
        ])
        self.assertFalse(DeferredImportIndex.objects.exists())
        self.assertIsNone(cache.get(DEFERRED_INDEX_LOCK_KEY))  # released

    def test_run_stops_when_the_window_closes_and_the_next_one_resumes(self):
        expected = self.two_concept_parts()

        self.assertEqual(self.run_rows(window=[True, True, True, False]), 2)  # run's check, then one before each row

        self.assertEqual(self.indexed, expected[:2])
        self.assertEqual(DeferredImportIndex.objects.count(), 1)
        self.assertEqual(self.run_rows(), 1)
        self.assertEqual(self.indexed, expected)
        self.assertFalse(DeferredImportIndex.objects.exists())

    def test_failed_row_stays_recorded_and_the_next_run_retries_it(self):
        expected = self.two_concept_parts()

        def fail_second_row(row):
            if len(self.indexed) == 1:
                self.indexed.append('failed')
                raise RuntimeError('ES unreachable')
            self.record_index(row)

        self.assertEqual(self.run_rows(index=fail_second_row), 1)

        self.assertEqual(self.indexed, [expected[0], 'failed'])  # the failure stopped the run
        rows = list(DeferredImportIndex.objects.order_by('stage'))
        self.assertEqual([(row.attempts, row.last_error) for row in rows], [
            (1, 'RuntimeError: ES unreachable'), (0, None)])
        self.indexed = []
        self.assertEqual(self.run_rows(), 2)
        self.assertEqual(self.indexed, expected[1:])
        self.assertFalse(DeferredImportIndex.objects.exists())

    def test_elasticsearch_refusing_writes_is_not_counted_as_an_attempt(self):
        self.defer_import(concept_line(self.source, 'C1'))

        def refuse(_):
            raise BatchIndexingError('ConceptDocument indexing failed', {'failed_docs': 2}, rejected=True)

        self.assertEqual(self.run_rows(index=refuse), 0)

        row = DeferredImportIndex.objects.get()
        self.assertEqual(row.attempts, 0)
        self.assertEqual(row.last_error, 'BatchIndexingError: ConceptDocument indexing failed')

    def test_row_that_keeps_failing_is_skipped_and_kept(self):
        expected = self.two_concept_parts()
        first_index, _, _ = DeferredImportIndex.objects.order_by('created_at', 'stage')
        DeferredImportIndex.objects.filter(id=first_index.id).update(
            attempts=DEFERRED_INDEX_MAX_ATTEMPTS, last_error='BulkIndexError: ...')

        self.assertEqual(self.run_rows(), 2)

        self.assertEqual(self.indexed, expected[1:])  # other parts go on
        self.assertEqual(list(DeferredImportIndex.objects.values_list('id', flat=True)), [first_index.id])

    def test_skipped_row_holds_its_parts_later_stages(self):
        expected = self.two_concept_parts()
        _, second_index, second_flags = DeferredImportIndex.objects.order_by('created_at', 'stage')
        DeferredImportIndex.objects.filter(id=second_index.id).update(attempts=DEFERRED_INDEX_MAX_ATTEMPTS)

        self.assertEqual(self.run_rows(), 1)

        # the superseded version keeps its flag while its replacement has no document
        self.assertEqual(self.indexed, expected[:1])
        self.assertEqual(
            sorted(DeferredImportIndex.objects.values_list('id', flat=True)), [second_index.id, second_flags.id])
        DeferredImportIndex.objects.filter(id=second_index.id).update(attempts=0)  # as an admin would, once it's fixed
        self.indexed = []
        self.assertEqual(self.run_rows(), 2)
        self.assertEqual(self.indexed, expected[1:])

    def test_rows_survive_a_runner_that_dies(self):
        expected = self.two_concept_parts()

        def die_on_second_row(row):
            if self.indexed:
                raise SystemExit()  # the worker stops mid-row, as on a deploy
            self.record_index(row)

        with self.assertRaises(SystemExit):
            self.run_rows(index=die_on_second_row)

        self.assertEqual(DeferredImportIndex.objects.count(), 2)  # only the finished row is gone
        self.assertEqual(set(DeferredImportIndex.objects.values_list('attempts', flat=True)), {0})
        # a runner killed outright leaves its lock until it expires; until then, other runs leave the rows alone
        cache.set(DEFERRED_INDEX_LOCK_KEY, 'killed-runner')
        self.indexed = []
        self.assertEqual(self.run_rows(), 0)
        self.assertEqual(cache.get(DEFERRED_INDEX_LOCK_KEY), 'killed-runner')
        cache.delete(DEFERRED_INDEX_LOCK_KEY)  # expired
        self.assertEqual(self.run_rows(), 2)
        self.assertEqual(self.indexed, expected[1:])

    def test_run_holds_the_lock_while_it_indexes(self):
        self.defer_import(concept_line(self.source, 'C1'))
        locks = []

        self.run_rows(index=lambda row: locks.append(cache.get(DEFERRED_INDEX_LOCK_KEY)))

        self.assertEqual(len(locks), 1)
        self.assertIsNotNone(locks[0])
        self.assertIsNone(cache.get(DEFERRED_INDEX_LOCK_KEY))

    def test_index_makes_the_calls_the_import_would_have_queued(self):
        self.two_concept_parts()
        ConceptFactory(parent=self.source, mnemonic='To')
        self.defer_import(mapping_line(self.source, 'C1', 'To'))
        rows = {(row.stage, row.resource): row for row in DeferredImportIndex.objects.all()}

        def ids(queryset):
            return set(queryset.values_list('id', flat=True))

        with patch.object(Mapping, 'batch_index') as batch_index_mock:
            rows[(INDEX, 'mapping')].index()
        queryset, document = batch_index_mock.call_args[0]
        self.assertEqual((ids(queryset), document), (set(rows[(INDEX, 'mapping')].ids), MappingDocument))
        self.assertEqual(batch_index_mock.call_args[1], {'refresh': False, **get_batch_index_relations(Mapping)})

        with patch.object(Concept, 'index_mapped_codes') as mapped_codes_mock:
            rows[(MAPPED_CODES, 'concept')].index()
        self.assertEqual(ids(mapped_codes_mock.call_args[0][0]), set(rows[(MAPPED_CODES, 'concept')].ids))

        with patch.object(Concept, 'index_latest_flags') as latest_flags_mock:
            rows[(LATEST_FLAGS, 'concept')].index()
        self.assertEqual(ids(latest_flags_mock.call_args[0][0]), set(rows[(LATEST_FLAGS, 'concept')].ids))

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
        run_import([concept_line(source, 'C1')])
        _, (first, ) = versions(source, 'C1')
        release = OrganizationSourceFactory(
            organization=source.organization, mnemonic=source.mnemonic, version='v1', released=True)
        first.sources.add(release)
        run_import([concept_line(source, 'C1', datatype='Text')])
        _, (first, second) = versions(source, 'C1')

        actions = {action['_id']: action for action in Concept.get_latest_flags_actions([first.id, second.id])}

        self.assertEqual(set(actions), {first.id, second.id})
        self.assertEqual(
            {key: value for key, value in actions[first.id].items() if key != 'doc'},
            {'_op_type': 'update', '_index': ConceptDocument._index._name,  # pylint: disable=protected-access
             '_id': first.id, 'retry_on_conflict': 3}
        )
        self.assertEqual(actions[first.id]['doc'], {'is_latest_version': False, 'source_version': ['v1']})
        self.assertEqual(actions[second.id]['doc'], {'is_latest_version': True, 'source_version': ['HEAD']})

    def test_mapping_actions_set_what_a_new_version_changes(self, *_):
        source = OrganizationSourceFactory(organization=OrganizationFactory(mnemonic='LatestFlagsMapOrg'))
        ConceptFactory(parent=source, mnemonic='From')
        ConceptFactory(parent=source, mnemonic='To')
        run_import([mapping_line(source, 'From', 'To', id='M1')])
        run_import([mapping_line(source, 'From', 'To', id='M1', map_type='Narrower Than')])
        _, (first, second) = versions(source, 'M1', Mapping)

        actions = {action['_id']: action for action in Mapping.get_latest_flags_actions([first.id, second.id])}

        self.assertEqual(actions[first.id]['_index'], MappingDocument._index._name)  # pylint: disable=protected-access
        self.assertEqual(actions[first.id]['doc'], {'is_latest_version': False, 'source_version': []})
        self.assertEqual(actions[second.id]['doc'], {'is_latest_version': True, 'source_version': ['HEAD']})

    def test_index_latest_flags_updates_documents_without_a_refresh(self, *_):
        queryset = Concept.objects.none()
        with patch.object(Concept, 'batch_index_partial_by_ids', return_value={'docs': 0}) as partial_mock:
            self.assertEqual(Concept.index_latest_flags(queryset), {'docs': 0})
        partial_mock.assert_called_once_with(
            queryset, ConceptDocument, Concept.get_latest_flags_actions, refresh=False)


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
    def get_flags(concept_id):
        """(is_latest_version, source_version) of a concept's document, or None when it has none."""
        ConceptDocument._index.refresh()  # pylint: disable=protected-access
        document = ConceptDocument.search().filter('ids', values=[concept_id]).execute()
        return (document[0].is_latest_version, sorted(document[0].source_version)) if document else None

    # parallel_bulk builds the documents on pool threads, whose DB connections can't see this test's rows
    @patch('core.common.models.parallel_bulk', streaming_bulk)
    @patch('core.importers.models.index_concepts_mapped_codes')
    @patch('core.importers.models.batch_index_resources')
    def test_large_update_import_leaves_one_latest_version_once_the_window_runs(
            self, batch_index_resources_mock, index_concepts_mapped_codes_mock):
        org = OrganizationFactory(mnemonic=f'DeferredSearch{uuid.uuid4().hex[:8]}')  # no documents from earlier runs
        source = OrganizationSourceFactory(organization=org)
        run_import([concept_line(source, 'Zephyranthes')])  # within the limit, so indexed as it finishes
        with override_settings(TEST_MODE=False):
            for call_args in batch_index_resources_mock.apply_async.call_args_list:
                batch_index_resources(*call_args[0][0])
        batch_index_resources_mock.reset_mock()
        zephyranthes_url = source.concepts_url + '?q=Zephyranthes'
        quillwort_url = source.concepts_url + '?q=Quillwort'

        with override_settings(IMPORT_INDEX_MAX_LINES=1):
            run_import([
                concept_line(source, 'Zephyranthes', name='Zephyranthes candida'), concept_line(source, 'Quillwort')])

        batch_index_resources_mock.apply_async.assert_not_called()
        _, (first, second) = versions(source, 'Zephyranthes')
        # until the window, the updated concept is still found by its previous version, rather than not at all
        self.assertEqual((self.get_flags(first.id), self.get_flags(second.id)), ((True, ['HEAD']), None))
        self.assertEqual(self.search(zephyranthes_url), [('Zephyranthes', first.version)])
        self.assertEqual(self.search(quillwort_url), [])

        with override_settings(TEST_MODE=False), \
                patch.object(DeferredImportIndex, 'is_window_open', return_value=True):
            self.assertEqual(DeferredImportIndex.run(), 2)  # the part's index, then the latest flags

        self.assertEqual((self.get_flags(first.id), self.get_flags(second.id)), ((False, []), (True, ['HEAD'])))
        MappingDocument._index.refresh()  # pylint: disable=protected-access
        self.assertEqual(self.search(zephyranthes_url), [('Zephyranthes', second.version)])
        self.assertEqual(self.search(source.concepts_url + '?q=candida'), [('Zephyranthes', second.version)])
        self.assertEqual([code for code, _ in self.search(quillwort_url)], ['Quillwort'])
        self.assertFalse(DeferredImportIndex.objects.exists())
        index_concepts_mapped_codes_mock.apply_async.assert_not_called()
