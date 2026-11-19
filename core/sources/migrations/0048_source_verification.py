from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sources', '0047_source_supplements'),
    ]

    operations = [
        migrations.AddField(
            model_name='source',
            name='verification',
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
