# Generated by Django 4.2.4 on 2024-01-18 10:36

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('orgs', '0021_organization_checksums'),
        ('url_registry', '0002_remove_urlregistry_global_url_unique_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='urlregistry',
            name='organization',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='url_registry_entries', to='orgs.organization'),
        ),
        migrations.AlterField(
            model_name='urlregistry',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='url_registry_entries', to=settings.AUTH_USER_MODEL),
        ),
    ]
