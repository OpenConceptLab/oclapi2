from django.db import migrations, models


def backfill_seeded_from(apps, schema_editor):  # pylint: disable=unused-argument
    """
    Every version made so far was seeded from HEAD, except those an import seeded. HEAD records the latest version an
    import seeded, if any.
    """
    Source = apps.get_model('sources', 'Source')
    to_update = []
    imported_by_head = {}
    versions = Source.objects.exclude(version='HEAD').order_by('created_at').only(
        'id', 'version', 'versioning_mode', 'organization_id', 'user_id', 'mnemonic')
    for version in versions.iterator(chunk_size=2000):
        if version.versioning_mode == 'isolated':
            version.seeded_from = {'type': 'import', 'task': None}
            imported_by_head[(version.organization_id, version.user_id, version.mnemonic)] = version.version
        else:
            version.seeded_from = {'type': 'repo_version', 'url': None}
        to_update.append(version)
    heads = {
        (head.organization_id, head.user_id, head.mnemonic): head
        for head in Source.objects.filter(version='HEAD').only('id', 'uri', 'organization_id', 'user_id', 'mnemonic')
    }
    for version in to_update:
        head = heads.get((version.organization_id, version.user_id, version.mnemonic))
        if version.seeded_from['type'] == 'repo_version':
            version.seeded_from['url'] = f'{head.uri}HEAD/' if head and head.uri else None
    for key, version in imported_by_head.items():
        head = heads.get(key)
        if head and head.uri:
            head.seeded_from = {'type': 'repo_version', 'url': f'{head.uri}{version}/'}
            to_update.append(head)
    Source.objects.bulk_update(to_update, ['seeded_from'], batch_size=2000)


class Migration(migrations.Migration):

    dependencies = [
        ('sources', '0047_source_versioning_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='source',
            name='seeded_from',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_seeded_from, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='source',
            name='versioning_mode',
        ),
    ]
