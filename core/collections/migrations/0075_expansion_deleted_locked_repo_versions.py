import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('collections', '0074_expansion_health'),
    ]

    operations = [
        migrations.AddField(
            model_name='expansion',
            name='deleted_locked_repo_versions',
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.JSONField(), blank=True, default=list, null=True, size=None),
        ),
    ]
