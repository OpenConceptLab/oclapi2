from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('concepts', '0078_auto_20250909_1351'),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                'CREATE INDEX IF NOT EXISTS concepts_sources_source_concept_idx '
                'ON concepts_sources (source_id, concept_id);'
            ),
            reverse_sql=(
                'DROP INDEX IF EXISTS concepts_sources_source_concept_idx;'
            ),
        ),
    ]
