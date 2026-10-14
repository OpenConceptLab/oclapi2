import re

from django.db import migrations

REPO_URI = re.compile(r'^/(?:orgs|users)/[^/]+/(?:sources|collections)/[^/]+/')


def get_private_uris(model, uris, **kwargs):
    # 'Edit' is retired and reads as 'View'
    return {
        uri for uri, public_access in model.objects.filter(uri__in=uris, **kwargs).values_list('uri', 'public_access')
        if (public_access or '').lower() not in ('view', 'edit')
    }


# Followed/Unfollowed events of an org or repo the public can't view (or of content in such a repo) are private.
def make_follow_events_of_private_objects_private(apps, schema_editor):  # pylint: disable=unused-argument
    events = apps.get_model('events', 'Event').objects.filter(event_type__in=['Followed', 'Unfollowed'], public=True)
    urls = set(filter(None, events.values_list('referenced_object_url', flat=True)))
    repo_uris = {url: match.group(0) for url in urls if (match := REPO_URI.match(url))}

    private_uris = get_private_uris(apps.get_model('orgs', 'Organization'), urls)
    for app_label, model_name in [('sources', 'Source'), ('collections', 'Collection')]:
        private_uris |= get_private_uris(
            apps.get_model(app_label, model_name), set(repo_uris.values()), version='HEAD')

    private_urls = [url for url in urls if url in private_uris or repo_uris.get(url) in private_uris]
    if private_urls:
        events.filter(referenced_object_url__in=private_urls).update(public=False)


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0010_auto_20250910_0142'),
        ('orgs', '0022_retire_public_edit_access'),
        ('sources', '0046_retire_public_edit_access'),
        ('collections', '0071_retire_public_edit_access'),
    ]

    operations = [
        migrations.RunPython(make_follow_events_of_private_objects_private, migrations.RunPython.noop),
    ]
