from django.db import migrations

CAPABILITIES = [
    ('mapper.projects', 'Number of map projects a user may create', 1),
    ('mapper.rows_per_project', 'Number of rows a single map project may declare', 25),
    ('mapper.match_operations', 'Number of concept-match operations (rows x algorithms)', 100),
    ('ai_assistant.calls', 'Number of Mapper AI Assistant invocations', 6),
]

PREVIEW_GROUP_NAME = 'preview'


def seed_mapper_capabilities(apps, _):
    capability_model = apps.get_model('capabilities', 'Capability')
    group_capability_model = apps.get_model('capabilities', 'GroupCapability')
    group_model = apps.get_model('auth', 'Group')

    preview_group, _created = group_model.objects.get_or_create(name=PREVIEW_GROUP_NAME)

    for name, description, limit in CAPABILITIES:
        capability, _created = capability_model.objects.get_or_create(
            name=name, defaults={'description': description}
        )
        group_capability_model.objects.update_or_create(
            group=preview_group, capability=capability, defaults={'limit': limit}
        )


def unseed_mapper_capabilities(apps, _):
    capability_model = apps.get_model('capabilities', 'Capability')
    group_capability_model = apps.get_model('capabilities', 'GroupCapability')
    group_model = apps.get_model('auth', 'Group')

    preview_group = group_model.objects.filter(name=PREVIEW_GROUP_NAME).first()
    if preview_group:
        group_capability_model.objects.filter(
            group=preview_group, capability__name__in=[name for name, _, _ in CAPABILITIES]
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('capabilities', '0001_initial'),
        ('users', '0036_seed_mapper_permissions_and_default_group'),
    ]

    operations = [
        migrations.RunPython(seed_mapper_capabilities, unseed_mapper_capabilities),
    ]
