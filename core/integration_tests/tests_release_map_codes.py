import io
import json
from contextlib import ExitStack

from django.core.management import call_command, CommandError
from django.test import override_settings, tag
from elasticsearch_dsl import Q
from mock import patch, Mock
import numpy
from rest_framework.test import APIRequestFactory, force_authenticate

from core.common.constants import ACCESS_TYPE_NONE
from core.common.search import CustomESSearch
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.concepts.documents import ConceptDocument, MappedCodes, RELEASE_MAP_CODES, MAPPING_VALUES, to_mapped_codes
from core.concepts.models import Concept
from core.concepts.search import ConceptFuzzySearch
from core.concepts.views import ConceptListView
from core.importers.models import BulkImportInline
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.users.models import UserProfile

LOINC = '/orgs/Regen/sources/LOINC/'
ICD = '/orgs/WHO/sources/ICD-10/'


class ReleasesFixture:
    """
    OpenConceptLab/ocl_online#703. LOINC v1 and v2, each seeded from an import. Concepts A and B don't change between
    them, so each keeps one row, which HEAD, v1 and v2 share. Their mappings to ICD-10 do change:
    - A: SAME-AS X01 and NARROWER-THAN X05 in v1, both dropped in v2;
    - B: SAME-AS X02 in both, and SAME-AS X03 added in v2.
    """
    @staticmethod
    def concept(mnemonic):
        return {
            'type': 'Concept', 'id': mnemonic, 'concept_class': 'LOINC', 'datatype': 'N/A', 'owner': 'Regen',
            'owner_type': 'Organization', 'source': 'LOINC',
            'names': [{'name': f'{mnemonic} name', 'locale': 'en', 'locale_preferred': True}],
        }

    @staticmethod
    def mapping(mnemonic, from_concept, code, map_type='SAME-AS'):
        return {
            'type': 'Mapping', 'id': mnemonic, 'map_type': map_type, 'owner': 'Regen', 'owner_type': 'Organization',
            'source': 'LOINC', 'from_concept_url': f'{LOINC}concepts/{from_concept}/', 'to_source_url': ICD,
            'to_concept_code': code
        }

    @staticmethod
    def version(version):
        return {
            'type': 'Source Version', 'id': version, 'source': 'LOINC', 'owner': 'Regen', 'owner_type': 'Organization',
            'description': f'LOINC {version}', 'released': True
        }

    def run_import(self, lines, isolated_version=True):
        source_line = {
            'type': 'Source', 'id': 'LOINC', 'name': 'LOINC', 'owner_type': 'Organization', 'owner': 'Regen',
            'default_locale': 'en', 'supported_locales': 'en'
        }
        importer = BulkImportInline(
            '\n'.join(json.dumps(line) for line in [source_line, *lines]), 'ocladmin', True,
            isolated_version=isolated_version)
        with ExitStack() as stack:
            for target in ('core.sources.models.index_source_mappings', 'core.sources.models.index_source_concepts',
                           'core.importers.models.batch_index_resources'):
                stack.enter_context(patch(target, Mock(__name__=target.rsplit('.', 1)[-1])))
            stack.enter_context(patch('core.importers.models.index_concepts_mapped_codes', Mock()))
            stack.enter_context(override_settings(SEED_FROM_IMPORT_LARGE_DROP_MIN=10))
            stack.enter_context(self.captureOnCommitCallbacks(execute=True))  # pylint: disable=no-member
            importer.run()
        self.assertEqual(importer.failed, [])  # pylint: disable=no-member

    def create_releases(self):
        OrganizationFactory(mnemonic='Regen')
        self.run_import([
            self.concept('A'), self.concept('B'), self.mapping('M1', 'A', 'X01'),
            self.mapping('M5', 'A', 'X05', 'NARROWER-THAN'), self.mapping('M2', 'B', 'X02'), self.version('v1')])
        self.run_import([
            self.concept('A'), self.concept('B'), self.mapping('M2', 'B', 'X02'), self.mapping('M3', 'B', 'X03'),
            self.version('v2')])

    def edit_a_in_v3(self):
        """v3 edits A, so A's v1/v2 row leaves HEAD: only v1 and v2 hold it."""
        edited_a = {**self.concept('A'), 'names': [{'name': 'A edited', 'locale': 'en', 'locale_preferred': True}]}
        self.run_import([
            edited_a, self.concept('B'), self.mapping('M2', 'B', 'X02'), self.mapping('M3', 'B', 'X03'),
            self.version('v3')])

    def drop_b_in_v3(self):
        """v3 drops B and its mappings: only v1 and v2 hold B's row."""
        self.run_import([self.concept('A'), self.version('v3')])

    @staticmethod
    def fields(row):
        return MappedCodes([row]).get_fields(row)

    @staticmethod
    def versions(entry):
        return sorted(Source.objects.filter(id__in=entry['source_version_id']).values_list('version', flat=True))

    def entries(self, fields):
        return [(self.versions(entry), sorted(entry['same_as_map_codes']), entry['other_map_codes'])
                for entry in fields[RELEASE_MAP_CODES]]

    @staticmethod
    def release(version):
        return Source.objects.get(mnemonic='LOINC', version=version)

    @staticmethod
    def row(version, mnemonic):
        """The concept row `version` holds (for HEAD, the latest version, which its searches read)."""
        rows = Source.objects.get(mnemonic='LOINC', version=version).concepts.filter(mnemonic=mnemonic)
        return (rows.filter(is_latest_version=True) if version == 'HEAD' else rows).get()


