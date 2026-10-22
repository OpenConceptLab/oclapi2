import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('capacity', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='GroupCapacityTier',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tier', models.CharField(max_length=32)),
                ('group', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='capacity_tier', to='auth.group')),
            ],
            options={
                'db_table': 'group_capacity_tiers',
            },
        ),
    ]
