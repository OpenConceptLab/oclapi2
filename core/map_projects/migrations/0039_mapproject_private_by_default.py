# Generated for OpenConceptLab/ocl_online#188

from django.db import migrations, models


def make_existing_projects_private(apps, schema_editor):
    """Existing map projects become private, matching the new default."""
    MapProject = apps.get_model('map_projects', 'MapProject')
    MapProject.objects.exclude(public_access='None').update(public_access='None')


class Migration(migrations.Migration):

    dependencies = [
        ('map_projects', '0038_automatchrun'),
    ]

    operations = [
        migrations.AlterField(
            model_name='mapproject',
            name='public_access',
            field=models.CharField(
                blank=True, choices=[('View', 'View'), ('Edit', 'Edit'), ('None', 'None')], default='None',
                max_length=16),
        ),
        migrations.RunPython(make_existing_projects_private, migrations.RunPython.noop),
    ]
