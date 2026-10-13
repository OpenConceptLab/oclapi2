"""
Adds concepts_repo_list_ids_idx: concepts_repo_list_idx's key and predicate with INCLUDE (id), so a deep page of a source's HEAD
concepts list can walk its OFFSET in the index alone, without reading every skipped row from the table
(OpenConceptLab/ocl_online#476). concepts_repo_list_idx stays until EXPLAIN on staging and production shows the new index in use;
a later migration drops it.

On a large table, build the index by hand before deploying: every API task runs `migrate` before it serves, and a
build that outlasts the health check's grace leaves an INVALID index behind. This migration then only checks it. It
builds the index itself only on a small table (a fresh, test or dev database).
"""
from django.db import migrations

TABLE = 'concepts'
NEW_INDEX = 'concepts_repo_list_ids_idx'
PREDICATE = 'not retired and is_active and id = versioned_object_id'
CREATE_NEW_INDEX = (
    f'create index concurrently if not exists {NEW_INDEX} on {TABLE} (parent_id, updated_at desc) include (id) '
    f'where {PREDICATE};'
)
MAX_ROWS_TO_BUILD_HERE = 3_000_000


def add_index(apps, schema_editor):  # pylint: disable=unused-argument
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('select to_regclass(%s) is not null', [NEW_INDEX])
        if not cursor.fetchone()[0]:
            cursor.execute('select reltuples from pg_class where oid = %s::regclass', [TABLE])
            rows = cursor.fetchone()[0]
            if rows > MAX_ROWS_TO_BUILD_HERE:
                raise RuntimeError(
                    f'{TABLE} has about {rows:,.0f} rows: build {NEW_INDEX} by hand before deploying, '
                    f'then migrate again. {CREATE_NEW_INDEX}')
            cursor.execute(CREATE_NEW_INDEX)

        cursor.execute('select indisvalid from pg_index where indexrelid = %s::regclass', [NEW_INDEX])
        if not cursor.fetchone()[0]:
            raise RuntimeError(
                f'{NEW_INDEX} is INVALID (an interrupted build). Run `drop index concurrently {NEW_INDEX};`, '
                f'build it again, then migrate again. {CREATE_NEW_INDEX}')


def drop_index(apps, schema_editor):  # pylint: disable=unused-argument
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'drop index concurrently if exists {NEW_INDEX};')


class Migration(migrations.Migration):
    atomic = False  # CREATE/DROP INDEX CONCURRENTLY can't run in a transaction

    dependencies = [
        ('concepts', '0089_merge_20261005_0816'),
    ]

    operations = [
        migrations.RunPython(add_index, drop_index),
    ]
