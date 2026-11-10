import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0005_role'),
        ('users', '0037_userprofile_sso_id'),
        ('orgs', '0022_retire_public_edit_access'),
    ]

    operations = [
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
                db_default=1, default=2, on_delete=django.db.models.deletion.PROTECT, to='common.role'),
        ),
    ]