class ReleaseMapCodesTest(ReleasesFixture, OCLTestCase):
    def setUp(self):
        super().setUp()
        self.create_releases()

    def test_a_shared_row_gets_an_entry_for_each_release_whose_codes_differ(self):
        row_a, row_b = self.row('HEAD', 'A'), self.row('HEAD', 'B')
        self.assertTrue(row_a.is_latest_version)
        self.assertEqual(self.row('v1', 'A').id, row_a.id)  # one row, so one search document
        self.assertEqual(self.row('v1', 'B').id, row_b.id)

        fields = self.fields(row_a)
        self.assertEqual((fields['same_as_map_codes'], fields['other_map_codes']), ([], []))
        entry, = fields[RELEASE_MAP_CODES]
        self.assertEqual(
            (self.versions(entry), entry['same_as_map_codes'], entry['other_map_codes']), (['v1'], ['X01'], ['X05']))
        self.assertEqual(sorted(entry['mapped_codes'], key=lambda code: code['code']), [
            {'source': ICD, 'code': 'X01', 'map_type': 'SAME-AS'},
            {'source': ICD, 'code': 'X05', 'map_type': 'NARROWER-THAN'}])

        fields = self.fields(row_b)
        self.assertEqual(sorted(fields['same_as_map_codes']), ['X02', 'X03'])
        self.assertEqual(fields[RELEASE_MAP_CODES], [{
            'source_version_id': [self.release('v1').id], 'same_as_map_codes': ['X02'], 'other_map_codes': [],
            'mapped_codes': [{'source': ICD, 'code': 'X02', 'map_type': 'SAME-AS'}]}])

        # HEAD's own rows are in no release
        self.assertEqual(self.fields(row_a.versioned_object)[RELEASE_MAP_CODES], [])

    def test_entries_load_in_two_queries_for_many_rows(self):
        rows = list(Concept.objects.filter(parent__mnemonic='LOINC'))

        with self.assertNumQueries(2):
            mapped_codes = MappedCodes(rows)

        self.assertEqual({row.mnemonic for row in rows if mapped_codes.get_fields(row)[RELEASE_MAP_CODES]}, {'A', 'B'})

    def test_releases_with_the_same_codes_share_an_entry(self):
        self.run_import([
            self.concept('A'), self.concept('B'), self.mapping('M2', 'B', 'X02'), self.mapping('M3', 'B', 'X03'),
            self.version('v3')])
        self.run_import([self.mapping('M6', 'B', 'X06')], isolated_version=False)  # HEAD only

        fields = self.fields(self.row('HEAD', 'B'))

        self.assertEqual(sorted(fields['same_as_map_codes']), ['X02', 'X03', 'X06'])
        self.assertEqual(self.entries(fields), [(['v1'], ['X02'], []), (['v2', 'v3'], ['X02', 'X03'], [])])

    def test_the_backfill_refuses_another_index(self):
        with self.assertRaisesMessage(CommandError, '--source rewrites docs in the concepts index only'):
            call_command('concept_release_map_codes', index='concepts-next', source=[LOINC], stdout=io.StringIO())

    @patch('core.concepts.search.get_embeddings', Mock(return_value=numpy.zeros(4)))
    def test_match_scopes_its_vector_filters_to_the_release(self):
        target = Q('nested', path='mapped_codes', query=Q('term', **{'mapped_codes.code': 'X01'}))

        def get_knn_filters(repo_url):
            search = ConceptFuzzySearch.search(
                {'name': 'A name'}, repo_url, is_semantic=True, additional_filter_criterion=target).to_dict()
            return [json.dumps(knn['filter'].to_dict() if hasattr(knn['filter'], 'to_dict') else knn['filter'])
                    for knn in search['knn']]

        self.assertTrue(all(f'{{"release_map_codes.source_version_id": {self.release("v1").id}}}' in knn_filter
                            for knn_filter in get_knn_filters(f'{LOINC}v1/')))
        self.assertFalse(any(RELEASE_MAP_CODES in knn_filter for knn_filter in get_knn_filters(f'{LOINC}HEAD/')))

    def test_a_release_the_user_cant_view_isnt_read(self):
        def get_release_id(user, version='latest'):
            request = APIRequestFactory().get('/')
            force_authenticate(request, user)
            view = ConceptListView()
            view.setup(request, org='Regen', source='LOINC', version=version)
            view.request = view.initialize_request(request)
            return view.map_codes_release

        self.assertEqual(get_release_id(None), self.release('v2').id)
        Source.objects.filter(id=self.release('v2').id).update(public_access=ACCESS_TYPE_NONE)

        self.assertIsNone(get_release_id(None))
        self.assertIsNone(get_release_id(None, 'v2'))
        self.assertEqual(get_release_id(UserProfile.objects.get(username='ocladmin')), self.release('v2').id)

    def test_a_row_only_releases_hold_reads_every_releases_codes_and_each_its_own_entry(self):
        self.edit_a_in_v3()
        old_a = self.row('v1', 'A')
        self.assertEqual(old_a.id, self.row('v2', 'A').id)
        self.assertNotEqual(old_a.id, self.row('HEAD', 'A').id)

        fields = self.fields(old_a)

        # its own fields, which collection searches read: v1's and v2's; each release reads its own entry
        self.assertEqual((fields['same_as_map_codes'], fields['other_map_codes']), (['X01'], ['X05']))
        self.assertEqual(self.entries(fields), [(['v2'], [], [])])

    def test_a_dropped_rows_releases_read_their_own_codes(self):
        self.drop_b_in_v3()
        row_b = self.row('v2', 'B')
        self.assertTrue(row_b.is_latest_version)
        self.assertFalse(Source.objects.get(mnemonic='LOINC', version='HEAD').concepts.filter(id=row_b.id).exists())

        fields = self.fields(row_b)

        self.assertEqual(fields['same_as_map_codes'], [])  # HEAD's, as global search reads the latest version
        self.assertEqual(self.entries(fields), [(['v1'], ['X02'], []), (['v2'], ['X02', 'X03'], [])])

    def test_a_private_releases_codes_stay_out_of_a_rows_own_fields(self):
        self.edit_a_in_v3()
        Source.objects.filter(id=self.release('v1').id).update(public_access=ACCESS_TYPE_NONE)

        fields = self.fields(self.row('v1', 'A'))

        self.assertEqual((fields['same_as_map_codes'], fields['other_map_codes']), ([], []))  # v2's only
        self.assertEqual(self.entries(fields), [(['v1'], ['X01'], ['X05'])])

    @patch('core.common.tasks.index_concepts_mapped_codes.apply_async')
    def test_a_releases_access_change_rebuilds_its_release_only_rows(self, apply_async):
        self.edit_a_in_v3()
        release = self.release('v1')
        release.public_access = ACCESS_TYPE_NONE
        release._should_update_public_access = True  # pylint: disable=protected-access

        release.save()

        apply_async.assert_called_once_with(([self.row('v1', 'A').id],), queue='indexing', permanent=False)

    def test_a_row_no_source_version_holds_reads_heads_mappings(self):
        self.edit_a_in_v3()
        old_a = self.row('v1', 'A')
        old_a.sources.clear()  # as for a version only a collection holds

        fields = self.fields(old_a)

        self.assertEqual((fields['same_as_map_codes'], fields['other_map_codes']), (['X01'], ['X05']))
        self.assertEqual(fields[RELEASE_MAP_CODES], [])

    def test_release_clauses_read_the_entry_for_the_release(self):
        code = {'match': {'same_as_map_codes': {'query': 'X01', 'boost': 5.5}}}
        target = {'nested': {'path': 'mapped_codes', 'query': {'term': {'mapped_codes.source': ICD}}}}
        name = {'match': {'name': {'query': 'X01'}}}
        query = {'bool': {'should': [code, name], 'filter': [target], 'must_not': [{'wildcard': {
            'other_map_codes': {'value': 'x*'}}}]}}

        scoped = ConceptDocument.scope_map_codes_to_release(query, 7)

        in_v1 = {'term': {'release_map_codes.source_version_id': 7}}
        has_v1_entry = {'nested': {'path': 'release_map_codes', 'query': in_v1}}

        def in_release(clause, release_clause):
            return {'bool': {'minimum_should_match': 1, 'should': [
                {'bool': {'must': [clause], 'must_not': [has_v1_entry]}},
                {'nested': {'path': 'release_map_codes', 'query': {
                    'bool': {'filter': [in_v1], 'must': [release_clause]}}}}]}}

        self.assertEqual(scoped, {'bool': {
            'should': [
                in_release(code, {'match': {'release_map_codes.same_as_map_codes': {'query': 'X01', 'boost': 5.5}}}),
                name],
            'filter': [in_release(target, {'nested': {'path': 'release_map_codes.mapped_codes', 'query': {
                'term': {'release_map_codes.mapped_codes.source': ICD}}}})],
            'must_not': [in_release(
                {'wildcard': {'other_map_codes': {'value': 'x*'}}},
                {'wildcard': {'release_map_codes.other_map_codes': {'value': 'x*'}}})],
        }})


