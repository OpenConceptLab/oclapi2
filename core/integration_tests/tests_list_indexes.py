import importlib
from unittest.mock import MagicMock

from django.db import connection
from django.test import SimpleTestCase, TestCase

from core.fhir.ops.expand import INDEXED_NAME_BYTES

MIGRATIONS = (
    'core.concepts.migrations.0090_concepts_repo_list_ids_idx',
    'core.mappings.migrations.0061_mappings_repo_list_ids_idx',
    'core.concepts.migrations.0091_concepts_head_parent_pub_all',
    'core.mappings.migrations.0062_mappings_head_parent_pub_all',
    'core.concepts.migrations.0092_concept_names_concept_id_incl_name',
    'core.concepts.migrations.0093_concept_names_long_concept_id',
    'core.concepts.migrations.0094_concepts_id_incl_expand_cols',
)


class ListCoveringIndexTest(TestCase):
    def test_list_indexes_cover_id(self):
        with connection.cursor() as cursor:
            for table in ('concepts', 'mappings'):
                cursor.execute('select indexdef from pg_indexes where indexname = %s', [f'{table}_repo_list_ids_idx'])
                definition = cursor.fetchone()[0]
                self.assertIn('(parent_id, updated_at DESC) INCLUDE (id)', definition)
                self.assertIn('WHERE ((NOT retired) AND is_active AND (id = versioned_object_id))', definition)

                # the old index stays until EXPLAIN in production shows the new one in use (a later migration)
                cursor.execute('select to_regclass(%s)', [f'{table}_repo_list_idx'])
                self.assertIsNotNone(cursor.fetchone()[0])

    def test_head_index_covers_retired_rows(self):
        with connection.cursor() as cursor:
            for table in ('concepts', 'mappings'):
                cursor.execute('select indexdef from pg_indexes where indexname = %s', [f'{table}_head_parent_pub_all'])
                definition = cursor.fetchone()[0]
                self.assertIn('(parent_id, public_access) WHERE (is_active AND (id = versioned_object_id))', definition)

    def test_expand_filter_indexes_cover_the_match(self):
        with connection.cursor() as cursor:
            for index, columns in (
                    ('concept_names_concept_id_incl_name',
                     f'(concept_id) INCLUDE (name) WHERE (octet_length(name) <= {INDEXED_NAME_BYTES})'),
                    ('concept_names_long_concept_id',
                     f'(concept_id) WHERE (octet_length(name) > {INDEXED_NAME_BYTES})'),
                    ('concepts_id_incl_expand_cols',
                     '(id) INCLUDE (is_active, retired, parent_id, mnemonic, versioned_object_id)'),
            ):
                cursor.execute('select indexdef from pg_indexes where indexname = %s', [index])
                self.assertTrue(cursor.fetchone()[0].endswith(f'USING btree {columns}'))


class ListCoveringIndexMigrationTest(SimpleTestCase):
    """The migrations never build on a production-sized table, and never accept an invalid index."""
    @staticmethod
    def run_migration(module, *answers):
        cursor = MagicMock()
        cursor.fetchone.side_effect = answers
        schema_editor = MagicMock()
        schema_editor.connection.cursor.return_value.__enter__.return_value = cursor
        error = None
        try:
            importlib.import_module(module).add_index(None, schema_editor)
        except RuntimeError as ex:
            error = str(ex)
        return error, [call.args[0] for call in cursor.execute.call_args_list]

    def test_refuses_to_build_on_a_large_table(self):
        for module in MIGRATIONS:
            error, statements = self.run_migration(module, (False,), (10_000_000,))
            self.assertIn('by hand before deploying', error)
            self.assertFalse([sql for sql in statements if 'create index' in sql])

    def test_uses_an_index_built_by_hand(self):
        for module in MIGRATIONS:
            error, statements = self.run_migration(module, (True,), (True,))
            self.assertIsNone(error)
            self.assertFalse([sql for sql in statements if 'create index' in sql])

    def test_stops_on_an_invalid_index(self):
        for module in MIGRATIONS:
            error, _ = self.run_migration(module, (True,), (False,))
            self.assertIn('INVALID', error)

    def test_never_drops_an_index_going_forward(self):
        for module in MIGRATIONS:
            for answers in (((False,), (1_000,), (True,)), ((True,), (True,)), ((True,), (False,))):
                _, statements = self.run_migration(module, *answers)
                self.assertFalse([sql for sql in statements if 'drop index' in sql])
