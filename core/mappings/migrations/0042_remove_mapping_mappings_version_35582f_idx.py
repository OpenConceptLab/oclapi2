# Generated by Django 4.1.7 on 2023-04-13 10:48

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mappings', '0041_remove_mapping_mappings_uri_f7a346_idx_and_more'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='mapping',
            name='mappings_version_35582f_idx',
        ),
    ]
