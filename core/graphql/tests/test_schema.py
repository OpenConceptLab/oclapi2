from django.test import SimpleTestCase, override_settings
from graphql import GraphQLError, Source

from core.graphql.schema import schema

QUERY_MARKER = 'graphql-query-marker'
QUERY = f'{{ concepts(query: "{QUERY_MARKER}") {{ noSuchField }} }}'


@override_settings(ENV='production')
class SchemaErrorLoggingTest(SimpleTestCase):
    """Outside development, GraphQL errors are logged without the query or the values the client sent."""

    def test_does_not_log_a_query_it_rejects(self):
        with self.assertNoLogs('strawberry.execution'):
            result = schema.execute_sync(QUERY)

        self.assertTrue(result.errors)

    def test_does_not_log_bad_variables(self):
        with self.assertNoLogs('strawberry.execution'):
            result = schema.execute_sync(
                'query($limit: Int) { concepts(query: "q", limit: $limit) { page } }',
                variable_values={'limit': QUERY_MARKER})

        self.assertIn("Variable '$limit'", result.errors[0].message)

    def test_does_not_log_a_graphql_error_a_resolver_raises(self):
        error = GraphQLError(
            f"Source '{QUERY_MARKER}' was not found.", source=Source(QUERY), positions=[2], path=['concepts'],
            original_error=GraphQLError(f"Source '{QUERY_MARKER}' was not found."))
        with self.assertNoLogs('strawberry.execution'):
            schema.process_errors([error])

    def test_logs_an_unexpected_resolver_exception_without_the_query_or_its_alias(self):
        error = GraphQLError(
            'Database unavailable', source=Source(QUERY), positions=[2], path=[QUERY_MARKER],
            original_error=ValueError('Database unavailable'))
        with self.assertLogs('strawberry.execution') as logs:
            schema.process_errors([error])

        output = '\n'.join(logs.output)
        self.assertNotIn(QUERY_MARKER, output)
        self.assertIn('GraphQL resolver error', output)
        self.assertIn('ValueError: Database unavailable', output)

    @override_settings(ENV='development')
    def test_development_logs_the_query(self):
        """Shows the capture above would see the query: in development it is logged."""
        with self.assertLogs('strawberry.execution') as logs:
            schema.execute_sync(QUERY)

        self.assertIn(QUERY_MARKER, '\n'.join(logs.output))
