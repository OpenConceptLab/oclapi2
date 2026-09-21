from django.db import migrations

PERMISSIONS = [
    ('mapper_use', 'Can use the Mapper ($match/$rerank) and manage own map projects'),
    ('mapper_ai_assistant', 'Can invoke the Mapper AI Assistant ($invoke)'),
    ('mapper_custom_algorithms', 'Can use externally hosted/custom match algorithms'),
    ('mapper_org_projects', 'Can create or use organization-owned map projects'),
]

PREVIEW_GROUP_NAME = 'preview'
PREVIEW_GROUP_PERMISSION_CODENAMES = ['mapper_use', 'mapper_ai_assistant']
# core/fixtures/auth_groups.yaml hardcodes pks 1-15 for the app's other auth groups
# and is loaded by OCLTestCase/OCLAPITestCase after migrations run. On a fresh test
# database this migration is the first thing to insert into auth_group, so without a
# pinned pk 'preview' would land on pk=1 and then get silently renamed away when that
# fixture overwrites pk=1's name - orphaning the permissions set below under the
# fixture's group instead. pk=16 is next after the fixture's own range and is exactly
# where 'preview' already lands naturally in any environment that already has those
# 15 groups (confirmed against the dev database).
PREVIEW_GROUP_PK = 16


def seed_mapper_permissions_and_default_group(apps, _):
    content_type_model = apps.get_model('contenttypes', 'ContentType')
    permission_model = apps.get_model('auth', 'Permission')
    group_model = apps.get_model('auth', 'Group')

    content_type, _created = content_type_model.objects.get_or_create(
        app_label='users', model='userprofile'
    )

    permissions_by_codename = {}
    for codename, name in PERMISSIONS:
        permission, _created = permission_model.objects.get_or_create(
            codename=codename, content_type=content_type, defaults={'name': name}
        )
        permissions_by_codename[codename] = permission

    preview_group, _created = group_model.objects.get_or_create(
        pk=PREVIEW_GROUP_PK, defaults={'name': PREVIEW_GROUP_NAME}
    )
    preview_group.permissions.set(
        [permissions_by_codename[codename] for codename in PREVIEW_GROUP_PERMISSION_CODENAMES]
    )


def unseed_mapper_permissions_and_default_group(apps, _):
    content_type_model = apps.get_model('contenttypes', 'ContentType')
    permission_model = apps.get_model('auth', 'Permission')
    group_model = apps.get_model('auth', 'Group')

    content_type = content_type_model.objects.filter(app_label='users', model='userprofile').first()
    if content_type:
        permission_model.objects.filter(
            content_type=content_type, codename__in=[codename for codename, _ in PERMISSIONS]
        ).delete()
    group_model.objects.filter(name=PREVIEW_GROUP_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0035_delete_userratelimit'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(seed_mapper_permissions_and_default_group, unseed_mapper_permissions_and_default_group),
    ]
