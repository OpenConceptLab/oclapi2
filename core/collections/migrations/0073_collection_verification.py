from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('collections', '0072_expansion_built_with'),
    ]

    operations = [
        migrations.AddField(
            model_name='collection',
            name='verification',
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
