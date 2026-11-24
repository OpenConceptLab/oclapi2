from django.test import SimpleTestCase, override_settings
from graphql import GraphQLError, Source

from core.graphql.schema import schema

QUERY_MARKER = 'graphql-query-marker'
QUERY = f'{{ concepts(query: "{QUERY_MARKER}") {{ noSuchField }} }}'


@override_settings(ENV='production')
class SchemaErrorLoggingTest(SimpleTestCase):
    """Outside development, GraphQL errors are logged without the query."""

    def test_does_not_log_a_query_it_rejects(self):
        with self.assertNoLogs('strawberry.execution'):
            result = schema.execute_sync(QUERY)

        self.assertTrue(result.errors)

    def test_logs_a_resolver_error_without_the_query(self):
        error = GraphQLError(
            'Not found', source=Source(QUERY), positions=[2], path=['concepts'], original_error=ValueError('Not found'))
        with self.assertLogs('strawberry.execution') as logs:
            schema.process_errors([error])

        output = '\n'.join(logs.output)
        self.assertNotIn(QUERY_MARKER, output)
        self.assertIn("GraphQL error at ['concepts']", output)
        self.assertIn('ValueError: Not found', output)

    @override_settings(ENV='development')
    def test_development_logs_the_query(self):
        """Shows the capture above would see the query: in development it is logged."""
        with self.assertLogs('strawberry.execution') as logs:
            schema.execute_sync(QUERY)

        self.assertIn(QUERY_MARKER, '\n'.join(logs.output))
