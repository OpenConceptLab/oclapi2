from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('collections', '0073_collection_verification'),
    ]

    operations = [
        migrations.AddField(
            model_name='expansion',
            name='health',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='expansion',
            name='health_is_current',
            field=models.BooleanField(blank=True, null=True),
        ),
    ]
