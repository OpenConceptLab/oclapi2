from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('mappings', '0058_mapping_retire_reason'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY mappings_head_parent_updated
                ON mappings(parent_id, updated_at DESC)
                WHERE id = versioned_object_id
            """,
            reverse_sql="DROP INDEX IF EXISTS mappings_head_parent_updated",
        ),
    ]
