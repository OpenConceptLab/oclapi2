from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('concepts', '0084_concept_concepts_prev_ver_non_latest'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY concepts_head_parent_updated
                ON concepts(parent_id, updated_at DESC)
                WHERE id = versioned_object_id
            """,
            reverse_sql="DROP INDEX IF EXISTS concepts_head_parent_updated",
        ),
    ]
