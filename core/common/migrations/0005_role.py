from django.db import migrations, models


def seed_roles(apps, _):
    """Only what orgs 0023 needs. load_fixtures fills in the rest from core/fixtures/roles.yaml."""
    role = apps.get_model('common', 'Role')
    role.objects.bulk_create([role(id=1, name='admin'), role(id=2, name='member')])


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0004_create_ext_unaccent'),
    ]

    operations = [
        migrations.CreateModel(
            name='Role',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(
                    choices=[('admin', 'Admin'), ('member', 'Member')], max_length=100, unique=True)),
                ('description', models.TextField(blank=True, default='')),
            ],
            options={
                'db_table': 'roles',
            },
        ),
        migrations.RunPython(seed_roles, migrations.RunPython.noop),
    ]
