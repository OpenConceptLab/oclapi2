# Generated by Django 4.2.11 on 2024-08-13 02:50

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('events', '0002_event_public'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='referenced_object_uri',
            field=models.CharField(max_length=255),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='event',
            name='created_by',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='creator_events', to=settings.AUTH_USER_MODEL),
        ),
    ]
