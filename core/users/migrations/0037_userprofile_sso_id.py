from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0036_retire_public_edit_access'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='sso_id',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
    ]
