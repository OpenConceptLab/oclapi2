from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('collections', '0075_expansion_deleted_locked_repo_versions'),
    ]

    operations = [
        migrations.AddField(
            model_name='expansion',
            name='health_checked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
