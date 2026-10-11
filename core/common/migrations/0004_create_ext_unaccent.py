from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0003_create_ext_btree_gin'),
    ]

    operations = [
        migrations.RunSQL(
            'CREATE EXTENSION IF NOT EXISTS unaccent SCHEMA public;',
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            """
            CREATE OR REPLACE FUNCTION public.immutable_unaccent(text) RETURNS text
            AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
            LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT;
            """,
            reverse_sql='DROP FUNCTION IF EXISTS public.immutable_unaccent(text);',
        ),
    ]
