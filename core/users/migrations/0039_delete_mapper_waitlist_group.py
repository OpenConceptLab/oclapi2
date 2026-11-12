from django.db import migrations


def delete_mapper_waitlist_group(apps, schema_editor):  # pylint: disable=unused-argument
    apps.get_model('auth', 'Group').objects.filter(name='mapper-waitlist').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('users', '0038_alter_userprofile_organizations'),
    ]

    operations = [
        migrations.RunPython(delete_mapper_waitlist_group, migrations.RunPython.noop),
    ]
