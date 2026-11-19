import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('capabilities', '0003_usage_window_start_and_reversed_units'),
        ('orgs', '0023_organizationmember'),
    ]

    operations = [
        migrations.AddField(
            model_name='usageevent',
            name='organization',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='usage_events',
                to='orgs.organization'
            ),
        ),
    ]
