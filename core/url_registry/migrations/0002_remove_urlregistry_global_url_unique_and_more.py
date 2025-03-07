# Generated by Django 4.2.4 on 2024-01-15 02:56

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('url_registry', '0001_initial'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='urlregistry',
            name='global_url_unique',
        ),
        migrations.AddConstraint(
            model_name='urlregistry',
            constraint=models.UniqueConstraint(condition=models.Q(('is_active', True), ('organization__isnull', True), ('user__isnull', True)), fields=('url',), name='global_url_unique'),
        ),
    ]
