import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_roles(apps, _):
    """Only what the new column needs. load_fixtures fills in the rest from core/fixtures/org_roles.yaml."""
    role = apps.get_model('orgs', 'Role')
    role.objects.bulk_create([role(id=1, name='admin'), role(id=2, name='member')])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0037_userprofile_sso_id'),
        ('orgs', '0022_retire_public_edit_access'),
    ]

    operations = [
        migrations.CreateModel(
            name='Role',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('description', models.TextField(blank=True, default='')),
            ],
            options={
                'db_table': 'org_roles',
            },
        ),
        migrations.RunPython(seed_roles, migrations.RunPython.noop),
        # The table exists already, as the automatic many-to-many table of UserProfile.organizations.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='OrganizationMember',
                    fields=[
                        ('id', models.AutoField(
                            auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('organization', models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE, related_name='memberships',
                            to='orgs.organization')),
                        ('userprofile', models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'db_table': 'user_profiles_organizations',
                        'unique_together': {('userprofile', 'organization')},
                    },
                ),
            ],
        ),
        # Every existing member becomes an admin, so that nobody loses what they can do today.
        migrations.AddField(
            model_name='organizationmember',
            name='role',
            field=models.ForeignKey(
                db_default=1, default=2, on_delete=django.db.models.deletion.PROTECT, to='orgs.role'),
        ),
    ]
