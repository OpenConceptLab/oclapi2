from django.contrib.auth.models import Group
from django.core.management import BaseCommand, CommandError

from core.users.models import UserProfile


class Command(BaseCommand):
    help = ('Add the users listed in a file (one username per line, # for comments) to a group. With Keycloak this '
            'mirrors a Keycloak membership into Django for users who only use API tokens, whose groups never '
            're-sync (ocl_online#230). Keycloak stays the source of truth.')

    def add_arguments(self, parser):
        parser.add_argument('--group', required=True)
        parser.add_argument('--usernames-file', required=True)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        group = Group.objects.filter(name=options['group']).first()
        if not group:
            raise CommandError(f"Unknown group {options['group']}: seed it from OCL_GROUPS_CONFIG first.")
        with open(options['usernames_file'], encoding='utf-8') as file:
            usernames = {line.strip() for line in file if line.strip() and not line.strip().startswith('#')}
        users = list(UserProfile.objects.filter(username__in=usernames))
        missing = sorted(usernames - {user.username for user in users})
        added = 0
        for user in users:
            if not user.groups.filter(id=group.id).exists():
                added += 1
                if not options['dry_run']:
                    user.groups.add(group)
        suffix = ' (dry run)' if options['dry_run'] else ''
        self.stdout.write(
            f'{group.name}: {added} added, {len(users) - added} already members, {len(missing)} not found{suffix}')
        for username in missing:
            self.stdout.write(f'  not found: {username}')
