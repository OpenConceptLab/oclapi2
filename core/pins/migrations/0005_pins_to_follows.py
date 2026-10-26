from django.db import migrations

# TBv2's "My Pins" listed a user's own pins plus the org pins they created (includeCreatorPins). TBv3 keeps pins as
# overview highlights and lists follows as personal shortcuts, so each person's list becomes follows.
RESOURCE_MODELS = [('orgs', 'organization'), ('sources', 'source'), ('collections', 'collection')]


def is_public(obj):
    # 'Edit' is retired and reads as 'View'
    return (obj.public_access or '').lower() in ('view', 'edit')


def can_view_org(user, org):
    """CanViewConceptDictionary on an org: it's public, or the user is staff or a member."""
    return user.is_staff or is_public(org) or user.organizations.filter(id=org.id).exists()


def can_view_repo(user, repo):
    """CanViewConceptDictionary on a repo: it's public (a version only while its HEAD is), or the user is staff, its
    owner or a member of its org."""
    if user.is_staff:
        return True
    if is_public(repo):
        head = repo if repo.version == 'HEAD' else type(repo).objects.filter(
            mnemonic=repo.mnemonic, organization_id=repo.organization_id, user_id=repo.user_id, version='HEAD',
            is_active=True).first()
        if head is None or is_public(head):
            return True
    if repo.organization_id:
        return user.organizations.filter(id=repo.organization_id).exists()
    return repo.user_id == user.id


def get_pin_follows(apps):
    """
    (follower id, content type id, resource id) for every pin, followed by the person whose TBv2 My Pins list showed
    it: a user pin's user, or an org pin's creator. Skips resources that are gone or deleted, and pins that list no
    longer showed: a resource, or the pinning org, the person can't view any more.
    """
    follows = set()
    pins = apps.get_model('pins', 'Pin').objects.select_related('resource_type', 'user', 'created_by', 'organization')
    for pin in pins:
        follower = pin.user or pin.created_by
        content_type = pin.resource_type
        if (content_type.app_label, content_type.model) not in RESOURCE_MODELS:
            continue
        resource = apps.get_model(content_type.app_label, content_type.model).objects.filter(
            id=pin.resource_id, is_active=True).first()
        can_view = can_view_org if content_type.model == 'organization' else can_view_repo
        if resource and can_view(follower, resource) and (
                pin.organization is None or can_view_org(follower, pin.organization)):
            follows.add((follower.id, content_type.id, resource.id))
    return follows


def create_follows_from_pins(apps, schema_editor):  # pylint: disable=unused-argument
    """Inserts the follows directly, not through UserProfile.follow(), so no Followed events are recorded. A follow
    the person already has stays as it is."""
    follow_model = apps.get_model('users', 'Follow')
    follow_model.objects.bulk_create(
        [
            follow_model(follower_id=follower_id, following_type_id=type_id, following_id=resource_id)
            for follower_id, type_id, resource_id in sorted(get_pin_follows(apps))
        ],
        ignore_conflicts=True
    )


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('pins', '0004_pin_created_by'),
        ('users', '0037_userprofile_sso_id'),
        ('orgs', '0022_retire_public_edit_access'),
        ('sources', '0046_retire_public_edit_access'),
        ('collections', '0071_retire_public_edit_access'),
    ]

    operations = [
        # Pins stay as they are. Undoing it does nothing, as by then people may have changed their follows themselves.
        migrations.RunPython(create_follows_from_pins, migrations.RunPython.noop),
    ]
