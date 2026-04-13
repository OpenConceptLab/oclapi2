from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mappings', '0056_auto_20250226_0544'),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                'CREATE INDEX IF NOT EXISTS mappings_sources_source_mapping_idx '
                'ON mappings_sources (source_id, mapping_id);'
            ),
            reverse_sql=(
                'DROP INDEX IF EXISTS mappings_sources_source_mapping_idx;'
            ),
        ),
    ]
