# Generated for OpenConceptLab/ocl_issues#2527

from django.contrib.postgres.fields import ArrayField
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('map_projects', '0036_mapproject_use_lexical_variants'),
    ]

    operations = [
        migrations.AddField(
            model_name='mapproject',
            name='input_locales',
            field=ArrayField(
                models.CharField(max_length=10),
                blank=True,
                default=list,
                null=True,
            ),
        ),
    ]
