from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0023_organizationmember'),
        ('users', '0037_userprofile_sso_id'),
    ]

    operations = [
        # The table doesn't change: orgs 0023 has already taken it over as OrganizationMember.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='userprofile',
                    name='organizations',
                    field=models.ManyToManyField(
                        related_name='members', through='orgs.OrganizationMember', to='orgs.organization'),
                ),
            ],
        ),
    ]