@tag('es')
class ReleaseMapCodesSearchTest(ReleasesFixture, OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.patch_concept_es_mapping_for_ci()
        call_command('concept_release_map_codes', stdout=io.StringIO())
        # the docs earlier tests left: the rows they were made from are rolled back, their ids reused
        ConceptDocument.search().filter('term', owner='regen').params(refresh=True).delete()
        self.create_releases()
        ConceptDocument().update(Concept.objects.filter(parent__mnemonic='LOINC'))

    def get_ids(self, version, query):
        response = self.client.get(f"{LOINC}{f'{version}/' if version else ''}concepts/{query}")
        self.assertEqual(response.status_code, 200)
        return sorted(concept['id'] for concept in response.data)

    def search(self, query):
        return {version: self.get_ids(version, query) for version in (None, 'v1', 'v2', 'latest')}

    def test_a_release_finds_the_codes_head_dropped(self):
        self.assertEqual(self.search('?q=X01'), {None: [], 'v1': ['A'], 'v2': [], 'latest': []})
        self.assertEqual(self.search('?q=X05'), {None: [], 'v1': ['A'], 'v2': [], 'latest': []})

    def test_a_release_skips_the_codes_head_added(self):
        self.assertEqual(self.search('?q=X03'), {None: ['B'], 'v1': [], 'v2': ['B'], 'latest': ['B']})
        self.assertEqual(self.search('?q=X02'), {None: ['B'], 'v1': ['B'], 'v2': ['B'], 'latest': ['B']})

    def test_a_dropped_concept_is_found_in_its_releases(self):
        self.drop_b_in_v3()
        ConceptDocument().update(Concept.objects.filter(parent__mnemonic='LOINC'))

        self.assertEqual(self.search('?q=X03'), {None: [], 'v1': [], 'v2': ['B'], 'latest': []})
        self.assertEqual(self.get_ids('v3', '?q=X03'), [])
        response = self.client.get('/concepts/?q=X03')  # global search, as before
        self.assertEqual([concept['id'] for concept in response.data], [])

    def test_the_target_repo_filter_reads_the_release(self):
        self.assertEqual(
            self.search(f'?target_repo={ICD}'), {None: ['B'], 'v1': ['A', 'B'], 'v2': ['B'], 'latest': ['B']})

    def test_facets_count_the_releases_codes(self):
        def get_target_repos(version):
            response = self.client.get(f"{LOINC}{f'{version}/' if version else ''}concepts/?facetsOnly=true")
            self.assertEqual(response.status_code, 200)
            return [list(bucket) for bucket in response.data['facets']['fields']['targetRepo']]

        self.assertEqual(get_target_repos(None), [[ICD, 2, False]])
        self.assertEqual(get_target_repos('v1'), [[ICD, 3, False]])
        self.assertEqual(get_target_repos('v2'), [[ICD, 2, False]])

    def test_match_reads_the_release(self):
        def match(repo_url):
            hits = ConceptFuzzySearch.search({'other_map_codes': 'X05'}, repo_url).execute()
            return sorted(hit.id for hit in hits)

        self.assertEqual(match(f'{LOINC}v1/'), ['A'])
        self.assertEqual(match(f'{LOINC}v2/'), [])

    def test_a_filter_only_match_reads_the_release(self):
        target = Q('nested', path='mapped_codes', query=Q('term', **{'mapped_codes.code': 'X01'}))

        def match(repo_url):
            hits = ConceptFuzzySearch.search({}, repo_url, additional_filter_criterion=target).execute()
            return sorted(hit.id for hit in hits)

        self.assertEqual(match(f'{LOINC}v1/'), ['A'])
        self.assertEqual(match(f'{LOINC}v2/'), [])

    def test_a_match_highlights_a_releases_own_code(self):
        es_search = CustomESSearch(
            ConceptFuzzySearch.search({'other_map_codes': 'X05'}, f'{LOINC}v1/')[0:10], ConceptDocument)
        es_search.to_queryset(False, True, False)

        self.assertEqual(list(es_search.queryset.values_list('mnemonic', flat=True)), ['A'])
        self.assertEqual(es_search.highlights[self.row('v1', 'A').id], {'other_map_codes': ['<em>X05</em>']})

    def test_appending_a_release_again_keeps_its_entries(self):
        row_a = self.row('v1', 'A')
        with override_settings(TEST_MODE=False):  # v1's indexing run again, after HEAD dropped A's mappings
            Concept.batch_index_source_version_append(Concept.objects.filter(id=row_a.id), ConceptDocument, 'v1')
        ConceptDocument._index.refresh()  # pylint: disable=protected-access

        self.assertEqual(self.get_ids('v1', '?q=X01'), ['A'])

    def test_a_fuzzy_search_reads_the_docs_own_codes(self):
        response = self.client.get(f'{LOINC}v1/concepts/?q=name&fuzzy=true&target_repo={ICD}')

        self.assertEqual({concept['id'] for concept in response.data}, {'B'})  # HEAD's codes, not v1's entry

    def test_the_command_fills_existing_docs(self):
        self.drop_b_in_v3()
        es = ConceptDocument._get_connection()  # pylint: disable=protected-access
        for row in Concept.objects.filter(parent__mnemonic='LOINC'):  # as indexed before: HEAD's codes, no entries
            codes = to_mapped_codes(row.get_unidirectional_mappings().values(*MAPPING_VALUES))
            es.update(index=ConceptDocument._index._name, id=row.id, refresh=True,  # pylint: disable=protected-access
                      doc={**MappedCodes.to_fields(codes), RELEASE_MAP_CODES: []})
        self.assertEqual(self.get_ids('v1', '?q=X01'), [])
        self.assertEqual(self.get_ids('v2', '?q=X03'), [])
        out = io.StringIO()

        with override_settings(TEST_MODE=False):
            call_command('concept_release_map_codes', source=[LOINC], stdout=out)
        ConceptDocument._index.refresh()  # pylint: disable=protected-access

        self.assertIn(f'{LOINC}: 2 docs updated', out.getvalue())
        self.assertEqual(self.get_ids('v1', '?q=X01'), ['A'])  # an entry
        self.assertEqual(self.get_ids('v2', '?q=X03'), ['B'])  # a dropped concept's entry
        self.assertEqual(self.get_ids(None, '?q=X01'), [])
