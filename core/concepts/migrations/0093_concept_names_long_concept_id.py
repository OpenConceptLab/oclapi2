"""
Adds concept_names_long_concept_id: the concept_id key for names over 2,000 bytes, the names
concept_names_concept_id_incl_name (0092) leaves out, so a FHIR $expand with a filter finds a member's long names
without reading all of its names from the table (OpenConceptLab/ocl_online#564). Few names are that long, so the index
is small.

On a large table, build the index by hand before deploying: every API task runs `migrate` before it serves, and a
build that outlasts the health check's grace leaves an INVALID index behind. This migration then only checks it. It
builds the index itself only on a small table (a fresh, test or dev database).
"""
from django.db import migrations

TABLE = 'concept_names'
NEW_INDEX = 'concept_names_long_concept_id'
CREATE_NEW_INDEX = (
    f'create index concurrently if not exists {NEW_INDEX} on {TABLE} (concept_id) '
    'where octet_length(name) > 2000;'
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
        ('concepts', '0092_concept_names_concept_id_incl_name'),
    ]

    operations = [
        migrations.RunPython(add_index, drop_index),
    ]
