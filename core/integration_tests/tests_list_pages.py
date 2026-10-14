from datetime import timedelta

from django.db import connection
from django.db.models import F
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.common.tests import OCLAPITestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.sources.tests.factories import OrganizationSourceFactory


class SourceHeadListPagesTest(OCLAPITestCase):
    """
    A source's HEAD concept and mapping lists select a page's ids first (CustomPaginator page_by_ids), so the page
    query's joins cover the page's rows only, not every row a deep page's OFFSET skips.
    """
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory()

        def concept():
            return ConceptFactory(parent=self.source, names=[ConceptNameFactory.build(locale_preferred=True)])

        for _ in range(5):
            MappingFactory(parent=self.source, from_concept=concept(), to_concept=concept())
        now = timezone.now()
        for model in (Concept, Mapping):
            for minutes, row in enumerate(model.objects.filter(parent=self.source, id=F('versioned_object_id'))):
                model.objects.filter(id=row.id).update(updated_at=now - timedelta(minutes=minutes))

    def assert_pages(self, url, table, total):
        everything = self.client.get(f'{url}?limit={total}', format='json')
        self.assertEqual(len(everything.data), total)

        rows = []
        for page in range(1, total // 4 + 2):
            with CaptureQueriesContext(connection) as context:
                response = self.client.get(f'{url}?limit=4&page={page}', format='json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['num_found'], str(total))
            rows += response.data

            page_queries = [
                query['sql'] for query in context.captured_queries
                if query['sql'].startswith('SELECT') and f'FROM "{table}"' in query['sql'] and ' JOIN ' in query['sql']
            ]
            self.assertEqual(len(page_queries), 1)
            self.assertIn(f'"{table}"."id" IN (SELECT', page_queries[0])
            self.assertNotIn('LIMIT', page_queries[0].rsplit(')', 1)[-1])  # paged inside the ids subquery

        self.assertEqual(rows, everything.data)

    def test_mappings(self):
        self.assert_pages(self.source.mappings_url, 'mappings', 5)

    def test_concepts(self):
        self.assert_pages(self.source.concepts_url, 'concepts', 10)
