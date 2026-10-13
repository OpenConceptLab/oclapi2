import importlib
from unittest.mock import MagicMock

from django.db import connection
from django.test import SimpleTestCase, TestCase

MIGRATIONS = (
    'core.concepts.migrations.0090_concepts_repo_list_ids_idx',
    'core.mappings.migrations.0061_mappings_repo_list_ids_idx',
)


class ListCoveringIndexTest(TestCase):
    def test_list_indexes_cover_id(self):
        with connection.cursor() as cursor:
            for table in ('concepts', 'mappings'):
                cursor.execute(
                    'select i.indexdef, x.indisvalid from pg_indexes i join pg_index x on '
                    'x.indexrelid = to_regclass(i.indexname) where i.indexname = %s', [f'{table}_repo_list_ids_idx'])
                definition, valid = cursor.fetchone()
                self.assertTrue(valid)
                self.assertIn('(parent_id, updated_at DESC) INCLUDE (id)', definition)
                self.assertIn('WHERE ((NOT retired) AND is_active AND (id = versioned_object_id))', definition)

                cursor.execute('select to_regclass(%s)', [f'{table}_repo_list_idx'])
                self.assertIsNone(cursor.fetchone()[0])


class ListCoveringIndexMigrationTest(SimpleTestCase):
    """The migrations never build on a production-sized table, and never drop the old index for an invalid one."""
    @staticmethod
    def run_migration(module, *answers):
        cursor = MagicMock()
        cursor.fetchone.side_effect = answers
        schema_editor = MagicMock()
        schema_editor.connection.cursor.return_value.__enter__.return_value = cursor
        error = None
        try:
            importlib.import_module(module).replace_index(None, schema_editor)
        except RuntimeError as ex:
            error = str(ex)
        return error, [call.args[0] for call in cursor.execute.call_args_list]

    def test_refuses_to_build_on_a_large_table(self):
        for module in MIGRATIONS:
            error, statements = self.run_migration(module, (False,), (10_000_000,))
            self.assertIn('by hand before deploying', error)
            self.assertFalse([sql for sql in statements if 'create index' in sql or 'drop index' in sql])

    def test_builds_on_a_small_table(self):
        for module in MIGRATIONS:
            error, statements = self.run_migration(module, (False,), (1_000,), (True,))
            self.assertIsNone(error)
            self.assertIn('create index concurrently', statements[2])
            self.assertIn('drop index concurrently', statements[-1])

    def test_uses_an_index_built_by_hand(self):
        for module in MIGRATIONS:
            error, statements = self.run_migration(module, (True,), (True,))
            self.assertIsNone(error)
            self.assertFalse([sql for sql in statements if 'create index' in sql])
            self.assertIn('drop index concurrently', statements[-1])

    def test_keeps_the_old_index_when_the_new_one_is_invalid(self):
        for module in MIGRATIONS:
            error, statements = self.run_migration(module, (True,), (False,))
            self.assertIn('INVALID', error)
            self.assertFalse([sql for sql in statements if 'drop index' in sql])
