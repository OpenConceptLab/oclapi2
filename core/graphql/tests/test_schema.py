import strawberry
from django.test import SimpleTestCase, override_settings
from graphql import GraphQLError
from strawberry.extensions import SchemaExtension

from core.graphql.schema import Schema, schema

QUERY_MARKER = 'graphql-query-marker'
ALIAS_MARKER = 'graphqlAliasMarker'


@strawberry.type
class FailingQuery:
    @strawberry.field
    def not_found(self, source: str) -> str:
        raise GraphQLError(f"Source '{source}' was not found.")

    @strawberry.field
    def broken(self, source: str) -> str:  # pylint: disable=unused-argument
        raise ValueError('Database unavailable')


class FailingExtension(SchemaExtension):
    def on_execute(self):
        raise ValueError('Extension failed')


@override_settings(LOG_REQUEST_BODIES=False)
class SchemaErrorLoggingTest(SimpleTestCase):
    """Unless LOG_REQUEST_BODIES, GraphQL errors are logged without the query or what the client sent."""

    def test_does_not_log_a_query_it_rejects(self):
        with self.assertNoLogs('strawberry.execution'):
            result = schema.execute_sync(f'{{ concepts(query: "{QUERY_MARKER}") {{ noSuchField }} }}')

        self.assertTrue(result.errors)

    def test_does_not_log_bad_variables(self):
        with self.assertNoLogs('strawberry.execution'):
            result = schema.execute_sync(
                'query($limit: Int) { concepts(query: "q", limit: $limit) { page } }',
                variable_values={'limit': QUERY_MARKER})

        self.assertIn("Variable '$limit'", result.errors[0].message)

    def test_does_not_log_a_graphql_error_a_resolver_raises(self):
        with self.assertNoLogs('strawberry.execution'):
            result = Schema(query=FailingQuery).execute_sync(f'{{ notFound(source: "{QUERY_MARKER}") }}')

        self.assertIn(QUERY_MARKER, result.errors[0].message)

    def test_logs_an_unexpected_resolver_exception_without_the_query_or_its_alias(self):
        with self.assertLogs('strawberry.execution') as logs:
            Schema(query=FailingQuery).execute_sync(f'{{ {ALIAS_MARKER}: broken(source: "{QUERY_MARKER}") }}')

        output = '\n'.join(logs.output)
        self.assertNotIn(QUERY_MARKER, output)
        self.assertNotIn(ALIAS_MARKER, output)
        self.assertIn('ValueError: Database unavailable', output)

    def test_logs_an_exception_strawberry_catches_outside_a_resolver(self):
        with self.assertLogs('strawberry.execution') as logs:
            result = Schema(query=FailingQuery, extensions=[FailingExtension]).execute_sync(
                f'{{ notFound(source: "{QUERY_MARKER}") }}')

        self.assertIsNone(result.errors[0].path)
        output = '\n'.join(logs.output)
        self.assertNotIn(QUERY_MARKER, output)
        self.assertIn('ValueError: Extension failed', output)

    @override_settings(LOG_REQUEST_BODIES=True)
    def test_logs_the_query_with_log_request_bodies(self):
        """Shows the capture above would see the query: with LOG_REQUEST_BODIES it is logged."""
        with self.assertLogs('strawberry.execution') as logs:
            schema.execute_sync(f'{{ concepts(query: "{QUERY_MARKER}") {{ noSuchField }} }}')

        self.assertIn(QUERY_MARKER, '\n'.join(logs.output))
