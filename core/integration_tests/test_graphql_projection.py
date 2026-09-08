"""Real Elasticsearch verification for GraphQL projection semantics and SQL avoidance."""

from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from elasticsearch_dsl.connections import connections

from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLTestCase
from core.concepts.documents import ConceptDocument
from core.concepts.tests.factories import ConceptDescriptionFactory, ConceptFactory, ConceptNameFactory
from core.graphql.schema import schema
from core.sources.documents import SourceDocument
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.tests.factories import UserProfileFactory


@skipUnless(getattr(settings, 'ES_ENABLED', False), 'Requires Elasticsearch')
class GraphQLProjectionIntegrationTests(OCLTestCase):
    """Use temporary index names and real indexed ORM fixtures, without touching shared indexes."""

    def setUp(self):
        """Prepare dedicated indexes; redirect GraphQL searches only for this test."""
        super().setUp()
        self.connection = connections.get_connection()
        suffix = uuid4().hex
        self.indexes = {}
        for document in (ConceptDocument, SourceDocument):
            index = document._index.clone(f'graphql-test-{document.Index.name}-{suffix}')  # pylint: disable=protected-access
            index.create()
            self.addCleanup(index.delete)
            self.indexes[document] = index._name  # pylint: disable=protected-access
            search = document.search().index().index(index._name)  # pylint: disable=protected-access
            patcher = patch.object(document, 'search', side_effect=lambda search=search: search)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.source = OrganizationSourceFactory(mnemonic='CASE-Sensitive', name='Clinical dictionary')
        self.concept = ConceptFactory(parent=self.source, mnemonic='AbC', datatype='Numeric', concept_class='Diagnosis')
        ConceptNameFactory(concept=self.concept, name='Hypertension-test', locale='en', locale_preferred=True)
        ConceptDescriptionFactory(concept=self.concept, name='Preferred definition', locale='en', locale_preferred=True)
        self.index(self.source, SourceDocument)
        self.index(self.concept, ConceptDocument)

    def index(self, instance, document):
        """Index the actual Document preparation output, including the additive projection fields."""
        self.connection.index(
            index=self.indexes[document], id=instance.id, document=document().prepare(instance), refresh=True,
        )

    def execute(self, query, user=None):
        """Use a fresh schema context so membership caches cannot cross principals."""
        return async_to_sync(schema.execute)(
            query, context_value=SimpleNamespace(user=user or AnonymousUser(), auth_status='valid' if user else 'none'),
        )

    def test_source_and_scoped_concepts_execute_without_sql(self):
        """Real source and concept lookups require no SQL after anonymous context creation."""
        query = '''{
          source(org: "%s", source: "%s") { name canonicalUrl uri }
          concepts(org: "%s", source: "%s", query: "Hypertension") {
            versionResolved totalCount results { conceptId display datatype { name } conceptClass }
          }
        }''' % (self.source.organization.mnemonic, self.source.mnemonic,
                self.source.organization.mnemonic, self.source.mnemonic)
        with self.assertNumQueries(0):
            result = self.execute(query)
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['source']['name'], self.source.name)
        # The rebuilt URI must match the one the ORM stores, including owner and mnemonic casing.
        self.assertEqual(result.data['source']['uri'], self.source.uri)
        self.assertEqual(result.data['concepts']['totalCount'], 1)
        self.assertEqual(result.data['concepts']['results'], [{
            'conceptId': 'AbC', 'display': 'Hypertension-test',
            'datatype': {'name': 'Numeric'}, 'conceptClass': 'Diagnosis',
        }])

    def test_description_selection_falls_back_to_the_database(self):
        """description is not indexed, so selecting it routes the whole request through the ORM."""
        result = self.execute('''{
          concepts(org: "%s", source: "%s", query: "Hypertension") { results { conceptId description } }
        }''' % (self.source.organization.mnemonic, self.source.mnemonic))
        self.assertIsNone(result.errors)
        self.assertEqual(
            result.data['concepts']['results'], [{'conceptId': 'AbC', 'description': 'Preferred definition'}],
        )

    def test_global_projection_uses_head_and_excludes_retired_and_inactive(self):
        """Global counts omit historical versions and inactive/retired documents."""
        for fields in ({'retired': True}, {'is_active': False}):
            excluded = ConceptFactory(parent=self.source, **fields)
            ConceptNameFactory(concept=excluded, name='Hypertension-test', locale='en', locale_preferred=True)
            self.index(excluded, ConceptDocument)
        self.index(self.concept.get_latest_version(), ConceptDocument)
        with self.assertNumQueries(0):
            result = self.execute('{ concepts(query: "Hypertension") { totalCount results { conceptId } } }')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['concepts']['totalCount'], 1)

    def test_private_parent_hides_its_concepts(self):
        """Concepts of a private repository stay hidden from anonymous global search."""
        self.source.public_access = ACCESS_TYPE_NONE
        self.source.save()
        self.index(self.source, SourceDocument)
        # Propagation copies the repository access onto children before they are reindexed;
        # the concept projection is filtered by that copied flag alone.
        self.concept.public_access = ACCESS_TYPE_NONE
        self.index(self.concept, ConceptDocument)
        with self.assertNumQueries(0):
            result = self.execute('{ concepts(query: "Hypertension") { totalCount results { display } } }')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['concepts']['totalCount'], 0)
        member = UserProfileFactory()
        self.source.organization.members.add(member)
        result = self.execute('{ concepts(query: "Hypertension") { totalCount results { display } } }', member)
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['concepts']['totalCount'], 1)

    def test_two_owners_with_same_source_mnemonic_do_not_mix(self):
        """Repository identity includes owner and owner type, not only the source mnemonic."""
        other = OrganizationSourceFactory(mnemonic=self.source.mnemonic)
        concept = ConceptFactory(parent=other, mnemonic='Other')
        self.index(concept, ConceptDocument)
        query = ('{ concepts(org: "%s", source: "%s", conceptIds: ["AbC", "Other"]) '
                 '{ totalCount results { conceptId } } }') % (
            self.source.organization.mnemonic, self.source.mnemonic,
        )
        with self.assertNumQueries(0):
            result = self.execute(query)
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['concepts']['results'], [{'conceptId': 'AbC'}])

    def test_release_projection_matches_membership(self):
        """Explicit releases search source_version membership instead of HEAD flags."""
        release = OrganizationSourceFactory(
            organization=self.source.organization, mnemonic=self.source.mnemonic, version='v1', released=True,
        )
        historical = self.concept.get_latest_version()
        historical.sources.add(release)
        self.index(release, SourceDocument)
        self.index(historical, ConceptDocument)
        query = ('{ concepts(org: "%s", source: "%s", version: "v1", conceptIds: ["AbC"]) '
                 '{ versionResolved results { id } } }') % (
            self.source.organization.mnemonic, self.source.mnemonic,
        )
        with self.assertNumQueries(0):
            result = self.execute(query)
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['concepts']['results'], [{'id': str(historical.id)}])

    def test_hydrated_payload_uses_same_head_and_selected_columns(self):
        """Requesting names hydrates the matching HEAD without selecting unused concept extras."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        query = ('{ concepts(org: "%s", source: "%s", query: "Hypertension") '
                 '{ totalCount results { id names { name } } } }') % (
            self.source.organization.mnemonic, self.source.mnemonic,
        )
        with CaptureQueriesContext(connection) as queries:
            result = self.execute(query)
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['concepts']['totalCount'], 1)
        self.assertEqual(result.data['concepts']['results'][0]['id'], str(self.concept.id))
        self.assertEqual(result.data['concepts']['results'][0]['names'], [{'name': 'Hypertension-test'}])
        self.assertTrue(queries.captured_queries)
        self.assertFalse(any('"concepts"."extras"' in query['sql'] for query in queries.captured_queries))
