from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('map_projects', '0035_mapproject_prompt_output_locale'),
    ]

    operations = [
        migrations.AddField(
            model_name='mapproject',
            name='use_lexical_variants',
            field=models.BooleanField(default=False),
        ),
    ]
